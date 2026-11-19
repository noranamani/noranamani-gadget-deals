"""Rakuten portal outage: offline authorization, expiry, identity and Pages gates."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import timedelta, timezone, datetime
from pathlib import Path
from unittest.mock import patch

from pipeline_common import PipelineError, load_json, write_json
from windows_credentials import (
    Credentials, affiliate_proof, authorize_maintenance_continuity,
    maintenance_path, record_affiliate_proof, main
)
from publication_pipeline import validate_collection
from test_windows_pipeline import PublicationTests, SUNDAY

CREDS = Credentials('example-application', 'example-access-secret', 'example-affiliate')
CONFIRMED = datetime(2026, 10, 21, 5, 37, 21, tzinfo=timezone.utc)
EXPIRES = CONFIRMED + timedelta(days=30)
END = EXPIRES + timedelta(days=7)


class LocalContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.proof_path = Path(self.temp.name) / 'affiliate-proof.json'
        record_affiliate_proof(self.proof_path, CREDS, CREDS.affiliate_id, CONFIRMED)
        self.original = self.proof_path.read_bytes()

    def authorize(self, when=EXPIRES - timedelta(hours=3)):
        return authorize_maintenance_continuity(self.proof_path, CREDS, when)

    def test_no_override_means_normal_expiry_still_fails(self):
        with self.assertRaisesRegex(PipelineError, 'PROOF_EXPIRED'):
            affiliate_proof(self.proof_path, CREDS, EXPIRES)

    def test_fixed_7_day_window_and_unmodified_original(self):
        stamp = self.authorize()
        self.assertEqual(stamp['expiresAt'], END.isoformat())
        self.assertEqual(self.proof_path.read_bytes(), self.original)
        before = affiliate_proof(self.proof_path, CREDS, EXPIRES - timedelta(seconds=1))
        self.assertEqual(before['mode'], 'official')
        self.assertEqual(before['expiresAt'], EXPIRES.isoformat())
        current = affiliate_proof(self.proof_path, CREDS, EXPIRES)
        self.assertEqual(current['mode'], 'maintenance_continuity')
        self.assertEqual(current['originalProofExpiresAt'], EXPIRES.isoformat())
        self.assertEqual(current['expiresAt'], END.isoformat())
        self.assertTrue(current['verified'])
        self.assertEqual(affiliate_proof(self.proof_path, CREDS, END - timedelta(seconds=1))['mode'],
                         'maintenance_continuity')
        with self.assertRaisesRegex(PipelineError, 'MAINTENANCE_AUTHORIZATION_EXPIRED'):
            affiliate_proof(self.proof_path, CREDS, END)
        self.assertEqual(self.proof_path.read_bytes(), self.original)

    def test_never_auto_extends_or_allows_duplicate_confirmation(self):
        self.authorize()
        marker = maintenance_path(self.proof_path)
        saved = marker.read_bytes()
        with self.assertRaisesRegex(PipelineError, 'MAINTENANCE_ALREADY_AUTHORIZED'):
            self.authorize(EXPIRES + timedelta(days=2))
        self.assertEqual(marker.read_bytes(), saved)
        marker.unlink()
        with self.assertRaisesRegex(PipelineError, 'MAINTENANCE_WINDOW_CLOSED'):
            authorize_maintenance_continuity(self.proof_path, CREDS, END)
        self.assertFalse(marker.exists())

    def test_rotation_tampering_marker_and_missing_marker_fail_closed(self):
        self.authorize()
        rotated = Credentials(CREDS.application_id, CREDS.access_key, 'new-affiliate')
        with self.assertRaisesRegex(PipelineError, 'AFFILIATE_PROOF_INVALID'):
            affiliate_proof(self.proof_path, rotated, EXPIRES)
        marker_path = maintenance_path(self.proof_path)
        marker = load_json(marker_path)
        marker['expiresAt'] = (END + timedelta(days=1)).isoformat()
        write_json(marker_path, marker)
        with self.assertRaisesRegex(PipelineError, 'MAINTENANCE_AUTHORIZATION_EXPIRED'):
            affiliate_proof(self.proof_path, CREDS, EXPIRES)
        marker_path.unlink()
        with self.assertRaisesRegex(PipelineError, 'PROOF_EXPIRED'):
            affiliate_proof(self.proof_path, CREDS, EXPIRES)

    def test_changed_original_proof_invalidates_marker(self):
        self.authorize()
        proof = load_json(self.proof_path)
        proof['verifiedAt'] = (CONFIRMED + timedelta(days=1)).isoformat()
        proof['expiresAt'] = (EXPIRES + timedelta(days=1)).isoformat()
        write_json(self.proof_path, proof)
        with self.assertRaisesRegex(PipelineError, 'MAINTENANCE_AUTHORIZATION_INVALID'):
            affiliate_proof(self.proof_path, CREDS, EXPIRES + timedelta(days=1))

    def test_noninteractive_cli_refuses_maintenance_without_marker(self):
        output = io.StringIO()
        with patch('windows_credentials.read_windows_credentials', return_value=CREDS), \
             patch('sys.argv', ['cmd', '--authorize-maintenance', str(self.proof_path)]), \
             patch('sys.stdin.isatty', return_value=False), redirect_stdout(output):
            self.assertEqual(main(), 1)
        self.assertFalse(maintenance_path(self.proof_path).exists())
        self.assertIn('INTERACTIVE_TERMINAL_REQUIRED', output.getvalue())
        self.assertNotIn(CREDS.affiliate_id, output.getvalue())

    def test_original_proof_is_not_silently_renewed(self):
        self.authorize()
        saved = self.proof_path.read_bytes()
        marker = maintenance_path(self.proof_path)
        self.assertTrue(marker.exists())
        self.assertNotIn(CREDS.affiliate_id, marker.read_text(encoding='utf-8'))
        self.assertEqual(saved, self.original)


class PublicationGateTests(unittest.TestCase):
    def setUp(self):
        fixture = PublicationTests('test_matching_generation_ready')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.fixture = fixture
        self.root = fixture.root

    def set_status(self, **fields):
        path = self.root / 'data' / 'collection-status.json'
        status = load_json(path)
        status.update(fields)
        write_json(path, status)

    def test_cloud_accepts_only_bounded_officially_labeled_continuity(self):
        original_expiry = SUNDAY.replace(hour=6, minute=0)
        kwargs = dict(
            affiliateVerified=True,
            affiliateVerificationMode='maintenance_continuity',
            affiliateOriginalProofExpiresAt=original_expiry.isoformat(),
            affiliateContinuityAuthorizedAt=(original_expiry-timedelta(days=1)).isoformat(),
            affiliateVerificationExpiresAt=(original_expiry+timedelta(days=7)).isoformat()
        )
        self.set_status(**kwargs)
        validate_collection(self.root, SUNDAY)
        self.set_status(affiliateVerificationExpiresAt=(original_expiry+timedelta(days=8)).isoformat())
        with self.assertRaisesRegex(PipelineError, 'MAINTENANCE_WINDOW_INVALID'):
            validate_collection(self.root, SUNDAY)
        self.set_status(**kwargs)
        self.set_status(affiliateContinuityAuthorizedAt=(original_expiry+timedelta(hours=2)).isoformat())
        with self.assertRaisesRegex(PipelineError, 'MAINTENANCE_WINDOW_INVALID'):
            validate_collection(self.root, SUNDAY)
        self.set_status(**kwargs)
        self.set_status(affiliateVerificationMode='unknown')
        with self.assertRaisesRegex(PipelineError, 'MAINTENANCE_MODE_INVALID'):
            validate_collection(self.root, SUNDAY)

if __name__ == '__main__':
    unittest.main()
