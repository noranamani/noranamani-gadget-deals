"""Deterministic, network-free Pages artifact preflight regression tests."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from check_pages_artifact_ready import (
    ArtifactNotReady, artifact_visible, wait_for_visible,
)

RUN_ID = 456789
NOW = datetime(2026, 11, 24, 7, 0, tzinfo=timezone.utc)


def payload(*, run_id=RUN_ID, name="github-pages", digest="sha256:"+"a"*64,
            expired=False, size=314159, expiry=None):
    return {"total_count": 1, "artifacts": [{
        "id": 987654, "name": name, "size_in_bytes": size,
        "expired": expired, "digest": digest,
        "expires_at": (expiry or NOW+timedelta(days=1)).isoformat(),
        "workflow_run": {"id": run_id}
    }]}


class ArtifactPreflightTests(unittest.TestCase):
    def test_verified_current_run_artifact_is_only_a_readiness_signal(self):
        self.assertTrue(artifact_visible(payload(), RUN_ID, now=NOW))

    def test_artifact_not_yet_visible(self):
        self.assertFalse(artifact_visible({"artifacts": []}, RUN_ID, now=NOW))

    def test_wrong_run_name_digest_size_or_expiry_never_grants_deploy(self):
        bad = [
            {"run_id":RUN_ID-1}, {"name":"validated-publication"},
            {"digest":"sha256:bad"}, {"expired":True}, {"size":0},
            {"expiry":NOW-timedelta(seconds=1)}
        ]
        for change in bad:
            with self.subTest(change=change):
                self.assertFalse(artifact_visible(payload(**change),RUN_ID,now=NOW))

    def test_duplicate_name_is_ambiguous_even_if_one_valid(self):
        item = payload()["artifacts"][0]
        with self.assertRaisesRegex(ArtifactNotReady,"AMBIGUOUS"):
            artifact_visible({"artifacts":[item,item]},RUN_ID,now=NOW)

    def test_missing_or_invalid_list_fail_closed(self):
        for x in [None,{},{"artifacts":None}, {"artifacts":"fake"}]:
            with self.subTest(payload=x),self.assertRaises(ArtifactNotReady):
                artifact_visible(x,RUN_ID,now=NOW)

    def test_two_consecutive_read_receipts_before_any_deploy(self):
        replies = [
            {"artifacts":[]}, payload(), {"artifacts":[]},
            payload(), payload(),
        ]
        read=Mock(side_effect=replies)
        pause=Mock()
        self.assertTrue(wait_for_visible(read,RUN_ID,attempts=6,seconds=0.01,
                                         pause=pause,clock=lambda:NOW))
        self.assertEqual(read.call_count,5)
        self.assertEqual(pause.call_count,4)

    def test_bounded_absent_fails_before_pages_write(self):
        read=Mock(return_value={"artifacts":[]})
        pause=Mock()
        with self.assertRaisesRegex(ArtifactNotReady,"NO_DEPLOY_ATTEMPT"):
            wait_for_visible(read,RUN_ID,attempts=4,seconds=0,
                             pause=pause,clock=lambda:NOW)
        self.assertEqual(read.call_count,4)
        self.assertEqual(pause.call_count,3)

    def test_invalid_params_do_not_call_provider(self):
        read=Mock()
        with self.assertRaises(ArtifactNotReady):
            wait_for_visible(read,-1)
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
