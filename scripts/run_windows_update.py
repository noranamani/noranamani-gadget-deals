"""Task Scheduler entry point. Preview by default; --apply also requires local enabled=true."""
from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import datetime, time, timezone
from pathlib import Path

from collection_pipeline import collect_generation, validate_queries
from git_pipeline import GitRepository
from pipeline_common import (COLLECTION_FILES, JST, PipelineError, exclusive_lock, load_json,
                             read_bytes, timestamp)
from rakuten_client import RakutenClient, validate_proxy
from windows_credentials import affiliate_proof, read_windows_credentials
from output_policy import OutputPolicy

REPOSITORY_URL = 'https://github.com/noranamani/noranamani-gadget-deals.git'
CODE_ROOT = Path(__file__).resolve().parents[1]


def load_config(path: Path) -> dict:
    cfg = load_json(path)
    allowed = {'enabled', 'runtime_dir', 'proxy_url', 'ca_bundle', 'affiliate_proof'}
    if (not isinstance(cfg, dict) or set(cfg) - allowed or type(cfg.get('enabled')) is not bool
            or not isinstance(cfg.get('runtime_dir'), str) or not isinstance(cfg.get('proxy_url'), str)):
        raise PipelineError('LOCAL_CONFIGURATION_INVALID')
    validate_proxy(cfg['proxy_url'])
    runtime = Path(os.path.expandvars(cfg['runtime_dir'])).expanduser()
    if not runtime.is_absolute() or '%' in str(runtime) or '$' in str(runtime):
        raise PipelineError('RUNTIME_PATH_INVALID')
    runtime = runtime.resolve()
    if runtime.is_relative_to(CODE_ROOT) or CODE_ROOT.is_relative_to(runtime):
        raise PipelineError('RUNTIME_MUST_BE_SEPARATE')
    cfg['runtime_dir'] = runtime
    for name in ('ca_bundle', 'affiliate_proof'):
        value = cfg.get(name)
        cfg[name] = Path(os.path.expandvars(value)).expanduser().resolve() if value else None
    return cfg


def require_start_window(now: datetime) -> None:
    local = now.astimezone(JST).time().replace(tzinfo=None)
    if not time(6, 10) <= local <= time(6, 20):
        raise PipelineError('OUTSIDE_COLLECTION_START_WINDOW')


def require_finish_window(now: datetime, started: datetime) -> None:
    if (now.astimezone(JST).date() != started.astimezone(JST).date()
            or not time(6, 10) <= now.astimezone(JST).time().replace(tzinfo=None) <= time(6, 25)):
        raise PipelineError('COLLECTION_DEADLINE_EXCEEDED')


def require_push_window(now: datetime, started: datetime, expiry: str) -> None:
    require_finish_window(now, started)
    # Reserve the Git subprocess timeout before 06:25; never start a late push.
    if now.astimezone(JST).time().replace(tzinfo=None) > time(6, 23, 30):
        raise PipelineError('INSUFFICIENT_PUSH_TIME')
    if timestamp(expiry) <= now:
        raise PipelineError('AFFILIATE_PROOF_EXPIRED')


def run_update(cfg: dict, apply: bool, clock=lambda: datetime.now(timezone.utc)) -> dict:
    if apply and not cfg['enabled']:
        raise PipelineError('PRODUCTION_DISABLED')
    started = clock()
    if apply:
        require_start_window(started)
    with exclusive_lock(cfg['runtime_dir']):
        credentials = read_windows_credentials()
        verification = affiliate_proof(cfg['affiliate_proof'], credentials, started)
        if apply and not verification['verified']:
            raise PipelineError('AFFILIATE_CONFIRMATION_REQUIRED')
        bare = cfg['runtime_dir'] / 'repository.git'
        created = not bare.exists()
        bare.mkdir(exist_ok=True)
        repo = GitRepository(bare, cfg['proxy_url'], cfg['ca_bundle'])
        if created:
            repo.run('init', '--bare')
            repo.run('remote', 'add', 'origin', REPOSITORY_URL)
        if (repo.run('rev-parse', '--is-bare-repository').strip() != b'true'
                or repo.run('remote', 'get-url', 'origin').decode().strip() != REPOSITORY_URL):
            raise PipelineError('RUNTIME_REPOSITORY_INVALID')
        base = repo.fetch_main()
        run = cfg['runtime_dir'] / 'runs' / uuid.uuid4().hex
        source = run / 'input'
        repo.archive(base, source)
        queries = load_json(source / 'config/queries.json')
        validate_queries(queries)
        client = RakutenClient(credentials, cfg['proxy_url'], 2 * len(queries), cfg['ca_bundle'])
        if apply:
            original_call = client.__call__
            def requester(params):
                require_finish_window(clock(), started)
                return original_call(params)
        else:
            requester = client
        result = collect_generation(source, run / 'store', requester, verification, started,
                                    base, credentials.values(), output_policy=(client.output_policy
                                    if isinstance(client.output_policy, OutputPolicy) else None))
        result['pushed'] = False
        if apply:
            require_finish_window(clock(), started)
            if timestamp(verification['expiresAt']) <= clock():
                raise PipelineError('AFFILIATE_PROOF_EXPIRED')
            generation = Path(result['generation'])
            files = {name: read_bytes(generation / name) for name in COLLECTION_FILES}
            commit = repo.prepare_commit(base, files, 'collection')
            repo.push_if_unchanged(base, commit,
                before_push=lambda: require_push_window(clock(), started, verification['expiresAt']))
            result.update(pushed=True, commit=commit)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        result = run_update(load_config(args.config), args.apply)
        print(json.dumps({'status': 'pushed' if result['pushed'] else 'prepared_only',
                          'categories': len(result['status']['categories'])}))
        return 0
    except PipelineError as exc:
        print('WINDOWS_UPDATE_STOPPED: ' + str(exc))
        return 1
    except Exception:
        print('WINDOWS_UPDATE_STOPPED: INTERNAL_ERROR')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
