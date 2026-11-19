"""Offline display-quality safety tests: never call Rakuten or note."""
from datetime import datetime, timezone, timedelta
import unittest
from affiliate_quality import (displayable, listing_period, merchant_promotion_claim,
                               recommendable, variant_price_uncertain)

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 19, 6, 50, tzinfo=JST)


def row(category, title, **extra):
    return {"categoryId": category, "name": title, **extra}


class AffiliateQualityTests(unittest.TestCase):
    def test_mixed_capacity_price_is_not_recommended(self):
        multiple = row("microsd", "8GB 16GB 32GB 64GB 128GB 容量選択 microSDカード")
        self.assertTrue(variant_price_uncertain(multiple))
        self.assertFalse(recommendable(multiple, NOW))
        self.assertTrue(recommendable(row("microsd", "128GB microSDXCカード"), NOW))
        self.assertTrue(variant_price_uncertain(row("power-bank", "5000mAh / 10000mAh バッテリー")))

    def test_multi_wattage_does_not_rank_as_65w_price(self):
        self.assertFalse(recommendable(row("usb-charger", "20W 30W 45W 65W 100W 選択可"), NOW))
        self.assertTrue(recommendable(row("usb-charger", "USB-C PD65W 充電器"), NOW))

    def test_wrong_category_not_promoted_even_with_old_data(self):
        target = row("mobile-router", "【国内専用・追加チャージ専用】ポケットWiFi")
        self.assertFalse(displayable(target))
        self.assertFalse(recommendable(target, NOW))
        self.assertTrue(displayable(row("mobile-router", "SIMフリーモバイルルーター"),))

    def test_api_listing_time_is_not_assumed_to_be_discount_duration(self):
        future = row("usb-charger", "USB-C PD65W", saleStart="2026-11-19 20:00")
        old = row("usb-charger", "USB-C PD65W", saleEnd="2026-11-18 23:59")
        self.assertEqual(listing_period(future, NOW), "upcoming")
        self.assertEqual(listing_period(old, NOW), "ended")
        self.assertFalse(recommendable(future, NOW))
        self.assertFalse(recommendable(old, NOW))
        self.assertTrue(recommendable(row("usb-charger", "USB-C PD65W"), NOW))
        self.assertEqual(listing_period(row("usb-charger", "USB-C PD65W", saleStart="bad"), NOW), "unverified")

    def test_time_bounds_and_title_claims(self):
        x = row("usb-charger", "【11/18まで35%OFFクーポン】USB-C 65W")
        self.assertTrue(merchant_promotion_claim(x))
        self.assertTrue(recommendable(x, NOW))  # No unverifiable coupon assertion.
        self.assertEqual(listing_period(x, NOW), "active_or_unknown")
        with self.assertRaisesRegex(ValueError, "NAIVE_CLOCK"):
            listing_period(x, datetime(2026, 11, 19))
