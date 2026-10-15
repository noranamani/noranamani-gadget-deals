#!/usr/bin/env python3
"""Three-site note queue planner. No browser, login or publishing."""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SITES = ("gadget", "local", "gacha")

def plan(inputs: dict[str, Path], state_path: Path) -> dict:
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    existing = state.get("posts", {})
    output = []
    for site in SITES:
        folder = inputs[site]
        meta_path = folder / "manifest.json"
        if not meta_path.is_file():
            # Gadget's legacy bundle is accepted for dry-run only, never auto-posted.
            output.append({"site_id": site, "action": "needs_manifest" if site == "gadget" else "missing_manifest"})
            continue
        try:
            m = json.loads(meta_path.read_text(encoding="utf-8"))
            if m.get("schema_version") != 1 or m.get("site_id") != site:
                raise ValueError("schema/site mismatch")
            if m.get("status") not in ("ready", "skip"):
                raise ValueError("invalid status")
            key = str(m.get("issue_key", ""))
            if not __import__("re").fullmatch(r"\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])", key):
                raise ValueError("invalid issue key")
            if m["status"] == "skip":
                output.append({"site_id": site, "issue_key": key, "action": "skip"})
                continue
            if m.get("article_path") != "article.md" or not str(m.get("source_url","")).startswith("https://"):
                raise ValueError("invalid article path or source URL")
            if not str(m.get("title","")).strip():
                raise ValueError("missing title")
            if not datetime.fromisoformat(m.get("generated_at","")).tzinfo:
                raise ValueError("missing timezone")
            text = (folder / "article.md").read_text(encoding="utf-8")
            if not text.strip():
                raise ValueError("empty article")
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            record_key = site + "/" + key
            record = existing.get(record_key)
            if record:
                action = "already_recorded" if record.get("sha256") == digest else "revision_requires_review"
            else:
                action = "draft_candidate"
            output.append({"site_id":site, "issue_key":key, "action":action, "sha256":digest, "title":m["title"]})
        except (OSError, ValueError, KeyError, json.JSONDecodeError, TypeError) as exc:
            output.append({"site_id":site, "action":"invalid", "reason":str(exc)})
    return {"mode":"dry_run_only", "generated_at":datetime.now(timezone.utc).isoformat(), "entries":output}

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--gadget", type=Path, required=True)
    p.add_argument("--local", type=Path, required=True)
    p.add_argument("--gacha", type=Path, required=True)
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    report=plan({"gadget":args.gadget,"local":args.local,"gacha":args.gacha},args.state)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"mode":report["mode"],"actions":[e["site_id"]+":"+e["action"] for e in report["entries"]]},ensure_ascii=False))
    return int(any(e["action"]=="invalid" for e in report["entries"]))
if __name__=="__main__":
    sys.exit(main())
