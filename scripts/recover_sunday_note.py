"""Safe Sunday note recovery: checks only, never auto-overwrites an existing weekly issue."""
from __future__ import annotations
import argparse
import json
import os
from datetime import datetime, timezone, time
from pathlib import Path
from pipeline_common import JST, PipelineError, load_json
from publication_pipeline import week_label
from windows_editorial import weekly

ROOT = Path(__file__).resolve().parents[1]

def plan(root: Path, now: datetime) -> dict:
    jst = now.astimezone(JST)
    if jst.weekday() != 6 or not (time(7, 0) <= jst.time() <= time(9, 5)):
        return {"status": "outside_window"}
    expected = week_label(jst.date())
    current = load_json(root / "data/note-weekly.json")
    if not isinstance(current, dict):
        raise PipelineError("NOTE_EDITORIAL_SCHEMA_INVALID")
    if current.get("weekLabel") == expected:
        return {"status": "already_current"}
    if str(current.get("weekLabel", "")) >= expected:
        raise PipelineError("NOTE_EDITORIAL_WEEK_CONFLICT")
    proposal = weekly(root, now)
    if proposal.get("weekLabel") != expected:
        raise PipelineError("NOTE_RECOVERY_GENERATION_MISMATCH")
    return {"status": "ready", "issue": expected, "editorial": proposal}

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = plan(args.root, datetime.now(timezone.utc))
        if args.apply:
            if os.environ.get("GITHUB_EVENT_NAME") != "schedule" or os.environ.get("GADGET_SUNDAY_RECOVERY") != "APPROVED":
                raise PipelineError("GADGET_RECOVERY_NOT_APPROVED")
            if result["status"] == "ready":
                target = args.root / "data/note-weekly.json"
                temp = target.with_name("note-weekly.json.recovery-tmp")
                with temp.open("x", encoding="utf-8") as stream:
                    json.dump(result["editorial"], stream, ensure_ascii=False, indent=2)
                    stream.write("\\n")
                temp.replace(target)
                result["status"] = "written_current_week_from_verified_source"
        result.pop("editorial", None)
        print("GADGET_NOTE_RECOVERY: " + str(result))
        return 0
    except PipelineError as exc:
        print("GADGET_NOTE_RECOVERY_STOPPED: " + str(exc))
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
