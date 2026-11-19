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
import validate
from affiliate_site_audit import audit_affiliate_links

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
            router_case = item("mobile-router", "c:case", "Macaroon SE 専用 モバイルルーター ケース", isDeal=False)
            ac_brick = item("wireless-charger", "c:ac", "30W AC充電器《当社Qi2 ワイヤレス充電器に最適》", isDeal=False)
            qi2_pad = item("wireless-charger", "c:pad", "Qi2 15W ワイヤレス充電器", isDeal=False)
            goods = [valid, option, pending, bad, right, router_case, ac_brick, qi2_pad]
            categories = [{"id": cat, "name": cat, "keyword": cat,
                           "items": [x for x in goods if x["categoryId"] == cat]}
                          for cat in ("usb-charger", "mobile-router", "wireless-charger")]
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
            sim = (docs / "sim-optimizer.html").read_text()
            selector = (docs / "product-selector.html").read_text()
            self.assertIn('href="product-selector.html"', index)
            self.assertIn('id="selector-category"', selector)
            self.assertIn('id="selector-grid"', selector)
            self.assertIn('<link rel="canonical" href="https://example.invalid/product-selector.html">', selector)
            self.assertIn('href="sim-optimizer.html"', index)
            self.assertIn('id="sim-tool"', sim)
            self.assertIn('id="sim-best"', sim)
            self.assertIn('href="category-mobile-router.html"', sim)
            self.assertIn('<link rel="canonical" href="https://example.invalid/sim-optimizer.html">', sim)
            router = (docs / "category-mobile-router.html").read_text()
            wireless = (docs / "category-wireless-charger.html").read_text()
            deal = (docs / "deals.html").read_text()
            feed = json.loads((docs / "feed.json").read_text())
            self.assertIn("仕様・表示価格要確認", usb)
            self.assertIn("掲載開始前", usb)
            self.assertIn('data-affiliate-code="c:valid"', usb)
            self.assertIn('data-affiliate-placement="category-card:button"', usb)
            self.assertIn('data-affiliate-placement="category-card:title"', usb)
            self.assertIn('aria-label="65W USB-C充電器：楽天市場の商品ページを新しいタブで開く"', usb)
            self.assertIn('<h3><a class="product-title-link"', usb)
            self.assertIn('data-affiliate-placement="table"', usb)
            self.assertNotIn("c:wrong", router)
            self.assertNotIn("c:case", router)
            self.assertIn("c:right", router)
            self.assertNotIn("c:ac", wireless)
            self.assertIn("c:pad", wireless)
            self.assertIn('item_code: link.dataset.affiliateCode', index)
            self.assertNotIn("googletagmanager.com/gtag", index)
            self.assertIn("c:valid", deal)
            self.assertIn('<link rel="canonical" href="https://example.invalid/index.html">', index)
            self.assertIn('<link rel="canonical" href="https://example.invalid/category-usb-charger.html">', usb)
            self.assertIn('<meta property="og:title"', usb)
            self.assertEqual(
                audit_affiliate_links(
                    json.loads((data / "products.json").read_text()),
                    [docs / "index.html", docs / "category-usb-charger.html"]
                )["pagesChecked"], 2
            )
            self.assertNotIn("c:variants", deal)
            self.assertNotIn("c:pending", deal)
            self.assertNotIn("c:wrong", deal)
            self.assertEqual([x["code"] for x in feed["deals"]], ["c:valid"])
            # The safe product gate leaves just one comparable SKU; the
            # validator must not demand a page which the builder withheld.
            compare_spec = {
                "rolloutStartDate": "2026-10-01",
                "pages": [{
                    "id": "usb-charger-65w", "name": "65W USB-C充電器",
                    "parentCategory": "usb-charger",
                    "includeAnyGroups": [["65w"]],
                    "minProducts": 3, "minComparableProducts": 2
                }]
            }
            (cfg / "comparisons.json").write_text(json.dumps(compare_spec))
            build_site.main()
            self.assertFalse((docs / "comparison-usb-charger-65w.html").exists())
            with (patch.object(validate, "CONFIG", cfg),
                  patch.object(validate, "DATA", data),
                  patch.object(validate, "DOCS", docs)):
                validate.main()
            compare_spec["pages"][0]["minComparableProducts"] = 1
            (cfg / "comparisons.json").write_text(json.dumps(compare_spec))
            build_site.main()
            detail = (docs / "comparison-usb-charger-65w.html").read_text()
            self.assertIn(
                '<link rel="canonical" href="https://example.invalid/comparison-usb-charger-65w.html">',
                detail
            )
            with (patch.object(validate, "CONFIG", cfg),
                  patch.object(validate, "DATA", data),
                  patch.object(validate, "DOCS", docs)):
                validate.main()
            # Prove the actual publication validator (not just the helper)
            # stops before Pages when an affiliate SKU is swapped in HTML.
            rendered_page = docs / "category-usb-charger.html"
            pristine_html = rendered_page.read_text()
            self.assertIn('data-affiliate-code="c:valid"', pristine_html)
            rendered_page.write_text(pristine_html.replace(
                'data-affiliate-code="c:valid"',
                'data-affiliate-code="c:other"', 1
            ))
            with (patch.object(validate, "CONFIG", cfg),
                  patch.object(validate, "DATA", data),
                  patch.object(validate, "DOCS", docs)):
                with self.assertRaisesRegex(SystemExit, "AFFILIATE_LINK_AUDIT_FAILED"):
                    validate.main()
            rendered_page.write_text(pristine_html)
            (cfg / "analytics.json").write_text('{"ga4_measurement_id": "G-ABC12345"}')
            build_site.main()
            self.assertIn("googletagmanager.com/gtag/js?id=G-ABC12345", (docs / "index.html").read_text())
            self.assertIn('gtag("config","G-ABC12345")', (docs / "sim-optimizer.html").read_text())
            self.assertIn('gtag("config","G-ABC12345")', (docs / "product-selector.html").read_text())
            with (patch.object(validate, "CONFIG", cfg),
                  patch.object(validate, "DATA", data),
                  patch.object(validate, "DOCS", docs)):
                validate.main()
                # A missing tag in only one category page must prevent the
                # next daily Pages artifact, rather than silently disabling GA4.
                tagged = docs / "category-usb-charger.html"
                original_tagged = tagged.read_text(encoding="utf-8")
                self.assertIn("gtag/js?id=G-ABC12345", original_tagged)
                tagged.write_text(original_tagged.replace(
                    "gtag/js?id=G-ABC12345", "gtag/js?id=G-REDACTED", 1),
                    encoding="utf-8")
                with self.assertRaisesRegex(SystemExit, "GA4_TAG_MISSING_OR_DUPLICATE"):
                    validate.main()
                tagged.write_text(original_tagged, encoding="utf-8")
                # The privacy disclosure is also a prerequisite for GA4 publishing.
                privacy = docs / "privacy.html"
                original_privacy = privacy.read_text(encoding="utf-8")
                privacy.write_text(original_privacy.replace(
                    "当サイトはGoogle Analytics 4（GA4）を利用", "別のサイト", 1),
                    encoding="utf-8")
                with self.assertRaisesRegex(SystemExit, "GA4_PRIVACY_DISCLOSURE_MISSING"):
                    validate.main()
                privacy.write_text(original_privacy, encoding="utf-8")
                validate.main()
            (cfg / "analytics.json").write_text('{"ga4_measurement_id": "not-a-real-id"}')
            with self.assertRaisesRegex(ValueError, "GA4_MEASUREMENT_ID_INVALID"):
                build_site.main()


if __name__ == "__main__":
    unittest.main()
