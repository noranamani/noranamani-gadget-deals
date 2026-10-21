"""Git object transactions: prepare one commit, never reset a checkout or force push."""
from __future__ import annotations

import io
import hashlib
import os
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from pipeline_common import PipelineError, COLLECTION_FILES


def valid_sha(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[0-9a-f]{40}', value))


class GitRepository:
    def __init__(self, root: Path, proxy_url=None, ca_bundle=None):
        self.root, self.proxy_url, self.ca_bundle = root.resolve(), proxy_url, ca_bundle

    def run(self, *args, input=None, env=None) -> bytes:
        command = ['git', '-c', 'safe.directory=' + self.root.as_posix(), '-c', 'http.sslVerify=true']
        if self.proxy_url:
            command += ['-c', 'http.proxy=' + self.proxy_url]
        if self.ca_bundle:
            command += ['-c', 'http.sslCAInfo=' + str(self.ca_bundle)]
        command += list(args)
        settings = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'Never',
                    'GIT_AUTHOR_NAME': 'gadget-pipeline-bot', 'GIT_AUTHOR_EMAIL': 'gadget-pipeline-bot@users.noreply.github.com',
                    'GIT_COMMITTER_NAME': 'gadget-pipeline-bot', 'GIT_COMMITTER_EMAIL': 'gadget-pipeline-bot@users.noreply.github.com',
                    **(env or {})}
        # An inherited debug switch must not dump authenticated HTTP traffic to a file.
        for name in list(settings):
            if name.startswith('GIT_TRACE') or name in ('GIT_CURL_VERBOSE', 'GIT_SSL_NO_VERIFY'):
                settings.pop(name, None)
        try:
            result = subprocess.run(command, cwd=self.root, input=input, capture_output=True,
                                    env=settings, timeout=90)
        except (OSError, subprocess.TimeoutExpired):
            raise PipelineError('GIT_UNAVAILABLE_OR_TIMEOUT') from None
        if result.returncode:
            raise PipelineError('GIT_COMMAND_FAILED')
        return result.stdout

    def remote_tip(self) -> str:
        rows = self.run('ls-remote', 'origin', 'refs/heads/main').decode('ascii').split()
        if len(rows) != 2 or not valid_sha(rows[0]) or rows[1] != 'refs/heads/main':
            raise PipelineError('REMOTE_MAIN_INVALID')
        return rows[0]

    def fetch_main(self) -> str:
        self.run('fetch', '--no-tags', 'origin', 'main')
        commit = self.run('rev-parse', 'FETCH_HEAD').decode('ascii').strip()
        if not valid_sha(commit):
            raise PipelineError('FETCHED_COMMIT_INVALID')
        return commit

    def archive(self, commit: str, destination: Path) -> None:
        if not valid_sha(commit) or destination.exists():
            raise PipelineError('ARCHIVE_CONTEXT_INVALID')
        # Status hashes describe Git blob bytes. Windows autocrlf must not alter the snapshot.
        payload = self.run('-c', 'core.autocrlf=false', '-c', 'core.eol=lf',
                           'archive', '--format=tar', commit)
        tracked = {}
        for entry in self.run('ls-tree', '-r', '-z', commit).split(b'\0'):
            if not entry:
                continue
            metadata, _, name = entry.partition(b'\t')
            mode, kind, oid = metadata.split()
            if kind != b'blob' or mode not in (b'100644', b'100755'):
                raise PipelineError('ARCHIVE_PATH_UNSAFE')
            tracked[name.decode('utf-8')] = oid.decode('ascii')
        with tarfile.open(fileobj=io.BytesIO(payload), mode='r:') as archive:
            # No links, special files or parent traversal, even from a compromised tree.
            actual = {}
            for member in archive.getmembers():
                path = PurePosixPath(member.name)
                if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                    raise PipelineError('ARCHIVE_PATH_UNSAFE')
                if member.isfile():
                    if member.name in actual:
                        raise PipelineError('ARCHIVE_CONTENT_MISMATCH')
                    data = archive.extractfile(member).read()
                    header = b'blob ' + str(len(data)).encode('ascii') + b'\0'
                    actual[member.name] = hashlib.sha1(header + data, usedforsecurity=False).hexdigest()
            if actual != tracked:
                raise PipelineError('ARCHIVE_CONTENT_MISMATCH')
            destination.mkdir(parents=True)
            archive.extractall(destination, filter='data')

    def prepare_commit(self, base: str, files: dict[str, bytes], kind: str) -> str:
        if not valid_sha(base) or not files:
            raise PipelineError('COMMIT_CONTEXT_INVALID')
        if kind == 'collection':
            if set(files) != set(COLLECTION_FILES):
                raise PipelineError('COLLECTION_COMMIT_FILES_INVALID')
        elif kind == 'note':
            required = {'note/latest/' + name for name in ('article.md', 'article-cover.png', 'note-post.zip', 'manifest.json')}
            if not required <= set(files):
                raise PipelineError('NOTE_COMMIT_FILES_INVALID')
            for name in files:
                if not (re.fullmatch(r'note/latest/(article\.md|article-cover\.png|note-post\.zip|manifest\.json)', name)
                        or re.fullmatch(r'note/archive/\d{4}-\d{2}-\d{2}/(article\.md|article-cover\.png)', name)):
                    raise PipelineError('NOTE_COMMIT_FILES_INVALID')
        else:
            raise PipelineError('COMMIT_KIND_INVALID')
        with tempfile.TemporaryDirectory(prefix='git-index-', dir=self.root.parent) as folder:
            assert Path(folder).resolve().is_relative_to(self.root.parent.resolve())
            env = {'GIT_INDEX_FILE': str(Path(folder) / 'index')}
            self.run('read-tree', base, env=env)
            for name, data in sorted(files.items()):
                blob = self.run('hash-object', '-w', '--stdin', input=data).decode('ascii').strip()
                self.run('update-index', '--add', '--cacheinfo', '100644,' + blob + ',' + name, env=env)
            tree = self.run('write-tree', env=env).decode('ascii').strip()
            previous_tree = self.run('rev-parse', base + '^{tree}').decode('ascii').strip()
            if tree == previous_tree:
                return base
            return self.run('commit-tree', tree, '-p', base, '-F', '-',
                input=('chore: validated ' + kind + ' generation\n').encode()).decode('ascii').strip()

    def push_if_unchanged(self, expected: str, commit: str, before_push=lambda: None) -> str:
        if not valid_sha(expected) or not valid_sha(commit):
            raise PipelineError('PUSH_CONTEXT_INVALID')
        if self.remote_tip() != expected:
            raise PipelineError('PUSH_CONFLICT')
        if commit != expected:
            parents = self.run('rev-list', '--parents', '-n', '1', commit).decode('ascii').split()
            if parents != [commit, expected]:
                raise PipelineError('PUSH_PARENT_INVALID')
            before_push()
            try:
                self.run('push', 'origin', commit + ':refs/heads/main')
            except PipelineError:
                raise PipelineError('PUSH_REJECTED_OR_UNCERTAIN') from None
        if self.remote_tip() != commit:
            raise PipelineError('PUSH_RESULT_UNCERTAIN')
        return commit
