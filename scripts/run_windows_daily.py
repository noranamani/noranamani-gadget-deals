"""Opt-in Windows orchestration: collect -> factual editorial -> validate -> dispatch.

No --apply: plan only, no credentials/network/writes. Never posts to note.com.
"""
import argparse
import json
import time as timing
import uuid
from datetime import datetime, time, timezone
from pathlib import Path
from daily_run_log import RunLog, fixed_code
from git_pipeline import GitRepository
from github_publication import (PublicationAPI, PublicationError, request_once, await_publication,
    require_publication_result, publication_result, publication_failure)
from output_policy import OutputPolicy
from pipeline_common import JST, PipelineError, digest, exclusive_lock, load_json, read_bytes, timestamp, write_json
from publication_pipeline import validate_collection, validate_daily, validate_publication, prepare_publication, verify_bundle, week_label
from run_windows_update import load_config, run_update, require_start_window, REPOSITORY_URL
from windows_credentials import read_windows_credentials
from windows_editorial import daily, weekly, require_windows_owner


def wait_until(slot, clock, pause=timing.sleep, monotonic=timing.monotonic):
    now = clock().astimezone(JST)
    day = now.date()
    target = datetime.combine(day, time(*slot), JST)
    started = monotonic()
    while now < target:
        if (target-now).total_seconds() > 3600 or monotonic()-started >= 3600:
            raise PipelineError('WAIT_DEADLINE_EXCEEDED')
        pause(min(30, (target-now).total_seconds()))
        now = clock().astimezone(JST)
        if now.date() != day:
            raise PipelineError('WAIT_DATE_CHANGED')


def edit_snapshot(root, kind, now, policy):
    _, _, status, _ = validate_collection(root, now)
    filename = 'data/editorial.json' if kind == 'editorial' else 'data/note-weekly.json'
    old = load_json(root/filename)  # malformed/missing existing JSON is a stop, not reset.
    if timestamp(old.get('generatedAt')) > now:
        raise PipelineError('EDITORIAL_GENERATION_MISMATCH')
    same = old.get('sourceProductsSha256') == status['productsSha256']
    if same:
        (validate_daily if kind == 'editorial' else validate_publication)(root, now)
        return {}  # Other editor already supplied a valid generation: do not overwrite.
    if kind != 'editorial' and str(old.get('weekLabel', '')).replace('～', '〜') == week_label(now.astimezone(JST).date()):
        raise PipelineError('NOTE_SOURCE_GENERATION_MISMATCH')
    value = daily(root, now) if kind == 'editorial' else weekly(root, now)
    policy.reject(value, 'editorial')
    write_json(root/filename, value)  # isolated Git archive only.
    (validate_daily if kind == 'editorial' else validate_publication)(root, now)
    return {filename: read_bytes(root/filename)}


def fetch_main_ready(repo, clock, pause):
    """Bounded read-only readiness probe; never retries a push or API write."""
    for attempt in range(3):
        require_start_window(clock())
        try:
            return repo.fetch_main()
        except PipelineError as exc:
            if str(exc) not in {'GIT_COMMAND_FAILED', 'GIT_UNAVAILABLE_OR_TIMEOUT'} or attempt == 2:
                raise
            pause(10)


