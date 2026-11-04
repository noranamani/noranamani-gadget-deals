"""Private fixed-schema run status; never serializes an exception or product data."""
import re
import uuid
from pipeline_common import PipelineError, write_json
from github_publication import require_publication_result

PHASES = {'startup', 'network_readiness', 'collection', 'daily_editorial', 'weekly_editorial', 'build', 'publication', 'complete'}
CODES = {'GIT_COMMAND_FAILED', 'GIT_UNAVAILABLE_OR_TIMEOUT', 'PUSH_CONFLICT',
    'RECOVERY_STATE_INVALID', 'RECOVERY_STATE_UNAVAILABLE', 'RECOVERY_GENERATION_CHANGED',
    'RECOVERY_EXTERNAL_STAGE',
    'RECOVERY_BACKOFF', 'PUBLICATION_ARTIFACT_EXPIRED',
    'GIT_AUTH_REQUIRED', 'GIT_TLS_INVALID',
    'GIT_RATE_LIMITED', 'PUBLICATION_RATE_LIMITED',
    'RECOVERY_ALREADY_COMPLETED',
    'RECOVERY_ALREADY_RUNNING',
    'STAGE_RESULT_UNCONFIRMED', 'STAGE_RETRY_LIMIT', 'OUTSIDE_RECOVERY_WINDOW',
    'MISSED_COLLECTION_WINDOW', 'NOTE_RECOVERY_BLOCKED',
    'NOTE_MATERIALS_PERSIST_FAILED', 'NOTE_PERSIST_OBSERVATION_UNAVAILABLE',
    'PUSH_REJECTED_OR_UNCERTAIN', 'PUSH_RESULT_UNCERTAIN', 'ALREADY_RUNNING',
    'OUTSIDE_COLLECTION_START_WINDOW', 'COLLECTION_DEADLINE_EXCEEDED',
    'CREDENTIAL_UNAVAILABLE', 'AFFILIATE_CONFIRMATION_REQUIRED', 'AFFILIATE_PROOF_EXPIRED',
    'PRODUCTS_STALE_OR_FUTURE', 'EDITORIAL_GENERATION_MISMATCH',
    'NOTE_WEEK_OR_GENERATION_STALE', 'NOTE_SOURCE_GENERATION_MISMATCH',
    'NOTE_EXISTING_BUNDLE_MISMATCH', 'COLLECTION_GENERATION_MISMATCH',
    'PUBLICATION_STATE_UNAVAILABLE', 'PUBLICATION_DEPLOYMENT_UNCERTAIN',
    'PUBLICATION_DISPATCH_UNCERTAIN', 'PUBLICATION_ALREADY_REQUESTED',
    'PUBLICATION_API_PERMISSION_REQUIRED', 'PUBLICATION_SOURCE_CHANGED',
    'PUBLICATION_EXECUTION_FAILED', 'PUBLICATION_RESULT_UNCONFIRMED',
    'PUBLICATION_STATE_PAGINATION_LIMIT',
    'PUBLICATION_RESULT_INVALID',
    'PUBLICATION_RECEIPT_SAVE_FAILED',
    'NOTE_VALIDATION_FAILED',
    'SECRET_IN_OUTPUT', 'JSON_INVALID', 'JSON_DUPLICATE_KEY', 'INPUT_UNAVAILABLE',
    'EDITORIAL_TOP_PICK_INVALID', 'EDITORIAL_CATEGORY_COVERAGE_INVALID',
    'HISTORY_OBJECT_REQUIRED', 'HISTORY_SCHEMA_INVALID', 'HISTORY_FROM_FUTURE',
    'HISTORY_RECORD_LOSS', 'CATEGORY_ABNORMALLY_SMALL', 'CATEGORY_EMPTY_RESPONSE',
    'NOTE_BUNDLE_CONTENT_INVALID', 'BUILD_OR_VALIDATION_FAILED',
    'WINDOWS_EDITORIAL_OWNERSHIP_REQUIRED', 'RUNTIME_REPOSITORY_INVALID',
    'RUN_LOG_UNAVAILABLE',
    'BEFORE_EDITORIAL_WINDOW', 'BEFORE_WEEKLY_EDITORIAL_WINDOW',
    'WAIT_DEADLINE_EXCEEDED', 'WAIT_DATE_CHANGED', 'INTERNAL_ERROR'}


def fixed_code(error):
    value = str(error)
    return value if value in CODES or re.fullmatch(r'RAKUTEN_HTTP_[1-5][0-9]{2}', value) else 'PIPELINE_ERROR'


class RunLog:
    def __init__(self, root, clock):
        self.clock = clock
        self.path = root / 'logs' / (uuid.uuid4().hex+'.json')
        self.value = {'schemaVersion': 1, 'startedAt': clock().isoformat(),
            'phase': 'startup', 'status': 'running', 'code': None,
            'publicationStatus': 'not_attempted', 'publicationRunId': None, 'publicationVerified': False,
            'weeklyNotePreviewStatus': 'not_due', 'weeklyNoteCode': None,
            'publicationApiReadFailure': None}
        self.save()

    def save(self):
        try:
            self.value['updatedAt'] = self.clock().isoformat()
            write_json(self.path, self.value)
        except Exception:
            raise PipelineError('RUN_LOG_UNAVAILABLE') from None

    def phase(self, value):
        if value not in PHASES:
            raise PipelineError('RUN_LOG_PHASE_INVALID')
        self.value['phase'] = value
        self.save()

    def finish(self, status, code=None, publication=None):
        if status not in {'success', 'failed'}:
            raise PipelineError('RUN_LOG_STATUS_INVALID')
        if status == 'success' and publication is None:
            raise PipelineError('PUBLICATION_RESULT_UNCONFIRMED')
        self.value.update(status=status, code=fixed_code(code) if code is not None else None)
        if publication is not None:
            value = require_publication_result(publication, verified_only=status == 'success')
            self.value.update(publicationStatus=value['status'], publicationRunId=value['runId'],
                publicationVerified=value['verified'])
        self.save()

    def api_read_failure(self, value):
        # A redacted and structurally verified GET failure diagnostic only.
        if (not isinstance(value, dict) or set(value) != {'resource', 'httpStatus', 'exceptionType'}
                or value['resource'] not in {'workflow_runs', 'workflow_run', 'workflow_jobs', 'other'}
                or value['exceptionType'] not in {'HTTPError', 'SSLError', 'URLError',
                    'TimeoutError', 'JSONDecodeError', 'ValueError', 'OSError', 'UnexpectedError'}
                or (value['httpStatus'] is not None and
                    (type(value['httpStatus']) is not int or not 100 <= value['httpStatus'] <= 599))):
            raise PipelineError('RUN_LOG_STATUS_INVALID')
        self.value['publicationApiReadFailure'] = value.copy()
        self.save()

    def note_preview(self, status, code=None):
        if status not in {'not_due', 'pending', 'validated', 'blocked'}:
            raise PipelineError('RUN_LOG_STATUS_INVALID')
        self.value.update(weeklyNotePreviewStatus=status,
            weeklyNoteCode=fixed_code(code) if code is not None else None)
        self.save()

    def skip(self, code):
        if code not in {'RECOVERY_ALREADY_COMPLETED', 'RECOVERY_ALREADY_RUNNING'}:
            raise PipelineError('RUN_LOG_STATUS_INVALID')
        self.value.update(status='skipped', code=code)
        self.save()
