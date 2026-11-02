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
from pipeline_common import JST, PipelineError, digest, load_json, read_bytes, timestamp, write_json
from publication_pipeline import validate_collection, validate_daily, validate_publication, prepare_publication, verify_bundle, week_label
from run_windows_update import load_config, run_update, require_start_window, REPOSITORY_URL
from windows_credentials import read_windows_credentials
from windows_editorial import daily, weekly, require_windows_owner
from recovery_state import RecoveryState, inspect_recovery
from recovery_lock import recoverable_lock
from publication_gate import publication_receipt


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
    if not isinstance(old, dict):
        raise PipelineError('EDITORIAL_SCHEMA_INVALID' if kind == 'editorial' else 'NOTE_EDITORIAL_SCHEMA_INVALID')
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


def require_recovery_window(now):
    local = now.astimezone(JST).time().replace(tzinfo=None)
    if not time(6, 10) <= local <= time(9, 10):
        raise PipelineError('OUTSIDE_RECOVERY_WINDOW')


def fetch_main_ready(repo, clock, pause, *, recovering=False):
    """Bounded read-only readiness probe; never retries a push or API write."""
    for attempt in range(3):
        (require_recovery_window if recovering else require_start_window)(clock())
        try:
            return repo.fetch_main()
        except PipelineError as exc:
            if str(exc) not in {'GIT_COMMAND_FAILED', 'GIT_UNAVAILABLE_OR_TIMEOUT'} or attempt == 2:
                raise
            pause(10 * 2**attempt)


def isolated_note_error(error):
    """Only known note/input/build validation failures are isolated from Pages.

    Git conflicts, uncertain writes, secrets and unexpected failures still stop.
    Collection and daily inputs are validated outside this catch boundary.
    """
    if not isinstance(error, PipelineError) or not (str(error).startswith('NOTE_') or str(error) in {
            'JSON_INVALID', 'JSON_DUPLICATE_KEY', 'JSON_NONFINITE', 'TIMESTAMP_INVALID',
            'INPUT_UNAVAILABLE', 'EDITORIAL_GENERATION_MISMATCH',
            'BUILD_OR_VALIDATION_FAILED', 'BUILD_UNAVAILABLE_OR_TIMEOUT'}):
        raise error
    code = fixed_code(error)
    return code if code != 'PIPELINE_ERROR' else 'NOTE_VALIDATION_FAILED'


