"""No-network regression tests for independent Pages free-tool monitoring."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import watch_free_tools as watch
from check_live_tools import Response
from test_live_tools import ID, snapshots


class IndependentFreeToolsWatchTests(unittest.TestCase):
    def test_historic_home_without_canonical_is_allowed(self):
        rows = snapshots()
        old = rows["index.html"]
        rows["index.html"] = Response(
            200, "text/html",
            old.body.replace('<link rel="canonical" href="' +
                             watch.SITE_URL + '/index.html">', ""))
        self.assertEqual(set(watch.inspect_availability(rows.__getitem__, ID).values()),
                         {"READY"})

    def test_both_real_tool_pages_must_resolve(self):
        for missing in watch.TOOLS:
            rows = snapshots()
            rows[missing] = Response(404, "text/html", "not found")
            report = watch.inspect_availability(rows.__getitem__, ID)
            self.assertEqual(report[missing], "HTTP_404")
            self.assertEqual(report["index.html"], "READY")

    def test_home_links_and_sitemap_must_be_correct(self):
        rows = snapshots()
        home = rows["index.html"]
        rows["index.html"] = Response(
            200, "text/html",
            home.body.replace('href="product-selector.html"', 'href="old-page.html"'))
        self.assertEqual(watch.inspect_availability(rows.__getitem__, ID)["index.html"],
                         "HOME_LINK_MISSING")
        rows = snapshots()
        rows["sitemap.xml"] = Response(200, "text/xml", " " * 450)
        self.assertEqual(watch.inspect_availability(rows.__getitem__, ID)["sitemap.xml"],
                         "SITEMAP_XML_INVALID")

    def test_missing_ga4_and_tool_js_are_not_accepted(self):
        rows = snapshots()
        product = rows["product-selector.html"]
        rows["product-selector.html"] = Response(
            200, "text/html", product.body.replace("G-1273VEC9JB", "G-BAD123456"))
        self.assertEqual(
            watch.inspect_availability(rows.__getitem__, ID)["product-selector.html"],
            "GA4_SCRIPT_MISSING_OR_DUPLICATE")
        rows = snapshots()
        sim = rows["sim-optimizer.html"]
        rows["sim-optimizer.html"] = Response(
            200, "text/html", sim.body.replace("function calculate(", "function missing("))
        self.assertEqual(
            watch.inspect_availability(rows.__getitem__, ID)["sim-optimizer.html"],
            "SIM_TOOL_CONTROLS_MISSING")

    def test_network_errors_are_fixed_codes_never_leak_values(self):
        def broken(name):
            raise OSError("hidden-token private-path https://bad.example")
        report = watch.inspect_availability(broken, ID)
        self.assertEqual(set(report.values()), {"HTTP_UNAVAILABLE"})
        with redirect_stdout(io.StringIO()) as output:
            self.assertFalse(watch.publish_report(report))
        self.assertNotIn("hidden-token", output.getvalue())
        self.assertIn("TOOL_HEALTH_STATUS: FAILED", output.getvalue())

    def test_single_transient_failure_recovers_after_retry(self):
        rows = snapshots()
        attempts = []
        sleeps = []
        def flaky(name):
            if name == "product-selector.html" and not sleeps:
                return Response(503, "", "")
            return rows[name]
        result = watch.check_with_retry(flaky, ID, attempts=3, interval=1,
                                        pause=lambda seconds: sleeps.append(seconds))
        self.assertEqual(sleeps, [1])
        self.assertEqual(set(result.values()), {"READY"})

    def test_persistent_failure_is_not_reported_as_healthy(self):
        rows = snapshots()
        rows["sim-optimizer.html"] = Response(404, "", "")
        calls = []
        result = watch.check_with_retry(rows.__getitem__, ID, attempts=3,
                                        interval=0, pause=lambda interval: calls.append(1))
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["sim-optimizer.html"], "HTTP_404")
        self.assertFalse(watch.publish_report(result))

    def test_invalid_config_stops_before_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config"
            config.mkdir()
            (config / "analytics.json").write_text(json.dumps({"ga4_measurement_id": ID}))
            (config / "site.json").write_text(json.dumps({"site_url": "https://other.invalid"}))
            with patch.object(watch, "ROOT", root), \
                 patch.object(watch, "fetch_public", side_effect=AssertionError("no network")), \
                 patch("sys.argv", ["watch"]), redirect_stdout(io.StringIO()):
                self.assertEqual(watch.main(), 2)

    def test_cli_check_returns_success_only_on_real_availability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config"
            config.mkdir()
            (config / "analytics.json").write_text(json.dumps({"ga4_measurement_id": ID}))
            (config / "site.json").write_text(json.dumps({"site_url": watch.SITE_URL}))
            rows = snapshots()
            with patch.object(watch, "ROOT", root), \
                 patch.object(watch, "fetch_public", side_effect=rows.__getitem__), \
                 patch("sys.argv", ["watch", "--attempts", "1", "--interval", "0"]), \
                 redirect_stdout(io.StringIO()) as out:
                self.assertEqual(watch.main(), 0)
                self.assertIn("TOOL_HEALTH_STATUS: READY", out.getvalue())
            rows["sim-optimizer.html"] = Response(404, "", "")
            with patch.object(watch, "ROOT", root), \
                 patch.object(watch, "fetch_public", side_effect=rows.__getitem__), \
                 patch("sys.argv", ["watch", "--attempts", "1", "--interval", "0"]), \
                 redirect_stdout(io.StringIO()) as out:
                self.assertEqual(watch.main(), 1)
                self.assertIn("TOOL_HEALTH_STATUS: FAILED", out.getvalue())


if __name__ == "__main__":
    unittest.main()
