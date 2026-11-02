"""Purpose-bound workflow dispatch using same-user GCM; no secret files or retry."""
import json
import re
import ssl
import time as timing
import urllib.error
import urllib.request
from datetime import datetime, time, timezone
from pipeline_common import JST, PipelineError, write_json, timestamp
from publication_gate import NoRedirect, publication_needed, publication_receipt

REPOSITORY = 'noranamani/noranamani-gadget-deals'
WORKFLOW = 'windows-daily-publish.yml'
VERIFIED_STATUSES = {'published', 'already_published', 'observed_other_run', 'observed_unattributed_run'}


def publication_result(status, run_id=None):
    if type(status) is not str or status not in VERIFIED_STATUSES | {'unknown', 'failed'} or (
            run_id is not None and (type(run_id) is not int or run_id <= 0)):
        raise PipelineError('PUBLICATION_RESULT_INVALID')
    verified = status in VERIFIED_STATUSES
    if verified and run_id is None:
        raise PipelineError('PUBLICATION_RESULT_UNCONFIRMED')
    return {'status': status, 'runId': run_id, 'verified': verified}


def require_publication_result(value, *, verified_only=False):
    if (not isinstance(value, dict) or set(value) != {'status', 'runId', 'verified'}
            or type(value.get('verified')) is not bool
            or value != publication_result(value.get('status'), value.get('runId'))
            or (verified_only and value['status'] not in VERIFIED_STATUSES)):
        raise PipelineError('PUBLICATION_RESULT_UNCONFIRMED')
    return value


class PublicationError(PipelineError):
    def __init__(self, code, status='unknown', run_id=None):
        super().__init__(code)
        self.publication = publication_result(status, run_id)


def publication_failure(error):
    if isinstance(error, PublicationError):
        return require_publication_result(error.publication)
    # A local/transport error during observation cannot prove that Pages failed.
    return publication_result('unknown')


class PublicationAPI:
    def __init__(self, repo, cfg):
        raw = repo.run('credential', 'fill', input=(
            'protocol=https\nhost=github.com\npath='+REPOSITORY+'.git\n\n').encode())
        fields = dict(x.split(b'=', 1) for x in raw.splitlines() if b'=' in x)
        if not fields.get(b'password'):
            raise PipelineError('PUBLICATION_API_PERMISSION_REQUIRED')
        self.token = fields[b'password'].decode()
        ctx = ssl.create_default_context()
        if cfg['ca_bundle']:
            ctx.load_verify_locations(cafile=str(cfg['ca_bundle']))
        self.opener = urllib.request.build_opener(NoRedirect(),
            urllib.request.ProxyHandler({'https': cfg['proxy_url']}),
            urllib.request.HTTPSHandler(context=ctx))

    def __repr__(self):
        return 'PublicationAPI([REDACTED])'

    def call(self, path, body=None):
        allowed_get = re.fullmatch(r'actions/(?:workflows/windows-daily-publish\.yml/runs|runs/[0-9]+(?:/jobs)?)(?:\?[^\r\n#]*)?', path)
        if (body is None and not allowed_get) or (body is not None and (
                path != 'actions/workflows/'+WORKFLOW+'/dispatches' or body != {'ref': 'main'})):
            raise PipelineError('PUBLICATION_API_CONTEXT_INVALID')
        request = urllib.request.Request('https://api.github.com/repos/'+REPOSITORY+'/'+path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': 'Bearer '+self.token, 'Accept': 'application/vnd.github+json',
                'Content-Type': 'application/json', 'User-Agent': 'gadget-windows-publication',
                'X-GitHub-Api-Version': '2026-03-10'})
        try:
            with self.opener.open(request, timeout=30) as response:
                if body is not None:
                    if response.status == 204:
                        return {}
                    if response.status == 200:
                        value = json.load(response)
                        run_id = value.get('workflow_run_id')
                        if type(run_id) is int and run_id > 0:
                            return {'workflowRunId': run_id}
                    raise ValueError()
                value = json.load(response)
                if not isinstance(value, dict):
                    raise ValueError()
                return value
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 404):
                error = (PublicationError('PUBLICATION_API_PERMISSION_REQUIRED', 'failed') if body is not None
                    else PipelineError('PUBLICATION_API_PERMISSION_REQUIRED'))
                raise error from None
            raise PipelineError('PUBLICATION_DISPATCH_UNCERTAIN' if body is not None
                else 'PUBLICATION_STATE_UNAVAILABLE') from None
        except Exception:
            raise PipelineError('PUBLICATION_DISPATCH_UNCERTAIN' if body is not None
                else 'PUBLICATION_STATE_UNAVAILABLE') from None

    def get(self, path):
        return self.call(path)

    def dispatch(self):
        return self.call('actions/workflows/'+WORKFLOW+'/dispatches', {'ref': 'main'})


