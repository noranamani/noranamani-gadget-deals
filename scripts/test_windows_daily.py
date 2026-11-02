"""No network/credentials/production writes: independent Windows pipeline rehearsal."""
import copy
import io
import json
import shutil
import tempfile
import ssl
import urllib.error
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

import test_windows_pipeline as fixtures
from daily_run_log import RunLog, fixed_code
from github_publication import PublicationAPI, request_once, await_publication
from git_pipeline import GitRepository
from pipeline_common import JST, PipelineError, digest, load_json, write_json, source_hashes
from publication_gate import DEPLOY_STEP
from publication_pipeline import validate_collection, validate_daily, validate_publication
from run_windows_daily import edit_snapshot, orchestrate, wait_until, main
from windows_editorial import daily, weekly


class EditorialTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublicationTests('test_matching_generation_ready')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root, self.base = self.fixture.root, self.fixture.base

    def edit(self, *args, **kwargs):
        return self.fixture.edit(*args, **kwargs)
    def test_factual_daily_dynamic_categories_and_no_unsupported_claims(self):
        before = source_hashes(self.root)
        value = daily(self.root, fixtures.SUNDAY)
        self.assertEqual(source_hashes(self.root), before)
        self.assertEqual([r['categoryId'] for r in value['categoryNotes']],
            [q['id'] for q in load_json(self.root/'config/queries.json')])
        self.assertEqual(value['generatedAt'], fixtures.SUNDAY.isoformat())
        self.assertEqual(value['sourceProductsSha256'], digest(self.root/'data/products.json'))
        self.assertTrue(all(not r['topPickCode'] for r in value['categoryNotes']))
        self.assertLessEqual(len(value['featuredDeals']), 5)

    def test_no_deal_does_not_invent_a_recommendation(self):
        products = load_json(self.root/'data/products.json')
        for c in products['categories']:
            for row in c['items']:
                row['isDeal'] = False
        write_json(self.root/'data/products.json', products)
        self.edit('collection-status.json', productsSha256=digest(self.root/'data/products.json'))
        value = daily(self.root, fixtures.SUNDAY)
        self.assertEqual(value['featuredDeals'], [])

    def test_clock_and_window_requirements_are_not_fabricated(self):
        with self.assertRaisesRegex(PipelineError, 'BEFORE_EDITORIAL'):
            daily(self.root, fixtures.SUNDAY.replace(hour=6, minute=20))
        with self.assertRaisesRegex(PipelineError, 'BEFORE_WEEKLY'):
            weekly(self.root, fixtures.SUNDAY.replace(hour=6, minute=35))
        with self.assertRaisesRegex(PipelineError, 'PRODUCTS_STALE'):
            daily(self.root, fixtures.SUNDAY.replace(hour=10))

    def test_weekly_uses_actual_sunday_and_existing_deal_codes(self):
        value = weekly(self.root, fixtures.SUNDAY)
        self.assertEqual(value['weekLabel'], '2026/10/19〜2026/10/25')
        self.assertEqual(value['generatedAt'], fixtures.SUNDAY.isoformat())
        self.assertEqual(value['sourceProductsSha256'], digest(self.root/'data/products.json'))

    def test_valid_other_editor_is_reused_without_overwrite(self):
        before = digest(self.root/'data/editorial.json')
        self.assertEqual(edit_snapshot(self.root, 'editorial', fixtures.SUNDAY, Mock()), {})
        self.assertEqual(digest(self.root/'data/editorial.json'), before)

    def test_broken_or_same_generation_invalid_editorial_stops(self):
        self.edit('editorial.json', categoryNotes=[])
        with self.assertRaisesRegex(PipelineError, 'COVERAGE'):
            edit_snapshot(self.root, 'editorial', fixtures.SUNDAY, Mock())

    def test_same_week_different_generation_is_not_replaced(self):
        self.edit('note-weekly.json', sourceProductsSha256='b'*64)
        before = digest(self.root/'data/note-weekly.json')
        with self.assertRaisesRegex(PipelineError, 'NOTE_SOURCE'):
            edit_snapshot(self.root, 'weekly_editorial', fixtures.SUNDAY, Mock())
        self.assertEqual(digest(self.root/'data/note-weekly.json'), before)

    def test_new_configured_category_is_generated_without_hard_coding(self):
        products = load_json(self.root/'data/products.json')
        products['categories'].append({**copy.deepcopy(products['categories'][0]), 'id': 'fixture-new'})
        queries = load_json(self.root/'config/queries.json')
        queries.append({**queries[0], 'id': 'fixture-new', 'name': '検証カテゴリ'})
        write_json(self.root/'data/products.json', products)
        write_json(self.root/'config/queries.json', queries)
        self.edit('collection-status.json', productsSha256=digest(self.root/'data/products.json'),
            queriesSha256=digest(self.root/'config/queries.json'))
        self.assertEqual(daily(self.root, fixtures.SUNDAY)['categoryNotes'][-1]['categoryId'], 'fixture-new')


