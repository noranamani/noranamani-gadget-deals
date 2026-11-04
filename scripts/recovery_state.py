"""Private atomic stage ledger. Fixed metadata only; never product text or credentials."""
import copy
import os
import re
import uuid
from pathlib import Path
from pipeline_common import PipelineError, load_json, write_json
from daily_run_log import fixed_code

STAGES = {'network_readiness', 'collection', 'daily_editorial', 'weekly_editorial', 'site_build',
    'note_materials', 'publication', 'note_persist', 'note_posting'}
STATUSES = {'pending', 'running', 'succeeded', 'failed', 'blocked', 'unknown', 'not_due', 'external'}


class RecoveryState:
    def __init__(self, root, day, clock):
        self.root, self.clock = Path(root), clock
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
            raise PipelineError('RECOVERY_STATE_INVALID')
        self.path = self.root/'state'/(day+'.json')
        self.value = load_json(self.path) if self.path.exists() else {
            'schemaVersion': 1, 'date': day, 'generation': None, 'stages': {}}
        if (not isinstance(self.value, dict) or set(self.value) != {'schemaVersion', 'date', 'generation', 'stages'}
                or self.value['schemaVersion'] != 1 or self.value['date'] != day
                or not isinstance(self.value['stages'], dict) or set(self.value['stages'])-STAGES):
            raise PipelineError('RECOVERY_STATE_INVALID')
        for stage, row in self.value['stages'].items():
            self.validate_row(row)
        for stage in STAGES:
            self.row(stage)

    @staticmethod
    def validate_row(row):
        keys = {'status', 'attempts', 'retryCount', 'startedAt', 'updatedAt', 'lastSuccessAt',
            'lastFailureCode', 'inputSha256', 'outputSha256', 'writeIntent', 'commit', 'artifact', 'publicationRunId', 'publicationStatus', 'owner', 'nextRetryAt', 'confirmation'}
        if (not isinstance(row, dict) or set(row) != keys or type(row['status']) is not str or row['status'] not in STATUSES
                or type(row['attempts']) is not int or row['attempts'] < 0
                or type(row['retryCount']) is not int or row['retryCount'] != max(0, row['attempts']-1)
                or type(row['writeIntent']) is not bool):
            raise PipelineError('RECOVERY_STATE_INVALID')
        if type(row['owner']) is not str or row['owner'] not in {'windows', 'actions', 'note-cloud'}:
            raise PipelineError('RECOVERY_STATE_INVALID')
        if row['confirmation'] not in (None, 'executed', 'remote_validated'):
            raise PipelineError('RECOVERY_STATE_INVALID')
        for hashes in (row['inputSha256'], row['outputSha256']):
            RecoveryState.validate_hashes(hashes)
        if row['commit'] is not None and not re.fullmatch(r'[0-9a-f]{40}', str(row['commit'])):
            raise PipelineError('RECOVERY_STATE_INVALID')
        if row['artifact'] is not None and not re.fullmatch(r'runs/[0-9a-f]{32}/(?:publication-output|note-preview)/bundle', str(row['artifact'])):
            raise PipelineError('RECOVERY_STATE_INVALID')
        if row['publicationRunId'] is not None and (type(row['publicationRunId']) is not int or row['publicationRunId'] <= 0):
            raise PipelineError('RECOVERY_STATE_INVALID')
        if row['publicationStatus'] not in (None, 'published', 'already_published', 'observed_other_run', 'observed_unattributed_run', 'unknown', 'failed'):
            raise PipelineError('RECOVERY_STATE_INVALID')
        if row['lastFailureCode'] is not None and fixed_code(row['lastFailureCode']) != row['lastFailureCode']:
            raise PipelineError('RECOVERY_STATE_INVALID')
        from pipeline_common import timestamp
        for key in ('startedAt', 'updatedAt', 'lastSuccessAt', 'nextRetryAt'):
            if row[key] is not None:
                timestamp(row[key])

    @staticmethod
    def validate_hashes(hashes):
        if not isinstance(hashes, dict) or any(not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]{0,63}', str(k))
                or not re.fullmatch(r'[0-9a-f]{64}', str(v)) for k, v in hashes.items()):
            raise PipelineError('RECOVERY_STATE_INVALID')

    def row(self, stage):
        if stage not in STAGES:
            raise PipelineError('RECOVERY_STATE_INVALID')
        return self.value['stages'].setdefault(stage, {'status': 'external' if stage == 'note_posting' else 'pending', 'attempts': 0,
            'retryCount': 0, 'startedAt': None, 'updatedAt': None, 'lastSuccessAt': None,
            'lastFailureCode': None, 'inputSha256': {}, 'outputSha256': {}, 'writeIntent': False,
            'commit': None, 'artifact': None, 'publicationRunId': None, 'publicationStatus': None,
            'nextRetryAt': None,
            'confirmation': None,
            'owner': 'note-cloud' if stage == 'note_posting' else 'actions' if stage == 'note_persist' else 'windows'})

    def save(self):
        # Copy/validate before replacement. A failed save preserves the last complete ledger.
        for row in self.value['stages'].values():
            self.validate_row(row)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name('.'+uuid.uuid4().hex+'.tmp')
        try:
            write_json(temporary, copy.deepcopy(self.value))
            if load_json(temporary) != self.value:
                raise ValueError()
            os.replace(temporary, self.path)
        except Exception:
            raise PipelineError('RECOVERY_STATE_UNAVAILABLE') from None
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                raise PipelineError('RECOVERY_STATE_UNAVAILABLE') from None

    def bind(self, generation):
        if not isinstance(generation, str) or not re.fullmatch(r'[0-9a-f]{64}', generation):
            raise PipelineError('RECOVERY_STATE_INVALID')
        if self.value['generation'] not in (None, generation):
            raise PipelineError('RECOVERY_GENERATION_CHANGED')
        self.value['generation'] = generation
        self.save()

    def begin(self, stage, inputs, *, limit=3, observing=False):
        self.validate_hashes(inputs)
        row = self.row(stage)
        from pipeline_common import timestamp
        if row['nextRetryAt'] is not None and self.clock() < timestamp(row['nextRetryAt']):
            raise PipelineError('NOTE_RECOVERY_BLOCKED' if stage.startswith('note') or stage == 'weekly_editorial'
                else 'RECOVERY_BACKOFF')
        if stage == 'note_posting' or (row['owner'] != 'windows' and not observing):
            raise PipelineError('RECOVERY_EXTERNAL_STAGE')
        if row['status'] in ('running', 'unknown') and row['writeIntent'] and not observing:
            raise PipelineError('NOTE_RECOVERY_BLOCKED' if stage.startswith('note') or stage == 'weekly_editorial'
                else 'STAGE_RESULT_UNCONFIRMED')
        if row['attempts'] >= limit:
            raise PipelineError('NOTE_RECOVERY_BLOCKED' if stage.startswith('note') or stage == 'weekly_editorial'
                else 'STAGE_RETRY_LIMIT')
        row.update(status='running', attempts=row['attempts']+1, startedAt=self.clock().isoformat(),
            updatedAt=self.clock().isoformat(), inputSha256=inputs, writeIntent=False)
        row['retryCount'] = row['attempts']-1
        self.save()

    def begin_readonly(self, stage, inputs):
        """Restartable Git/HTTP readiness. No write intent, no permanent 3-attempt cap.

        Each invocation is still bounded by its caller's three local GET/fetch
        attempts, the JST recovery window, and nextRetryAt. Only the named
        network-readiness stage may use this method.
        """
        if stage != 'network_readiness':
            raise PipelineError('RECOVERY_EXTERNAL_STAGE')
        self.validate_hashes(inputs)
        row = self.row(stage)
        from pipeline_common import timestamp
        if row['writeIntent']:
            raise PipelineError('STAGE_RESULT_UNCONFIRMED')
        if row['nextRetryAt'] is not None and self.clock() < timestamp(row['nextRetryAt']):
            raise PipelineError('RECOVERY_BACKOFF')
        row.update(status='running', attempts=row['attempts']+1,
            retryCount=row['attempts'], startedAt=self.clock().isoformat(),
            updatedAt=self.clock().isoformat(), inputSha256=inputs, writeIntent=False)
        self.save()

    def checkpoint(self, stage, *, outputs=None, commit=None, artifact=None, write_intent=False):
        row = self.row(stage)
        if outputs is not None:
            self.validate_hashes(outputs)
            row['outputSha256'] = outputs
        row.update(commit=commit, artifact=artifact, writeIntent=write_intent, updatedAt=self.clock().isoformat())
        self.save()

    def success(self, stage, inputs, outputs, *, run_id=None, artifact=None, publication_status=None, confirmation='executed'):
        if stage == 'note_posting':
            raise PipelineError('RECOVERY_EXTERNAL_STAGE')
        self.validate_hashes(inputs)
        self.validate_hashes(outputs)
        row = self.row(stage)
        last_success = row['lastSuccessAt'] if self.reusable(stage, inputs, outputs) else self.clock().isoformat()
        row.update(status='succeeded', updatedAt=self.clock().isoformat(), lastSuccessAt=last_success,
            inputSha256=inputs, outputSha256=outputs, writeIntent=False,
            publicationRunId=run_id, artifact=artifact, publicationStatus=publication_status,
            nextRetryAt=None, confirmation=confirmation)
        self.save()

    def failure(self, stage, error, *, unknown=False, blocked=False):
        row = self.row(stage)
        from datetime import timedelta
        row.update(status='unknown' if unknown else 'blocked' if blocked else 'failed',
            updatedAt=self.clock().isoformat(), lastFailureCode=fixed_code(error),
            nextRetryAt=(self.clock()+timedelta(seconds=min(10*2**min(row['attempts'], 5), 300))).isoformat())
        self.save()

    def reusable(self, stage, inputs, outputs):
        row = self.row(stage)
        return row['status'] == 'succeeded' and row['inputSha256'] == inputs and row['outputSha256'] == outputs

    def not_due(self, stage):
        self.row(stage).update(status='not_due', updatedAt=self.clock().isoformat())
        self.save()


