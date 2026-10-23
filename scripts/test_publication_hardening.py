"""Hardening rehearsal: fixed Sunday fixtures only; never changes production."""
import copy
import shutil
import unittest
import contextlib
import io
from datetime import timedelta
from unittest.mock import Mock, patch
from pathlib import Path

import test_windows_pipeline as fixtures
from category_rollout import require_apply_window, decide
from pipeline_common import PipelineError, digest, load_json, write_json
from publication_gate import gate, publication_needed, DEPLOY_STEP
from publication_pipeline import prepare_publication, verify_bundle, main as publication_main

NOW = fixtures.SUNDAY
RUN = {'id': 12, 'head_branch': 'main', 'created_at': NOW.isoformat()}


def api_for(steps=(), conclusion='success'):
    def get(path):
        if '/runs?' in path:
            return {'workflow_runs': [RUN]}
        return {'jobs': [{'name': 'deploy', 'conclusion': conclusion, 'steps': list(steps)}]}
    return Mock(side_effect=get)


class PublicationReceiptTests(unittest.TestCase):
    def test_no_deployment_can_publish(self):
        self.assertTrue(publication_needed(api_for(), NOW))

    def test_success_skips_second_deployment(self):
        get = api_for([{'name': DEPLOY_STEP, 'conclusion': 'success'}])
        self.assertFalse(publication_needed(get, NOW))

    def test_successful_pages_step_is_receipt_even_if_job_failed_afterwards(self):
        get = api_for([{'name': DEPLOY_STEP, 'conclusion': 'success'}], 'failure')
        self.assertFalse(publication_needed(get, NOW))

    def test_failed_pages_attempt_is_ambiguous_and_requires_review(self):
        with self.assertRaisesRegex(PipelineError, 'DEPLOYMENT_UNCERTAIN'):
            publication_needed(api_for([{'name': DEPLOY_STEP, 'conclusion': 'failure'}]), NOW)

    def test_cancelled_inflight_pages_requires_review(self):
        with self.assertRaisesRegex(PipelineError, 'DEPLOYMENT_UNCERTAIN'):
            publication_needed(api_for([{'name': DEPLOY_STEP, 'started_at': NOW.isoformat(), 'conclusion': None}]), NOW)

    def test_predeploy_failure_can_recover(self):
        self.assertTrue(publication_needed(api_for([{'name': DEPLOY_STEP, 'conclusion': 'skipped'}], 'failure'), NOW))

    def test_unknown_state_fails_closed(self):
        with self.assertRaisesRegex(PipelineError, 'STATE_INVALID'):
            publication_needed(lambda _: {}, NOW)

    def test_wrong_branch_receipt_rejected(self):
        with self.assertRaisesRegex(PipelineError, 'STATE_INVALID'):
            publication_needed(lambda _: {'workflow_runs': [{**RUN, 'head_branch': 'feature/untrusted'}]}, NOW)

    def test_jobs_all_attempts_are_paginated(self):
        def get(path):
            if '/runs?' in path:
                return {'workflow_runs': [RUN]}
            if path.endswith('page=1'):
                return {'jobs': [{'name': 'prepare'}] * 100}
            return {'jobs': [{'name': 'deploy', 'steps': [{'name': DEPLOY_STEP, 'conclusion': 'success'}]}]}
        self.assertFalse(publication_needed(get, NOW))

    def test_api_error_is_not_interpreted_as_unpublished(self):
        get = Mock(side_effect=PipelineError('PUBLICATION_STATE_UNAVAILABLE'))
        with self.assertRaisesRegex(PipelineError, 'STATE_UNAVAILABLE'):
            publication_needed(get, NOW)


class SundayRehearsalTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublicationTests('test_matching_generation_ready')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root, self.base = self.fixture.root, self.fixture.base

    def copy_build_sources(self):
        for folder in ('scripts', 'docs', 'note'):
            shutil.copytree(fixtures.ROOT / folder, self.root / folder)

    def test_early_publication_refused_without_api(self):
        get = Mock()
        with self.assertRaisesRegex(PipelineError, 'BEFORE_PUBLICATION'):
            gate(self.root, NOW.replace(hour=6, minute=49), get)
        get.assert_not_called()

    def test_sunday_normal_then_duplicate_trigger_skip(self):
        self.assertTrue(gate(self.root, NOW, api_for()))
        self.assertFalse(gate(self.root, NOW + timedelta(minutes=25),
            api_for([{'name': DEPLOY_STEP, 'conclusion': 'success'}])))

    def test_weekly_delayed_until_0720_recovers_at_0745(self):
        weekly = load_json(self.root / 'data/note-weekly.json')
        weekly['generatedAt'] = NOW.replace(hour=7, minute=20).isoformat()
        write_json(self.root / 'data/note-weekly.json', weekly)
        get = api_for()
        with self.assertRaisesRegex(PipelineError, 'NOTE_WEEK'):
            gate(self.root, NOW, get)
        get.assert_not_called()
        self.assertTrue(gate(self.root, NOW.replace(hour=7, minute=45), get))

    def test_missing_weekly_never_reaches_deployment_state_check(self):
        (self.root / 'data/note-weekly.json').unlink()
        get = Mock()
        with self.assertRaises(PipelineError):
            gate(self.root, NOW + timedelta(minutes=55), get)
        get.assert_not_called()

    def test_weekly_wrong_hash_stops_without_api(self):
        self.fixture.edit('note-weekly.json', sourceProductsSha256='b'*64)
        get = Mock()
        with self.assertRaisesRegex(PipelineError, 'NOTE_SOURCE'):
            gate(self.root, NOW, get)
        get.assert_not_called()

    def test_stale_products_still_rejected_on_recovery(self):
        get = Mock()
        with self.assertRaisesRegex(PipelineError, 'PRODUCTS_STALE'):
            gate(self.root, NOW.replace(hour=9, minute=11), get)
        get.assert_not_called()

    def test_pages_failure_retry_reuses_exact_note_and_preserves_source(self):
        self.copy_build_sources()
        first = self.base / 'first'
        prepare_publication(self.root, first, fixtures.SHA, NOW)
        shutil.copytree(first / 'bundle/note', self.root / 'note', dirs_exist_ok=True)
        old = {p.relative_to(self.root).as_posix(): digest(p) for p in (self.root / 'note').rglob('*') if p.is_file()}
        second = self.base / 'second'
        manifest = prepare_publication(self.root, second, fixtures.SHA, NOW + timedelta(minutes=25))
        for name, sha in old.items():
            self.assertEqual(digest(self.root / name), sha)
        for name in manifest['sha256']:
            if name.startswith('note/'):
                self.assertEqual(digest(second / 'bundle' / name), digest(self.root / name))
        with self.assertRaisesRegex(PipelineError, 'EXPIRED'):
            verify_bundle(first / 'bundle', NOW + timedelta(minutes=31))

    def test_same_week_changed_editorial_cannot_overwrite_persisted_note(self):
        self.copy_build_sources()
        first = self.base / 'first'
        prepare_publication(self.root, first, fixtures.SHA, NOW)
        shutil.copytree(first / 'bundle/note', self.root / 'note', dirs_exist_ok=True)
        self.fixture.edit('note-weekly.json', title='変更した同週の編集')
        original = digest(self.root / 'note/latest/article.md')
        with self.assertRaises(PipelineError):
            prepare_publication(self.root, self.base / 'changed', fixtures.SHA, NOW + timedelta(minutes=25))
        self.assertEqual(digest(self.root / 'note/latest/article.md'), original)

    def test_same_week_archive_tamper_is_not_overwritten(self):
        self.copy_build_sources()
        first = self.base / 'first'
        prepare_publication(self.root, first, fixtures.SHA, NOW)
        shutil.copytree(first / 'bundle/note', self.root / 'note', dirs_exist_ok=True)
        (self.root / 'note/archive/2026-10-25/article.md').write_text('tampered', encoding='utf-8')
        with self.assertRaisesRegex(PipelineError, 'ARCHIVE'):
            prepare_publication(self.root, self.base / 'retry', fixtures.SHA, NOW + timedelta(minutes=25))

    def test_final_cli_verification_refuses_expired_artifact(self):
        self.copy_build_sources()
        output = self.base / 'prepared'
        prepare_publication(self.root, output, fixtures.SHA, NOW)
        stream = io.StringIO()
        with patch('sys.argv', ['publication', 'verify-current', '--root', str(self.root),
                '--bundle', str(output / 'bundle'), '--expected-main', fixtures.SHA]), \
                patch('publication_pipeline.datetime') as clock, \
                patch('publication_pipeline.GitRepository') as repository, contextlib.redirect_stdout(stream):
            clock.now.return_value = NOW + timedelta(minutes=31)
            self.assertEqual(publication_main(), 1)
            repository.return_value.remote_tip.assert_not_called()
        self.assertIn('EXPIRED', stream.getvalue())

    def test_final_cli_verification_refuses_products_older_than_three_hours(self):
        stream = io.StringIO()
        with patch('sys.argv', ['publication', 'verify-current', '--root', str(self.root),
                '--bundle', str(self.base / 'unused'), '--expected-main', fixtures.SHA]), \
                patch('publication_pipeline.datetime') as clock, \
                patch('publication_pipeline.GitRepository') as repository, contextlib.redirect_stdout(stream):
            clock.now.return_value = NOW.replace(hour=9, minute=11)
            self.assertEqual(publication_main(), 1)
            repository.assert_not_called()
        self.assertIn('PRODUCTS_STALE', stream.getvalue())

    def test_identical_note_files_do_not_create_a_second_git_commit(self):
        fixture = fixtures.GitTransactionTests('test_single_commit_triple_no_checkout_mutation')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        files = {'note/latest/' + name: b'fixture' for name in
            ('article.md', 'article-cover.png', 'note-post.zip', 'manifest.json')}
        first = fixture.repo.prepare_commit(fixture.base, files, 'note')
        fixture.repo.push_if_unchanged(fixture.base, first)
        second = fixture.repo.prepare_commit(first, files, 'note')
        self.assertEqual(second, first)
        self.assertEqual(fixture.repo.push_if_unchanged(first, second), first)
        self.assertEqual(fixture.repo.run('rev-list', '--count', first).strip(), b'2')


