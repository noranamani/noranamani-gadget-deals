"""Prepare a legacy note manifest outside the repository. No Git writes, posting or API."""
from __future__ import annotations

import argparse
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from git_pipeline import GitRepository, valid_sha
from note_provenance import make_manifest
from pipeline_common import JST, PipelineError, digest, load_json, sha256, timestamp, write_json
from publication_pipeline import week_label


def prepare_legacy_manifest(root: Path, output: Path, source_commit: str, now: datetime) -> dict:
    root, output = root.resolve(), output.resolve()
    if (not valid_sha(source_commit) or now.tzinfo is None or output.exists()
            or output.is_relative_to(root) or root.is_relative_to(output)):
        raise PipelineError('NOTE_LEGACY_BOOTSTRAP_CONTEXT_INVALID')
    today = now.astimezone(JST).date()
    sunday = today - timedelta(days=(today.weekday() + 1) % 7)
    weekly = load_json(root / 'data/note-weekly.json')
    generated = timestamp(weekly.get('generatedAt'))
    if (today.weekday() == 6 or generated > now or generated.astimezone(JST).date() != sunday
            or str(weekly.get('weekLabel', '')).replace('～', '〜') != week_label(sunday)):
        raise PipelineError('NOTE_LEGACY_BOOTSTRAP_WEEK_INVALID')
    repo = GitRepository(root)
    if repo.run('rev-parse', 'HEAD').decode().strip() != source_commit:
        raise PipelineError('NOTE_LEGACY_BOOTSTRAP_SOURCE_CHANGED')
    # Verify exact committed bytes, even in a checkout with unrelated local files.
    for name in ('data/note-weekly.json', 'note/latest/article.md',
                 'note/latest/article-cover.png', 'note/latest/note-post.zip'):
        recorded = repo.run('rev-parse', source_commit + ':' + name).strip()
        if repo.run('hash-object', '--path=' + name, name).strip() != recorded:
            raise PipelineError('NOTE_LEGACY_BOOTSTRAP_SOURCE_CHANGED')
    # Hash canonical committed bytes, not Windows checkout CRLF or smudge-filter output.
    with tempfile.TemporaryDirectory(prefix='legacy-note-source-') as temporary:
        snapshot = Path(temporary) / 'source'
        repo.archive(source_commit, snapshot)
        for name in ('data/note-weekly.json', 'note/latest/article.md',
                     'note/latest/article-cover.png', 'note/latest/note-post.zip'):
            if digest(snapshot / name) != sha256(repo.run('show', source_commit + ':' + name)):
                raise PipelineError('NOTE_LEGACY_BOOTSTRAP_SOURCE_CHANGED')
        manifest = make_manifest(snapshot, source_commit)
    write_json(output, manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    args = parser.parse_args()
    try:
        prepare_legacy_manifest(args.root, args.output, args.source_commit, datetime.now(timezone.utc))
        print('NOTE_LEGACY_MANIFEST_PREPARED: no repository writes')
        return 0
    except PipelineError as error:
        print('NOTE_LEGACY_MANIFEST_STOPPED: ' + str(error))
        return 1
    except Exception:
        print('NOTE_LEGACY_MANIFEST_STOPPED: INTERNAL_ERROR')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
