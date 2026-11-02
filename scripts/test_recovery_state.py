"""Crash/restart and isolation tests. Synthetic data/child processes; no external requests."""
import io
import json
import os
import subprocess
import urllib.error
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import test_windows_pipeline as fixtures
import test_windows_daily as daily_tests
from github_publication import PublicationAPI
from git_pipeline import GitRepository
from pipeline_common import PipelineError, digest, load_json, write_json
from recovery_lock import process_alive, recoverable_lock
from recovery_state import RecoveryState, inspect_recovery
from run_windows_daily import orchestrate, main

NOW = fixtures.SUNDAY
HASHES = {'products': 'a'*64}


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.current = NOW
        self.state = RecoveryState(self.root, NOW.date().isoformat(), lambda: self.current)

    def test_round_trip_includes_all_units_without_claiming_external_posting(self):
        self.state.bind('a'*64)
        value = load_json(self.state.path)
        self.assertEqual(value['date'], NOW.date().isoformat())
        self.assertEqual(value['generation'], 'a'*64)
        self.assertEqual(value['stages']['note_posting']['status'], 'external')
        self.assertEqual(value['stages']['note_posting']['owner'], 'note-cloud')
        self.assertIsNone(value['stages']['note_posting']['lastSuccessAt'])

    def test_success_retains_input_output_hashes_and_last_success_when_revalidated(self):
        self.state.begin('daily_editorial', HASHES)
        self.state.success('daily_editorial', HASHES, {'editorial': 'b'*64})
        finished = self.state.row('daily_editorial')['lastSuccessAt']
        self.current += timedelta(minutes=5)
        self.state.success('daily_editorial', HASHES, {'editorial': 'b'*64})
        self.assertEqual(self.state.row('daily_editorial')['lastSuccessAt'], finished)
        self.assertEqual(self.state.row('daily_editorial')['attempts'], 1)
        self.assertTrue(self.state.reusable('daily_editorial', HASHES, {'editorial': 'b'*64}))

    def test_failure_is_fixed_code_and_backoff_is_persistent(self):
        self.state.begin('site_build', HASHES)
        self.state.failure('site_build', PipelineError('cookie=fixture-secret'))
        row = self.state.row('site_build')
        self.assertEqual(row['lastFailureCode'], 'PIPELINE_ERROR')
        self.assertNotIn('fixture-secret', self.state.path.read_text())
        recovered = RecoveryState(self.root, NOW.date().isoformat(), lambda: self.current)
        with self.assertRaisesRegex(PipelineError, 'RECOVERY_BACKOFF'):
            recovered.begin('site_build', HASHES)

    def test_retry_count_and_limit_survive_new_process_state(self):
        for attempt in range(3):
            self.state.begin('site_build', HASHES)
            self.state.failure('site_build', PipelineError('BUILD_OR_VALIDATION_FAILED'))
            self.current += timedelta(minutes=10)
            self.state = RecoveryState(self.root, NOW.date().isoformat(), lambda: self.current)
        self.assertEqual(self.state.row('site_build')['retryCount'], 2)
        with self.assertRaisesRegex(PipelineError, 'STAGE_RETRY_LIMIT'):
            self.state.begin('site_build', HASHES)

    def test_uncertain_write_cannot_be_repeated_without_actual_remote_confirmation(self):
        self.state.begin('daily_editorial', HASHES)
        self.state.checkpoint('daily_editorial', commit='a'*40, outputs={'editorial': 'b'*64}, write_intent=True)
        recovered = RecoveryState(self.root, NOW.date().isoformat(), lambda: self.current)
        with self.assertRaisesRegex(PipelineError, 'STAGE_RESULT_UNCONFIRMED'):
            recovered.begin('daily_editorial', HASHES)
        recovered.success('daily_editorial', HASHES, {'editorial': 'b'*64})
        self.assertEqual(recovered.row('daily_editorial')['status'], 'succeeded')

    def test_failed_atomic_replace_preserves_previous_complete_state(self):
        self.state.bind('a'*64)
        before = digest(self.state.path)
        with patch('recovery_state.os.replace', side_effect=OSError('fixture-secret')):
            with self.assertRaisesRegex(PipelineError, 'RECOVERY_STATE_UNAVAILABLE'):
                self.state.begin('site_build', HASHES)
        self.assertEqual(digest(self.state.path), before)
        self.assertEqual(load_json(self.state.path)['stages']['site_build']['attempts'], 0)

    def test_broken_state_is_not_reset(self):
        self.state.path.parent.mkdir(parents=True)
        self.state.path.write_text('{broken', encoding='utf-8')
        before = digest(self.state.path)
        with self.assertRaises(PipelineError):
            RecoveryState(self.root, NOW.date().isoformat(), lambda: self.current)
        self.assertEqual(digest(self.state.path), before)

    def test_changed_product_generation_stops_before_reusing_old_success(self):
        self.state.bind('a'*64)
        with self.assertRaisesRegex(PipelineError, 'RECOVERY_GENERATION_CHANGED'):
            self.state.bind('b'*64)
        self.assertEqual(load_json(self.state.path)['generation'], 'a'*64)

    def test_secret_like_metadata_and_arbitrary_artifact_paths_are_rejected(self):
        with self.assertRaises(PipelineError):
            self.state.begin('site_build', {'https://fixture-secret.invalid': 'a'*64})
        with self.assertRaises(PipelineError):
            self.state.checkpoint('site_build', artifact='../fixture-secret')

    def test_external_note_posting_is_never_executed_or_marked_successful(self):
        with self.assertRaisesRegex(PipelineError, 'RECOVERY_EXTERNAL_STAGE'):
            self.state.begin('note_posting', HASHES)
        with self.assertRaisesRegex(PipelineError, 'RECOVERY_EXTERNAL_STAGE'):
            self.state.success('note_posting', HASHES, {})

    def test_prior_unfinished_day_is_detected_without_backfill_or_state_writes(self):
        yesterday = (NOW-timedelta(days=1)).date().isoformat()
        prior = RecoveryState(self.root, yesterday, lambda: NOW)
        prior.bind('a'*64)
        before = digest(prior.path)
        result = inspect_recovery(self.root, NOW)
        self.assertFalse(result['ledgerExists'])
        self.assertEqual(result['priorUnresolvedDates'], [yesterday])
        self.assertIn('collection', result['pendingCoreStages'])
        self.assertEqual(digest(prior.path), before)
        self.assertFalse(self.state.path.exists())


