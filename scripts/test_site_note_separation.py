"""Daily Pages and Sunday note have independent, strict acceptance contracts."""
import io
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from unittest.mock import Mock, patch

import test_windows_pipeline as fixtures
import test_windows_daily as daily_tests
from test_publication_hardening import api_for
from daily_run_log import RunLog
from pipeline_common import PipelineError, digest, load_json, write_json
from publication_gate import DEPLOY_STEP, gate
from publication_pipeline import (prepare_publication, verify_bundle, validate_publication,
    validate_site_publication, persist_note, main as publication_main)
from run_windows_daily import isolated_note_error


class SeparateArtifactTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublicationTests('test_matching_generation_ready')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root, self.base = self.fixture.root, self.fixture.base
        fixtures.copy_publication_sources(self.root)
        self.now = fixtures.SUNDAY

    def weekday(self):
        self.now += timedelta(days=1)
        self.fixture.edit('products.json', updatedAt=self.now.replace(minute=10).isoformat())
        self.fixture.edit('collection-status.json', collectedAt=self.now.replace(minute=10).isoformat(),
            productsSha256=digest(self.root/'data/products.json'))
        self.fixture.edit('editorial.json', generatedAt=self.now.replace(minute=30).isoformat(),
            sourceProductsSha256=digest(self.root/'data/products.json'))

    def prepare(self, purpose, suffix='output'):
        result = prepare_publication(self.root, self.base/suffix, fixtures.SHA, self.now, purpose=purpose)
        self.assertEqual(verify_bundle(self.base/suffix/'bundle', self.now), result)
        return result

    def test_first_weekday_needs_no_weekly_json_or_note_directory(self):
        self.weekday()
        (self.root/'data/note-weekly.json').unlink()
        source, destination = self.root/'note', self.base/'saved-note'
        self.assertTrue(source.resolve().is_relative_to(self.base.resolve()))
        self.assertTrue(destination.resolve().is_relative_to(self.base.resolve()))
        source.rename(destination)
        before = {name: digest(self.root/'data'/name) for name in ('products.json', 'history.json', 'editorial.json')}
        manifest = self.prepare('site')
        self.assertFalse(manifest['includeNote'])
        self.assertIn('docs/index.html', manifest['sha256'])
        self.assertTrue(all(n.startswith('docs/') for n in manifest['sha256']))
        for name, value in before.items():
            self.assertEqual(digest(self.root/'data'/name), value)
        self.assertFalse((self.root/'data/note-weekly.json').exists())

    def test_corrupt_weekly_does_not_invalidate_daily_site_but_note_still_stops(self):
        self.weekday()
        path = self.root/'data/note-weekly.json'
        path.write_text('{invalid', encoding='utf-8')
        before = digest(path)
        self.prepare('site')
        self.assertEqual(digest(path), before)
        with self.assertRaises(PipelineError):
            validate_publication(self.root, self.now)

    def test_sunday_missing_weekly_blocks_note_only(self):
        (self.root/'data/note-weekly.json').unlink()
        self.prepare('site')
        with self.assertRaisesRegex(PipelineError, 'INPUT_UNAVAILABLE'):
            self.prepare('note', 'note')

    def test_sunday_wrong_hash_or_future_edit_blocks_note_only(self):
        for changes in ({'sourceProductsSha256': 'b'*64}, {'generatedAt': (self.now+timedelta(minutes=1)).isoformat()}):
            with self.subTest(changes=changes):
                self.fixture.edit('note-weekly.json', **changes)
                self.assertFalse(validate_site_publication(self.root, self.now)['includeNote'])
                with self.assertRaises(PipelineError):
                    self.prepare('note', 'blocked')

    def test_sunday_note_contains_no_pages_and_retains_common_manifest_contract(self):
        before = digest(self.root/'data/history.json')
        manifest = self.prepare('note')
        self.assertTrue(manifest['includeNote'])
        self.assertEqual(manifest['purpose'], 'note')
        self.assertTrue(all(n.startswith('note/') for n in manifest['sha256']))
        note = load_json(self.base/'output/bundle/note/latest/manifest.json')
        self.assertEqual(note['schema_version'], 1)
        self.assertEqual(note['source_products_sha256'], digest(self.root/'data/products.json'))
        self.assertEqual(digest(self.root/'data/history.json'), before)

    def test_note_generation_refuses_weekday_instead_of_repricing_old_issue(self):
        self.weekday()
        with self.assertRaisesRegex(PipelineError, 'NOTE_NOT_DUE'):
            self.prepare('note')

    def test_site_contract_still_rejects_stale_products_and_old_daily(self):
        with self.assertRaisesRegex(PipelineError, 'PRODUCTS_STALE_OR_FUTURE'):
            validate_site_publication(self.root, self.now+timedelta(hours=3))
        self.fixture.edit('editorial.json', generatedAt=(self.now-timedelta(days=1)).isoformat())
        with self.assertRaisesRegex(PipelineError, 'EDITORIAL_GENERATION_MISMATCH'):
            self.prepare('site')

    def test_site_contract_still_rejects_missing_categories_and_hash_mismatch(self):
        self.fixture.edit('editorial.json', categoryNotes=[])
        with self.assertRaisesRegex(PipelineError, 'COVERAGE'):
            self.prepare('site')
        self.fixture.edit('collection-status.json', historySha256='b'*64)
        with self.assertRaisesRegex(PipelineError, 'COLLECTION_GENERATION_MISMATCH'):
            self.prepare('site')

    def test_site_gate_retains_duplicate_and_uncertain_publication_guards(self):
        (self.root/'data/note-weekly.json').unlink()
        self.assertFalse(gate(self.root, self.now, api_for([{'name': DEPLOY_STEP, 'conclusion': 'success'}]), site_only=True))
        with self.assertRaisesRegex(PipelineError, 'DEPLOYMENT_UNCERTAIN'):
            gate(self.root, self.now, api_for([{'name': DEPLOY_STEP, 'conclusion': 'failure'}]), site_only=True)

    def test_site_gate_stops_early_or_stale_before_reading_github(self):
        get = Mock()
        with self.assertRaisesRegex(PipelineError, 'BEFORE_PUBLICATION'):
            gate(self.root, self.now.replace(minute=49), get, site_only=True)
        with self.assertRaisesRegex(PipelineError, 'PRODUCTS_STALE'):
            gate(self.root, self.now+timedelta(hours=3), get, site_only=True)
        get.assert_not_called()

    def test_note_artifact_cannot_be_deployed_as_site(self):
        self.prepare('note')
        output = io.StringIO()
        with patch('sys.argv', ['publication', 'verify-current', '--purpose', 'site', '--root', str(self.root),
                '--bundle', str(self.base/'output/bundle'), '--expected-main', fixtures.SHA]), \
                patch('publication_pipeline.datetime') as clock, \
                patch('publication_pipeline.GitRepository') as repo, redirect_stdout(output):
            clock.now.return_value = self.now
            self.assertEqual(publication_main(), 1)
        repo.assert_not_called()
        self.assertIn('PURPOSE_MISMATCH', output.getvalue())

    def test_site_artifact_cannot_smuggle_note_files(self):
        self.prepare('site')
        bundle = self.base/'output/bundle'
        path = bundle/'note/extra.json'
        path.parent.mkdir()
        path.write_text('{}', encoding='utf-8')
        value = load_json(bundle/'publication-manifest.json')
        value['sha256']['note/extra.json'] = digest(path)
        write_json(bundle/'publication-manifest.json', value)
        with self.assertRaisesRegex(PipelineError, 'PURPOSE_MISMATCH'):
            verify_bundle(bundle, self.now)

    def test_same_week_note_retry_reuses_exact_files_and_preserves_source(self):
        import shutil
        self.prepare('note')
        shutil.copytree(self.base/'output/bundle/note', self.root/'note', dirs_exist_ok=True)
        before = {p.relative_to(self.root): digest(p) for p in (self.root/'note').rglob('*') if p.is_file()}
        manifest = self.prepare('note', 'retry')
        for name, value in before.items():
            self.assertEqual(digest(self.root/name), value)
            if name.as_posix() in manifest['sha256']:
                self.assertEqual(digest(self.base/'retry/bundle'/name), value)

    def test_same_week_changed_note_cannot_overwrite_saved_issue(self):
        import shutil
        self.prepare('note')
        shutil.copytree(self.base/'output/bundle/note', self.root/'note', dirs_exist_ok=True)
        self.fixture.edit('note-weekly.json', title='fixture changed title')
        with self.assertRaises(PipelineError):
            self.prepare('note', 'retry')