def request_once(api, journal, now, source_commit, check_current=lambda: None):
    if now.astimezone(JST).time() < time(6, 50):
        raise PipelineError('BEFORE_PUBLICATION_WINDOW')
    if journal.exists():
        if publication_receipt(api.get, now) is not None:
            return 'already_published'
        raise PipelineError('PUBLICATION_ALREADY_REQUESTED')
    start = datetime.combine(now.astimezone(JST).date(), time.min, JST).astimezone(timezone.utc)
    import urllib.parse
    query = urllib.parse.urlencode({'branch': 'main', 'created': '>='+start.isoformat()})
    rows = api.get('actions/workflows/'+WORKFLOW+'/runs?'+query+'&per_page=100')['workflow_runs']
    if not isinstance(rows, list) or any(not isinstance(r.get('id'), int)
            or r.get('head_branch') != 'main' or timestamp(r.get('created_at')) < start for r in rows):
        raise PipelineError('PUBLICATION_STATE_INVALID')
    if len(rows) >= 100:
        raise PipelineError('PUBLICATION_STATE_PAGINATION_LIMIT')
    if any(r.get('status') in ('queued', 'in_progress', 'waiting', 'pending', 'requested') for r in rows):
        return 'publication_in_progress'
    if not publication_needed(api.get, now):
        return 'already_published'
    check_current()
    # Persist intent BEFORE sending. A lost response never causes an automatic retry.
    write_json(journal, {'schemaVersion': 1, 'state': 'requested',
        'requestedAt': now.isoformat(), 'sourceCommit': source_commit,
        'previousRunIds': [r['id'] for r in rows]})
    receipt = api.dispatch()
    if isinstance(receipt, dict) and type(receipt.get('workflowRunId')) is int and receipt['workflowRunId'] > 0:
        from pipeline_common import load_json
        accepted = load_json(journal)
        accepted.update(state='accepted', workflowRunId=receipt['workflowRunId'])
        write_json(journal, accepted)
    return 'requested'


def await_publication(api, journal, outcome, source_commit, clock, pause, monotonic=timing.monotonic):
    """Observe only; never repeat dispatch. Wait at most 15 minutes / 45 polls."""
    from datetime import timedelta
    from pipeline_common import load_json, timestamp
    started = clock()
    steady_started = monotonic()
    if outcome not in {'already_published', 'requested', 'publication_in_progress'}:
        raise PublicationError('PUBLICATION_RESULT_INVALID', 'failed')
    if outcome == 'already_published':
        receipt = publication_receipt(api.get, started)
        if receipt is None:
            raise PublicationError('PUBLICATION_RESULT_UNCONFIRMED')
        return publication_result('already_published', receipt['runId'])
    intent = load_json(journal) if outcome == 'requested' else None
    selected = intent.get('workflowRunId') if intent else None
    for _ in range(45):
        now = clock()
        if (now.astimezone(JST).date() != started.astimezone(JST).date()
                or now-started > timedelta(minutes=15) or monotonic()-steady_started >= 900):
            break
        start = datetime.combine(now.astimezone(JST).date(), time.min, JST).astimezone(timezone.utc)
        import urllib.parse
        query = urllib.parse.urlencode({'branch': 'main', 'created': '>='+start.isoformat()})
        rows = api.get('actions/workflows/'+WORKFLOW+'/runs?'+query+'&per_page=100')['workflow_runs']
        if len(rows) >= 100:
            raise PipelineError('PUBLICATION_STATE_PAGINATION_LIMIT')
        if selected is None:
            if intent:
                candidates = [r for r in rows if r['id'] not in intent['previousRunIds']
                    and r.get('event') == 'workflow_dispatch' and r.get('head_branch') == 'main'
                    and r.get('head_sha') == source_commit
                    and timestamp(r['created_at']) >= timestamp(intent['requestedAt'])-timedelta(seconds=5)]
                if len(candidates) > 1:
                    raise PipelineError('PUBLICATION_DISPATCH_UNCERTAIN')
            else:
                candidates = [r for r in rows if r.get('head_branch') == 'main'
                    and r.get('status') in ('queued', 'in_progress', 'waiting', 'pending', 'requested')]
                if not candidates:
                    receipt = publication_receipt(api.get, now)
                    if receipt is not None:
                        return publication_result('observed_other_run', receipt['runId'])
                    raise PublicationError('PUBLICATION_EXECUTION_FAILED', 'failed')
            if candidates:
                selected = candidates[0]['id']
        if selected is not None:
            run = api.get('actions/runs/'+str(selected))
            if run.get('head_branch') != 'main' or (intent and run.get('head_sha') != source_commit):
                raise PublicationError('PUBLICATION_SOURCE_CHANGED', 'unknown', selected)
            if run.get('status') in ('completed', 'in_progress'):
                jobs = api.get('actions/runs/'+str(selected)+'/jobs?filter=all&per_page=100')
                if jobs.get('total_count', len(jobs['jobs'])) > 100:
                    raise PipelineError('PUBLICATION_STATE_PAGINATION_LIMIT')
                for job in jobs['jobs']:
                    if job.get('name') != 'deploy':
                        continue
                    for step in job.get('steps', []):
                        if step.get('name') != 'Publish Pages' or step.get('conclusion') == 'skipped':
                            continue
                        if step.get('conclusion') == 'success':
                            # Only a 200 response containing this run ID proves
                            # ownership. A unique legacy-204 candidate can still
                            # be a simultaneous manual dispatch by another actor.
                            status = ('published' if intent and intent.get('workflowRunId') == selected
                                else 'observed_unattributed_run' if intent else 'observed_other_run')
                            return publication_result(status, selected)
                        if step.get('conclusion') is None and run.get('status') != 'completed':
                            continue  # Pages is still running; no success or failure inferred.
                        raise PublicationError('PUBLICATION_DEPLOYMENT_UNCERTAIN', 'unknown', selected)
                # The requested workflow can finish with Publish Pages skipped
                # because a competing scheduled run won the same-day receipt.
                if run.get('status') == 'completed':
                    receipt = publication_receipt(api.get, now, fail_on_uncertain=False)
                    if receipt is not None and receipt['runId'] != selected:
                        return publication_result('observed_other_run', receipt['runId'])
                    raise PublicationError('PUBLICATION_EXECUTION_FAILED', 'failed', selected)
        elif intent:
            receipt = publication_receipt(api.get, now, fail_on_uncertain=False)
            if receipt is not None:
                return publication_result('observed_other_run', receipt['runId'])
        pause(20)
    raise PublicationError('PUBLICATION_RESULT_UNCONFIRMED', 'unknown', selected)
