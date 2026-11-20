"""Offline safety/structure checks for one-time static Pages recovery."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from recover_static_tool_pages import (
    SITE_URL, cards_from_archive, safe_index_patch, safe_sitemap_patch,
    selector_recovery_page
)


class RecoverySafetyTests(unittest.TestCase):
    def test_add_links_to_existing_home_without_rewriting_existing_cards(self):
        old = '<html><aside><a href="deals.html">特価候補</a></aside><main class="main-content"><article>OldPrice123</article></main></html>'
        new = safe_index_patch(old)
        self.assertIn('<article>OldPrice123</article>', new)
        self.assertIn('href="sim-optimizer.html"', new)
        self.assertIn('href="product-selector.html"', new)
        self.assertIn('href="deals.html"', new)
        self.assertIn("最終取得は2026/11/19 06:10", new)
        self.assertEqual(new.count('OldPrice123'), 1)

    def test_already_patched_home_stops_instead_of_duplicate_deployment(self):
        html = '<aside><a href="sim-optimizer.html">以前のツール</a></aside>'
        with self.assertRaisesRegex(ValueError, "INDEX_STRUCTURE_CHANGED"):
            safe_index_patch(html)

    def test_sitemap_additions_parse_and_old_url_remains(self):
        old = ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               '<url><loc>' + SITE_URL + '/index.html</loc></url></urlset>')
        new = safe_sitemap_patch(old)
        ET.fromstring(new)
        self.assertIn(SITE_URL + '/index.html', new)
        self.assertIn(SITE_URL + '/sim-optimizer.html', new)
        self.assertIn(SITE_URL + '/product-selector.html', new)
        with self.assertRaisesRegex(ValueError, "SITEMAP_ALREADY_CONTAINS_TOOL"):
            safe_sitemap_patch(new)

    def test_archived_cards_become_internal_links_without_old_affiliate_links(self):
        with tempfile.TemporaryDirectory() as t:
            docs = Path(t)
            (docs/'category-usb-charger.html').write_text(
                '<article class="product-card" data-price="1234" data-reviews="20" '
                'data-score="75.4" data-drop="-12.0" data-variant="0">'
                '<a href="https://hb.afl.rakuten.co.jp/hgc/private-example">Shop</a>'
                '<h3>充電器 &amp; USB-C</h3></article>',
                encoding="utf-8")
            q = [{"id": "usb-charger", "name": "充電器"}]
            cards, count = cards_from_archive(docs, q)
            self.assertEqual(count, 1)
            self.assertIn('充電器 &amp; USB-C', cards)
            self.assertIn('href="category-usb-charger.html"', cards)
            self.assertNotIn("hb.afl.rakuten.co.jp", cards)
            self.assertNotIn("private-example", cards)
            page = selector_recovery_page(docs, q)
            for marker in ("selector-grid", "selector-category", "selector-reset",
                           "selector-price", "function choose("):
                self.assertIn(marker, page)
            self.assertIn("2026年11月19日", page)

    def test_variant_price_not_copied_into_comparison(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "category-mobile-router.html"
            p.write_text('<article class="product-card" data-price="500" data-reviews="5" '
                         'data-score="11" data-drop="1" data-variant="1"><h3>仕様選択品</h3></article>',
                         encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ARCHIVE_NO_COMPARABLE_CARDS"):
                cards_from_archive(Path(t), [{"id": "mobile-router", "name": "ルーター"}])

    def test_untrusted_category_name_cannot_make_a_path(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(ValueError, "CATEGORY_ID_INVALID"):
                cards_from_archive(Path(t), [{"id": "../secrets", "name": "X"}])


if __name__ == '__main__':
    unittest.main()
