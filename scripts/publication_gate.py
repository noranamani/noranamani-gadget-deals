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
from publication_pipeline import validate_publication

WORKFLOW = 'windows-daily-publish.yml'
DEPLOY_STEP = 'Publish Pages'


def publication_needed(get, now: datetime) -> bool:
    """GitHub's completed deployment step is the receipt, not a note commit.

    A failed/cancelled deployment attempt is ambiguous: it may have reached
    Pages before losing its response. Stop instead of blindly deploying twice.
    Failed builds and missing/late editorial have no deployment attempt and
    can recover on the next scheduled check.
    """
    start = datetime.combine(now.astimezone(JST).date(), time.min, JST).astimezone(timezone.utc)
    query = urllib.parse.urlencode({'branch': 'main', 'created': '>=' + start.isoformat()})
    uncertain = False
    published = False
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
                            published = True
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
        return False
    if uncertain:
        raise PipelineError('PUBLICATION_DEPLOYMENT_UNCERTAIN')
    return True


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


def gate(root: Path, now: datetime, get) -> bool:
    # Never make an early trigger publish before the intended publication slot.
    if now.astimezone(JST).time() < time(6, 50):
        raise PipelineError('BEFORE_PUBLICATION_WINDOW')
    validate_publication(root, now)  # same strict freshness, category and hash rules
    return publication_needed(get, now)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
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
        needed = gate(args.root, now, get)
        value = 'true' if needed else 'false'
        print('publish=' + value)
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as f:
                f.write('publish=' + value + '\n')
        return 0
    except PipelineError as exc:
        print('PUBLICATION_GATE_STOPPED: ' + str(exc))
        return 1
    except Exception:
        print('PUBLICATION_GATE_STOPPED: INTERNAL_ERROR')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
