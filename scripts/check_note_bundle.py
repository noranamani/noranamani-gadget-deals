#!/usr/bin/env python3
"""Validate a weekly note handoff bundle; never logs in or publishes."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from datetime import datetime

SITES = {"gadget", "local", "gacha"}
KEY = re.compile(r"^\d{4}-W(?:0[1-9]|[1-4][0-9]|5[0-3])$")


def validate(folder: Path, allow_legacy: bool = False) -> dict:
    manifest_file = folder / "manifest.json"
    if not manifest_file.exists():
        if allow_legacy and (folder / "article.md").is_file():
            return {"status": "legacy", "article_sha256": hashlib.sha256((folder / "article.md").read_bytes()).hexdigest()}
        raise ValueError("missing manifest.json")
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or data.get("site_id") not in SITES:
        raise ValueError("invalid schema_version or site_id")
    if not KEY.fullmatch(str(data.get("issue_key", ""))):
        raise ValueError("invalid issue_key (YYYY-Www)")
    if data.get("status") not in {"ready", "skip"}:
        raise ValueError("status must be ready or skip")
    timestamp = datetime.fromisoformat(str(data.get("generated_at", "")))
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("generated_at must include timezone")
    if data["status"] == "skip":
        return {"site_id": data["site_id"], "issue_key": data["issue_key"], "status": "skip"}
    if not str(data.get("title", "")).strip():
        raise ValueError("ready requires title")
    source_url = str(data.get("source_url", ""))
    if not source_url.startswith("https://"):
        raise ValueError("ready requires https source_url")
    article_path = data.get("article_path")
    if article_path != "article.md":
        raise ValueError("article_path must be article.md")
    article = folder / "article.md"
    if not article.is_file() or not article.read_text(encoding="utf-8").strip():
        raise ValueError("missing or empty article.md")
    cover = data.get("cover_path")
    if cover not in (None, "article-cover.png"):
        raise ValueError("cover_path must be null or article-cover.png")
    if cover and not (folder / cover).is_file():
        raise ValueError("declared cover image does not exist")
    return {
        "site_id": data["site_id"], "issue_key": data["issue_key"],
        "status": "ready", "article_sha256": hashlib.sha256(article.read_bytes()).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", nargs="?", default="note/latest")
    parser.add_argument("--allow-legacy", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(validate(Path(args.folder), args.allow_legacy), ensure_ascii=False))
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"NOTE_BUNDLE_INVALID: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
