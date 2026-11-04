"""Publication proof/ownership regression tests; synthetic API and private fixtures only."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import test_windows_pipeline as fixtures
import test_windows_daily as daily_tests
from daily_run_log import RunLog
from github_publication import (PublicationError, await_publication, publication_failure,
    publication_result, require_publication_result, request_once)
from pipeline_common import JST, PipelineError, digest, load_json, write_json
from publication_gate import DEPLOY_STEP, publication_receipt
from publication_pipeline import validate_publication, verify_bundle
from run_windows_daily import fetch_main_ready, main

NOW = datetime(2026, 11, 8, 6, 50, tzinfo=JST)


class ResultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = Path(self.temp.name)/'intent.json'
        self.api = Mock()
        self.rows = [self.row(51)]
        self.jobs = {51: 'success'}
        self.api.get.side_effect = self.get

    def row(self, run_id, status='completed'):
        return {'id': run_id, 'event': 'workflow_dispatch', 'head_branch': 'main',
            'head_sha': fixtures.SHA, 'created_at': NOW.isoformat(), 'status': status}

    def get(self, path):
        if '/runs?' in path:
            return {'workflow_runs': self.rows}
        if '/jobs?' in path:
            run_id = int(path.split('/')[2])
            conclusion = self.jobs.get(run_id)
            return {'jobs': [] if conclusion is None else [{'name': 'deploy',
                'steps': [{'name': DEPLOY_STEP, 'conclusion': conclusion}]}]}
        run_id = int(path.split('/')[2])
        return next(row for row in self.rows if row['id'] == run_id)

    def intent(self, run_id=51):
        value = {'schemaVersion': 1, 'requestedAt': NOW.isoformat(),
            'sourceCommit': fixtures.SHA, 'previousRunIds': []}
        if run_id is not None:
            value['workflowRunId'] = run_id
        write_json(self.journal, value)

    def observe(self, outcome):
        return await_publication(self.api, self.journal, outcome, fixtures.SHA, lambda: NOW, Mock())

    def test_existing_success_is_rechecked_with_real_run_id(self):
        self.assertEqual(self.observe('already_published'), publication_result('already_published', 51))
        self.api.dispatch.assert_not_called()

    def test_existing_success_without_receipt_is_unknown_never_none_or_success(self):
        self.rows = []
        with self.assertRaises(PublicationError) as caught:
            self.observe('already_published')
        self.assertEqual(caught.exception.publication, publication_result('unknown'))

    def test_only_accepted_response_run_id_counts_as_new_publication(self):
        self.intent()
        self.assertEqual(self.observe('requested'), publication_result('published', 51))

    def test_pages_success_is_confirmed_while_independent_note_job_still_runs(self):
        self.intent()
        self.rows = [self.row(51, 'in_progress')]
        self.assertEqual(self.observe('requested'), publication_result('published', 51))

    def test_legacy_204_unique_candidate_proves_publication_but_not_ownership(self):
        self.intent(None)
        self.assertEqual(self.observe('requested'), publication_result('observed_unattributed_run', 51))

    def test_other_run_finished_before_poll_is_observed_not_new(self):
        self.assertEqual(self.observe('publication_in_progress'), publication_result('observed_other_run', 51))

    def test_other_active_run_is_observed_until_publish_pages_succeeds(self):
        self.rows = [self.row(51, 'in_progress')]
        def pause(_):
            self.rows[0]['status'] = 'completed'
        result = await_publication(self.api, self.journal, 'publication_in_progress',
            fixtures.SHA, lambda: NOW, pause)
        self.assertEqual(result, publication_result('observed_other_run', 51))

    def test_own_skip_after_another_run_won_is_not_own_success(self):
        self.intent()
        self.rows.append(self.row(52))
        self.jobs.update({51: 'skipped', 52: 'success'})
        self.assertEqual(self.observe('requested'), publication_result('observed_other_run', 52))
        self.assertEqual(load_json(self.journal)['workflowRunId'], 51)

    def test_workflow_success_with_missing_or_skipped_deploy_is_failed(self):
        self.intent()
        for conclusion in (None, 'skipped'):
            with self.subTest(conclusion=conclusion):
                self.jobs[51] = conclusion
                with self.assertRaises(PublicationError) as caught:
                    self.observe('requested')
                self.assertEqual(caught.exception.publication, publication_result('failed', 51))
        self.api.dispatch.assert_not_called()

    def test_failed_or_cancelled_pages_attempt_is_ambiguous_not_safe_to_repeat(self):
        self.intent()
        for conclusion in ('failure', 'cancelled', 'timed_out'):
            with self.subTest(conclusion=conclusion):
                self.jobs[51] = conclusion
                with self.assertRaises(PublicationError) as caught:
                    self.observe('requested')
                self.assertEqual(caught.exception.publication, publication_result('unknown', 51))
        self.assertTrue(self.journal.exists())

    def test_timeout_retains_intent_and_never_resends(self):
        self.intent()
        self.rows = [self.row(51, 'queued')]
        self.jobs[51] = None
        with self.assertRaises(PublicationError) as caught:
            self.observe('requested')
        self.assertEqual(caught.exception.publication, publication_result('unknown', 51))
        with self.assertRaisesRegex(PipelineError, 'ALREADY_REQUESTED'):
            request_once(self.api, self.journal, NOW, fixtures.SHA)
        self.api.dispatch.assert_not_called()

    def test_multiple_204_candidates_stop_without_claiming_ownership(self):
        self.intent(None)
        self.rows.append(self.row(52))
        with self.assertRaisesRegex(PipelineError, 'DISPATCH_UNCERTAIN'):
            self.observe('requested')
        self.api.dispatch.assert_not_called()

    def test_unreadable_or_permission_lost_after_dispatch_is_unknown(self):
        self.intent()
        for code in ('PUBLICATION_STATE_UNAVAILABLE', 'PUBLICATION_API_PERMISSION_REQUIRED'):
            self.api.get.side_effect = PipelineError(code)
            with self.assertRaises(PipelineError) as caught:
                self.observe('requested')
            self.assertEqual(publication_failure(caught.exception)['status'], 'unknown')

    def test_existing_intent_can_be_resolved_by_proven_publication_without_resend(self):
        self.intent()
        self.assertEqual(request_once(self.api, self.journal, NOW, fixtures.SHA), 'already_published')
        self.api.dispatch.assert_not_called()

    def test_invalid_or_unverified_results_never_count_as_success(self):
        for value in (None, False, {}, publication_result('unknown'), publication_result('failed'),
                {'status': 'published', 'runId': None, 'verified': True},
                {'status': 'published', 'runId': True, 'verified': True},
                {'status': [], 'runId': 51, 'verified': True}):
            with self.subTest(value=value), self.assertRaises(PipelineError):
                require_publication_result(value, verified_only=True)

    def test_receipt_exposes_only_confirmed_run_id(self):
        self.assertEqual(publication_receipt(self.get, NOW), {'runId': 51})


class ReadinessTests(unittest.TestCase):
    def test_read_only_fetch_recovers_after_network_resumes(self):
        repo, pause = Mock(), Mock()
        repo.fetch_main.side_effect = [PipelineError('GIT_COMMAND_FAILED'), fixtures.SHA]
        self.assertEqual(fetch_main_ready(repo, lambda: NOW.replace(minute=10), pause), fixtures.SHA)
        self.assertEqual(repo.fetch_main.call_count, 2)
        pause.assert_called_once_with(10)
        repo.push_if_unchanged.assert_not_called()

    def test_three_failures_stop_instead_of_unlimited_retry(self):
        repo, pause = Mock(), Mock()
        repo.fetch_main.side_effect = PipelineError('GIT_UNAVAILABLE_OR_TIMEOUT')
        with self.assertRaisesRegex(PipelineError, 'GIT_UNAVAILABLE'):
            fetch_main_ready(repo, lambda: NOW.replace(minute=10), pause)
        self.assertEqual(repo.fetch_main.call_count, 3)
        self.assertEqual(pause.call_count, 2)

    def test_retry_cannot_cross_collection_start_deadline(self):
        repo, current = Mock(), [NOW.replace(minute=19, second=55)]
        repo.fetch_main.side_effect = PipelineError('GIT_COMMAND_FAILED')
        def pause(seconds):
            current[0] += timedelta(seconds=seconds)
        with self.assertRaisesRegex(PipelineError, 'OUTSIDE_COLLECTION'):
            fetch_main_ready(repo, lambda: current[0], pause)
        self.assertEqual(repo.fetch_main.call_count, 1)

    def test_invalid_git_data_is_not_retried(self):
        repo, pause = Mock(), Mock()
        repo.fetch_main.side_effect = PipelineError('FETCHED_COMMIT_INVALID')
        with self.assertRaisesRegex(PipelineError, 'FETCHED_COMMIT_INVALID'):
            fetch_main_ready(repo, lambda: NOW.replace(minute=10), pause)
        pause.assert_not_called()


class LogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_five_outcomes_have_distinct_persistent_status_and_proof(self):
        for status in ('published', 'already_published', 'observed_other_run', 'unknown', 'failed', 'observed_unattributed_run'):
            result = publication_result(status, 51)
            log = RunLog(self.root, lambda: NOW)
            log.phase('publication')
            log.finish('success' if result['verified'] else 'failed', publication=result)
            saved = load_json(log.path)
            self.assertEqual(saved['publicationStatus'], status)
            self.assertEqual(saved['publicationVerified'], result['verified'])
            self.assertEqual(saved['publicationRunId'], 51)

    def test_read_failure_metadata_is_redacted_validated_and_stays_private(self):
        log = RunLog(self.root, lambda: NOW)
        safe = {'resource': 'workflow_runs', 'httpStatus': 200,
            'exceptionType': 'JSONDecodeError'}
        log.api_read_failure(safe)
        self.assertEqual(load_json(log.path)['publicationApiReadFailure'], safe)
        self.assertNotIn('fixture-secret', log.path.read_text(encoding='utf-8'))
        with self.assertRaisesRegex(PipelineError, 'STATUS_INVALID'):
            log.api_read_failure({**safe, 'url': 'https://secret.invalid/?cookie=fixture-secret'})
        with self.assertRaisesRegex(PipelineError, 'STATUS_INVALID'):
            log.api_read_failure({**safe, 'exceptionType': 'cookie=fixture-secret'})
        self.assertEqual(load_json(log.path)['publicationApiReadFailure'], safe)

    def test_success_without_publication_proof_is_rejected(self):
        log = RunLog(self.root, lambda: NOW)
        with self.assertRaisesRegex(PipelineError, 'RESULT_UNCONFIRMED'):
            log.finish('success')
        self.assertEqual(load_json(log.path)['status'], 'running')

    def cli(self, result=None, error=None):
        def run(cfg, log):
            log.phase('publication')
            if error:
                raise error
            return result
        output = io.StringIO()
        with patch('sys.argv', ['daily', '--config', 'unused', '--apply']), \
                patch('run_windows_daily.load_config', return_value={'runtime_dir': self.root}), \
                patch('run_windows_daily.orchestrate', side_effect=run), redirect_stdout(output):
            code = main()
        logs = sorted((self.root/'daily/logs').glob('*.json'), key=lambda p: p.stat().st_mtime_ns)
        return code, output.getvalue(), load_json(logs[-1])

    def test_cli_preserves_already_and_other_status_instead_of_claiming_new_publish(self):
        for status in ('already_published', 'observed_other_run'):
            code, output, saved = self.cli({'status': status, 'publicationRunId': 51, 'publicationVerified': True})
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output)['status'], status)
            self.assertEqual(saved['publicationStatus'], status)

    def test_cli_unknown_or_failed_is_nonzero_without_secret_error_text(self):
        for status in ('unknown', 'failed'):
            code, output, saved = self.cli(error=PublicationError('fixture-secret cookie=invalid', status, 51))
            self.assertEqual(code, 1)
            self.assertEqual(saved['publicationStatus'], status)
            self.assertFalse(saved['publicationVerified'])
            self.assertNotIn('fixture-secret', output)
            self.assertNotIn('fixture-secret', json.dumps(saved))

    def test_cli_none_result_is_not_success(self):
        code, _, saved = self.cli()
        self.assertEqual(code, 1)
        self.assertEqual(saved['publicationStatus'], 'unknown')

    def test_cli_persistence_error_retains_verified_publication_and_nonzero_exit(self):
        code, output, saved = self.cli(error=PublicationError('PUBLICATION_RECEIPT_SAVE_FAILED', 'published', 51))
        self.assertEqual(code, 1)
        self.assertIn('PUBLICATION_RECEIPT_SAVE_FAILED', output)
        self.assertEqual(saved['publicationStatus'], 'published')
        self.assertTrue(saved['publicationVerified'])

    def test_log_failure_after_verified_publish_does_not_erase_actual_publication(self):
        result = {'status': 'published', 'publicationRunId': 51, 'publicationVerified': True}
        with patch('daily_run_log.RunLog.finish', side_effect=[PipelineError('RUN_LOG_UNAVAILABLE'), None]) as finish:
            code, output, _ = self.cli(result)
        self.assertEqual(code, 1)
        self.assertIn('RUN_LOG_UNAVAILABLE', output)
        self.assertEqual(finish.call_args.kwargs['publication'], publication_result('published', 51))


class IntegrationTests(unittest.TestCase):
    def setup_pipeline(self):
        fixture = daily_tests.OrchestrationTests('test_complete_sunday_reuses_collection_builds_real_artifacts_dispatches_once')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_coordinator_propagates_existing_receipt_and_preserves_requested_id(self):
        fixture = self.setup_pipeline()
        with patch('run_windows_daily.await_publication', return_value=publication_result('observed_other_run', 52)):
            result = fixture.run_pipeline()
        self.assertEqual(result['status'], 'observed_other_run')
        self.assertEqual(result['publicationRunId'], 52)
        journal = next((fixture.cfg['runtime_dir']/'daily/dispatch').glob('*.json'))
        value = load_json(journal)
        self.assertEqual(value['workflowRunId'], 901)
        self.assertEqual(value['confirmedWorkflowRunId'], 52)
        self.assertEqual(value['state'], 'observed')

    def test_coordinator_rejects_none_before_marking_intent_verified(self):
        fixture = self.setup_pipeline()
        with patch('run_windows_daily.await_publication', return_value=None):
            with self.assertRaisesRegex(PipelineError, 'RESULT_UNCONFIRMED'):
                fixture.run_pipeline()
        journal = next((fixture.cfg['runtime_dir']/'daily/dispatch').glob('*.json'))
        self.assertEqual(load_json(journal)['state'], 'accepted')

    def test_coordinator_journal_save_error_preserves_successful_deployment_fact(self):
        fixture = self.setup_pipeline()
        def save(path, value):
            if value.get('state') == 'verified':
                raise OSError('fixture-private-error')
            write_json(path, value)
        with patch('run_windows_daily.write_json', side_effect=save):
            with self.assertRaises(PublicationError) as caught:
                fixture.run_pipeline()
        self.assertEqual(str(caught.exception), 'PUBLICATION_RECEIPT_SAVE_FAILED')
        self.assertEqual(caught.exception.publication, publication_result('published', 901))
        journal = next((fixture.cfg['runtime_dir']/'daily/dispatch').glob('*.json'))
        self.assertEqual(load_json(journal)['state'], 'accepted')

    def test_november8_first_sunday_creates_valid_note_bundle_from_real_ordered_fixture(self):
        fixture = self.setup_pipeline()
        fixture.current = NOW.replace(minute=10)
        fixture.fixture.edit('products.json', updatedAt=fixture.current.isoformat())
        fixture.fixture.edit('collection-status.json', collectedAt=fixture.current.isoformat(),
            productsSha256=digest(fixture.root/'data/products.json'),
            affiliateVerificationExpiresAt=(NOW+timedelta(days=5)).isoformat())
        history = digest(fixture.root/'data/history.json')
        result = fixture.run_pipeline()
        self.assertEqual(result['status'], 'published')
        daily = load_json(fixture.root/'data/editorial.json')
        weekly = load_json(fixture.root/'data/note-weekly.json')
        self.assertEqual(daily['generatedAt'], NOW.replace(minute=30).isoformat())
        self.assertEqual(weekly['generatedAt'], NOW.replace(minute=40).isoformat())
        self.assertEqual(weekly['weekLabel'], '2026/11/02〜2026/11/08')
        self.assertEqual(weekly['sourceProductsSha256'], daily['sourceProductsSha256'])
        self.assertEqual(digest(fixture.root/'data/history.json'), history)
        self.assertTrue(validate_publication(fixture.root, NOW)['includeNote'])
        bundle = next((fixture.cfg['runtime_dir']/'daily/runs').glob('*/publication-output/bundle'))
        verify_bundle(bundle, NOW)
        note_bundle = next((fixture.cfg['runtime_dir']/'daily/runs').glob('*/note-preview/bundle'))
        verify_bundle(note_bundle, NOW)
        self.assertTrue((note_bundle/'note/archive/2026-11-08/article.md').exists())


if __name__ == '__main__':
    unittest.main()