class CoordinatorSeparationTests(unittest.TestCase):
    def fixture(self):
        f = daily_tests.OrchestrationTests('test_complete_sunday_reuses_collection_builds_real_artifacts_dispatches_once')
        f.setUp()
        self.addCleanup(f.doCleanups)
        return f

    def test_missing_sunday_weekly_is_logged_as_blocked_without_stopping_site(self):
        f = self.fixture()
        (f.root/'data/note-weekly.json').unlink()
        result = f.run_pipeline()
        self.assertEqual(result['status'], 'published')
        self.assertEqual(result['weeklyNotePreviewStatus'], 'blocked')
        self.assertEqual(result['weeklyNoteCode'], 'INPUT_UNAVAILABLE')
        self.assertFalse((f.root/'data/note-weekly.json').exists())
        self.assertEqual(f.repo.prepare_commit.call_count, 1)

    def test_wrong_same_week_hash_keeps_original_and_publishes_only_site(self):
        f = self.fixture()
        f.fixture.edit('note-weekly.json', weekLabel='2026/10/19〜2026/10/25', sourceProductsSha256='b'*64)
        before = digest(f.root/'data/note-weekly.json')
        result = f.run_pipeline()
        self.assertEqual(result['weeklyNotePreviewStatus'], 'blocked')
        self.assertEqual(result['status'], 'published')
        self.assertEqual(digest(f.root/'data/note-weekly.json'), before)

    def test_invalid_weekly_timestamp_is_isolated_without_rewriting_old_note(self):
        f = self.fixture()
        f.fixture.edit('note-weekly.json', generatedAt='invalid-fixture-date')
        before = digest(f.root/'data/note-weekly.json')
        result = f.run_pipeline()
        self.assertEqual(result['status'], 'published')
        self.assertEqual(result['weeklyNotePreviewStatus'], 'blocked')
        self.assertEqual(digest(f.root/'data/note-weekly.json'), before)

    def test_nonobject_weekly_is_isolated_but_never_initialised_or_persisted(self):
        f = self.fixture()
        write_json(f.root/'data/note-weekly.json', [])
        before = digest(f.root/'data/note-weekly.json')
        result = f.run_pipeline()
        self.assertEqual(result['status'], 'published')
        self.assertEqual(result['weeklyNotePreviewStatus'], 'blocked')
        self.assertEqual(digest(f.root/'data/note-weekly.json'), before)

    def test_note_build_failure_does_not_change_site_publication_result(self):
        from run_windows_daily import prepare_publication as original
        f = self.fixture()
        def prepare(*args, **kwargs):
            if kwargs.get('purpose') == 'note':
                raise PipelineError('BUILD_OR_VALIDATION_FAILED')
            return original(*args, **kwargs)
        with patch('run_windows_daily.prepare_publication', side_effect=prepare):
            result = f.run_pipeline()
        self.assertEqual(result['status'], 'published')
        self.assertEqual(result['weeklyNotePreviewStatus'], 'blocked')

    def test_secret_git_and_unknown_errors_are_never_ignored_as_note_errors(self):
        for error in (PipelineError('SECRET_IN_OUTPUT'), PipelineError('PUSH_CONFLICT'),
                PipelineError('PUSH_REJECTED_OR_UNCERTAIN'), ValueError('fixture-private')):
            with self.subTest(kind=type(error).__name__), self.assertRaises(type(error)):
                isolated_note_error(error)

    def test_blocked_note_log_has_fixed_code_without_secret_text(self):
        f = self.fixture()
        log = RunLog(f.base/'logs', f.clock)
        log.note_preview('blocked', 'fixture-secret cookie=invalid')
        saved = load_json(log.path)
        self.assertEqual(saved['weeklyNoteCode'], 'PIPELINE_ERROR')
        self.assertNotIn('fixture-secret', log.path.read_text())


if __name__ == '__main__':
    unittest.main()
