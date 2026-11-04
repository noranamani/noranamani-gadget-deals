"""A single, fail-closed wake-up for a real Windows daily editorial push.

This is a *trigger*, never a second publisher. The existing publication
workflow owns all deployment, freshness, and once-per-day checks. No Rakuten
API, note.com, or Git writes are performed here.
"""
from __future__ import annotations

import argparse
import json
import os
import time as clock_time
import urllib.parse
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from pipeline_common import JST, PipelineError, timestamp
from publication_gate import github_reader, publication_receipt
from publication_pipeline import validate_site_publication

REPOSITORY = "noranamani/noranamani-gadget-deals"
WORKFLOW = "windows-daily-publish.yml"
EARLIEST = time(6, 20)
TARGET = time(6, 50)
LATEST = time(9, 5)
MAX_EVENT_AGE = timedelta(hours=3)
ACTIVE = {"queued", "in_progress", "waiting", "pending", "requested"}


def slot_delay(now: datetime, pushed: datetime) -> float | None:
    """Seconds until 06:50, zero inside the permitted window, None to skip."""
    now_local, pushed_local = now.astimezone(JST), pushed.astimezone(JST)
    if now_local.date() != pushed_local.date():
        return None  # A next-day delayed push may not wake a new day's site.
    if (now_local.time() < EARLIEST or now_local.time() > LATEST
            or pushed_local.time() < EARLIEST):
        return None
    if pushed > now + timedelta(minutes=2) or now - pushed > MAX_EVENT_AGE:
        return None
    target = datetime.combine(now_local.date(), TARGET, JST)
    return max(0.0, (target - now_local).total_seconds())


def dispatch_allowed(rows: list, now: datetime) -> bool:
    """A Windows dispatch or any active publication owns this day already."""
    if len(rows) >= 100:
        raise PipelineError("WAKEUP_RUNS_PAGINATION_LIMIT")
    today = now.astimezone(JST).date()
    for row in rows:
        if not isinstance(row, dict) or type(row.get("id")) is not int:
            raise PipelineError("WAKEUP_RUNS_INVALID")
        created = timestamp(row.get("created_at")).astimezone(JST).date()
        if created != today:
            continue
        if row.get("head_branch") != "main":
            raise PipelineError("WAKEUP_RUNS_INVALID")
        if row.get("event") == "workflow_dispatch" or row.get("status") in ACTIVE:
            return False  # Never resend an uncertain Windows dispatch.
    return True


def require_push_event() -> datetime:
    if (os.getenv("GITHUB_REPOSITORY") != REPOSITORY
            or os.getenv("GITHUB_REF") != "refs/heads/main"
            or os.getenv("GITHUB_EVENT_NAME") != "push"):
        raise PipelineError("WAKEUP_EVENT_INVALID")
    path = os.getenv("GITHUB_EVENT_PATH")
    if not path:
        raise PipelineError("WAKEUP_EVENT_MISSING")
    try:
        event = json.loads(Path(path).read_text(encoding="utf-8"))
        if event.get("ref") != "refs/heads/main":
            raise ValueError()
        pushed = timestamp(event["head_commit"]["timestamp"])
    except (OSError, ValueError, KeyError, TypeError, PipelineError):
        raise PipelineError("WAKEUP_PUSH_TIMESTAMP_INVALID") from None
    return pushed


def output(eligible: bool, reason: str) -> None:
    # Never emit tokens, source content, or request/response bodies.
    print("EDITORIAL_WAKEUP: " + reason)
    path = os.getenv("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as file:
            file.write("eligible=" + ("true" if eligible else "false") + "\n")
            file.write("reason=" + reason + "\n")


def run_slot(now: datetime, pushed: datetime, pause=clock_time.sleep) -> bool:
    delay = slot_delay(now, pushed)
    if delay is None:
        output(False, "PUSH_OUTSIDE_FRESH_DAY_WINDOW")
        return False
    if delay:
        pause(delay)
    after = datetime.now(timezone.utc)
    if slot_delay(after, pushed) != 0:
        output(False, "PUSH_DELAY_EXPIRED")
        return False
    output(True, "READY_FOR_READONLY_GATE")
    return True


def run_plan(now: datetime, pushed: datetime, root: Path, get) -> bool:
    if slot_delay(now, pushed) != 0:
        output(False, "PUSH_DELAY_EXPIRED")
        return False
    try:
        validate_site_publication(root, now)
        if publication_receipt(get, now) is not None:
            output(False, "SITE_ALREADY_PUBLISHED")
            return False
        start = datetime.combine(now.astimezone(JST).date(), time.min, JST).astimezone(timezone.utc)
        query = urllib.parse.urlencode({"branch": "main", "created": ">=" + start.isoformat()})
        result = get(f"actions/workflows/{WORKFLOW}/runs?{query}&per_page=100")
        rows = result.get("workflow_runs")
        if not isinstance(rows, list):
            raise PipelineError("WAKEUP_RUNS_INVALID")
        if not dispatch_allowed(rows, now):
            output(False, "EXISTING_DISPATCH_OR_ACTIVE_RUN")
            return False
    except PipelineError as exc:
        # The main workflow's independent scheduled alert owns stale-data notices.
        print("EDITORIAL_WAKEUP_BLOCKED: " + str(exc))
        output(False, "SOURCE_OR_PUBLICATION_GATE_BLOCKED")
        return False
    output(True, "DISPATCH_ONCE")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("slot", "plan"))
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    try:
        pushed = require_push_event()
        if args.mode == "slot":
            run_slot(datetime.now(timezone.utc), pushed)
        else:
            reader = github_reader(os.getenv("GITHUB_REPOSITORY", ""),
                                   os.getenv("GH_TOKEN", ""))
            run_plan(datetime.now(timezone.utc), pushed, args.root, reader)
        return 0
    except PipelineError as exc:
        print("EDITORIAL_WAKEUP_BLOCKED: " + str(exc))
        output(False, "UNSAFE_CONTEXT")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
