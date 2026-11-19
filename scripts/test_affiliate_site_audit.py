"""Offline regression tests for publication-time outbound link verification."""
import tempfile
import unittest
from html import escape
from pathlib import Path

from affiliate_site_audit import audit_affiliate_links
from pipeline_common import PipelineError
import build_site

URL = ("https://hb.afl.rakuten.co.jp/hgc/public-test/"
       "?pc=https%3A%2F%2Fitem.rakuten.co.jp%2Fshop%2Fa%2F&scid=af_link")


def sample():
    return {"categories": [{"id": "usb-charger", "items": [{
        "categoryId": "usb-charger", "code": "shop:sku",
        "url": URL, "name": "USB-C 65W充電器"
    }]}]}


def link(*, href=URL, code="shop:sku", category="usb-charger", place="category-card:title",
         rel="sponsored nofollow noopener", decorated=True):
    fields = (f' data-affiliate-code="{code}" data-affiliate-category="{category}"'
              f' data-affiliate-placement="{place}"') if decorated else ""
    return (f'<a href="{escape(href, quote=True)}" rel="{rel}" target="_blank"'
            f'{fields}>楽天市場で確認</a>')


class AffiliateSiteAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.page = Path(self.tmp.name) / "category-usb-charger.html"

    def check(self, markup):
        self.page.write_text('<html><body>' + markup + '</body></html>', encoding="utf-8")
        return audit_affiliate_links(sample(), [self.page])

    def test_exact_url_survives_html_escaping_and_three_link_placements(self):
        result = self.check(link() + link(place="category-card:image") +
                            link(place="category-card:button"))
        self.assertEqual(result, {"pagesChecked": 1, "linksChecked": 3})

    def test_wrong_product_code_does_not_route_to_different_affiliate_product(self):
        with self.assertRaisesRegex(PipelineError, "AFFILIATE_RENDERED_LINK_INVALID"):
            self.check(link(code="other:sku"))

    def test_wrong_category_and_modified_url_not_accepted(self):
        for fragment in [link(category="mobile-router"),
                         link(href=URL + "&extra=unapproved"),
                         link(href="https://evil.example/?to=hb.afl.rakuten.co.jp"),
                         link(href="http://hb.afl.rakuten.co.jp/hgc/fake/")]:
            with self.subTest(fragment=fragment[:25]):
                with self.assertRaisesRegex(PipelineError, "AFFILIATE_RENDERED_LINK_INVALID"):
                    self.check(fragment)

    def test_unmarked_ad_link_is_rejected_instead_of_losing_affiliate_tracking(self):
        with self.assertRaisesRegex(PipelineError, "AFFILIATE_RENDERED_LINK_INVALID"):
            self.check(link(decorated=False))

    def test_missing_sponsor_noopener_or_placement_is_rejected(self):
        for fragment in [link(rel="nofollow noopener"),
                         link(rel="sponsored nofollow"),
                         link(place=""),
                         link(place="bad placement")]:
            with self.subTest(fragment=fragment[:35]):
                with self.assertRaisesRegex(PipelineError, "AFFILIATE_RENDERED_LINK_INVALID"):
                    self.check(fragment)

    def test_missing_site_page_fails_and_never_calls_outbound_network(self):
        with self.assertRaisesRegex(PipelineError, "AFFILIATE_RENDERED_PAGE_MISSING"):
            audit_affiliate_links(sample(), [self.page])

    def test_static_page_without_affiliate_is_valid(self):
        self.assertEqual(self.check("<p>比較方法の説明です</p>")["linksChecked"], 0)

    def test_self_canonical_matches_active_page_url_and_sitemap(self):
        html = build_site.layout(
            {"site_name": "試験サイト", "site_url": "https://example.invalid/shop"},
            "65W USB-C充電器比較", "<h1>比較</h1>", "お買い得な充電器",
            canonical_file="comparison-usb-charger-65w.html"
        )
        canonical = "https://example.invalid/shop/comparison-usb-charger-65w.html"
        self.assertIn(f'<link rel="canonical" href="{canonical}">', html)
        self.assertIn(f'<meta property="og:url" content="{canonical}">', html)
        self.assertIn('<meta property="og:type" content="website">', html)
        self.assertNotIn("noindex", html)
        with self.assertRaisesRegex(ValueError, "CANONICAL_FILENAME_INVALID"):
            build_site.layout(
                {"site_name": "試験", "site_url": "https://example.invalid"},
                "危険", "", canonical_file="../../trick.html"
            )


if __name__ == "__main__":
    unittest.main()
