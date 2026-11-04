"""Prepare Pages/note artifacts after strict generation and editorial checks. No note posting."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collection_pipeline import validate_queries
from git_pipeline import GitRepository, valid_sha
from note_provenance import make_manifest as make_note_manifest, validate_existing as validate_existing_note
from pipeline_common import (JST, PipelineError, digest, load_json, read_bytes, sha256,
                             timestamp, validate_history, write_json)


def week_label(end):
    return (end - timedelta(days=6)).strftime('%Y/%m/%d') + '〜' + end.strftime('%Y/%m/%d')


def validate_collection(root: Path, now: datetime, *, max_age_hours: int = 3) -> tuple:
    if max_age_hours not in (3, 11):
        raise PipelineError('COLLECTION_AGE_POLICY_INVALID')
    products = load_json(root / 'data/products.json')
    history = load_json(root / 'data/history.json')
    status = load_json(root / 'data/collection-status.json')
    queries = load_json(root / 'config/queries.json')
    validate_history(history)
    validate_queries(queries)
    if (status.get('schemaVersion') != 1 or not valid_sha(status.get('sourceCommit'))
            or status.get('productsSha256') != digest(root / 'data/products.json')
            or status.get('historySha256') != digest(root / 'data/history.json')
            or status.get('queriesSha256') != digest(root / 'config/queries.json')
            or status.get('siteConfigSha256') != digest(root / 'config/site.json')
            or status.get('collectedAt') != products.get('updatedAt')):
        raise PipelineError('COLLECTION_GENERATION_MISMATCH')
    if status.get('affiliateVerified') is not True or timestamp(status.get('affiliateVerificationExpiresAt')) <= now:
        raise PipelineError('AFFILIATE_CONFIRMATION_REQUIRED')
    collected = timestamp(products.get('updatedAt'))
    today = now.astimezone(JST).date()
    if (collected.astimezone(JST).date() != today or collected > now or now - collected > timedelta(hours=max_age_hours)
            or (collected.astimezone(JST).hour, collected.astimezone(JST).minute) < (6, 10)):
        raise PipelineError('PRODUCTS_STALE_OR_FUTURE')
    if [c['id'] for c in products.get('categories', [])] != [q['id'] for q in queries]:
        raise PipelineError('PRODUCT_CATEGORY_COVERAGE_INVALID')
    return products, history, status, queries


def validate_daily(root: Path, now: datetime, *, max_age_hours: int = 3) -> dict:
    products, history, status, queries = validate_collection(root, now, max_age_hours=max_age_hours)
    editorial = load_json(root / 'data/editorial.json')
    collected, edited = timestamp(products.get('updatedAt')), timestamp(editorial.get('generatedAt'))
    today = now.astimezone(JST).date()
    if (edited < collected or edited > now or edited.astimezone(JST).date() != today
            or (edited.astimezone(JST).hour, edited.astimezone(JST).minute) < (6, 30)
            or editorial.get('sourceProductsSha256') != status['productsSha256']):
        raise PipelineError('EDITORIAL_GENERATION_MISMATCH')
    categories = products.get('categories', [])
    ids = {q['id'] for q in queries}
    if [c['id'] for c in categories] != [q['id'] for q in queries]:
        raise PipelineError('PRODUCT_CATEGORY_COVERAGE_INVALID')
    notes = editorial.get('categoryNotes', [])
    if not isinstance(notes, list) or len(notes) != len(ids) or {r.get('categoryId') for r in notes} != ids:
        raise PipelineError('EDITORIAL_CATEGORY_COVERAGE_INVALID')
    by_id = {c['id']: {i['code'] for i in c['items']} for c in categories}
    for row in notes:
        if row.get('topPickCode') and row['topPickCode'] not in by_id[row['categoryId']]:
            raise PipelineError('EDITORIAL_TOP_PICK_INVALID')
    return editorial


def validate_publication(root: Path, now: datetime, *, max_age_hours: int = 3) -> dict:
    editorial = validate_daily(root, now, max_age_hours=max_age_hours)
    status = load_json(root / 'data/collection-status.json')
    weekly = load_json(root / 'data/note-weekly.json')
    if not isinstance(weekly, dict):
        raise PipelineError('NOTE_EDITORIAL_SCHEMA_INVALID')
    edited = timestamp(editorial.get('generatedAt'))
    today = now.astimezone(JST).date()
    sunday = today - timedelta(days=(today.weekday() + 1) % 7)
    try:
        generated = timestamp(weekly.get('generatedAt'))
    except PipelineError:
        raise PipelineError('NOTE_WEEK_OR_GENERATION_STALE') from None
    if (str(weekly.get('weekLabel', '')).replace('～', '〜') != week_label(sunday)
            or generated.astimezone(JST).date() != sunday or generated > now
            or now - generated > timedelta(days=7)):
        raise PipelineError('NOTE_WEEK_OR_GENERATION_STALE')
    if today.weekday() == 6:
        if (generated < edited or weekly.get('sourceProductsSha256') != status['productsSha256']
                or (generated.astimezone(JST).hour, generated.astimezone(JST).minute) < (6, 40)):
            raise PipelineError('NOTE_SOURCE_GENERATION_MISMATCH')
    else:
        # On weekdays reuse the prior Sunday's bundle; do not regenerate old editorial with new prices.
        validate_existing_note(root, weekly)
    return {'includeNote': today.weekday() == 6, 'weeklyEnd': sunday.isoformat(),
            'productsSha256': status['productsSha256']}


def validate_site_publication(root: Path, now: datetime) -> dict:
    """Daily site contract: no dependency on weekly editorial or note bundles."""
    validate_daily(root, now)
    return {'includeNote': False,
        'productsSha256': load_json(root/'data/collection-status.json')['productsSha256']}


def validate_purpose(root: Path, now: datetime, purpose: str) -> dict:
    if purpose == 'site':
        return validate_site_publication(root, now)
    if purpose == 'note' and now.astimezone(JST).weekday() != 6:
        raise PipelineError('NOTE_NOT_DUE')
    if purpose not in ('combined', 'note'):
        raise PipelineError('PUBLICATION_CONTEXT_INVALID')
    return validate_publication(root, now, max_age_hours=11 if purpose == 'note' else 3)


def run_script(root: Path, name: str, *args):
    try:
        result = subprocess.run([sys.executable, '-X', 'utf8', str(root / 'scripts' / name), *args],
            cwd=root, capture_output=True, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'}, timeout=90)
    except (OSError, subprocess.TimeoutExpired):
        raise PipelineError('BUILD_UNAVAILABLE_OR_TIMEOUT') from None
    if result.returncode:
        raise PipelineError('BUILD_OR_VALIDATION_FAILED')


def prepare_publication(root: Path, output: Path, source_commit: str, now: datetime, *, purpose='combined') -> dict:
    root, output = root.resolve(), output.resolve()
    if (not valid_sha(source_commit) or output.exists() or output.is_relative_to(root)
            or root.is_relative_to(output)):
        raise PipelineError('PUBLICATION_CONTEXT_INVALID')
    decision = validate_purpose(root, now, purpose)
    work = output / 'work'
    work.mkdir(parents=True)
    folders = ('scripts', 'config', 'data') + (() if purpose == 'note' else ('docs',)) + (() if purpose == 'site' else ('note',))
    for folder in folders:
        if (root / folder).is_symlink() or any(p.is_symlink() for p in (root / folder).rglob('*')):
            raise PipelineError('PUBLICATION_SOURCE_PATH_UNSAFE')
        if purpose == 'note' and folder == 'note' and not (root/folder).is_dir():
            raise PipelineError('NOTE_BUNDLE_PATH_INVALID')
        shutil.copytree(root / folder, work / folder)
    bundle = output / 'bundle'
    if purpose != 'note':
        run_script(work, 'build_site.py')
        run_script(work, 'validate.py')
        shutil.copytree(work / 'docs', bundle / 'docs')
    if decision['includeNote']:
        # A Pages failure may follow a successful note commit. Reuse that exact
        # issue on recovery; never overwrite an already persisted Sunday issue.
        note_manifest = work / 'note/latest/manifest.json'
        saved_manifest = load_json(note_manifest) if note_manifest.is_file() else None
        if saved_manifest is not None and not isinstance(saved_manifest, dict):
            raise PipelineError('NOTE_BUNDLE_CONTENT_INVALID')
        same_issue = (saved_manifest is not None and
            timestamp(saved_manifest.get('generated_at')).astimezone(JST).date().isoformat() == decision['weeklyEnd'])
        if same_issue:
            if validate_existing_note(work, load_json(work / 'data/note-weekly.json')) != 'products-sha256':
                raise PipelineError('NOTE_SOURCE_GENERATION_MISMATCH')
            for name in ('article.md', 'article-cover.png'):
                archive = work / 'note/archive' / decision['weeklyEnd'] / name
                if not archive.is_file() or digest(archive) != digest(work / 'note/latest' / name):
                    raise PipelineError('NOTE_EXISTING_ARCHIVE_MISMATCH')
        else:
            archive = work / 'note/archive' / decision['weeklyEnd']
            if archive.exists():
                raise PipelineError('NOTE_EXISTING_ARCHIVE_MISMATCH')
            run_script(work, 'build_note_article.py')
            manifest = make_note_manifest(work)
            write_json(note_manifest, manifest)
        run_script(work, 'check_note_bundle.py', 'note/latest')
        names = ['note/latest/' + n for n in ('article.md', 'article-cover.png', 'note-post.zip', 'manifest.json')]
        names += ['note/archive/' + decision['weeklyEnd'] + '/' + n for n in ('article.md', 'article-cover.png')]
        for name in names:
            path = bundle / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(work / name, path)
    elif purpose == 'combined':
        run_script(work, 'check_note_bundle.py', 'note/latest')
    files = {}
    for path in bundle.rglob('*'):
        if path.is_symlink():
            raise PipelineError('PUBLICATION_ARTIFACT_PATH_INVALID')
        if path.is_file():
            files[path.relative_to(bundle).as_posix()] = digest(path)
    manifest = {'schemaVersion': 1, 'sourceCommit': source_commit, 'preparedAt': now.isoformat(),
                'includeNote': decision['includeNote'], 'sha256': files}
    if purpose != 'combined':
        manifest['purpose'] = purpose
    write_json(bundle / 'publication-manifest.json', manifest)
    return manifest


def verify_bundle(bundle: Path, now: datetime) -> dict:
    manifest = load_json(bundle / 'publication-manifest.json')
    if (manifest.get('schemaVersion') != 1 or not valid_sha(manifest.get('sourceCommit'))
            or type(manifest.get('includeNote')) is not bool):
        raise PipelineError('PUBLICATION_MANIFEST_INVALID')
    prepared = timestamp(manifest.get('preparedAt'))
    if prepared > now or now - prepared > timedelta(minutes=30):
        raise PipelineError('PUBLICATION_ARTIFACT_EXPIRED')
    actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob('*') if p.is_file()}
    if bundle.is_symlink() or any(p.is_symlink() for p in bundle.rglob('*')):
        raise PipelineError('PUBLICATION_ARTIFACT_PATH_INVALID')
    expected = manifest.get('sha256', {})
    if not isinstance(expected, dict) or actual != set(expected) | {'publication-manifest.json'}:
        raise PipelineError('PUBLICATION_ARTIFACT_FILES_MISMATCH')
    purpose = manifest.get('purpose', 'combined')
    if (type(purpose) is not str or purpose not in ('site', 'note', 'combined')
            or (purpose == 'site' and (manifest['includeNote'] or 'docs/index.html' not in expected
                or any(not n.startswith('docs/') for n in expected)))
            or (purpose == 'note' and (not manifest['includeNote'] or not expected
                or any(not n.startswith('note/') for n in expected)))):
        raise PipelineError('PUBLICATION_ARTIFACT_PURPOSE_MISMATCH')
    for name, value in expected.items():
        path = bundle / name
        if (path.is_symlink() or not path.resolve().is_relative_to(bundle.resolve())
                or not re.fullmatch(r'[0-9a-f]{64}', str(value))
                or not (name.startswith('docs/') or name.startswith('note/')) or digest(path) != value):
            raise PipelineError('PUBLICATION_ARTIFACT_HASH_MISMATCH')
    return manifest


def persist_note(repo: GitRepository, bundle: Path, now: datetime, apply=False) -> str:
    if not apply:
        raise PipelineError('PUBLICATION_WRITE_NOT_APPROVED')
    manifest = verify_bundle(bundle, now)
    base = manifest['sourceCommit']
    if repo.remote_tip() != base:
        raise PipelineError('PUSH_CONFLICT')
    if not manifest['includeNote']:
        return base
    files = {name: read_bytes(bundle / name) for name in manifest['sha256'] if name.startswith('note/')}
    commit = repo.prepare_commit(base, files, 'note')
    return repo.push_if_unchanged(base, commit)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'persist-note', 'verify-current'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--source-commit')
    parser.add_argument('--expected-main')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--purpose', choices=('combined', 'site', 'note'), default='combined')
    args = parser.parse_args()
    try:
        now = datetime.now(timezone.utc)
        if args.mode == 'prepare':
            if args.output is None:
                raise PipelineError('OUTPUT_REQUIRED')
            manifest = prepare_publication(args.root, args.output, args.source_commit, now, purpose=args.purpose)
            result = {'source_commit': manifest['sourceCommit']}
        else:
            if args.bundle is None:
                raise PipelineError('BUNDLE_REQUIRED')
            validate_purpose(args.root, now, args.purpose)
            manifest = verify_bundle(args.bundle, now)
            if manifest.get('purpose', 'combined') != args.purpose:
                raise PipelineError('PUBLICATION_ARTIFACT_PURPOSE_MISMATCH')
            repo = GitRepository(args.root)
            if args.mode == 'persist-note':
                if os.environ.get('GADGET_PUBLICATION_ENABLED') != '1':
                    raise PipelineError('PRODUCTION_DISABLED')
                result = {'published_commit': persist_note(repo, args.bundle, now, args.apply)}
            else:
                verify_bundle(args.bundle, now)
                if not valid_sha(args.expected_main) or repo.remote_tip() != args.expected_main:
                    raise PipelineError('REMOTE_ADVANCED_BEFORE_DEPLOY')
                result = {'verified': True}
        for key, value in result.items():
            print(str(key) + '=' + str(value))
            if os.environ.get('GITHUB_OUTPUT'):
                with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as handle:
                    handle.write(str(key) + '=' + str(value) + '\n')
        return 0
    except PipelineError as exc:
        print('PUBLICATION_STOPPED: ' + str(exc))
        return 1
    except Exception:
        print('PUBLICATION_STOPPED: INTERNAL_ERROR')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
