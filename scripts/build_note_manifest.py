#!/usr/bin/env python3
"""Generate the common note manifest for the existing gadget weekly bundle.

This does not log in, create a draft, or publish. It validates week metadata
rather than treating every previously generated article as a new post.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

SITE_URL = "https://noranamani.github.io/noranamani-gadget-deals/"
WEEK = re.compile(r"^(\d{4})/(\d{2})/(\d{2})[〜～](\d{4})/(\d{2})/(\d{2})$")


def build_manifest(root: Path) -> dict:
    meta = json.loads((root / "data/note-weekly.json").read_text(encoding="utf-8"))
    label = str(meta.get("weekLabel", ""))
    match = WEEK.fullmatch(label)
    if not match:
        raise ValueError("invalid weekLabel")
    sy, sm, sd, ey, em, ed = (int(x) for x in match.groups())
    start, end = date(sy, sm, sd), date(ey, em, ed)
    if start.weekday() != 0 or end != start + timedelta(days=6):
        raise ValueError("weekLabel is not a Monday-Sunday week")
    stamp = datetime.fromisoformat(str(meta.get("generatedAt", "")).replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("generatedAt requires timezone")
    article_file = root / "note/latest/article.md"
    article = article_file.read_text(encoding="utf-8")
    title = str(meta.get("title", "")).strip()
    if not title or not article.lstrip().startswith("# " + title + "\n"):
        raise ValueError("article title does not match editorial")
    if label not in article or len(article.strip()) < 100:
        raise ValueError("article does not contain its stated week or is empty")
    cover = root / "note/latest/article-cover.png"
    if cover.exists() and cover.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("invalid cover PNG")
    iso = start.isocalendar()
    return {
        "schema_version": 1,
        "site_id": "gadget",
        "issue_key": f"{iso.year}-W{iso.week:02d}",
        "status": "ready",
        "title": title,
        "article_path": "article.md",
        "cover_path": "article-cover.png" if cover.is_file() else None,
        "source_url": SITE_URL,
        "generated_at": stamp.isoformat(),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = p.parse_args()
    manifest = build_manifest(args.root)
    dest = args.root / "note/latest/manifest.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(dest)
    print(f"GADGET_NOTE_MANIFEST_READY: {manifest['issue_key']} (no posting)")


if __name__ == "__main__":
    main()
