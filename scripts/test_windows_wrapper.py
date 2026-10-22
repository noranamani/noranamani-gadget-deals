"""Wrapper transaction tests with mocked credentials, transport and remote."""
from unittest.mock import Mock, patch
from test_windows_pipeline import TemporarySource, CREDS, SUNDAY, SHA, ROOT, response
from pipeline_common import COLLECTION_FILES, PipelineError, load_json
from run_windows_update import run_update, require_push_window
from datetime import timedelta
import shutil
import unittest

class WrapperTests(TemporarySource):
    def config(self):
        return {'enabled':True,'runtime_dir':self.base/'runtime',
                'proxy_url':'http://proxy.example.invalid:3128','ca_bundle':None,'affiliate_proof':None}
    def fake_repo(self):
        repo=Mock()
        def run(*args):
            if args[:1]==('rev-parse',): return b'true'
            if args[:2]==('remote','get-url'):
                return b'https://github.com/noranamani/noranamani-gadget-deals.git'
            return b''
        repo.run.side_effect=run
        repo.fetch_main.return_value=SHA
        repo.archive.side_effect=lambda sha,dest: shutil.copytree(self.root,dest)
        repo.prepare_commit.return_value='b'*40
        return repo
    def patches(self,verified):
        expiry=(SUNDAY+timedelta(days=1)).isoformat() if verified else None
        return (patch('run_windows_update.read_windows_credentials',return_value=CREDS),
            patch('run_windows_update.affiliate_proof',return_value={'verified':verified,'expiresAt':expiry}))
    def payloads(self):
        return [response(category=q['id']) for q in load_json(self.root/'config/queries.json') for _ in range(2)]
    def test_preview_never_prepares_or_pushes_a_commit(self):
        repo=self.fake_repo(); first,second=self.patches(False)
        with first,second,patch('run_windows_update.GitRepository',return_value=repo), patch('run_windows_update.RakutenClient',return_value=Mock(side_effect=self.payloads())):
            result=run_update(self.config(),False,clock=lambda:SUNDAY)
        self.assertFalse(result['pushed'])
        repo.prepare_commit.assert_not_called(); repo.push_if_unchanged.assert_not_called()
    def test_apply_uses_only_validated_triple_and_one_commit(self):
        repo=self.fake_repo(); first,second=self.patches(True)
        with first,second,patch('run_windows_update.GitRepository',return_value=repo), patch('run_windows_update.RakutenClient',return_value=Mock(side_effect=self.payloads())):
            result=run_update(self.config(),True,clock=lambda:SUNDAY.replace(hour=6,minute=10))
        self.assertTrue(result['pushed'])
        self.assertEqual(set(repo.prepare_commit.call_args.args[1]),set(COLLECTION_FILES))
        repo.push_if_unchanged.assert_called_once()
        repo.push_if_unchanged.call_args.kwargs['before_push']()
    def test_missing_affiliate_confirmation_stops_before_git(self):
        first,second=self.patches(False)
        with first,second,patch('run_windows_update.GitRepository') as git:
            with self.assertRaisesRegex(PipelineError,'AFFILIATE_CONFIRMATION'):
                run_update(self.config(),True,clock=lambda:SUNDAY.replace(hour=6,minute=10))
            git.assert_not_called()
    def test_api_failure_never_commits_or_pushes(self):
        repo=self.fake_repo(); first,second=self.patches(True)
        with first,second,patch('run_windows_update.GitRepository',return_value=repo), patch('run_windows_update.RakutenClient',return_value=Mock(side_effect=PipelineError('RAKUTEN_HTTP_429'))):
            with self.assertRaisesRegex(PipelineError,'429'):
                run_update(self.config(),True,clock=lambda:SUNDAY.replace(hour=6,minute=10))
        repo.prepare_commit.assert_not_called(); repo.push_if_unchanged.assert_not_called()

class WorkflowTests(unittest.TestCase):
    def test_push_has_timeout_reserve_and_checks_affiliate_expiry(self):
        start=SUNDAY.replace(hour=6,minute=10)
        expiry=(SUNDAY+timedelta(days=1)).isoformat()
        require_push_window(start,start,expiry)
        with self.assertRaisesRegex(PipelineError,'INSUFFICIENT_PUSH_TIME'):
            require_push_window(start.replace(minute=24),start,expiry)
        with self.assertRaisesRegex(PipelineError,'PROOF_EXPIRED'):
            require_push_window(start,start,start.isoformat())
    def test_legacy_shells_stop_before_git_or_build(self):
        for name in ('publish.sh','run_without_ai.sh','rebuild_from_editorial.sh'):
            text=(ROOT/'scripts'/name).read_text(encoding='utf-8')
            self.assertIn('exit 3',text)
            self.assertNotIn('git push',text)
            self.assertNotIn('git reset',text)
            self.assertNotIn('python3 ',text)
    def test_production_workflow_is_main_only_and_opt_in(self):
        text=(ROOT/'.github/workflows/windows-daily-publish.yml').read_text(encoding='utf-8')
        self.assertIn("cron: '50 21 * * *'",text)
        self.assertEqual(text.count("vars.WINDOWS_PIPELINE_ENABLED == 'true' && github.ref == 'refs/heads/main'"),3)
        self.assertEqual(text.count('contents: write'),1)
        self.assertEqual(text.count('pages: write'),1)
        self.assertEqual(text.count('id-token: write'),1)
        self.assertNotIn('pull_request_target',text)
        for line in text.splitlines():
            if 'uses:' in line: self.assertRegex(line,r'@[0-9a-f]{40} # v')
    def test_offline_ci_has_no_write_permissions_or_api_keys(self):
        text=(ROOT/'.github/workflows/windows-pipeline-checks.yml').read_text(encoding='utf-8')
        self.assertIn('windows-latest, ubuntu-latest',text)
        self.assertNotIn(': write',text)
        self.assertNotIn('secrets.',text)
