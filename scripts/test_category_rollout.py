#!/usr/bin/env python3
"""Offline category rollout safety tests. No API, Git, or network involved."""
from __future__ import annotations

import json
import copy
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from category_rollout import JST, apply, decide, validate

SUNDAY = datetime(2026, 10, 25, 6, 0, tzinfo=JST)
NEXT = datetime(2026, 11, 8, 6, 0, tzinfo=JST)
BASE = {
    "id": "usb-charger", "name": "USB充電器", "keyword": "USB PD 充電器",
    "minPrice": 500, "maxPrice": 10000,
}
CANDIDATES = [
    {"id": "power-bank", "name": "モバイルバッテリー",
     "keyword": "モバイルバッテリー PD 10000mAh", "minPrice": 1000, "maxPrice": 16000},
    {"id": "usb-c-cable", "name": "USB-Cケーブル",
     "keyword": "USB-C PD 100W ケーブル", "minPrice": 500, "maxPrice": 6000},
]


def seed(now=SUNDAY):
    config = {
        "schema_version": 1, "enabled": True, "first_addition_jst": "2026-10-25",
        "minimum_days_between_additions": 14, "max_total_categories": 5,
        "max_additions_per_run": 1, "minimum_previous_new_category_products": 6,
        "maximum_data_age_hours": 36, "candidates": copy.deepcopy(CANDIDATES),
    }
    state = {"schema_version": 1, "history": []}
    queries = [BASE]
    products = {
        "updatedAt": (now - timedelta(hours=8)).astimezone(timezone.utc).isoformat(),
        "categories": [{"id": "usb-charger", "items": [{"code": "1"}]}],
    }
    return config, state, queries, products


class CategorySafetyTests(unittest.TestCase):
    def test_starts_only_on_or_after_oct_25_and_on_sundays(self):
        cfg, state, qs, products = seed()
        earlier = SUNDAY - timedelta(days=7)
        self.assertEqual(decide(cfg, state, qs, products, now=earlier)["action"], "waiting_until_start")
        self.assertEqual(decide(cfg, state, qs, products,
                                now=SUNDAY + timedelta(days=1))["action"], "not_sunday")

    def test_one_first_category_becomes_eligible(self):
        cfg, state, qs, products = seed()
        plan = decide(cfg, state, qs, products, now=SUNDAY)
        self.assertEqual(plan["action"], "eligible")
        self.assertEqual(plan["candidate_id"], "power-bank")

    def test_cooldown_and_quality_gate_before_second_category(self):
        cfg, state, qs, products = seed()
        cfg["candidates"] = copy.deepcopy(CANDIDATES)
        qs = [BASE, CANDIDATES[0]]
        state["history"] = [{"id": "power-bank", "date_jst": "2026-10-25",
                             "query_sha256": "x"}]
        products["updatedAt"] = (NEXT - timedelta(hours=5)).isoformat()
        self.assertEqual(decide(cfg, state, qs, products,
                                now=NEXT - timedelta(days=7))["action"], "cooldown")
        self.assertEqual(decide(cfg, state, qs, products, now=NEXT)["action"],
                         "previous_category_not_collected")
        products["categories"].append({"id": "power-bank",
                                       "items": [{"code": str(i)} for i in range(4)]})
        self.assertEqual(decide(cfg, state, qs, products, now=NEXT)["action"],
                         "previous_category_too_sparse")
        products["categories"][-1]["items"] = [{"code": str(i)} for i in range(6)]
        self.assertEqual(decide(cfg, state, qs, products, now=NEXT)["candidate_id"], "usb-c-cable")

    def test_stale_collector_data_prevents_new_query(self):
        cfg, state, qs, products = seed()
        products["updatedAt"] = (SUNDAY - timedelta(days=3)).isoformat()
        self.assertEqual(decide(cfg, state, qs, products, now=SUNDAY)["action"],
                         "stale_product_data")

    def test_failed_or_damaged_query_config_never_adds(self):
        cfg, state, qs, products = seed()
        cfg["candidates"].append(CANDIDATES[0])
        with self.assertRaisesRegex(ValueError, "CATEGORY_DUPLICATE"):
            decide(cfg, state, qs, products, now=SUNDAY)
        cfg, state, qs, products = seed()
        cfg["candidates"][0] = {**CANDIDATES[0], "maxPrice": 50}
        with self.assertRaisesRegex(ValueError, "CATEGORY_PRICE"):
            decide(cfg, state, qs, products, now=SUNDAY)

    def test_apply_requires_scheduled_actions_job_and_writes_one_category(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cfg, state, qs, products = seed()
            files = {
                "config/category-expansion.json": cfg,
                "config/queries.json": qs,
                "data/category-expansion-state.json": state,
                "data/products.json": products,
            }
            for name, obj in files.items():
                p = root / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(apply(root, now=SUNDAY, allow_write=False)["action"], "eligible")
            self.assertEqual(len(json.loads((root / "config/queries.json").read_text())), 1)
            old_env = os.environ.pop("CATEGORY_ROLLOUT_SCHEDULED", None)
            try:
                with self.assertRaisesRegex(ValueError, "SCHEDULED_JOB_CONSENT_REQUIRED"):
                    apply(root, now=SUNDAY, allow_write=True)
                os.environ["CATEGORY_ROLLOUT_SCHEDULED"] = "1"
                outcome = apply(root, now=SUNDAY, allow_write=True)
                self.assertEqual(outcome["action"], "added_to_queries")
                self.assertEqual(outcome["candidate_id"], "power-bank")
                self.assertEqual(len(json.loads((root / "config/queries.json").read_text())), 2)
                self.assertEqual(len(json.loads((root / "data/category-expansion-state.json").read_text())["history"]), 1)
                self.assertEqual(apply(root, now=SUNDAY, allow_write=True)["action"], "cooldown")
            finally:
                if old_env is not None:
                    os.environ["CATEGORY_ROLLOUT_SCHEDULED"] = old_env
                else:
                    os.environ.pop("CATEGORY_ROLLOUT_SCHEDULED", None)

    def test_stop_at_cap_or_end_of_curated_pool(self):
        cfg, state, qs, products = seed()
        cfg["max_total_categories"] = 1
        self.assertEqual(decide(cfg, state, qs, products, now=SUNDAY)["action"],
                         "category_cap_reached")
        cfg["max_total_categories"] = 5
        qs = [BASE, *CANDIDATES]
        self.assertEqual(decide(cfg, state, qs, products, now=SUNDAY)["action"],
                         "candidate_pool_exhausted")


if __name__ == "__main__":
    unittest.main()
