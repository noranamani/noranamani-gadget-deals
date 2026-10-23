#!/usr/bin/env python3
"""Expand the site's ROOT search categories conservatively.

This program NEVER calls Rakuten or note. It uses only products previously
retrieved by the registered-IP Windows collector. Only the scheduled GitHub Actions job
may apply additions; manual/PR runs are read-only checks.

The existing conditional comparison pages in config/comparisons.json are
separate: this rollout adds config/queries.json TOP LEVEL categories.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
JST = timezone(timedelta(hours=9))
SLUG = re.compile(r"^[a-z][a-z0-9-]{2,48}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FORBIDDEN = re.compile(r"[\r\n\0<>]")
CONFIG = "config/category-expansion.json"
QUERIES = "config/queries.json"
STATE = "data/category-expansion-state.json"
PRODUCTS = "data/products.json"


def read_json(root: Path, rel: str) -> Any:
    return json.loads((root / rel).read_text(encoding="utf-8"))


def check_row(row: dict) -> None:
    if not isinstance(row, dict):
        raise ValueError("CATEGORY_NOT_OBJECT")
    slug = row.get("id")
    name = row.get("name")
    keyword = row.get("keyword")
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        raise ValueError("CATEGORY_ID_INVALID")
    if not isinstance(name, str) or not 2 <= len(name) <= 35 or FORBIDDEN.search(name):
        raise ValueError("CATEGORY_NAME_INVALID")
    if not isinstance(keyword, str) or not 6 <= len(keyword) <= 115 or FORBIDDEN.search(keyword):
        raise ValueError("CATEGORY_KEYWORD_INVALID")
    if not isinstance(row.get("minPrice"), int) or not isinstance(row.get("maxPrice"), int):
        raise ValueError("CATEGORY_PRICE_INVALID")
    if not 100 <= row["minPrice"] < row["maxPrice"] <= 100000:
        raise ValueError("CATEGORY_PRICE_BOUNDS_INVALID")
    if set(row.keys()) != {"id", "name", "keyword", "minPrice", "maxPrice"}:
        raise ValueError("CATEGORY_UNEXPECTED_KEYS")


def validate(config: dict, state: dict, queries: list) -> None:
    if config.get("schema_version") != 1 or state.get("schema_version") != 1:
        raise ValueError("ROLLOUT_SCHEMA_INVALID")
    if not isinstance(config.get("enabled"), bool):
        raise ValueError("ROLLOUT_SWITCH_INVALID")
    if not isinstance(queries, list) or not (1 <= len(queries) <= 24):
        raise ValueError("EXISTING_CATEGORIES_INVALID")
    candidates = config.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("ROLLOUT_CANDIDATES_INVALID")
    if not isinstance(state.get("history"), list):
        raise ValueError("ROLLOUT_HISTORY_INVALID")
    for k, lower, upper in [
        ("minimum_days_between_additions", 14, 90),
        ("max_total_categories", len(queries), 24),
        ("max_additions_per_run", 1, 1),
        ("minimum_previous_new_category_products", 5, 25),
        ("maximum_data_age_hours", 12, 48),
    ]:
        v = config.get(k)
        if not isinstance(v, int) or not lower <= v <= upper:
            raise ValueError("ROLLOUT_LIMIT_INVALID_" + k)
    date_s = config.get("first_addition_jst", "")
    if not isinstance(date_s, str) or not DATE.fullmatch(date_s):
        raise ValueError("ROLLOUT_DATE_INVALID")
    datetime.fromisoformat(date_s)
    # A previously activated candidate legitimately exists in both the
    # current query list AND the vetted candidate pool.
    for rows in (queries, candidates):
        ids, names, words = set(), set(), set()
        for row in rows:
            check_row(row)
            normalized = " ".join(row["keyword"].lower().split())
            if row["id"] in ids or row["name"] in names or normalized in words:
                raise ValueError("CATEGORY_DUPLICATE_ID_NAME_OR_QUERY")
            ids.add(row["id"])
            names.add(row["name"])
            words.add(normalized)
    existing = {row["id"]: row for row in queries}
    for candidate in candidates:
        if candidate["id"] in existing and candidate != existing[candidate["id"]]:
            raise ValueError("ACTIVATED_CATEGORY_CHANGED_AFTER_ROLLOUT")
    last = ""
    for entry in state["history"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise ValueError("ROLLOUT_HISTORY_ENTRY_INVALID")
        if not isinstance(entry.get("date_jst"), str) or not DATE.fullmatch(entry["date_jst"]):
            raise ValueError("ROLLOUT_HISTORY_DATE_INVALID")
        datetime.fromisoformat(entry["date_jst"])
        if entry["date_jst"] <= last:
            raise ValueError("ROLLOUT_HISTORY_NOT_IN_ORDER")
        last = entry["date_jst"]


def _result(action: str, *, candidate: dict | None = None, note: str = "") -> dict:
    row = {"action": action}
    if candidate:
        row["candidate_id"] = candidate["id"]
        row["candidate_name"] = candidate["name"]
    if note:
        row["reason"] = note
    return row


def decide(config: dict, state: dict, queries: list, products: dict, *, now: datetime) -> dict:
    """Pure review/decision function. No file changes or API calls."""
    validate(config, state, queries)
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_REQUIRED")
    today = now.astimezone(JST).date()
    if not config["enabled"]:
        return _result("disabled")
    if today.isoformat() < config["first_addition_jst"]:
        return _result("waiting_until_start", note=config["first_addition_jst"])
    if today.weekday() != 6:
        return _result("not_sunday")
    if len(queries) >= config["max_total_categories"]:
        return _result("category_cap_reached")
    if state["history"]:
        previous = state["history"][-1]
        last = datetime.fromisoformat(previous["date_jst"]).date()
        if (today - last).days < config["minimum_days_between_additions"]:
            return _result("cooldown", note=previous["id"])
    updated_s = products.get("updatedAt")
    if not isinstance(updated_s, str) or not updated_s:
        return _result("product_data_timestamp_missing")
    try:
        updated = datetime.fromisoformat(updated_s.replace("Z", "+00:00"))
        if updated.tzinfo is None:
            raise ValueError("missing time zone")
        hours_old = (now - updated).total_seconds() / 3600
    except (TypeError, ValueError):
        return _result("product_data_timestamp_invalid")
    if hours_old < 0 or hours_old > config["maximum_data_age_hours"]:
        return _result("stale_product_data")
    cats = products.get("categories")
    if not isinstance(cats, list):
        return _result("product_categories_missing")
    if state["history"]:
        previous_id = state["history"][-1]["id"]
        previous_rows = [c for c in cats if isinstance(c, dict) and c.get("id") == previous_id]
        if len(previous_rows) != 1:
            return _result("previous_category_not_collected", note=previous_id)
        items = previous_rows[0].get("items")
        if not isinstance(items, list):
            return _result("previous_category_items_invalid", note=previous_id)
        unique_codes = {i.get("code") for i in items if isinstance(i, dict) and
                        isinstance(i.get("code"), str) and i.get("code")}
        if len(unique_codes) < config["minimum_previous_new_category_products"]:
            return _result("previous_category_too_sparse", note=previous_id)
    present = {q["id"] for q in queries}
    historic = {e["id"] for e in state["history"]}
    for candidate in config["candidates"]:
        if candidate["id"] not in present and candidate["id"] not in historic:
            return _result("eligible", candidate=candidate)
    return _result("candidate_pool_exhausted")


def require_apply_window(now: datetime) -> None:
    local = now.astimezone(JST)
    if local.weekday() != 6 or not time(5, 0) <= local.time() <= time(6, 0):
        raise ValueError('CATEGORY_ROLLOUT_OUTSIDE_SAFE_WINDOW')


def apply(root: Path, *, now: datetime, allow_write: bool) -> dict:
    config = read_json(root, CONFIG)
    state = read_json(root, STATE)
    queries = read_json(root, QUERIES)
    products = read_json(root, PRODUCTS)
    plan = decide(config, state, queries, products, now=now)
    if plan["action"] != "eligible" or not allow_write:
        return plan
    if os.environ.get("CATEGORY_ROLLOUT_SCHEDULED") != "1":
        raise ValueError("SCHEDULED_JOB_CONSENT_REQUIRED")
    require_apply_window(now)
    new_row = next(c for c in config["candidates"] if c["id"] == plan["candidate_id"])
    new_queries = [*queries, new_row]
    new_state = {"schema_version": 1, "history": [
        *state["history"],
        {"id": new_row["id"], "date_jst": now.astimezone(JST).date().isoformat(),
         "query_sha256": hashlib.sha256(json.dumps(
             new_row, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()}
    ]}
    # Files are committed by the GitHub workflow as a single commit.
    (root / QUERIES).write_text(json.dumps(new_queries, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
    (root / STATE).write_text(json.dumps(new_state, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    return {**plan, "action": "added_to_queries",
            "total_categories": len(new_queries),
            "note": "Data will be fetched by the Windows collector; never by this action."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="read only, default")
    parser.add_argument("--apply", action="store_true", help="scheduled job only")
    parser.add_argument("--assert-apply-window", action="store_true", help="check immediately before Git push")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    if args.assert_apply_window:
        require_apply_window(datetime.now(timezone.utc))
        print('CATEGORY_ROLLOUT_WINDOW_OK')
        return
    if args.apply and args.check:
        parser.error("choose only one mode")
    result = apply(args.root, now=datetime.now(timezone.utc), allow_write=args.apply)
    print("CATEGORY_ROLLOUT: " + json.dumps(result, ensure_ascii=False, sort_keys=True))
    if args.apply and result["action"] not in ("added_to_queries",):
        print("CATEGORY_ROLLOUT_NO_CHANGE: no new API query and no file modifications")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("CATEGORY_ROLLOUT_STOPPED: " + str(exc).splitlines()[0][:160], file=sys.stderr)
        raise SystemExit(1)
