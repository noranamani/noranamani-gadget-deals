"""Read-only integration test for affiliate site HTML; no API or publishing."""
from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import build_site

JST = timezone(timedelta(hours=9))
URL = "https://hb.afl.rakuten.co.jp/hgc/test-public/?pc=https%3A%2F%2Fitem.rakuten.co.jp%2Ftest%2Fexample%2F"


def item(category, code, title, price=1000, **overrides):
    default = {"categoryId": category, "category": category, "code": code, "name": title,
               "price": price, "image": "", "url": URL, "reviewAverage": 4.5,
               "reviewCount": 110, "postageIncluded": True, "pointRate": 1,
               "shopName": "検証店舗", "historySamples": 10, "baselinePrice": 1600,
               "dropPct": 37.5, "isDeal": True, "dealLevel": "strong",
               "score": 75.0, "saleStart": "", "saleEnd": ""}
    return {**default, **overrides}


class BuildAnalyticsTests(unittest.TestCase):
    def test_site_filters_wrong_categories_and_marks_variant_prices(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            cfg, data, docs = root / "config", root / "data", root / "docs"
            cfg.mkdir(); data.mkdir()
            future = (datetime.now(JST) + timedelta(days=2)).strftime("%Y-%m-%d %H:%M")
            valid = item("usb-charger", "c:valid", "65W USB-C充電器")
            option = item("usb-charger", "c:variants", "20W 30W 65W USB-C充電器")
            pending = item("usb-charger", "c:pending", "65W USB-C充電器 特価候補", saleStart=future)
            bad = item("mobile-router", "c:wrong", "追加チャージ専用モバイルルーター")
            right = item("mobile-router", "c:right", "SIMフリーモバイルルーター", isDeal=False)
            goods = [valid, option, pending, bad, right]
            categories = [{"id": cat, "name": cat, "keyword": cat,
                           "items": [x for x in goods if x["categoryId"] == cat]}
                          for cat in ("usb-charger", "mobile-router")]
            (cfg / "site.json").write_text(json.dumps({"site_name": "Test", "site_url": "https://example.invalid"}))
            (cfg / "analytics.json").write_text('{"ga4_measurement_id": ""}')
            (cfg / "queries.json").write_text(json.dumps([{"id": x["id"], "name": x["name"]} for x in categories]))
            (cfg / "comparisons.json").write_text('{"pages": []}')
            (data / "products.json").write_text(json.dumps({"updatedAt": datetime.now(timezone.utc).isoformat(),
                "categories": categories, "deals": goods, "stats": {"products": 5,"tracked":5,"deals":5},
                "methodology": {"dealMinSamples":5}}))
            (data / "editorial.json").write_text(json.dumps({
                "featuredDeals": [{"code": x["code"], "reason":"セール候補"} for x in goods],
                "categoryNotes": [{"categoryId": x["id"], "topPickCode": "c:variants",
                                   "summary": "比較", "watchouts": []} for x in categories]}))
            for label, value in (("CONFIG", cfg), ("DATA", data), ("DOCS", docs), ("ASSETS", docs / "assets")):
                stack.enter_context(patch.object(build_site, label, value))
            build_site.main()
            usb = (docs / "category-usb-charger.html").read_text()
            index = (docs / "index.html").read_text()
            router = (docs / "category-mobile-router.html").read_text()
            deal = (docs / "deals.html").read_text()
            feed = json.loads((docs / "feed.json").read_text())
            self.assertIn("仕様・表示価格要確認", usb)
            self.assertIn("掲載開始前", usb)
            self.assertIn('data-affiliate-code="c:valid"', usb)
            self.assertIn('data-affiliate-placement="category-card:button"', usb)
            self.assertIn('data-affiliate-placement="table"', usb)
            self.assertNotIn("c:wrong", router)
            self.assertIn("c:right", router)
            self.assertIn('item_code: link.dataset.affiliateCode', index)
            self.assertNotIn("googletagmanager.com/gtag", index)
            self.assertIn("c:valid", deal)
            self.assertNotIn("c:variants", deal)
            self.assertNotIn("c:pending", deal)
            self.assertNotIn("c:wrong", deal)
            self.assertEqual([x["code"] for x in feed["deals"]], ["c:valid"])
            (cfg / "analytics.json").write_text('{"ga4_measurement_id": "G-ABC12345"}')
            build_site.main()
            self.assertIn("googletagmanager.com/gtag/js?id=G-ABC12345", (docs / "index.html").read_text())
            (cfg / "analytics.json").write_text('{"ga4_measurement_id": "not-a-real-id"}')
            with self.assertRaisesRegex(ValueError, "GA4_MEASUREMENT_ID_INVALID"):
                build_site.main()


if __name__ == "__main__":
    unittest.main()