class CategoryMigrationTests(unittest.TestCase):
    def test_first_windows_sunday_is_postponed_without_disabling_expansion(self):
        import test_category_rollout as rollout
        cfg, state, queries, products = rollout.seed()
        production = load_json(fixtures.ROOT / 'config/category-expansion.json')
        self.assertTrue(production['enabled'])
        cfg['first_addition_jst'] = production['first_addition_jst']
        self.assertEqual(decide(cfg, state, queries, products, now=rollout.SUNDAY)['action'], 'waiting_until_start')
        later = rollout.SUNDAY + timedelta(days=7)
        products['updatedAt'] = (later - timedelta(hours=24)).isoformat()
        self.assertEqual(decide(cfg, state, queries, products, now=later)['action'], 'eligible')

    def test_delayed_category_push_refused_before_windows_collection(self):
        require_apply_window(NOW.replace(hour=5, minute=15))
        with self.assertRaisesRegex(ValueError, 'SAFE_WINDOW'):
            require_apply_window(NOW.replace(hour=6, minute=5))

    def test_rollout_does_not_apply_on_weekdays(self):
        with self.assertRaisesRegex(ValueError, 'SAFE_WINDOW'):
            require_apply_window(NOW.replace(hour=5, minute=15) + timedelta(days=1))


if __name__ == '__main__':
    unittest.main()