class LockTests(unittest.TestCase):
    def test_live_owner_and_duplicate_start_remain_locked(self):
        self.assertTrue(process_alive(os.getpid()))
        with tempfile.TemporaryDirectory() as folder:
            with recoverable_lock(Path(folder)):
                with self.assertRaisesRegex(PipelineError, 'ALREADY_RUNNING'):
                    with recoverable_lock(Path(folder)):
                        self.fail('duplicate lock entered')
            self.assertFalse((Path(folder)/'pipeline.lock').exists())

    def test_process_crash_releases_kernel_lock_and_archives_only_proven_dead_pid(self):
        with tempfile.TemporaryDirectory() as folder:
            script = "from pathlib import Path;import os,sys;from recovery_lock import recoverable_lock\nwith recoverable_lock(Path(sys.argv[1])):os._exit(19)"
            process = subprocess.run([sys.executable, '-B', '-c', script, folder], cwd=Path(__file__).parent,
                capture_output=True, timeout=10)
            self.assertEqual(process.returncode, 19)
            self.assertTrue((Path(folder)/'pipeline.lock').exists())
            with recoverable_lock(Path(folder)):
                self.assertEqual(len(list((Path(folder)/'abandoned-locks').glob('*.lock'))), 1)

    def test_malformed_or_live_legacy_marker_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder)/'pipeline.lock'
            for text in ('unknown-owner', str(os.getpid())):
                marker.write_text(text, encoding='ascii')
                with self.assertRaisesRegex(PipelineError, 'ALREADY_RUNNING'):
                    with recoverable_lock(Path(folder)):
                        self.fail('legacy owner ignored')
                self.assertEqual(marker.read_text(), text)


