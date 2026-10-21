"""Shared fail-closed validation and local snapshot storage (standard library only)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
COLLECTION_FILES = ('data/products.json', 'data/history.json', 'data/collection-status.json')
SOURCE_FILES = ('data/products.json', 'data/history.json', 'config/queries.json', 'config/site.json')


class PipelineError(RuntimeError):
    """Only fixed error codes belong here; never interpolate URLs, payloads or credentials."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_bytes(path: Path) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError:
        raise PipelineError('INPUT_UNAVAILABLE') from None


def digest(path: Path) -> str:
    return sha256(read_bytes(path))


def json_bytes(value) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    except (ValueError, TypeError, UnicodeError):
        raise PipelineError('JSON_SERIALIZATION_INVALID') from None


def parse_json(data: bytes):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise PipelineError('JSON_DUPLICATE_KEY')
            result[key] = value
        return result
    def nonfinite(value):
        raise PipelineError('JSON_NONFINITE')
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_constant=nonfinite)
    except (ValueError, UnicodeError):
        raise PipelineError('JSON_INVALID') from None


def load_json(path: Path):
    return parse_json(read_bytes(path))


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def write_json(path: Path, value) -> None:
    write_bytes(path, json_bytes(value))


def timestamp(value: str) -> datetime:
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError()
        return stamp
    except (ValueError, TypeError):
        raise PipelineError('TIMESTAMP_INVALID') from None


def validate_history(history) -> None:
    if not isinstance(history, dict) or not history:
        raise PipelineError('HISTORY_OBJECT_REQUIRED')
    for code, rows in history.items():
        if not isinstance(code, str) or not code or not isinstance(rows, list):
            raise PipelineError('HISTORY_SCHEMA_INVALID')
        previous = ''
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'date', 'price'}:
                raise PipelineError('HISTORY_ROW_INVALID')
            stamp, price = row['date'], row['price']
            try:
                valid = isinstance(stamp, str) and date.fromisoformat(stamp).isoformat() == stamp
            except ValueError:
                valid = False
            if not valid or (previous and stamp <= previous):
                raise PipelineError('HISTORY_DATES_INVALID')
            if type(price) is not int or price <= 0:
                raise PipelineError('HISTORY_PRICE_INVALID')
            previous = stamp


def history_preserved(before: dict, after: dict) -> dict:
    validate_history(after)
    for code, rows in before.items():
        if code not in after or after[code][:len(rows)] != rows:
            raise PipelineError('HISTORY_RECORD_LOSS')
    return {'previousKeys': len(before), 'previousRecords': sum(map(len, before.values())),
            'currentKeys': len(after), 'currentRecords': sum(map(len, after.values()))}


def source_hashes(root: Path) -> dict:
    root = root.resolve()
    for name in SOURCE_FILES:
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise PipelineError('INPUT_PATH_UNSAFE')
    return {name: digest(root / name) for name in SOURCE_FILES}


def assert_source_unchanged(root: Path, expected: dict) -> None:
    if source_hashes(root) != expected:
        raise PipelineError('SOURCE_CHANGED')


def assert_no_secrets(value, secrets) -> None:
    data = json_bytes(value)
    if any(secret and secret.encode('utf-8') in data for secret in secrets):
        raise PipelineError('SECRET_IN_OUTPUT')


@contextmanager
def exclusive_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'pipeline.lock'
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise PipelineError('ALREADY_RUNNING') from None
    try:
        with os.fdopen(fd, 'w', encoding='ascii') as handle:
            handle.write(str(os.getpid()))
            handle.flush()
            os.fsync(handle.fileno())
        yield
    finally:
        path.unlink()


class SnapshotStore:
    """Readers select an immutable triple through one atomically replaced pointer."""
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def commit(self, source: Path, products: dict, history: dict, status: dict, expected: dict,
               checkpoint=lambda phase: None) -> Path:
        assert_source_unchanged(source, expected)
        serial = uuid.uuid4().hex
        staging = self.root / ('.staging-' + serial)
        staging.mkdir()
        payloads = dict(zip(COLLECTION_FILES, (products, history, status)))
        for index, (name, value) in enumerate(payloads.items()):
            path = staging / name
            write_json(path, value)
            if load_json(path) != value:
                raise PipelineError('JSON_ROUNDTRIP_FAILED')
            checkpoint('after_products' if index == 0 else 'after_file')
        backup = self.root / 'backups' / serial
        backup.mkdir(parents=True)
        for name in SOURCE_FILES[:2]:
            shutil.copy2(source / name, backup / Path(name).name)
            if digest(backup / Path(name).name) != expected[name]:
                raise PipelineError('BACKUP_VERIFY_FAILED')
        generations = self.root / 'generations'
        generations.mkdir(exist_ok=True)
        final = generations / serial
        os.replace(staging, final)
        pointer = {'generation': 'generations/' + serial,
                   'sha256': {name: digest(final / name) for name in COLLECTION_FILES}}
        pending = self.root / ('CURRENT-' + serial + '.tmp')
        write_json(pending, pointer)
        checkpoint('before_pointer')
        assert_source_unchanged(source, expected)
        os.replace(pending, self.root / 'CURRENT.json')
        return final
