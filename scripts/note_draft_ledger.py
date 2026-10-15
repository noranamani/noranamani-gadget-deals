#!/usr/bin/env python3
"""SQLite ledger for note drafts; never operates note or stores credentials."""
import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DDL = """CREATE TABLE IF NOT EXISTS posts (
 site_id TEXT NOT NULL, issue_key TEXT NOT NULL, sha256 TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','draft_verified','published_verified','uncertain')),
 note_url TEXT, updated_at TEXT NOT NULL,
 PRIMARY KEY(site_id, issue_key)
)"""

def connect(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db=sqlite3.connect(str(path),timeout=10,isolation_level=None)
    db.execute("PRAGMA busy_timeout=10000")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(DDL)
    return db

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",required=True,type=Path)
    p.add_argument("--report",required=True,type=Path)
    p.add_argument("--mark-draft",action="store_true")
    p.add_argument("--site")
    p.add_argument("--issue")
    p.add_argument("--sha256")
    p.add_argument("--url")
    a=p.parse_args()
    db=connect(a.db)
    if a.mark_draft:
        # This is a human-attested update after checking the note editor URL.
        if not (a.site in {"gadget","local","gacha"} and a.issue and a.sha256 and len(a.sha256)==64 and a.url and a.url.startswith("https://note.com/")):
            p.error("verified draft requires site, issue, sha256 and note.com URL")
        with db:
            existing=db.execute("SELECT sha256,status FROM posts WHERE site_id=? AND issue_key=?",(a.site,a.issue)).fetchone()
            if existing and existing[0]!=a.sha256:
                p.error("hash differs from existing issue; manual reconciliation required")
            db.execute("INSERT INTO posts VALUES (?,?,?,?,?,?) ON CONFLICT(site_id,issue_key) DO UPDATE SET status=excluded.status,note_url=excluded.note_url,updated_at=excluded.updated_at",(a.site,a.issue,a.sha256,"draft_verified",a.url,datetime.now(timezone.utc).isoformat()))
        print("verified draft recorded")
        return
    report=json.loads(a.report.read_text(encoding="utf-8"))
    for entry in report.get("entries",[]):
        if entry.get("action")!="draft_candidate": continue
        key=(entry["site_id"],entry["issue_key"])
        prior=db.execute("SELECT sha256,status FROM posts WHERE site_id=? AND issue_key=?",key).fetchone()
        if prior:
            print(*key,"already_recorded" if prior[0]==entry["sha256"] else "revision_requires_review")
        else:
            print(*key,"eligible_for_manual_draft_test")
    db.close()

if __name__=="__main__":
    main()
