"""Read-only publication gate. No Rakuten calls, Git writes or deployment."""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, time, timezone
from pathlib import Path

from pipeline_common import JST, PipelineError, timestamp
from publication_pipeline import validate_publication, validate_site_publication

WORKFLOW = 'windows-daily-publish.yml'
DEPLOY_STEP = 'Publish Pages'


def publication_receipt(get, now: datetime, *, fail_on_uncertain=True):
    """GitHub's completed deployment step is the receipt, not a note commit.

    A failed/cancelled deployment attempt is ambiguous: it may have reached
    Pages before losing its response. Stop instead of blindly deploying twice.
    Failed builds and missing/late editorial have no deployment attempt and
    can recover on the next scheduled check.
    """
    start = datetime.combine(now.astimezone(JST).date(), time.min, JST).astimezone(timezone.utc)
    query = urllib.parse.urlencode({'branch': 'main', 'created': '>=' + start.isoformat()})
    uncertain = False
    published = None
    for page in range(1, 11):
        payload = get(f'actions/workflows/{WORKFLOW}/runs?{query}&per_page=100&page={page}')
        rows = payload.get('workflow_runs')
        if not isinstance(rows, list):
            raise PipelineError('PUBLICATION_STATE_INVALID')
        for run in rows:
            if (not isinstance(run.get('id'), int) or run.get('head_branch') != 'main'
                    or timestamp(run.get('created_at')) < start):
                raise PipelineError('PUBLICATION_STATE_INVALID')
            for job_page in range(1, 11):
                jobs = get(f'actions/runs/{run["id"]}/jobs?filter=all&per_page=100&page={job_page}').get('jobs')
                if not isinstance(jobs, list):
                    raise PipelineError('PUBLICATION_STATE_INVALID')
                for job in jobs:
                    if job.get('name') != 'deploy':
                        continue
                    for step in job.get('steps', []):
                        if step.get('name') != DEPLOY_STEP or step.get('conclusion') == 'skipped':
                            continue
                        conclusion = step.get('conclusion')
                        if conclusion == 'success':
                            published = {'runId': run['id']}
                        elif step.get('started_at') or conclusion in ('failure', 'cancelled', 'timed_out'):
                            uncertain = True
                if len(jobs) < 100:
                    break
            else:
                raise PipelineError('PUBLICATION_STATE_PAGINATION_LIMIT')
        if len(rows) < 100:
            break
    else:
        raise PipelineError('PUBLICATION_STATE_PAGINATION_LIMIT')
    if published:
        return published
    if uncertain and fail_on_uncertain:
        raise PipelineError('PUBLICATION_DEPLOYMENT_UNCERTAIN')
    return None


def publication_needed(get, now: datetime) -> bool:
    # New requests remain fail-closed. Observation-only callers may wait for
    # an in-flight deployment, but can never infer success from its presence.
    return publication_receipt(get, now) is None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def github_reader(repository: str, token: str):
    if repository != 'noranamani/noranamani-gadget-deals' or not token:
        raise PipelineError('PUBLICATION_API_CONTEXT_INVALID')
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    def get(path):
        request = urllib.request.Request('https://api.github.com/repos/' + repository + '/' + path,
            headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                     'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'gadget-publication-gate'})
        try:
            with opener.open(request, timeout=30) as response:
                value = json.load(response)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except Exception:
            raise PipelineError('PUBLICATION_STATE_UNAVAILABLE') from None
    return get


def gate(root: Path, now: datetime, get, *, site_only=False) -> bool:
    # Never make an early trigger publish before the intended publication slot.
    if now.astimezone(JST).time() < time(6, 50):
        raise PipelineError('BEFORE_PUBLICATION_WINDOW')
    (validate_site_publication if site_only else validate_publication)(root, now)
    return publication_needed(get, now)



def scheduled_gate(root: Path, now: datetime, get) -> str:
    """Scheduled *observation* decision, never a deployment authorization on errors.

    A real Pages receipt wins even if fresh product data has since expired.
    Otherwise all content and ambiguity gates remain strict. The caller
    converts errors to BLOCKED, not to published or publishable.
    """
    if now.astimezone(JST).time() < time(6, 50):
        raise PipelineError('BEFORE_PUBLICATION_WINDOW')
    if publication_receipt(get, now) is not None:
        return 'already_published'
    validate_site_publication(root, now)
    return 'ready'


def output_decision(publish: bool, status: str, reason: str = 'NONE') -> None:
    if status not in {'ready', 'already_published', 'blocked'}:
        raise PipelineError('PUBLICATION_GATE_DECISION_INVALID')
    if not re.fullmatch(r'[A-Z][A-Z0-9_]{2,79}', reason):
        raise PipelineError('PUBLICATION_GATE_REASON_INVALID')
    fields = {'publish': 'true' if publish else 'false',
              'gate_status': status, 'gate_reason': reason}
    print('PUBLICATION_GATE_DECISION: ' + status + ' / ' + reason)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            for key, value in fields.items():
                output.write(key + '=' + value + '\n')



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--site-only', action='store_true')
    parser.add_argument('--scheduled-check', action='store_true',
        help='schedule only: block safely and report once instead of repeated failure runs')
    args = parser.parse_args()
    try:
        now = datetime.now(timezone.utc)
        get = github_reader(os.environ.get('GITHUB_REPOSITORY', ''), os.environ.get('GH_TOKEN', ''))
        run_id = os.environ.get('GITHUB_RUN_ID', '')
        if not re.fullmatch(r'[0-9]+', run_id):
            raise PipelineError('PUBLICATION_API_CONTEXT_INVALID')
        run = get('actions/runs/' + run_id)
        # Re-run today's attempts only; an old run is not a new daily receipt.
        if timestamp(run.get('created_at')).astimezone(JST).date() != now.astimezone(JST).date():
            raise PipelineError('PUBLICATION_RUN_WRONG_DAY')
        if args.scheduled_check:
            # Only a GitHub schedule may turn a validation error into a
            # monitored, non-publishing BLOCKED outcome. Manual dispatch and
            # the real Windows publication path keep their strict behavior.
            if os.environ.get('GITHUB_EVENT_NAME') != 'schedule' or not args.site_only:
                raise PipelineError('PUBLICATION_SCHEDULE_CONTEXT_INVALID')
            try:
                status = scheduled_gate(args.root, now, get)
            except PipelineError as exc:
                raw = str(exc)
                reason = raw if re.fullmatch(r'[A-Z][A-Z0-9_]{2,79}', raw) else 'PUBLICATION_GATE_ERROR'
                output_decision(False, 'blocked', reason)
                return 0
            output_decision(status == 'ready', status)
            return 0
        needed = gate(args.root, now, get, site_only=args.site_only)
        output_decision(needed, 'ready' if needed else 'already_published')
        return 0
    except PipelineError as exc:
        print('PUBLICATION_GATE_STOPPED: ' + str(exc))
        return 1
    except Exception:
        print('PUBLICATION_GATE_STOPPED: INTERNAL_ERROR')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