def orchestrate(cfg, clock=lambda: datetime.now(timezone.utc), pause=timing.sleep, log=None):
    if not cfg['enabled']:
        raise PipelineError('PRODUCTION_DISABLED')
    require_start_window(clock())
    root = cfg['runtime_dir']/'daily'
    phase = log.phase if log else lambda _: None
    with exclusive_lock(root):
        run = root/'runs'/uuid.uuid4().hex
        repo = GitRepository(cfg['runtime_dir']/'repository.git', cfg['proxy_url'], cfg['ca_bundle'])

        def snapshot(label, expected=None):
            sha = repo.fetch_main()
            dest = run/label
            repo.archive(sha, dest)
            validate_collection(dest, clock())
            if expected is not None and digest(dest/'data/products.json') != expected:
                raise PipelineError('PUBLICATION_SOURCE_CHANGED')
            return sha, dest

        phase('network_readiness')
        reused = False
        created = not repo.root.exists()
        repo.root.mkdir(parents=True, exist_ok=True)
        if created:
            repo.run('init', '--bare')
            repo.run('remote', 'add', 'origin', REPOSITORY_URL)
        if repo.run('rev-parse', '--is-bare-repository').strip() != b'true' or repo.run('remote', 'get-url', 'origin').decode().strip() != REPOSITORY_URL:
            raise PipelineError('RUNTIME_REPOSITORY_INVALID')
        base = fetch_main_ready(repo, clock, pause)
        phase('collection')
        existing = run/'existing'
        repo.archive(base, existing)
        require_windows_owner(existing)  # before any Rakuten request
        status = load_json(existing/'data/collection-status.json')
        if timestamp(status['collectedAt']).astimezone(JST).date() == clock().astimezone(JST).date():
            validate_collection(existing, clock())
            reused = True
        if not reused:
            run_update(cfg, True, clock=clock)  # Original API budget and 06:25 deadline unchanged.
        _, collected = snapshot('collected')
        product_hash = digest(collected/'data/products.json')
        policy = OutputPolicy(read_windows_credentials())
        phase('daily_editorial')
        wait_until((6, 30), clock, pause)
        base, source = snapshot('daily', product_hash)
        files = edit_snapshot(source, 'editorial', clock(), policy)
        if files:
            commit = repo.prepare_commit(base, files, 'editorial')
            repo.push_if_unchanged(base, commit, before_push=lambda: validate_daily(source, clock()))
        if clock().astimezone(JST).weekday() == 6:
            phase('weekly_editorial')
            wait_until((6, 40), clock, pause)
            base, source = snapshot('weekly', product_hash)
            files = edit_snapshot(source, 'weekly_editorial', clock(), policy)
            if files:
                commit = repo.prepare_commit(base, files, 'weekly_editorial')
                repo.push_if_unchanged(base, commit, before_push=lambda: validate_publication(source, clock()))
        wait_until((6, 50), clock, pause)
        phase('build')
        base, source = snapshot('publication', product_hash)
        output = run/'publication-output'
        prepare_publication(source, output, base, clock())
        verify_bundle(output/'bundle', clock())
        phase('publication')
        def check_current():
            validate_publication(source, clock())
            if repo.remote_tip() != base:
                raise PipelineError('PUBLICATION_SOURCE_CHANGED')
        check_current()
        api = PublicationAPI(repo, cfg)
        journal = root/'dispatch'/(clock().astimezone(JST).date().isoformat()+'.json')
        result = request_once(api, journal, clock(), base, check_current)
        publication = require_publication_result(
            await_publication(api, journal, result, base, clock, pause), verified_only=True)
        try:
            if journal.exists():
                receipt = load_json(journal)
                receipt.update(state='verified' if publication['status'] == 'published' else 'observed',
                    verifiedAt=clock().isoformat(), confirmedWorkflowRunId=publication['runId'],
                    publicationStatus=publication['status'])
                write_json(journal, receipt)
            phase('complete')
        except Exception:
            # Receipt/log persistence can fail AFTER a verified deployment.
            # Preserve the observed fact while still exiting with an error.
            raise PublicationError('PUBLICATION_RECEIPT_SAVE_FAILED',
                publication['status'], publication['runId']) from None
        return {'status': publication['status'], 'publicationRunId': publication['runId'],
            'publicationVerified': publication['verified'], 'collectionReused': reused, 'sourceCommit': base,
            'productsSha256': product_hash}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not args.apply:
        print('PLAN_ONLY: 06:10 collection, 06:30 daily, Sunday 06:40 weekly, 06:50 validated dispatch; no writes or network')
        return 0
    log, result = None, None
    try:
        cfg = load_config(args.config)
        log = RunLog(cfg['runtime_dir']/'daily', lambda: datetime.now(timezone.utc))
        result = orchestrate(cfg, log=log)
        publication = publication_result(result['status'], result['publicationRunId'])
        if result.get('publicationVerified') is not True:
            raise PipelineError('PUBLICATION_RESULT_UNCONFIRMED')
        require_publication_result(publication, verified_only=True)
        log.finish('success', publication=publication)
        print(json.dumps(result))
        return 0
    except Exception as exc:
        code = fixed_code(exc) if isinstance(exc, PipelineError) else 'INTERNAL_ERROR'
        publication = None
        if isinstance(result, dict) and result.get('publicationVerified') is True:
            try:
                publication = require_publication_result(
                    publication_result(result.get('status'), result.get('publicationRunId')), verified_only=True)
            except PipelineError:
                publication = publication_failure(exc)
        elif isinstance(exc, PublicationError) or (log and log.value['phase'] == 'publication'):
            publication = publication_failure(exc)
        if log:
            try:
                log.finish('failed', code, publication=publication)
            except PipelineError:
                code = 'RUN_LOG_UNAVAILABLE'
        print('WINDOWS_DAILY_STOPPED: '+code)
        if publication:
            print(json.dumps({'publicationStatus': publication['status'],
                'publicationRunId': publication['runId'], 'publicationVerified': publication['verified']}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
