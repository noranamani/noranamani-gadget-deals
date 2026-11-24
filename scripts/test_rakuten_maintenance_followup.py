"""Offline tests: distinct Nov 24 continuity authorization; no provider calls."""
from datetime import timedelta, datetime
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
import io

from windows_credentials import (
    Credentials, record_affiliate_proof, affiliate_proof,
    authorize_maintenance_continuity, authorize_maintenance_followup,
    maintenance_path, _followup_path, FOLLOWUP_MODE, FOLLOWUP_START,
    FOLLOWUP_END, FOLLOWUP_ORIGINAL_EXPIRY, MAINTENANCE_ABSOLUTE_END, main,
)
from pipeline_common import PipelineError, load_json, write_json, digest, JST
from publication_pipeline import validate_collection
from test_windows_pipeline import PublicationTests as Fixture

CREDS = Credentials('test-app', 'test-secret', 'test-affiliate')
EXPIRES = FOLLOWUP_ORIGINAL_EXPIRY
APPROVED = FOLLOWUP_START + timedelta(hours=8)


class FollowupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.proof = Path(temp.name) / 'affiliate-proof.json'
        record_affiliate_proof(self.proof, CREDS, CREDS.affiliate_id, EXPIRES-timedelta(days=30))
        self.prior = maintenance_path(self.proof)
        authorize_maintenance_continuity(self.proof, CREDS, EXPIRES-timedelta(days=1))
        self.proof_bytes = self.proof.read_bytes()
        self.prior_bytes = self.prior.read_bytes()

    def test_without_new_authorization_old_cutoff_unchanged(self):
        with self.assertRaises(PipelineError):
            affiliate_proof(self.proof, CREDS, MAINTENANCE_ABSOLUTE_END)

    def test_separate_explicit_marker_and_fixed_end(self):
        authorize_maintenance_followup(self.proof, CREDS, APPROVED)
        self.assertEqual(self.proof.read_bytes(), self.proof_bytes)
        self.assertEqual(self.prior.read_bytes(), self.prior_bytes)
        receipt = affiliate_proof(self.proof, CREDS, APPROVED)
        self.assertEqual(receipt['mode'], FOLLOWUP_MODE)
        self.assertEqual(receipt['expiresAt'], FOLLOWUP_END.isoformat())
        self.assertEqual(affiliate_proof(self.proof, CREDS, FOLLOWUP_END-timedelta(seconds=1))['mode'], FOLLOWUP_MODE)
        with self.assertRaises(PipelineError):
            affiliate_proof(self.proof, CREDS, FOLLOWUP_END)
        with self.assertRaises(PipelineError):
            authorize_maintenance_followup(self.proof, CREDS, APPROVED+timedelta(hours=1))

    def test_authorization_must_precede_old_cutoff(self):
        for when in (FOLLOWUP_START-timedelta(seconds=1), MAINTENANCE_ABSOLUTE_END):
            with self.subTest(when=when), self.assertRaises(PipelineError):
                authorize_maintenance_followup(self.proof, CREDS, when)
        self.assertFalse(_followup_path(self.proof).exists())

    def test_tampered_prior_marker_and_new_cutoff_rejected(self):
        authorize_maintenance_followup(self.proof, CREDS, APPROVED)
        path = _followup_path(self.proof)
        original = load_json(path)
        for key, value in [('expiresAt', (FOLLOWUP_END+timedelta(days=1)).isoformat()),
                           ('authorizedAt', (APPROVED+timedelta(days=5)).isoformat()),
                           ('priorMarkerSha256', '0'*64)]:
            with self.subTest(key=key):
                write_json(path, {**original, key:value})
                with self.assertRaises(PipelineError):
                    affiliate_proof(self.proof, CREDS, APPROVED)
        write_json(path, original)
        self.prior.unlink()
        with self.assertRaises(PipelineError):
            affiliate_proof(self.proof, CREDS, APPROVED)

    def test_new_official_verification_takes_precedence(self):
        authorize_maintenance_followup(self.proof, CREDS, APPROVED)
        record_affiliate_proof(self.proof, CREDS, CREDS.affiliate_id, APPROVED)
        self.assertEqual(affiliate_proof(self.proof, CREDS, APPROVED)['mode'], 'official')

    def test_different_credentials_and_noninteractive_cli_refused(self):
        output = io.StringIO()
        with patch('windows_credentials.read_windows_credentials', return_value=CREDS), \
             patch('sys.argv', ['cmd', '--authorize-maintenance-20261124', str(self.proof)]), \
             patch('sys.stdin.isatty', return_value=False), redirect_stdout(output):
            self.assertEqual(main(), 1)
        self.assertFalse(_followup_path(self.proof).exists())
        authorize_maintenance_followup(self.proof, CREDS, APPROVED)
        with self.assertRaises(PipelineError):
            affiliate_proof(self.proof, Credentials('different', 'secret', 'different'), APPROVED)


class FollowupPublicationTests(unittest.TestCase):
    def test_cloud_fixed_outage_window_and_explicit_prior_receipt(self):
        fixture = Fixture('test_matching_generation_ready')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        root = fixture.root
        now = datetime(2026, 11, 28, 6, 50, tzinfo=JST)
        products_path = root/'data/products.json'
        products = load_json(products_path)
        products['updatedAt'] = now.replace(minute=10).isoformat()
        write_json(products_path, products)
        status_path = root/'data/collection-status.json'
        status = load_json(status_path)
        status.update(productsSha256=digest(products_path), collectedAt=products['updatedAt'],
                      affiliateVerificationMode=FOLLOWUP_MODE,
                      affiliateOriginalProofExpiresAt=EXPIRES.isoformat(),
                      affiliatePriorContinuityAuthorizedAt=(EXPIRES-timedelta(days=1)).isoformat(),
                      affiliateContinuityAuthorizedAt=APPROVED.isoformat(),
                      affiliateVerificationExpiresAt=FOLLOWUP_END.isoformat())
        write_json(status_path, status)
        validate_collection(root, now)
        for key, value in [('affiliateVerificationExpiresAt',(FOLLOWUP_END+timedelta(days=1)).isoformat()),
                           ('affiliateOriginalProofExpiresAt',(EXPIRES+timedelta(days=1)).isoformat()),
                           ('affiliatePriorContinuityAuthorizedAt',(APPROVED+timedelta(days=1)).isoformat()),
                           ('affiliateContinuityAuthorizedAt',MAINTENANCE_ABSOLUTE_END.isoformat())]:
            with self.subTest(key=key):
                write_json(status_path, {**status, key:value})
                with self.assertRaises(PipelineError):
                    validate_collection(root, now)


if __name__ == '__main__':
    unittest.main()
