"""Safe Sunday note recovery: checks only, never auto-overwrites an existing weekly issue."""
from __future__ import annotations
import argparse
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
    return {"status": "ready", "issue": expected}

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        print("GADGET_NOTE_RECOVERY: " + str(plan(args.root, datetime.now(timezone.utc))))
        return 0
    except PipelineError as exc:
        print("GADGET_NOTE_RECOVERY_STOPPED: " + str(exc))
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
