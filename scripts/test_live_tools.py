"""Offline regressions for the real HTTP checker (no live network in tests)."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import check_live_tools as live

ID = "G-1273VEC9JB"


def page(name):
    tool = {
        "index.html": '<a href="sim-optimizer.html">通信費</a><a href="product-selector.html">ガジェット選定</a>',
        "sim-optimizer.html": '<section id="sim-tool"><div id="sim-count" id="sim-best"></div>'
                              '<div id="sim-best"></div><div id="sim-a-annual"></div>'
                              '<div id="sim-b-annual"></div></section>'
                              '<script>function calculate(value){return value;}</script>',
        "product-selector.html": '<div id="selector-grid"></div><div id="selector-category"></div>'
                                 '<div id="selector-price"></div><div id="selector-reset"></div>'
                                 '<script>function choose(value){return value;}</script>'
    }[name]
    return ('<!doctype html><html lang="ja"><head>'
            '<link rel="canonical" href="' + live.SITE_URL + '/' + name + '">'
            '<script src="https://www.googletagmanager.com/gtag/js?id=' + ID + '"></script>'
            '<script>gtag("config","' + ID + '");'
            'window.gtag("event", "affiliate_click", {});</script>'
            '</head><body><p>楽天アフィリエイトの掲載があります。'
            '公開済みページの検証テキストを300文字以上にするための検証説明。'
            + ("テストサイト" * 36) + '</p>' + tool + '</body></html>')


def snapshots():
    result = {name: live.Response(200, "text/html", page(name))
              for name in live.FILES if name != "sitemap.xml"}
    result["sitemap.xml"] = live.Response(200, "application/xml",
        '<?xml version="1.0" ?><urlset><loc>' + live.SITE_URL +
        '/sim-optimizer.html</loc><loc>' + live.SITE_URL +
        '/product-selector.html</loc></urlset>' + " " * 300)
    return result


class LiveReadinessTests(unittest.TestCase):
    def test_all_expected_public_pages_are_required(self):
        rows = snapshots()
        result = live.inspect_site(rows.__getitem__, ID)
        self.assertEqual(set(result), set(live.FILES))
        self.assertTrue(all(value == "READY" for value in result.values()), result)

    def test_old_home_page_is_not_published_ready(self):
        rows = snapshots()
        rows["index.html"] = live.Response(200, "text/html",
            rows["index.html"].body.replace('href="product-selector.html"', 'href="old.html"'))
        self.assertEqual(live.inspect_site(rows.__getitem__, ID)["index.html"], "HOME_TOOL_LINK_MISSING")

    def test_missing_live_tool_page_blocks_readiness(self):
        rows = snapshots()
        rows["sim-optimizer.html"] = live.Response(404, "text/html", "not found")
        self.assertEqual(live.inspect_site(rows.__getitem__, ID)["sim-optimizer.html"], "HTTP_404")

    def test_analytics_mismatch_does_not_count_as_live(self):
        rows = snapshots()
        old = rows["product-selector.html"]
        rows["product-selector.html"] = live.Response(200, "text/html",
            old.body.replace(ID, "G-OLD123456"))
        self.assertEqual(live.inspect_site(rows.__getitem__, ID)["product-selector.html"],
                         "GA4_SCRIPT_MISSING_OR_DUPLICATE")

    def test_html_fallback_and_sitemap_inconsistency_fail(self):
        rows = snapshots()
        rows["product-selector.html"] = live.Response(200, "text/html", rows["index.html"].body)
        self.assertEqual(live.inspect_site(rows.__getitem__, ID)["product-selector.html"], "CANONICAL_MISMATCH")
        rows = snapshots()
        rows["sitemap.xml"] = live.Response(200, "application/xml", " " * 300)
        self.assertEqual(live.inspect_site(rows.__getitem__, ID)["sitemap.xml"], "SITEMAP_TOOL_MISSING")

    def test_invalid_robots_and_bad_mime_not_accepted(self):
        rows = snapshots()
        rows["sim-optimizer.html"] = live.Response(200, "application/octet-stream",
                                                    rows["sim-optimizer.html"].body)
        self.assertEqual(live.inspect_site(rows.__getitem__, ID)["sim-optimizer.html"], "HTML_MIME_INVALID")

    def test_network_failure_is_fixed_code_not_exception_text(self):
        def broken(_):
            raise OSError("PRIVATE_PASSWORD fixture-secret")
        result = live.inspect_site(broken, ID)
        self.assertEqual(set(result.values()), {"HTTP_UNAVAILABLE"})
        self.assertNotIn("PRIVATE_PASSWORD", repr(result))

    def test_invalid_measurement_id_fails_before_any_request(self):
        with self.assertRaisesRegex(ValueError, "GA4_ID_INVALID"):
            live.inspect_site(lambda name: self.fail("must not call network"), "invalid")

    def test_cli_observe_reports_pending_without_false_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config"
            cfg.mkdir()
            (cfg / "analytics.json").write_text(json.dumps({"ga4_measurement_id": ID}))
            (cfg / "site.json").write_text(json.dumps({"site_url": live.SITE_URL}))
            rows = snapshots()
            rows["sim-optimizer.html"] = live.Response(404, "", "")
            buf = io.StringIO()
            with patch.object(live, "ROOT", Path(tmp)), \
                 patch.object(live, "fetch_public", side_effect=rows.__getitem__), \
                 patch("sys.argv", ["live", "--observe"]), redirect_stdout(buf):
                self.assertEqual(live.main(), 0)
            self.assertIn("LIVE_TOOL_STATUS: PENDING", buf.getvalue())
            self.assertNotIn("LIVE_TOOL_STATUS: READY", buf.getvalue())

    def test_cli_require_refuses_to_claim_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config"
            cfg.mkdir()
            (cfg / "analytics.json").write_text(json.dumps({"ga4_measurement_id": ID}))
            (cfg / "site.json").write_text(json.dumps({"site_url": live.SITE_URL}))
            rows = snapshots()
            rows["product-selector.html"] = live.Response(404, "", "")
            with patch.object(live, "ROOT", Path(tmp)), \
                 patch.object(live, "fetch_public", side_effect=rows.__getitem__), \
                 patch("sys.argv", ["live", "--require"]), redirect_stdout(io.StringIO()):
                self.assertEqual(live.main(), 1)


if __name__ == "__main__":
    unittest.main()
