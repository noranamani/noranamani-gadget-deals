"""Offline check that this repo's GA4 stream is embedded by site generation.

Does not call Analytics, Rakuten, GitHub Pages, or note.com.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import build_site


class GadgetGA4ActivationTests(unittest.TestCase):
    def test_configured_stream_is_used_once_in_html_and_click_events(self):
        config = json.loads((Path(__file__).resolve().parents[1] /
                             "config" / "analytics.json").read_text(encoding="utf-8"))
        measurement_id = config.get("ga4_measurement_id")
        self.assertRegex(measurement_id, r"^G-[A-Z0-9]{6,16}$")
        html = build_site.layout(
            {"site_name": "GA4 test", "site_url": "https://example.invalid",
             "note_url": "https://note.com/noranamani"},
            "ガジェット比較", "<h1>テスト</h1>",
            canonical_file="index.html"
        )
        self.assertEqual(
            html.count("googletagmanager.com/gtag/js?id=" + measurement_id), 1)
        self.assertEqual(
            html.count('gtag("config","' + measurement_id + '")'), 1)
        self.assertIn('window.gtag("event", "affiliate_click"', html)
        for dimension in ("item_code", "item_category", "link_placement", "link_target"):
            with self.subTest(dimension=dimension):
                self.assertIn(dimension + ":", html)
        self.assertIn('transport_type: "beacon"', html)


if __name__ == "__main__":
    unittest.main()
