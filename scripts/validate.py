#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DOCS = ROOT / "docs"
CONFIG = ROOT / "config"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fail(message):
    raise SystemExit("VALIDATION ERROR: " + message)


def item_matches_comparison(item, spec):
    text = str(item.get("name") or "").lower()
    for group in spec.get("includeAnyGroups", []):
        terms = [str(term).lower() for term in group if str(term).strip()]
        if terms and not any(term in text for term in terms):
            return False
    for term in spec.get("excludeAny", []):
        if str(term).lower() in text:
            return False
    return True


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

    comparison_config = load(CONFIG / "comparisons.json")
    category_by_id = {str(cat.get("id") or ""): cat for cat in products.get("categories", [])}
    today_jst = datetime.now(timezone(timedelta(hours=9))).date()
    rollout_text = str(comparison_config.get("rolloutStartDate") or today_jst.isoformat())
    try:
        rollout_start = datetime.fromisoformat(rollout_text).date()
    except ValueError:
        rollout_start = today_jst
    comparison_pages = []
    for spec in comparison_config.get("pages", []):
        parent = category_by_id.get(str(spec.get("parentCategory") or ""))
        if not parent:
            continue
        matches = [item for item in parent.get("items", []) if item_matches_comparison(item, spec)]
        activate_date = rollout_start + timedelta(days=int(spec.get("activateAfterDays") or 0))
        if today_jst >= activate_date and len(matches) >= int(spec.get("minProducts") or 5):
            comparison_pages.append(DOCS / f"comparison-{spec['id']}.html")

    for path in [DOCS / "index.html", DOCS / "categories.html", DOCS / "comparisons.html", DOCS / "price-drops.html", DOCS / "deals.html", DOCS / "methodology.html", DOCS / "privacy.html", *category_pages, *comparison_pages]:
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