def inspect_recovery(root, now):
    """Ledger-only diagnostic, usable after the collection window without credentials."""
    from pipeline_common import JST
    day = now.astimezone(JST).date().isoformat()
    core = {'collection', 'daily_editorial', 'site_build', 'publication'}
    pending, note_pending, previous = sorted(core), [], []
    run_id = None
    exists = (Path(root)/'state'/(day+'.json')).exists()
    if exists:
        state = RecoveryState(root, day, lambda: now)
        pending = sorted(s for s in core if state.row(s)['status'] != 'succeeded')
        note_pending = sorted(s for s in ('weekly_editorial', 'note_materials', 'note_persist')
            if state.row(s)['status'] not in ('succeeded', 'not_due'))
        run_id = state.row('publication')['publicationRunId']
    for path in sorted((Path(root)/'state').glob('*.json')):
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', path.stem) and path.stem < day:
            old = RecoveryState(root, path.stem, lambda: now)
            if any(old.row(s)['status'] != 'succeeded' for s in core):
                previous.append(path.stem)
    return {'date': day, 'ledgerExists': exists, 'pendingCoreStages': pending,
        'pendingNoteStages': note_pending, 'priorUnresolvedDates': previous,
        'savedPublicationRunId': run_id, 'allCompleted': exists and not pending and not note_pending}
