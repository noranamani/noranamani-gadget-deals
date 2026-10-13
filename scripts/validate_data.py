#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def fail(message: str) -> None:
    raise SystemExit("DATA VALIDATION ERROR: " + message)


def main() -> None:
    products = load(DATA / "products.json")
    history = load(DATA / "history.json")

    if not products.get("updatedAt"):
        fail("products.updatedAt is missing")
    categories = products.get("categories")
    if not isinstance(categories, list) or not categories:
        fail("products.categories must be a non-empty list")
    if not isinstance(history, dict):
        fail("history must be an object")

    total_items = 0
    for category in categories:
        items = category.get("items")
        if not isinstance(items, list):
            fail(f"category items must be a list: {category.get('id')}")
        total_items += len(items)

    if total_items <= 0:
        fail("no products were fetched")

    print(f"data validation ok: categories={len(categories)} items={total_items}")


if __name__ == "__main__":
    main()
