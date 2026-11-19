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

    def test_reference_to_another_wireless_product_does_not_make_ac_brick_qi2(self):
        ac_only = row(
            "wireless-charger",
            "30W急速充電器 AC充電器 USB-Cx1＋USB-Ax1"
            "《当社Qi2 15W ワイヤレス充電器に最適！》"
        )
        qi2_pad = row(
            "wireless-charger",
            "Qi2 マグネット ワイヤレス充電器 15W (ACアダプター別売)"
        )
        self.assertFalse(displayable(ac_only))
        self.assertFalse(recommendable(ac_only, NOW))
        self.assertTrue(displayable(qi2_pad))

    def test_router_protective_case_not_treated_as_router_but_bundle_remains(self):
        only_case = row(
            "mobile-router",
            "LOE Macaroon SE 専用 モバイルルーター ケース 保護フィルム付き"
        )
        legitimate = row(
            "mobile-router",
            "SIMフリー モバイルルーター 本体 ケース付き 付属品セット"
        )
        self.assertFalse(displayable(only_case))
        self.assertTrue(displayable(legitimate))
        # The existing collector already excludes some case bundles.  This
        # display-only change does not attempt to widen source collection.

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
