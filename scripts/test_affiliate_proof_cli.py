"""No-network / no-secret test of Affiliate ID proof expiry reporting."""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from windows_credentials import Credentials, record_affiliate_proof, main


class AffiliateProofCLITests(unittest.TestCase):
    def test_recheck_reports_valid_expiry_but_never_renews(self):
        credentials = Credentials("fixture-app", "fixture-access", "fixture-affiliate")
        with tempfile.TemporaryDirectory() as temp:
            proof = Path(temp) / "affiliate-proof.json"
            now = datetime.now(timezone.utc)
            record_affiliate_proof(proof, credentials, credentials.affiliate_id, now)
            before = proof.read_bytes()
            output = io.StringIO()
            with patch("windows_credentials.read_windows_credentials", return_value=credentials), \
                 patch("sys.argv", ["credential", "--check-affiliate-proof", str(proof)]), \
                 redirect_stdout(output):
                self.assertEqual(main(), 0)
            self.assertEqual(before, proof.read_bytes())
            self.assertIn('"affiliateProof": "valid"', output.getvalue())
            self.assertNotIn(credentials.affiliate_id, output.getvalue())

    def test_expired_proof_is_failure_without_auto_extension(self):
        credentials = Credentials("fixture-app", "fixture-access", "fixture-affiliate")
        with tempfile.TemporaryDirectory() as temp:
            proof = Path(temp) / "affiliate-proof.json"
            past = datetime.now(timezone.utc)-timedelta(days=31)
            record_affiliate_proof(proof, credentials, credentials.affiliate_id, past)
            before = proof.read_bytes()
            output = io.StringIO()
            with patch("windows_credentials.read_windows_credentials", return_value=credentials), \
                 patch("sys.argv", ["credential", "--check-affiliate-proof", str(proof)]), \
                 redirect_stdout(output):
                self.assertEqual(main(), 1)
            self.assertEqual(before, proof.read_bytes())
            self.assertIn("AFFILIATE_PROOF_EXPIRED", output.getvalue())
            self.assertNotIn(credentials.affiliate_id, output.getvalue())

if __name__ == "__main__":
    unittest.main()