def orchestrate(cfg, clock=lambda: datetime.now(timezone.utc), pause=timing.sleep, log=None, *, recovering=False):
    if not cfg['enabled']:
        raise PipelineError('PRODUCTION_DISABLED')
    (require_recovery_window if recovering else require_start_window)(clock())
    root = cfg['runtime_dir']/'daily'
    with recoverable_lock(root):
        state = RecoveryState(root, clock().astimezone(JST).date().isoformat(), clock)
        active = [None]

        def phase(name):
            active[0] = {'build': 'site_build', 'complete': None}.get(name, name)
            if log:
                log.phase(name)

        run = root/'runs'/uuid.uuid4().hex
        repo = GitRepository(cfg['runtime_dir']/'repository.git', cfg['proxy_url'], cfg['ca_bundle'])

        def hashes(source):
            return {label: digest(source/name) for label, name in (
                ('products', 'data/products.json'), ('history', 'data/history.json'),
                ('queries', 'config/queries.json'), ('siteConfig', 'config/site.json'))}

        def snapshot(label, expected=None):
            sha = fetch_main_ready(repo, clock, pause, recovering=True)
            dest = run/label
            repo.archive(sha, dest)
            validate_collection(dest, clock())
            if expected is not None and digest(dest/'data/products.json') != expected:
                raise PipelineError('PUBLICATION_SOURCE_CHANGED')
            return sha, dest

        def edit_stage(source, base, kind, stage):
            inputs = hashes(source)
            if kind != 'editorial':
                inputs['dailyEditorial'] = digest(source/'data/editorial.json')
            filename = 'data/editorial.json' if kind == 'editorial' else 'data/note-weekly.json'
            validator = validate_daily if kind == 'editorial' else validate_publication
            try:
                validator(source, clock())
                ready = True
            except PipelineError:
                ready = False  # edit_snapshot still rejects invalid same-generation data.
            if ready:
                # The stage is ready, but this does not prove our prior push won.
                state.checkpoint(stage, commit=base)
                state.success(stage, inputs, {'editorial': digest(source/filename)}, confirmation='remote_validated')
                return
            state.begin(stage, inputs)
            files = edit_snapshot(source, kind, clock(), policy)
            if files:
                commit = repo.prepare_commit(base, files, kind)
                state.checkpoint(stage, outputs={'editorial': digest(source/filename)}, commit=commit, write_intent=True)
                repo.push_if_unchanged(base, commit, before_push=lambda: validator(source, clock()))
            state.success(stage, inputs, {'editorial': digest(source/filename)})

        def build_stage(source, base, purpose, stage, folder):
            inputs = {**hashes(source), 'dailyEditorial': digest(source/'data/editorial.json'),
                'builder': digest(source/'scripts'/('build_site.py' if purpose == 'site' else 'build_note_article.py'))}
            if purpose == 'note':
                inputs['weeklyEditorial'] = digest(source/'data/note-weekly.json')
            previous = state.row(stage)
            if previous['status'] == 'succeeded' and previous['inputSha256'] == inputs and previous['artifact']:
                artifact = root/previous['artifact']
                try:
                    manifest = verify_bundle(artifact, clock())
                    if manifest['sourceCommit'] == base and previous['outputSha256'] == {'manifest': digest(artifact/'publication-manifest.json')}:
                        return artifact
                except PipelineError:
                    pass  # Expired/damaged private artifacts are rebuilt, never deployed.
            state.begin(stage, inputs)
            output = run/folder
            prepare_publication(source, output, base, clock(), purpose=purpose)
            artifact = output/'bundle'
            verify_bundle(artifact, clock())
            state.checkpoint(stage, commit=base)
            state.success(stage, inputs, {'manifest': digest(artifact/'publication-manifest.json')},
                artifact=artifact.relative_to(root).as_posix())
            return artifact

        try:
            phase('network_readiness')
            state.begin('network_readiness', {})
            reused = False
            created = not repo.root.exists()
            repo.root.mkdir(parents=True, exist_ok=True)
            if created:
                repo.run('init', '--bare')
                repo.run('remote', 'add', 'origin', REPOSITORY_URL)
            if repo.run('rev-parse', '--is-bare-repository').strip() != b'true' or repo.run('remote', 'get-url', 'origin').decode().strip() != REPOSITORY_URL:
                raise PipelineError('RUNTIME_REPOSITORY_INVALID')
            base = fetch_main_ready(repo, clock, pause, recovering=recovering)
            state.checkpoint('network_readiness', commit=base)
            state.success('network_readiness', {}, {})
            phase('collection')
            existing = run/'existing'
            repo.archive(base, existing)
            require_windows_owner(existing)
            status = load_json(existing/'data/collection-status.json')
            if timestamp(status['collectedAt']).astimezone(JST).date() == clock().astimezone(JST).date():
                validate_collection(existing, clock())
                reused = True
            if not reused:
                try:
                    require_start_window(clock())
                except PipelineError:
                    raise PipelineError('MISSED_COLLECTION_WINDOW') from None
                # An interrupted/failed API attempt is never repeated automatically.
                state.begin('collection', hashes(existing), limit=1)
                state.checkpoint('collection', write_intent=True)
                run_update(cfg, True, clock=clock)
            _, collected = snapshot('collected')
            product_hash = digest(collected/'data/products.json')
            state.bind(product_hash)
            state.success('collection', hashes(existing), hashes(collected), confirmation='remote_validated' if reused else 'executed')
            policy = OutputPolicy(read_windows_credentials())
            phase('daily_editorial')
            wait_until((6, 30), clock, pause)
            base, source = snapshot('daily', product_hash)
            edit_stage(source, base, 'editorial', 'daily_editorial')
            note_status, note_code = 'not_due', None
            if clock().astimezone(JST).weekday() == 6:
                note_status = 'pending'
                phase('weekly_editorial')
                wait_until((6, 40), clock, pause)
                base, source = snapshot('weekly', product_hash)
                validate_daily(source, clock())
                try:
                    edit_stage(source, base, 'weekly_editorial', 'weekly_editorial')
                except PipelineError as exc:
                    note_code = (fixed_code(exc) if str(exc) in {'GIT_COMMAND_FAILED', 'GIT_UNAVAILABLE_OR_TIMEOUT',
                        'PUSH_CONFLICT', 'PUSH_REJECTED_OR_UNCERTAIN', 'PUSH_RESULT_UNCERTAIN'} else isolated_note_error(exc))
                    state.failure('weekly_editorial', exc, blocked=True,
                        unknown=state.row('weekly_editorial')['writeIntent'])
                    note_status = 'blocked'
            else:
                state.not_due('weekly_editorial')
                state.not_due('note_materials')
            wait_until((6, 50), clock, pause)
            phase('build')
            base, source = snapshot('publication', product_hash)
            artifact = build_stage(source, base, 'site', 'site_build', 'publication-output')
            if note_status == 'pending':
                try:
                    build_stage(source, base, 'note', 'note_materials', 'note-preview')
                    note_status = 'validated'
                except PipelineError as exc:
                    note_code = isolated_note_error(exc)
                    state.failure('note_materials', exc, blocked=True)
                    note_status = 'blocked'
            if log:
                log.note_preview(note_status, note_code)
            phase('publication')
            def check_current():
                validate_daily(source, clock())
                verify_bundle(artifact, clock())
                if repo.remote_tip() != base:
                    raise PipelineError('PUBLICATION_SOURCE_CHANGED')
            check_current()
            api = PublicationAPI(repo, cfg)
            journal = root/'dispatch'/(clock().astimezone(JST).date().isoformat()+'.json')
            state.begin('publication', {'manifest': digest(artifact/'publication-manifest.json')}, observing=journal.exists())
            state.checkpoint('publication', commit=base, write_intent=True)
            observed_source = base
            if recovering and journal.exists():
                intent = load_json(journal)
                observed_source = intent['sourceCommit']
                result = ('already_published' if publication_receipt(api.get, clock(), fail_on_uncertain=False)
                    else 'requested')  # Read/observe the previous intent; no new dispatch.
            else:
                result = request_once(api, journal, clock(), base, check_current)
            publication = require_publication_result(
                await_publication(api, journal, result, observed_source, clock, pause), verified_only=True)
            try:
                state.success('publication', {'manifest': digest(artifact/'publication-manifest.json')}
                    if publication['status'] == 'published' else {}, {}, run_id=publication['runId'],
                    publication_status=publication['status'], confirmation='remote_validated')
                # Actions owns persistence. Pending/failed is never inferred as success.
                if clock().astimezone(JST).weekday() == 6:
                    try:
                        jobs = api.get('actions/runs/'+str(publication['runId'])+'/jobs?filter=all&per_page=100')['jobs']
                        note_jobs = [j for j in jobs if j.get('name') == 'weekly-note']
                        if note_jobs and note_jobs[-1].get('conclusion') == 'success':
                            _, persisted = snapshot('note-persisted', product_hash)
                            validate_publication(persisted, clock())
                            from note_provenance import validate_existing
                            validate_existing(persisted, load_json(persisted/'data/note-weekly.json'))
                            state.success('note_persist', {'products': product_hash},
                                {label: digest(persisted/'note/latest'/name) for label, name in
                                    (('manifest', 'manifest.json'), ('article', 'article.md'),
                                     ('cover', 'article-cover.png'), ('zip', 'note-post.zip'))},
                                run_id=publication['runId'], confirmation='remote_validated')
                        elif note_jobs and note_jobs[-1].get('conclusion') in ('failure', 'cancelled'):
                            state.failure('note_persist', PipelineError('NOTE_MATERIALS_PERSIST_FAILED'), blocked=True)
                        else:
                            state.row('note_persist')['publicationRunId'] = publication['runId']
                            state.save()
                    except PipelineError:
                        state.failure('note_persist', PipelineError('NOTE_PERSIST_OBSERVATION_UNAVAILABLE'), unknown=True)
                else:
                    state.row('note_persist')['status'] = 'not_due'
                    state.save()
                if journal.exists():
                    receipt = load_json(journal)
                    receipt.update(state='verified' if publication['status'] == 'published' else 'observed',
                        verifiedAt=clock().isoformat(), confirmedWorkflowRunId=publication['runId'],
                        publicationStatus=publication['status'])
                    write_json(journal, receipt)
                phase('complete')
            except Exception:
                raise PublicationError('PUBLICATION_RECEIPT_SAVE_FAILED', publication['status'], publication['runId']) from None
            return {'status': publication['status'], 'publicationRunId': publication['runId'],
                'publicationVerified': publication['verified'], 'collectionReused': reused, 'sourceCommit': base,
                'productsSha256': product_hash, 'weeklyNotePreviewStatus': note_status, 'weeklyNoteCode': note_code}
        except Exception as exc:
            if active[0] in {'network_readiness', 'collection', 'daily_editorial', 'weekly_editorial', 'site_build', 'publication'}:
                row = state.row(active[0])
                try:
                    state.failure(active[0], exc, unknown=row['writeIntent'])
                except PipelineError:
                    if not isinstance(exc, PublicationError) or not exc.publication['verified']:
                        raise
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--resume', action='store_true', help='Reconcile incomplete stages; no late API collection')
    parser.add_argument('--status', action='store_true', help='Read local stage state only; no credentials or network')
    args = parser.parse_args()
    if not args.apply and not args.status:
        print('PLAN_ONLY: 06:10 collection, 06:30 daily, Sunday 06:40 weekly, 06:50 validated dispatch; no writes or network')
        return 0
    log, result = None, None
    try:
        cfg = load_config(args.config)
        if args.status:
            print(json.dumps(inspect_recovery(cfg['runtime_dir']/'daily', datetime.now(timezone.utc))))
            return 0
        log = RunLog(cfg['runtime_dir']/'daily', lambda: datetime.now(timezone.utc))
        if args.resume:
            diagnostic = inspect_recovery(cfg['runtime_dir']/'daily', datetime.now(timezone.utc))
            print('RECOVERY_STATUS: '+json.dumps(diagnostic))
            if diagnostic['allCompleted']:
                log.skip('RECOVERY_ALREADY_COMPLETED')
                return 0  # Explicit skip, not a new publication-success claim.
        result = orchestrate(cfg, log=log, recovering=True) if args.resume else orchestrate(cfg, log=log)
        publication = publication_result(result['status'], result['publicationRunId'])
        if result.get('publicationVerified') is not True:
            raise PipelineError('PUBLICATION_RESULT_UNCONFIRMED')
        require_publication_result(publication, verified_only=True)
        log.finish('success', publication=publication)
        print(json.dumps(result))
        return 0
    except Exception as exc:
        code = fixed_code(exc) if isinstance(exc, PipelineError) else 'INTERNAL_ERROR'
        if args.resume and isinstance(exc, PipelineError) and str(exc) == 'ALREADY_RUNNING':
            try:
                log.skip('RECOVERY_ALREADY_RUNNING')
                print('RECOVERY_DEFERRED: ALREADY_RUNNING')
                return 0  # Skipped because another runner owns the lock; no publication claim.
            except PipelineError:
                code = 'RUN_LOG_UNAVAILABLE'
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
