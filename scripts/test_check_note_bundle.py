#!/usr/bin/env python3
"""Offline checks for shared weekly note bundles."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from check_note_bundle import validate


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        (self.folder / "article.md").write_text("# テスト\n本文", encoding="utf-8")
        self.data = {
            "schema_version": 1, "site_id": "gadget", "issue_key": "2026-W42",
            "status": "ready", "title": "今週の記事",
            "article_path": "article.md", "cover_path": None,
            "source_url": "https://example.com/", "generated_at": "2026-10-15T08:00:00+09:00",
        }
    def tearDown(self):
        self.tmp.cleanup()
    def write(self):
        (self.folder / "manifest.json").write_text(json.dumps(self.data), encoding="utf-8")
    def test_ready(self):
        self.write()
        self.assertEqual(validate(self.folder)["status"], "ready")
    def test_skip_without_article(self):
        self.data["status"] = "skip"
        (self.folder / "article.md").unlink()
        self.write()
        self.assertEqual(validate(self.folder)["status"], "skip")
    def test_reject_bad_site(self):
        self.data["site_id"] = "unknown"
        self.write()
        with self.assertRaises(ValueError):
            validate(self.folder)
    def test_reject_relative_url(self):
        self.data["source_url"] = "/local"
        self.write()
        with self.assertRaises(ValueError):
            validate(self.folder)
    def test_reject_missing_cover(self):
        self.data["cover_path"] = "article-cover.png"
        self.write()
        with self.assertRaises(ValueError):
            validate(self.folder)
    def test_legacy_requires_opt_in(self):
        with self.assertRaises(ValueError):
            validate(self.folder)
        self.assertEqual(validate(self.folder, allow_legacy=True)["status"], "legacy")


if __name__ == "__main__":
    unittest.main()
