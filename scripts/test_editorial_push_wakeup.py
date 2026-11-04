"""Offline boundary tests for the guarded editorial push fallback."""
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch

from pipeline_common import PipelineError
from editorial_push_wakeup import dispatch_allowed, slot_delay, run_plan

JST = ZoneInfo("Asia/Tokyo")
def at(hour, minute=0, day=4):
    return datetime(2026, 11, day, hour, minute, tzinfo=JST)

def row(event="schedule", status="completed", day=4):
    return {
        "id": 123,
        "created_at": at(7, day=day).isoformat(),
        "head_branch": "main",
        "event": event,
        "status": status,
    }

class WakeupRegressionTests(unittest.TestCase):
    def test_early_editorial_waits_until_publication_slot(self):
        self.assertEqual(slot_delay(at(6,30), at(6,30)), 20 * 60)
        self.assertEqual(slot_delay(at(6,50), at(6,30)), 0)

    def test_wrong_day_old_or_future_pushes_are_rejected(self):
        self.assertIsNone(slot_delay(at(6,50), at(6,30,day=3)))
        self.assertIsNone(slot_delay(at(6,50), at(6,53)))
        self.assertIsNone(slot_delay(at(6,50), at(6,19)))
        self.assertIsNone(slot_delay(at(9,6), at(6,30)))
        self.assertIsNone(slot_delay(at(9,5), at(6,0)))

    def test_late_same_day_push_can_run_before_end(self):
        self.assertEqual(slot_delay(at(8,50), at(6,30)), 0)

    def test_prior_dispatch_and_inflight_schedule_block(self):
        self.assertFalse(dispatch_allowed([row(event="workflow_dispatch")], at(8)))
        self.assertFalse(dispatch_allowed([row(status="in_progress")], at(8)))
        self.assertTrue(dispatch_allowed([row(status="failure")], at(8)))
        self.assertTrue(dispatch_allowed([row(day=3, event="workflow_dispatch")], at(8)))

    def test_unknown_or_paginated_list_fails_closed(self):
        with self.assertRaisesRegex(PipelineError, "INVALID"):
            dispatch_allowed([{"id": "bad"}], at(8))
        with self.assertRaisesRegex(PipelineError, "PAGINATION"):
            dispatch_allowed([row()] * 100, at(8))

    def test_source_validation_failure_never_authorizes(self):
        with patch("editorial_push_wakeup.validate_site_publication",
                   side_effect=PipelineError("PRODUCTS_STALE_OR_FUTURE")):
            self.assertFalse(run_plan(at(6,50), at(6,30), None, lambda _: None))

    def test_verified_publication_never_authorizes_again(self):
        with patch("editorial_push_wakeup.validate_site_publication"), \
             patch("editorial_push_wakeup.publication_receipt",return_value={"runId":123}):
            self.assertFalse(run_plan(at(6,50), at(6,30), None, lambda _: None))

    def test_eligible_only_with_fresh_source_no_prior_receipt_and_no_active_run(self):
        with patch("editorial_push_wakeup.validate_site_publication"), \
             patch("editorial_push_wakeup.publication_receipt",return_value=None):
            self.assertTrue(run_plan(at(6,50), at(6,30), None,
                                     lambda _: {"workflow_runs": []}))

if __name__ == "__main__":
    unittest.main()
