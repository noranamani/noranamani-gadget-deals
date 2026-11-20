"""Only offers in the published category snapshot may become deal links."""
import unittest
from datetime import datetime, timezone

from build_site import current_safe_deals
from test_affiliate_build import item


class CurrentDealSelectionTests(unittest.TestCase):
    def test_orphaned_deals_cannot_become_public_outbound_links(self):
        listed = item("usb-charger", "shop:list", "65W USB-C充電器")
        orphan = item("usb-charger", "shop:orphan", "65W USB-C充電器")
        products = {
            "categories": [{"id": "usb-charger", "items": [listed]}],
            "deals": [orphan, listed],
        }
        self.assertEqual(current_safe_deals(products, datetime.now(timezone.utc)), [listed])

    def test_deal_snapshot_cannot_change_live_category_price_or_target(self):
        listed = item("usb-charger", "shop:list", "65W USB-C充電器", price=1000)
        stale_price = dict(listed, price=1)
        altered_url = dict(listed, url="https://hb.afl.rakuten.co.jp/hgc/other-destination/")
        products = {
            "categories": [{"id": "usb-charger", "items": [listed]}],
            "deals": [altered_url, stale_price, listed],
        }
        result = current_safe_deals(products, datetime.now(timezone.utc))
        self.assertEqual(len(result), 1)
        self.assertIs(result[0], listed)
        self.assertEqual(result[0]["price"], 1000)

    def test_duplicate_entries_and_unverified_offers_are_excluded(self):
        listed = item("usb-charger", "shop:list", "65W USB-C充電器")
        variant = item("usb-charger", "shop:variant", "20W / 65W USB-C充電器")
        not_deal = item("usb-charger", "shop:not", "65W USB-C充電器", isDeal=False)
        products = {
            "categories": [{"id": "usb-charger", "items": [listed, variant, not_deal]}],
            "deals": [listed, listed, variant, not_deal],
        }
        self.assertEqual(current_safe_deals(products, datetime.now(timezone.utc)), [listed])

    def test_no_categories_means_no_tracked_affiliate_products(self):
        rogue = item("usb-charger", "shop:orphan", "65W USB-C充電器")
        self.assertEqual(current_safe_deals(
            {"categories": [], "deals": [rogue]}, datetime.now(timezone.utc)), [])


if __name__ == "__main__":
    unittest.main()
