"""Eight-hour cron delay resilience without weakening the daily Pages freshness gate."""
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import recover_sunday_note as recover
import test_windows_pipeline as fixtures
from pipeline_common import PipelineError, digest
from publication_pipeline import validate_site_publication, validate_purpose


SUNDAY = datetime(2026, 11, 8, 7, 10, tzinfo=timezone(timedelta(hours=9)))


class RecoveryPlannerTests(unittest.TestCase):
    def test_cron_delayed_eight_hours_still_reaches_current_week_plan(self):
        with patch.object(recover, 'load_json', return_value={'weekLabel':'2026/10/26〜2026/11/01'}), \
             patch.object(recover, 'weekly', return_value={'weekLabel':'2026/11/02〜2026/11/08'}) as generator:
            result = recover.plan(Path('/unused'), SUNDAY + timedelta(hours=8))
        self.assertEqual(result['status'], 'ready')
        generator.assert_called_once()
        self.assertEqual(generator.call_args.kwargs['max_collection_age_hours'], 11)

    def test_current_week_is_not_regenerated(self):
        with patch.object(recover, 'load_json', return_value={'weekLabel':'2026/11/02〜2026/11/08'}), \
             patch.object(recover, 'weekly') as generator:
            result = recover.plan(Path('/unused'), SUNDAY + timedelta(hours=8))
        self.assertEqual(result['status'], 'already_current')
        generator.assert_not_called()

    def test_wrong_day_and_nine_hours_late_fail_closed(self):
        with patch.object(recover, 'load_json') as reader:
            self.assertEqual(recover.plan(Path('/unused'), SUNDAY + timedelta(days=1))['status'], 'outside_window')
            self.assertEqual(recover.plan(Path('/unused'), SUNDAY + timedelta(hours=11))['status'], 'outside_window')
            reader.assert_not_called()

    def test_future_week_never_overwritten(self):
        with patch.object(recover, 'load_json', return_value={'weekLabel':'2026/11/09〜2026/11/15'}), \
             patch.object(recover, 'weekly') as generator:
            with self.assertRaisesRegex(PipelineError, 'CONFLICT'):
                recover.plan(Path('/unused'), SUNDAY)
            generator.assert_not_called()


class SourceFreshnessIsolationTests(unittest.TestCase):
    def setUp(self):
        f = fixtures.PublicationTests('test_matching_generation_ready')
        f.setUp()
        self.addCleanup(f.tearDown)
        self.root, self.now = f.root, fixtures.SUNDAY
        self.fixture = f

    def test_ten_hour_old_same_day_source_only_allowed_in_note_contract(self):
        delayed = self.now + timedelta(hours=8)
        self.fixture.edit('note-weekly.json', generatedAt=delayed.isoformat())
        # On purpose we simulate an actually later weekly editorial with
        # real same-day source hashes. Daily Pages must still refuse old prices.
        with self.assertRaisesRegex(PipelineError, 'PRODUCTS_STALE_OR_FUTURE'):
            validate_site_publication(self.root, delayed)
        result = validate_purpose(self.root, delayed, 'note')
        self.assertTrue(result['includeNote'])

    def test_later_date_still_refused_even_under_note_extension(self):
        with self.assertRaisesRegex(PipelineError, 'PRODUCTS_STALE_OR_FUTURE'):
            validate_purpose(self.root, self.now + timedelta(days=1), 'note')