class ReadRetryTests(unittest.TestCase):
    def test_get_recovers_with_bounded_exponential_waits(self):
        api = PublicationAPI.__new__(PublicationAPI)
        api.call = Mock(side_effect=[PipelineError('PUBLICATION_STATE_UNAVAILABLE'), PipelineError('PUBLICATION_STATE_UNAVAILABLE'), {'ok': True}])
        with patch('github_publication.timing.sleep') as pause:
            self.assertEqual(api.get('actions/runs/1'), {'ok': True})
        self.assertEqual([c.args[0] for c in pause.call_args_list], [2, 4])

    def test_get_authentication_failure_is_not_retried(self):
        api = PublicationAPI.__new__(PublicationAPI)
        api.call = Mock(side_effect=PipelineError('PUBLICATION_API_PERMISSION_REQUIRED'))
        with patch('github_publication.timing.sleep') as pause, self.assertRaises(PipelineError):
            api.get('actions/runs/1')
        api.call.assert_called_once()
        pause.assert_not_called()

    def test_get_stops_after_three_transport_failures(self):
        api = PublicationAPI.__new__(PublicationAPI)
        api.call = Mock(side_effect=PipelineError('PUBLICATION_STATE_UNAVAILABLE'))
        with patch('github_publication.timing.sleep'), self.assertRaises(PipelineError):
            api.get('actions/runs/1')
        self.assertEqual(api.call.call_count, 3)

    def test_rate_limit_is_not_hammered_or_logged_with_response_body(self):
        api = PublicationAPI.__new__(PublicationAPI)
        api.token = 'fixture-token'
        api.opener = Mock()
        for status, headers in ((429, {'Retry-After': '120'}), (403, {'X-RateLimit-Remaining': '0'})):
            api.opener.open.reset_mock()
            api.opener.open.side_effect = urllib.error.HTTPError('https://fixture.invalid', status,
                'cookie=fixture-secret', headers, None)
            with patch('github_publication.timing.sleep') as pause, self.assertRaisesRegex(PipelineError, 'RATE_LIMITED'):
                api.get('actions/runs/1')
            api.opener.open.assert_called_once()
            pause.assert_not_called()

    def test_git_auth_and_ca_errors_are_classified_without_secret_stderr(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = GitRepository(Path(folder))
            for message, expected in ((b'Authentication failed token=fixture-secret', 'GIT_AUTH_REQUIRED'),
                    (b'SSL certificate problem secret=fixture-secret', 'GIT_TLS_INVALID')):
                response = subprocess.CompletedProcess([], 1, b'', message)
                with patch('git_pipeline.subprocess.run', return_value=response), self.assertRaises(PipelineError) as caught:
                    repo.fetch_main()
                self.assertEqual(str(caught.exception), expected)
                self.assertNotIn('fixture-secret', str(caught.exception))


class ResumeTests(unittest.TestCase):
    def fixture(self):
        f = daily_tests.OrchestrationTests('test_complete_sunday_reuses_collection_builds_real_artifacts_dispatches_once')
        f.setUp()
        self.addCleanup(f.doCleanups)
        return f

    def resume(self, f):
        with patch('run_windows_daily.GitRepository', return_value=f.repo), \
                patch('run_windows_daily.read_windows_credentials', return_value=fixtures.CREDS), \
                patch('run_windows_daily.PublicationAPI', return_value=f.api), \
                patch('run_windows_daily.run_update') as collection:
            result = orchestrate(f.cfg, f.clock, f.pause, recovering=True)
            collection.assert_not_called()
            return result

    def ledger(self, f):
        return load_json(f.cfg['runtime_dir']/'daily/state'/(f.current.date().isoformat()+'.json'))

    def test_completed_stages_and_valid_artifacts_are_not_repeated_on_restart(self):
        f = self.fixture()
        f.run_pipeline()
        before = self.ledger(f)
        commits = f.repo.prepare_commit.call_count
        f.current += timedelta(minutes=5)
        result = self.resume(f)
        self.assertEqual(result['status'], 'already_published')
        self.assertEqual(f.repo.prepare_commit.call_count, commits)
        f.api.dispatch.assert_called_once()
        for stage in ('daily_editorial', 'weekly_editorial', 'site_build', 'note_materials'):
            self.assertEqual(self.ledger(f)['stages'][stage]['attempts'], before['stages'][stage]['attempts'])

    def test_network_outage_before_collection_recovers_using_fresh_remote_data(self):
        f = self.fixture()
        f.repo.fetch_main.side_effect = PipelineError('GIT_COMMAND_FAILED')
        with self.assertRaises(PipelineError):
            f.run_pipeline()
        f.repo.fetch_main.side_effect = lambda: f.head
        f.current = f.current.replace(minute=55)
        self.assertEqual(self.resume(f)['status'], 'published')
        self.assertEqual(self.ledger(f)['stages']['collection']['status'], 'succeeded')

    def test_missed_collection_after_reboot_does_not_call_api_late(self):
        f = self.fixture()
        f.current = f.current.replace(minute=55)
        f.fixture.edit('collection-status.json', collectedAt=(f.current-timedelta(days=1)).isoformat())
        with self.assertRaisesRegex(PipelineError, 'MISSED_COLLECTION_WINDOW'):
            self.resume(f)
        f.api.dispatch.assert_not_called()

    def test_api_failure_or_interruption_is_not_automatically_retried(self):
        f = self.fixture()
        f.fixture.edit('collection-status.json', collectedAt=(f.current-timedelta(days=1)).isoformat())
        with patch('run_windows_daily.GitRepository', return_value=f.repo), \
                patch('run_windows_daily.run_update', side_effect=PipelineError('RAKUTEN_HTTP_429')) as api:
            with self.assertRaisesRegex(PipelineError, '429'):
                orchestrate(f.cfg, f.clock, f.pause)
            f.current += timedelta(minutes=2)
            with self.assertRaisesRegex(PipelineError, 'STAGE_RESULT_UNCONFIRMED|STAGE_RETRY_LIMIT'):
                orchestrate(f.cfg, f.clock, f.pause, recovering=True)
            api.assert_called_once()

    def test_lost_dispatch_response_is_reconciled_without_second_post(self):
        f = self.fixture()
        f.api.dispatch.side_effect = PipelineError('PUBLICATION_DISPATCH_UNCERTAIN')
        with self.assertRaisesRegex(PipelineError, 'DISPATCH_UNCERTAIN'):
            f.run_pipeline()
        f.current += timedelta(minutes=5)
        result = self.resume(f)
        self.assertEqual(result['status'], 'already_published')
        f.api.dispatch.assert_called_once()
        self.assertEqual(self.ledger(f)['stages']['publication']['publicationRunId'], 901)

    def test_unknown_publication_with_no_visible_run_never_redispatches(self):
        f = self.fixture()
        f.api.dispatch.side_effect = PipelineError('PUBLICATION_DISPATCH_UNCERTAIN')
        with self.assertRaises(PipelineError):
            f.run_pipeline()
        f.current += timedelta(minutes=5)
        f.api.get.side_effect = lambda path: {'workflow_runs': []} if '/runs?' in path else {'jobs': []}
        with self.assertRaisesRegex(PipelineError, 'RESULT_UNCONFIRMED'):
            self.resume(f)
        f.api.dispatch.assert_called_once()
        self.assertEqual(self.ledger(f)['stages']['publication']['status'], 'unknown')

    def test_daily_push_response_lost_is_reconciled_by_actual_main_hashes(self):
        f = self.fixture()
        original = f.repo.push_if_unchanged.side_effect
        def push(base, sha, before_push):
            original(base, sha, before_push)
            raise PipelineError('PUSH_REJECTED_OR_UNCERTAIN')
        f.repo.push_if_unchanged.side_effect = push
        with self.assertRaisesRegex(PipelineError, 'PUSH_REJECTED'):
            f.run_pipeline()
        f.repo.push_if_unchanged.side_effect = original
        f.current = f.current.replace(minute=55)
        self.assertEqual(self.resume(f)['status'], 'published')
        self.assertEqual(self.ledger(f)['stages']['daily_editorial']['attempts'], 1)

    def test_unconfirmed_daily_push_without_remote_result_is_not_repeated(self):
        f = self.fixture()
        f.repo.push_if_unchanged.side_effect = PipelineError('PUSH_REJECTED_OR_UNCERTAIN')
        with self.assertRaises(PipelineError):
            f.run_pipeline()
        f.current = f.current.replace(minute=55)
        with self.assertRaisesRegex(PipelineError, 'STAGE_RESULT_UNCONFIRMED'):
            self.resume(f)
        self.assertEqual(f.repo.push_if_unchanged.call_count, 1)

    def test_valid_other_editor_resolves_stage_without_claiming_our_uncertain_push_won(self):
        f = self.fixture()
        original = f.repo.push_if_unchanged.side_effect
        f.repo.push_if_unchanged.side_effect = PipelineError('PUSH_REJECTED_OR_UNCERTAIN')
        with self.assertRaises(PipelineError):
            f.run_pipeline()
        f.fixture.edit('editorial.json', generatedAt=(f.current+timedelta(minutes=1)).isoformat(),
            sourceProductsSha256=digest(f.root/'data/products.json'), dailyHeadline='別の編集による検証用見出し')
        f.head = 'd'*40
        f.repo.push_if_unchanged.side_effect = original
        f.current = f.current.replace(minute=55)
        self.assertEqual(self.resume(f)['status'], 'published')
        row = self.ledger(f)['stages']['daily_editorial']
        self.assertEqual(row['confirmation'], 'remote_validated')
        self.assertEqual(row['commit'], 'd'*40)
        self.assertEqual(row['lastFailureCode'], 'PUSH_REJECTED_OR_UNCERTAIN')

    def test_weekly_note_over_seven_days_old_never_blocks_daily_site(self):
        f = self.fixture()
        f.current += timedelta(days=15)
        f.fixture.edit('products.json', updatedAt=f.current.isoformat())
        f.fixture.edit('collection-status.json', collectedAt=f.current.isoformat(),
            productsSha256=digest(f.root/'data/products.json'), affiliateVerificationExpiresAt=(f.current+timedelta(days=2)).isoformat())
        before = digest(f.root/'data/note-weekly.json')
        result = self.resume(f)
        self.assertEqual(result['status'], 'published')
        self.assertEqual(digest(f.root/'data/note-weekly.json'), before)
        self.assertEqual(self.ledger(f)['stages']['note_posting']['status'], 'external')

    def test_note_material_failure_is_persisted_while_site_remains_verified(self):
        from run_windows_daily import prepare_publication as original
        f = self.fixture()
        def build(*args, **kwargs):
            if kwargs['purpose'] == 'note':
                raise PipelineError('BUILD_OR_VALIDATION_FAILED')
            return original(*args, **kwargs)
        with patch('run_windows_daily.prepare_publication', side_effect=build):
            result = f.run_pipeline()
        self.assertEqual(result['status'], 'published')
        state = self.ledger(f)['stages']
        self.assertEqual(state['note_materials']['status'], 'blocked')
        self.assertEqual(state['publication']['status'], 'succeeded')

    def test_resume_cli_without_apply_remains_plan_only(self):
        with patch('sys.argv', ['daily', '--resume', '--config', 'missing']), \
                patch('run_windows_daily.orchestrate') as run, \
                patch('run_windows_daily.load_config') as cfg, redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 0)
        run.assert_not_called()
        cfg.assert_not_called()

    def test_status_cli_is_read_only_without_credentials_or_network(self):
        f = self.fixture()
        with patch('sys.argv', ['daily', '--status', '--config', 'unused']), \
                patch('run_windows_daily.load_config', return_value=f.cfg), \
                patch('run_windows_daily.orchestrate') as run, \
                patch('run_windows_daily.read_windows_credentials') as credentials, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(), 0)
        self.assertFalse(json.loads(output.getvalue())['ledgerExists'])
        run.assert_not_called()
        credentials.assert_not_called()

    def test_busy_recovery_timer_is_explicit_skip_not_a_publication_success(self):
        f = self.fixture()
        with patch('sys.argv', ['daily', '--resume', '--apply', '--config', 'unused']), \
                patch('run_windows_daily.load_config', return_value=f.cfg), \
                patch('run_windows_daily.orchestrate', side_effect=PipelineError('ALREADY_RUNNING')), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(), 0)
        value = load_json(next((f.cfg['runtime_dir']/'daily/logs').glob('*.json')))
        self.assertEqual(value['status'], 'skipped')
        self.assertFalse(value['publicationVerified'])
        self.assertIn('RECOVERY_DEFERRED', output.getvalue())

    def test_completed_weekday_recovery_timer_skips_all_work_without_false_new_success(self):
        f = self.fixture()
        f.current += timedelta(days=1)
        f.fixture.edit('products.json', updatedAt=f.current.isoformat())
        f.fixture.edit('collection-status.json', collectedAt=f.current.isoformat(), productsSha256=digest(f.root/'data/products.json'))
        f.run_pipeline()
        with patch('sys.argv', ['daily', '--resume', '--apply', '--config', 'unused']), \
                patch('run_windows_daily.load_config', return_value=f.cfg), \
                patch('run_windows_daily.datetime') as clock, \
                patch('run_windows_daily.orchestrate') as run, redirect_stdout(io.StringIO()) as output:
            clock.now.return_value = f.current
            self.assertEqual(main(), 0)
        run.assert_not_called()
        self.assertIn('RECOVERY_STATUS', output.getvalue())
        logs = list((f.cfg['runtime_dir']/'daily/logs').glob('*.json'))
        self.assertEqual(len(logs), 1)
        value = load_json(logs[0])
        self.assertEqual(value['status'], 'skipped')
        self.assertFalse(value['publicationVerified'])

    def test_expired_artifacts_are_rebuilt_without_reediting_or_recollecting(self):
        f = self.fixture()
        f.run_pipeline()
        commits = f.repo.prepare_commit.call_count
        old_bundle = next((f.cfg['runtime_dir']/'daily/runs').glob('*/publication-output/bundle'))
        old_hash = digest(old_bundle/'publication-manifest.json')
        f.current += timedelta(minutes=35)
        result = self.resume(f)
        self.assertEqual(result['status'], 'already_published')
        self.assertEqual(f.repo.prepare_commit.call_count, commits)
        self.assertEqual(self.ledger(f)['stages']['site_build']['attempts'], 2)
        self.assertEqual(digest(old_bundle/'publication-manifest.json'), old_hash)
        f.api.dispatch.assert_called_once()

    def test_resume_outside_freshness_window_stops_before_network(self):
        f = self.fixture()
        f.current = f.current.replace(hour=10)
        with self.assertRaisesRegex(PipelineError, 'OUTSIDE_RECOVERY_WINDOW'):
            self.resume(f)
        f.repo.fetch_main.assert_not_called()

    def test_uncertain_weekly_push_is_isolated_then_reconciled_by_remote_data(self):
        f = self.fixture()
        original = f.repo.push_if_unchanged.side_effect
        def push(base, sha, before_push):
            original(base, sha, before_push)
            if sha == 'c'*40:
                raise PipelineError('PUSH_REJECTED_OR_UNCERTAIN')
        f.repo.push_if_unchanged.side_effect = push
        self.assertEqual(f.run_pipeline()['status'], 'published')
        self.assertEqual(self.ledger(f)['stages']['weekly_editorial']['status'], 'unknown')
        f.current += timedelta(minutes=5)
        self.assertEqual(self.resume(f)['status'], 'already_published')
        self.assertEqual(f.repo.push_if_unchanged.call_count, 2)
        self.assertEqual(self.ledger(f)['stages']['weekly_editorial']['status'], 'succeeded')


if __name__ == '__main__':
    unittest.main()
