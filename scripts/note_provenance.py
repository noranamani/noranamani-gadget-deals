"""Integrity/provenance for note bundles; keep the existing schema_version=1 reader contract."""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from build_note_manifest import build_manifest
from check_note_bundle import validate as check_note_bundle
from git_pipeline import valid_sha
from pipeline_common import PipelineError, digest, load_json, timestamp

NOTE_FILES = ('article.md', 'article-cover.png', 'note-post.zip')
COMMON_FIELDS = ('schema_version', 'site_id', 'issue_key', 'status', 'title',
                 'article_path', 'cover_path', 'source_url', 'generated_at')


def content_metadata(root: Path) -> tuple[dict, dict]:
    """Read/validate only; errors never include article text, URLs or ZIP entry names."""
    folder = root / 'note/latest'
    try:
        for name in NOTE_FILES:
            path = folder / name
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
                raise PipelineError('NOTE_BUNDLE_PATH_INVALID')
        common = build_manifest(root)
        hashes = {name: digest(folder / name) for name in NOTE_FILES}
        with zipfile.ZipFile(folder / 'note-post.zip') as archive:
            entries = archive.infolist()
            if (len(entries) != 2 or {p.filename for p in entries} != {'article.md', 'article-cover.png'}
                    or any(p.file_size > 32 * 1024 * 1024 for p in entries)):
                raise PipelineError('NOTE_ZIP_CONTENT_MISMATCH')
            for name in ('article.md', 'article-cover.png'):
                packed, separate = archive.read(name), (folder / name).read_bytes()
                # Git for Windows can check out Markdown as CRLF; retain both original files.
                if name == 'article.md':
                    packed, separate = packed.replace(b'\r\n', b'\n'), separate.replace(b'\r\n', b'\n')
                if packed != separate:
                    raise PipelineError('NOTE_ZIP_CONTENT_MISMATCH')
        return common, hashes
    except PipelineError:
        raise
    except Exception:
        raise PipelineError('NOTE_BUNDLE_CONTENT_INVALID') from None


def make_manifest(root: Path, source_commit: str | None = None) -> dict:
    """Legacy mode records an unbound preserved bundle; it never invents a product hash."""
    weekly = load_json(root / 'data/note-weekly.json')
    common, hashes = content_metadata(root)
    result = {**common, 'editorial_sha256': digest(root / 'data/note-weekly.json'), 'files_sha256': hashes}
    product_hash = weekly.get('sourceProductsSha256')
    if source_commit is None:
        if not re.fullmatch(r'[0-9a-f]{64}', str(product_hash or '')):
            raise PipelineError('NOTE_SOURCE_GENERATION_MISMATCH')
        result.update(source_binding='products-sha256', source_products_sha256=product_hash)
    else:
        if not valid_sha(source_commit) or 'sourceProductsSha256' in weekly:
            raise PipelineError('NOTE_LEGACY_BOOTSTRAP_CONTEXT_INVALID')
        result.update(source_binding='legacy-preserved-unbound', legacy_source_commit=source_commit)
    return result


def validate_existing(root: Path, weekly: dict) -> str:
    path = root / 'note/latest/manifest.json'
    if not path.is_file():
        raise PipelineError('NOTE_BOOTSTRAP_REQUIRED')
    manifest = load_json(path)
    common, hashes = content_metadata(root)
    if (any(manifest.get(k) != common.get(k) for k in COMMON_FIELDS)
            or manifest.get('generated_at') != timestamp(weekly.get('generatedAt')).isoformat()
            or manifest.get('files_sha256') != hashes
            or manifest.get('editorial_sha256') != digest(root / 'data/note-weekly.json')):
        raise PipelineError('NOTE_EXISTING_BUNDLE_MISMATCH')
    mode = manifest.get('source_binding')
    if mode == 'products-sha256':
        product_hash = weekly.get('sourceProductsSha256')
        if (not re.fullmatch(r'[0-9a-f]{64}', str(product_hash or ''))
                or manifest.get('source_products_sha256') != product_hash):
            raise PipelineError('NOTE_EXISTING_BUNDLE_MISMATCH')
    elif mode == 'legacy-preserved-unbound':
        if ('sourceProductsSha256' in weekly or 'source_products_sha256' in manifest
                or not valid_sha(manifest.get('legacy_source_commit'))):
            raise PipelineError('NOTE_EXISTING_BUNDLE_MISMATCH')
    else:
        raise PipelineError('NOTE_BOOTSTRAP_REQUIRED')
    try:
        check_note_bundle(root / 'note/latest')
    except Exception:
        raise PipelineError('NOTE_BUNDLE_CONTENT_INVALID') from None
    return mode