class WindowAndLogTests(unittest.TestCase):
    def test_wait_uses_real_progress_and_never_changes_generation_clock(self):
        current = [fixtures.SUNDAY.replace(hour=6, minute=11)]
        def pause(seconds):
            current[0] += timedelta(seconds=seconds)
        wait_until((6, 30), lambda: current[0], pause)
        self.assertEqual(current[0], fixtures.SUNDAY.replace(hour=6, minute=30))

    def test_clock_date_jump_stops(self):
        current = [fixtures.SUNDAY.replace(hour=6, minute=40)]
        def pause(_):
            current[0] += timedelta(days=1)
        with self.assertRaisesRegex(PipelineError, 'WAIT_DATE_CHANGED'):
            wait_until((6, 50), lambda: current[0], pause)

    def test_backwards_clock_is_bounded(self):
        with self.assertRaisesRegex(PipelineError, 'WAIT_DEADLINE'):
            wait_until((6, 50), lambda: fixtures.SUNDAY.replace(hour=5, minute=1), Mock())

    def test_run_log_never_records_arbitrary_exception_or_secret_values(self):
        with tempfile.TemporaryDirectory() as folder:
            log = RunLog(Path(folder), lambda: fixtures.SUNDAY)
            log.phase('collection')
            log.finish('failed', 'cookie=fixture-secret https://invalid.example')
            value = load_json(log.path)
            self.assertEqual(value['code'], 'PIPELINE_ERROR')
            self.assertEqual(value['phase'], 'collection')
            self.assertNotIn('fixture-secret', log.path.read_text())
            with self.assertRaises(PipelineError):
                log.phase('unknown-secret-phase')
        self.assertEqual(fixed_code(PipelineError('GIT_COMMAND_FAILED')), 'GIT_COMMAND_FAILED')

    def test_default_cli_is_plan_only_without_reading_config_or_network(self):
        with patch('sys.argv', ['daily', '--config', 'missing-private-config']), \
                patch('run_windows_daily.load_config') as cfg, \
                patch('run_windows_daily.orchestrate') as run, redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 0)
        cfg.assert_not_called()
        run.assert_not_called()


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublicationTests('test_matching_generation_ready')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root, self.base = self.fixture.root, self.fixture.base
        fixtures.copy_publication_sources(self.root)
        self.fixture.edit('editorial.json', generatedAt=(fixtures.SUNDAY-timedelta(days=1)).isoformat(),
            sourceProductsSha256='b'*64)
        self.fixture.edit('note-weekly.json', generatedAt=(fixtures.SUNDAY-timedelta(days=7)).isoformat(),
            sourceProductsSha256='b'*64, weekLabel='2026/10/12〜2026/10/18')
        self.current = fixtures.SUNDAY.replace(hour=6, minute=10)
        self.cfg = {'enabled': True, 'runtime_dir': self.base/'runtime',
            'proxy_url': 'http://proxy.invalid:3128', 'ca_bundle': None, 'affiliate_proof': None}
        self.repo = Mock()
        self.repo.run.side_effect = lambda *args: b'true' if args[0] == 'rev-parse' else b'https://github.com/noranamani/noranamani-gadget-deals.git'
        self.repo.root = self.cfg['runtime_dir']/'repository.git'
        self.repo.root.mkdir(parents=True)
        self.head = fixtures.SHA
        self.pending = {}
        self.repo.fetch_main.side_effect = lambda: self.head
        self.repo.remote_tip.side_effect = lambda: self.head
        self.repo.archive.side_effect = lambda sha, dest: shutil.copytree(self.root, dest)
        def commit(base, files, kind):
            self.pending = files
            return ('b' if kind == 'editorial' else 'c')*40
        def push(base, sha, before_push):
            before_push()
            for name, data in self.pending.items():
                (self.root/name).write_bytes(data)
            self.head = sha
        self.repo.prepare_commit.side_effect = commit
        self.repo.push_if_unchanged.side_effect = push
        self.api = Mock()
        def api_get(path):
            if '/runs?' in path:
                return {'workflow_runs': [] if not self.api.dispatch.called else [
                    {'id': 901+(self.current.date()-fixtures.SUNDAY.date()).days, 'event': 'workflow_dispatch', 'head_sha': self.head,
                     'head_branch': 'main', 'created_at': self.current.isoformat(), 'status': 'completed'}]}
            if '/jobs?' in path:
                return {'jobs': [{'name': 'deploy', 'steps': [{'name': DEPLOY_STEP, 'conclusion': 'success'}]}]}
            return {'status': 'completed', 'conclusion': 'success', 'head_branch': 'main', 'head_sha': self.head}
        self.api.get.side_effect = api_get

    def clock(self):
        return self.current

    def pause(self, seconds):
        self.current += timedelta(seconds=seconds)

    def run_pipeline(self):
        with patch('run_windows_daily.GitRepository', return_value=self.repo), \
                patch('run_windows_daily.read_windows_credentials', return_value=fixtures.CREDS), \
                patch('run_windows_daily.PublicationAPI', return_value=self.api), \
                patch('run_windows_daily.run_update') as collect:
            result = orchestrate(self.cfg, self.clock, self.pause)
            collect.assert_not_called()
            return result

    def test_complete_sunday_reuses_collection_builds_real_artifacts_dispatches_once(self):
        protected = {name: digest(self.root/name) for name in (
            'data/products.json', 'data/history.json', 'data/collection-status.json',
            'note/latest/article.md', 'note/latest/article-cover.png', 'note/latest/note-post.zip')}
        result = self.run_pipeline()
        self.assertTrue(result['collectionReused'])
        self.assertEqual(result['status'], 'published')
        self.assertEqual(result['publicationRunId'], 901)
        self.assertEqual(load_json(self.root/'data/editorial.json')['generatedAt'],
            fixtures.SUNDAY.replace(minute=30).isoformat())
        self.assertEqual(load_json(self.root/'data/note-weekly.json')['generatedAt'],
            fixtures.SUNDAY.replace(minute=40).isoformat())
        self.assertEqual(self.current, fixtures.SUNDAY)
        self.api.dispatch.assert_called_once()
        self.assertEqual(self.repo.prepare_commit.call_count, 2)
        self.assertEqual(self.repo.prepare_commit.call_args_list[0].args[2], 'editorial')
        self.assertEqual(self.repo.prepare_commit.call_args_list[1].args[2], 'weekly_editorial')
        for name, old in protected.items():
            self.assertEqual(digest(self.root/name), old)
        bundles = list((self.cfg['runtime_dir']/'daily/runs').glob('*/publication-output/bundle'))
        self.assertEqual(len(bundles), 1)
        self.assertEqual((bundles[0]/'note/latest/article-cover.png').read_bytes()[:8], b'\x89PNG\r\n\x1a\n')
        for category in load_json(self.root/'config/queries.json'):
            self.assertTrue((bundles[0]/'docs'/('category-'+category['id']+'.html')).exists())
        self.assertGreaterEqual(len(list((bundles[0]/'docs').glob('comparison-*.html'))), 5)

    def test_git_conflict_never_reaches_publication(self):
        self.repo.push_if_unchanged.side_effect = PipelineError('PUSH_CONFLICT')
        before = digest(self.root/'data/history.json')
        with self.assertRaisesRegex(PipelineError, 'PUSH_CONFLICT'):
            self.run_pipeline()
        self.api.dispatch.assert_not_called()
        self.assertEqual(digest(self.root/'data/history.json'), before)

    def test_following_weekday_preserves_sunday_note_without_weekly_rewrite(self):
        self.run_pipeline()
        bundle = next((self.cfg['runtime_dir']/'daily/runs').glob('*/publication-output/bundle'))
        shutil.copytree(bundle/'note', self.root/'note', dirs_exist_ok=True)  # simulate validated Actions persistence
        old_note = {p.relative_to(self.root): digest(p) for p in (self.root/'note').rglob('*') if p.is_file()}
        weekly_hash = digest(self.root/'data/note-weekly.json')
        self.current = fixtures.SUNDAY.replace(hour=6, minute=10)+timedelta(days=1)
        self.fixture.edit('products.json', updatedAt=self.current.isoformat())
        self.fixture.edit('collection-status.json', collectedAt=self.current.isoformat(),
            productsSha256=digest(self.root/'data/products.json'))
        self.api.dispatch.reset_mock()
        result = self.run_pipeline()
        self.assertEqual(result['status'], 'published')
        self.assertEqual(self.repo.prepare_commit.call_count, 3)  # two Sunday, daily only Monday
        self.assertEqual(digest(self.root/'data/note-weekly.json'), weekly_hash)
        for p, sha in old_note.items():
            self.assertEqual(digest(self.root/p), sha)

    def test_weekday_with_missing_current_sunday_note_stops_instead_of_backdating(self):
        self.current = fixtures.SUNDAY.replace(hour=6, minute=10)+timedelta(days=1)
        self.fixture.edit('products.json', updatedAt=self.current.isoformat())
        self.fixture.edit('collection-status.json', collectedAt=self.current.isoformat(),
            productsSha256=digest(self.root/'data/products.json'))
        before = digest(self.root/'data/note-weekly.json')
        with self.assertRaisesRegex(PipelineError, 'NOTE_WEEK'):
            self.run_pipeline()
        self.api.dispatch.assert_not_called()
        self.assertEqual(digest(self.root/'data/note-weekly.json'), before)

    def test_duplicate_runner_stops_before_collection_or_git(self):
        lock = self.cfg['runtime_dir']/'daily/pipeline.lock'
        lock.parent.mkdir()
        lock.write_text('fixture')
        with self.assertRaisesRegex(PipelineError, 'ALREADY_RUNNING'):
            self.run_pipeline()
        self.repo.fetch_main.assert_not_called()
        self.api.dispatch.assert_not_called()

    def test_old_start_time_stops_before_any_io(self):
        self.current = self.current.replace(hour=10)
        with self.assertRaisesRegex(PipelineError, 'OUTSIDE_COLLECTION_START_WINDOW'):
            self.run_pipeline()
        self.repo.fetch_main.assert_not_called()

    def test_collection_failure_does_not_edit_or_request_publication(self):
        self.fixture.edit('collection-status.json', collectedAt=(self.current-timedelta(days=1)).isoformat())
        with patch('run_windows_daily.GitRepository', return_value=self.repo), \
                patch('run_windows_daily.run_update', side_effect=PipelineError('RAKUTEN_HTTP_429')) as collect:
            with self.assertRaisesRegex(PipelineError, '429'):
                orchestrate(self.cfg, self.clock, self.pause)
            collect.assert_called_once()
        self.repo.prepare_commit.assert_not_called()
        self.api.dispatch.assert_not_called()

    def test_windows_ownership_required_before_api_or_editorial_write(self):
        write_json(self.root/'config/automation-owner.json', {'schemaVersion': 1, 'editorialOwner': 'legacy'})
        with patch('run_windows_daily.GitRepository', return_value=self.repo), \
                patch('run_windows_daily.run_update') as collect:
            with self.assertRaisesRegex(PipelineError, 'OWNERSHIP_REQUIRED'):
                orchestrate(self.cfg, self.clock, self.pause)
            collect.assert_not_called()
        self.repo.prepare_commit.assert_not_called()


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = Path(self.temp.name)/'request.json'
        self.api = Mock()
        self.api.get.return_value = {'workflow_runs': []}

    def test_one_request_has_intent_before_network_send_and_no_second_request(self):
        def send():
            self.assertEqual(load_json(self.journal)['state'], 'requested')
        self.api.dispatch.side_effect = send
        self.assertEqual(request_once(self.api, self.journal, fixtures.SUNDAY, fixtures.SHA), 'requested')
        with self.assertRaisesRegex(PipelineError, 'ALREADY_REQUESTED'):
            request_once(self.api, self.journal, fixtures.SUNDAY, fixtures.SHA)
        self.api.dispatch.assert_called_once()
    def test_lost_dispatch_response_keeps_intent_and_never_retries(self):
        self.api.dispatch.side_effect = PipelineError('PUBLICATION_DISPATCH_UNCERTAIN')
        with self.assertRaisesRegex(PipelineError, 'UNCERTAIN'):
            request_once(self.api, self.journal, fixtures.SUNDAY, fixtures.SHA)
        with self.assertRaisesRegex(PipelineError, 'ALREADY_REQUESTED'):
            request_once(self.api, self.journal, fixtures.SUNDAY, fixtures.SHA)
        self.api.dispatch.assert_called_once()

    def test_active_run_prevents_parallel_dispatch(self):
        self.api.get.return_value = {'workflow_runs': [{'id': 9, 'head_branch': 'main',
            'created_at': fixtures.SUNDAY.isoformat(), 'status': 'in_progress'}]}
        self.assertEqual(request_once(self.api, self.journal, fixtures.SUNDAY, fixtures.SHA), 'publication_in_progress')
        self.api.dispatch.assert_not_called()
        self.assertFalse(self.journal.exists())

    def test_success_or_ambiguous_deploy_uses_existing_receipt_rules(self):
        row = {'id': 1, 'head_branch': 'main', 'created_at': fixtures.SUNDAY.isoformat()}
        for outcome in ['success', 'failure']:
            def get(path):
                return {'workflow_runs': [row]} if '/runs?' in path else {'jobs': [
                    {'name': 'deploy', 'steps': [{'name': DEPLOY_STEP, 'conclusion': outcome}]}]}
            self.api.get.side_effect = get
            if outcome == 'success':
                self.assertEqual(request_once(self.api, self.journal, fixtures.SUNDAY, fixtures.SHA), 'already_published')
            else:
                with self.assertRaisesRegex(PipelineError, 'DEPLOYMENT_UNCERTAIN'):
                    request_once(self.api, self.journal, fixtures.SUNDAY, fixtures.SHA)
        self.api.dispatch.assert_not_called()

    def test_git_conflict_or_early_time_stops_before_intent(self):
        with self.assertRaisesRegex(PipelineError, 'PUSH_CONFLICT'):
            request_once(self.api, self.journal, fixtures.SUNDAY, fixtures.SHA,
                lambda: (_ for _ in ()).throw(PipelineError('PUSH_CONFLICT')))
        with self.assertRaisesRegex(PipelineError, 'BEFORE_PUBLICATION'):
            request_once(self.api, self.journal, fixtures.SUNDAY.replace(minute=49), fixtures.SHA)
        self.api.dispatch.assert_not_called()
        self.assertFalse(self.journal.exists())

    def test_api_rejects_other_workflows_hosts_and_write_payloads_without_request(self):
        api = PublicationAPI.__new__(PublicationAPI)
        api.opener = Mock()
        for path, body in [('https://other.invalid', None), ('issues', {}),
                ('actions/workflows/other.yml/dispatches', {'ref': 'main'}),
                ('actions/workflows/windows-daily-publish.yml/dispatches', {'ref': 'develop'})]:
            with self.assertRaisesRegex(PipelineError, 'API_CONTEXT'):
                api.call(path, body)
        api.opener.open.assert_not_called()
        self.assertEqual(repr(api), 'PublicationAPI([REDACTED])')

    def test_transport_uses_tls_proxy_and_never_returns_server_error_content(self):
        repo = Mock()
        repo.run.return_value = b'password=fixture-github-secret\n'
        api = PublicationAPI(repo, {'proxy_url': 'http://proxy.invalid:3128', 'ca_bundle': None})
        tls = next(h for h in api.opener.handlers if hasattr(h, '_context'))
        self.assertEqual(tls._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(tls._context.check_hostname)
        self.assertNotIn('fixture-github-secret', repr(api))
        api.opener = Mock()
        for status in (401, 403, 404, 500):
            api.opener.open.side_effect = urllib.error.HTTPError('https://invalid.example', status,
                'cookie=fixture-private', {}, None)
            with self.assertRaises(PipelineError) as caught:
                api.dispatch()
            self.assertNotIn('fixture-private', str(caught.exception))
            self.assertEqual(str(caught.exception), 'PUBLICATION_API_PERMISSION_REQUIRED' if status < 500 else 'PUBLICATION_DISPATCH_UNCERTAIN')

    def test_current_and_legacy_dispatch_responses_are_verified_without_echoing_urls(self):
        api = PublicationAPI.__new__(PublicationAPI)
        api.token = 'fixture-github-secret'
        api.opener = Mock()
        for status, payload, expected in [(200, b'{"workflow_run_id":901}', {'workflowRunId': 901}),
                (204, b'', {})]:
            response = Mock()
            response.status = status
            response.read.return_value = payload
            api.opener.open.return_value = MagicMock()
            api.opener.open.return_value.__enter__.return_value = response
            self.assertEqual(api.dispatch(), expected)
            req = api.opener.open.call_args.args[0]
            self.assertEqual(req.get_header('X-github-api-version'), '2026-03-10')
        response.status = 200
        response.read.return_value = b'{"workflow_run_id":"invalid"}'
        with self.assertRaisesRegex(PipelineError, 'DISPATCH_UNCERTAIN'):
            api.dispatch()

    def test_result_wait_is_bounded_even_if_wall_clock_moves_back(self):
        request_once(self.api, self.journal, fixtures.SUNDAY, fixtures.SHA)
        steady = iter([0, 901])
        with self.assertRaisesRegex(PipelineError, 'RESULT_UNCONFIRMED'):
            await_publication(self.api, self.journal, 'requested', fixtures.SHA,
                lambda: fixtures.SUNDAY, Mock(), monotonic=lambda: next(steady))
        self.api.dispatch.assert_called_once()

    def test_dispatch_without_observed_run_is_not_reported_as_published(self):
        request_once(self.api, self.journal, fixtures.SUNDAY, fixtures.SHA)
        with self.assertRaisesRegex(PipelineError, 'RESULT_UNCONFIRMED'):
            await_publication(self.api, self.journal, 'requested', fixtures.SHA,
                lambda: fixtures.SUNDAY, Mock())
        self.api.dispatch.assert_called_once()

    def test_observed_run_failure_and_uncertain_deployment_never_redispatch(self):
        request_once(self.api, self.journal, fixtures.SUNDAY, fixtures.SHA)
        for deploy in ['skipped', 'failure']:
            def get(path):
                if '/runs?' in path:
                    return {'workflow_runs': [{'id': 5, 'head_branch': 'main', 'head_sha': fixtures.SHA,
                        'event': 'workflow_dispatch', 'created_at': fixtures.SUNDAY.isoformat()}]}
                if '/jobs?' in path:
                    return {'jobs': [{'name': 'deploy', 'steps': [{'name': DEPLOY_STEP, 'conclusion': deploy}]}]}
                return {'status': 'completed', 'head_branch': 'main', 'head_sha': fixtures.SHA}
            self.api.get.side_effect = get
            with self.assertRaisesRegex(PipelineError, 'EXECUTION_FAILED' if deploy == 'skipped' else 'DEPLOYMENT_UNCERTAIN'):
                await_publication(self.api, self.journal, 'requested', fixtures.SHA,
                    lambda: fixtures.SUNDAY, Mock())
        self.api.dispatch.assert_called_once()


class EditorialGitTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.GitTransactionTests('test_single_commit_triple_no_checkout_mutation')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.repo, self.base = self.fixture.repo, self.fixture.base

    def test_editorial_transaction_only_changes_its_one_allowed_file(self):
        for kind, name in [('editorial', 'data/editorial.json'), ('weekly_editorial', 'data/note-weekly.json')]:
            sha = self.repo.prepare_commit(self.base, {name: b'{}\n'}, kind)
            changed = self.repo.run('diff-tree', '--no-commit-id', '--name-only', '-r', sha).decode().splitlines()
            self.assertEqual(changed, [name])
        for kind in ['editorial', 'weekly_editorial']:
            with self.assertRaisesRegex(PipelineError, 'EDITORIAL_COMMIT_FILES_INVALID'):
                self.repo.prepare_commit(self.base, {'data/products.json': b'{}'}, kind)


if __name__ == '__main__':
    unittest.main()
