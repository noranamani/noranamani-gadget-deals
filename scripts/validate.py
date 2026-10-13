#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DOCS = ROOT / "docs"
CONFIG = ROOT / "config"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fail(message):
    raise SystemExit("VALIDATION ERROR: " + message)


def main():
    products = load(DATA / "products.json")
    editorial = load(DATA / "editorial.json")
    if not isinstance(products.get("categories"), list):
        fail("products.categories must be a list")
    if not isinstance(editorial.get("featuredDeals"), list) or not isinstance(editorial.get("categoryNotes"), list):
        fail("editorial schema is invalid")

    known_codes = {item.get("code") for cat in products.get("categories", []) for item in cat.get("items", [])}
    known_categories = {cat.get("id") for cat in products.get("categories", [])}
    for row in editorial.get("featuredDeals", []):
        if row.get("code") not in known_codes:
            fail(f"unknown featured deal code: {row.get('code')}")
    for row in editorial.get("categoryNotes", []):
        if row.get("categoryId") not in known_categories:
            fail(f"unknown categoryId: {row.get('categoryId')}")
        if row.get("topPickCode") and row.get("topPickCode") not in known_codes:
            fail(f"unknown topPickCode: {row.get('topPickCode')}")

    public_editorial = json.dumps(editorial, ensure_ascii=False)
    forbidden_editorial_terms = ["Codex", "OpenAI", "生成AI", "人工知能", "LLM"]
    for term in forbidden_editorial_terms:
        if term in public_editorial:
            fail(f"public editorial must not mention generation method: {term}")
    if re.search(r"(?<![A-Za-z])AI(?![A-Za-z])", public_editorial, flags=re.IGNORECASE):
        fail("public editorial must not mention generation method: AI")

    queries = load(CONFIG / "queries.json")
    category_pages = [DOCS / f"category-{row['id']}.html" for row in queries]
    for path in [DOCS / "index.html", DOCS / "categories.html", DOCS / "deals.html", DOCS / "methodology.html", DOCS / "privacy.html", *category_pages]:
        if not path.exists() or path.stat().st_size < 300:
            fail(f"missing or too small: {path}")
        text = path.read_text(encoding="utf-8")
        if "楽天アフィリエイト" not in text:
            fail(f"affiliate disclosure missing: {path}")

    secret_patterns = [r"RAKUTEN_ACCESS_KEY\s*=", r"GITHUB_WRITE_TOKEN\s*=", r"ghp_[A-Za-z0-9]+", r"github_pat_[A-Za-z0-9_]+"]
    for path in list(DATA.glob("*.json")) + list(DOCS.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in secret_patterns:
            if re.search(pattern, text):
                fail(f"possible secret leaked into {path}")
    print("validation ok")


if __name__ == "__main__":
    main()
