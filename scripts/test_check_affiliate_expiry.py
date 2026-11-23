import unittest
from datetime import datetime, timedelta, timezone

from check_affiliate_expiry import evaluate

UTC=timezone.utc
NOW=datetime(2026,11,23,2,0,tzinfo=UTC)


def doc(offset_hours,verified=True):
    return {
        "affiliateVerificationExpiresAt":(NOW+timedelta(hours=offset_hours)).isoformat(),
        "affiliateVerified":verified,
    }


class AffiliateContinuityObservationTests(unittest.TestCase):
    def test_no_alert_far_from_expiry(self):
        self.assertEqual(evaluate(doc(100),now=NOW)["state"],"OK")
        self.assertFalse(evaluate(doc(100),now=NOW)["alert"])

    def test_three_day_warning_boundary(self):
        self.assertEqual(evaluate(doc(72),now=NOW)["state"],"EXPIRING")
        self.assertEqual(evaluate(doc(73),now=NOW)["state"],"OK")

    def test_expiration_and_unverified_both_alert(self):
        for status in [evaluate(doc(0),now=NOW),
                       evaluate(doc(-24),now=NOW),
                       evaluate(doc(100,False),now=NOW)]:
            self.assertTrue(status["alert"])
        self.assertEqual(evaluate(doc(-24),now=NOW)["state"],"EXPIRED")

    def test_bad_or_absent_proof_is_not_claimed_valid(self):
        for bad in [{}, {"affiliateVerified":True},
                    {"affiliateVerificationExpiresAt":"bad","affiliateVerified":True},
                    {"affiliateVerificationExpiresAt":"2026-11-27","affiliateVerified":True}]:
            self.assertEqual(evaluate(bad,now=NOW)["state"],"UNVERIFIABLE")


if __name__=="__main__":
    unittest.main()
