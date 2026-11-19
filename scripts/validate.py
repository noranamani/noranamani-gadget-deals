#!/usr/bin/env python3
from __future__ import annotations

import json
import re

from affiliate_quality import displayable, recommendable
from affiliate_site_audit import audit_affiliate_links
from build_site import comparison_price_is_uncertain, item_matches_comparison
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
    check_clock = datetime.now(timezone.utc)
    today_jst = check_clock.astimezone(timezone(timedelta(hours=9))).date()
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
        # Match build_site.py exactly; otherwise a deliberately withheld
        # unsafe comparison page could be misdiagnosed as a missing page.
        matches = [item for item in parent.get("items", [])
                   if displayable(item) and item_matches_comparison(item, spec)]
        comparable_items = [item for item in matches
                            if recommendable(item, check_clock)
                            and not comparison_price_is_uncertain(item, spec)]
        activate_date = rollout_start + timedelta(days=int(spec.get("activateAfterDays") or 0))
        if (
            today_jst >= activate_date
            and len(matches) >= int(spec.get("minProducts") or 5)
            and len(comparable_items) >= int(spec.get("minComparableProducts") or 0)
        ):
            comparison_pages.append(DOCS / f"comparison-{spec['id']}.html")

    rendered_pages = [DOCS / "index.html", DOCS / "categories.html",
                      DOCS / "comparisons.html", DOCS / "price-drops.html",
                      DOCS / "deals.html", DOCS / "methodology.html",
                      DOCS / "privacy.html", *category_pages, *comparison_pages]
    site_url = str(load(CONFIG / "site.json").get("site_url") or "").rstrip("/")
    for path in rendered_pages:
        if not path.exists() or path.stat().st_size < 300:
            fail("RENDERED_PAGE_MISSING_OR_TOO_SMALL")
        text = path.read_text(encoding="utf-8")
        if "楽天アフィリエイト" not in text:
            fail("AFFILIATE_DISCLOSURE_MISSING")
        # Canonical URLs are self-referential, absolute and match sitemap
        # URLs; this prevents conflicting SEO signals on category pages.
        if site_url:
            expected = site_url + "/" + path.name
            if f'<link rel="canonical" href="{expected}">' not in text:
                fail("CANONICAL_URL_MISMATCH")

    # Verify original API-returned affiliate URLs survived HTML escaping and
    # every outbound link retains sponsored attributes and product mapping.
    try:
        audit = audit_affiliate_links(products, rendered_pages)
    except Exception as exc:
        if isinstance(exc, SystemExit):
            raise
        fail("AFFILIATE_LINK_AUDIT_FAILED")

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
    print("validation ok: pages=%d affiliate_links=%d"
          % (audit["pagesChecked"], audit["linksChecked"]))


if __name__ == "__main__":
    main()
