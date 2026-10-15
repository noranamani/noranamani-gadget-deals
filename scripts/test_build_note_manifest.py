#!/usr/bin/env python3
"""Offline tests for the existing gadget weekly note -> common manifest adapter."""
from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from build_note_manifest import build_manifest

class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        (self.root/"note/latest").mkdir(parents=True)
        (self.root/"data").mkdir()
        self.meta={
            "generatedAt":"2026-10-14T04:55:00Z",
            "weekLabel":"2026/10/12〜2026/10/18",
            "title":"テスト記事",
        }
        self.write_meta()
        (self.root/"note/latest/article.md").write_text("# テスト記事\n\n2026/10/12〜2026/10/18\n\n" + "記事本文。"*20,encoding="utf-8")
    def write_meta(self):
        (self.root/"data/note-weekly.json").write_text(json.dumps(self.meta,ensure_ascii=False),encoding="utf-8")
    def tearDown(self):
        self.temp.cleanup()
    def test_normal_manifest(self):
        m=build_manifest(self.root)
        self.assertEqual(m["issue_key"],"2026-W42")
        self.assertEqual(m["status"],"ready")
        self.assertIsNone(m["cover_path"])
    def test_title_mismatch(self):
        self.meta["title"]="違うタイトル"
        self.write_meta()
        with self.assertRaisesRegex(ValueError,"title"):
            build_manifest(self.root)
    def test_non_week_range(self):
        self.meta["weekLabel"]="2026/10/13〜2026/10/19"
        self.write_meta()
        with self.assertRaisesRegex(ValueError,"Monday-Sunday"):
            build_manifest(self.root)
    def test_naive_timestamp_rejected(self):
        self.meta["generatedAt"]="2026-10-14T04:55:00"
        self.write_meta()
        with self.assertRaisesRegex(ValueError,"timezone"):
            build_manifest(self.root)

if __name__=="__main__":
    unittest.main()
