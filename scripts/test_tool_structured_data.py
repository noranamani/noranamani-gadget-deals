"""Structured-data SEO checks for the free browser tools (offline)."""
from __future__ import annotations

import json
import re
import unittest

from build_site import free_tool_schema, layout


class FreeToolStructuredDataTests(unittest.TestCase):
    SITE = {"site_name": "test", "site_url": "https://example.invalid",
            "note_url": "https://note.com/example"}
    TOOLS = (
        ("sim-optimizer", "sim-optimizer.html", "FinanceApplication"),
        ("product-selector", "product-selector.html", "ShoppingApplication"),
    )

    def test_canonical_free_webapp_schema_exactly_once(self):
        for page, filename, category in self.TOOLS:
            with self.subTest(page=page):
                html = layout(self.SITE, "テスト", "<p>テスト</p>",
                              "無料の診断・比較ツール", active_page=page,
                              canonical_file=filename)
                payloads = re.findall(
                    r'<script type="application/ld\+json">(.*?)</script>',
                    html, re.S)
                self.assertEqual(len(payloads), 1)
                schema = json.loads(payloads[0])
                url = self.SITE["site_url"] + "/" + filename
                self.assertEqual(schema["@type"], "WebApplication")
                self.assertEqual(schema["@context"], "https://schema.org")
                self.assertEqual(schema["url"], url)
                self.assertEqual(schema["@id"], url + "#webapp")
                self.assertEqual(schema["applicationCategory"], category)
                self.assertEqual(schema["offers"]["price"], 0)
                self.assertEqual(schema["offers"]["priceCurrency"], "JPY")
                self.assertIs(schema["isAccessibleForFree"], True)
                self.assertEqual(schema["inLanguage"], "ja")
                self.assertNotIn("review", schema)
                self.assertNotIn("aggregateRating", schema)
                self.assertEqual(html.count('<link rel="canonical" href="' + url + '">'), 1)

    def test_other_pages_do_not_inherit_tool_markup(self):
        output = layout(self.SITE, "ホーム", "<h1>テスト</h1>", canonical_file="index.html")
        self.assertNotIn('type="application/ld+json"', output)

    def test_never_mark_the_wrong_canonical_as_the_app(self):
        with self.assertRaisesRegex(ValueError, "FREE_TOOL_CANONICAL_MISMATCH"):
            free_tool_schema(self.SITE, "sim-optimizer", "index.html", "無料")
        with self.assertRaisesRegex(ValueError, "FREE_TOOL_SITE_URL_INVALID"):
            free_tool_schema({"site_url": "http://example.invalid"},
                             "sim-optimizer", "sim-optimizer.html", "無料")

    def test_future_description_cannot_break_script_tag(self):
        raw = '</script><script>alert("unexpected")</script>'
        script = free_tool_schema(self.SITE, "product-selector",
                                  "product-selector.html", raw)
        self.assertEqual(script.count("</script>"), 1)
        payload = re.search(
            r'<script type="application/ld\+json">(.*?)</script>', script, re.S)
        self.assertIsNotNone(payload)
        schema = json.loads(payload.group(1))
        self.assertEqual(schema["description"], raw)
        self.assertNotIn(raw, script)


if __name__ == "__main__":
    unittest.main()
