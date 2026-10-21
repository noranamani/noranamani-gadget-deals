#!/usr/bin/env python3
from __future__ import annotations

import json
import copy
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
QUERIES = CONFIG_DIR / "queries.json"
SITE = CONFIG_DIR / "site.json"
PRODUCTS = DATA_DIR / "products.json"
HISTORY = DATA_DIR / "history.json"
ENDPOINT = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701"


def unwrap_items(data: dict) -> list[dict]:
    rows = data.get("items", []) or data.get("Items", []) or []
    out = []
    for row in rows:
        if isinstance(row, dict) and "Item" in row and isinstance(row["Item"], dict):
            out.append(row["Item"])
        elif isinstance(row, dict):
            out.append(row)
    return out


def first_image(item: dict) -> str:
    for key in ("mediumImageUrls", "smallImageUrls"):
        rows = item.get(key) or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, str) and row:
                return row
            if isinstance(row, dict):
                value = row.get("imageUrl") or row.get("imageUrlHttps")
                if value:
                    return value
    return ""


def percentile_rank(values: list[int], value: int) -> float:
    if not values:
        return 0.5
    return sum(1 for x in values if x <= value) / len(values)


def comparison_score(item: dict, category_prices: list[int], drop_pct: float | None) -> float:
    rating = float(item.get("reviewAverage") or 0)
    reviews = int(item.get("reviewCount") or 0)
    price = int(item.get("itemPrice") or 0)
    postage = int(item.get("postageFlag", 1))
    point_rate = int(item.get("pointRate") or 1)

    rating_score = min(max(rating / 5.0, 0), 1) * 30
    review_score = min(math.log10(reviews + 1) / 4.0, 1) * 20
    price_rank = percentile_rank(sorted(category_prices), price)
    price_score = (1 - price_rank) * 20
    shipping_score = 5 if postage == 0 else 0
    point_score = min(max(point_rate - 1, 0), 5)
    history_score = min(max(drop_pct or 0, 0), 20)
    return round(rating_score + review_score + price_score + shipping_score + point_score + history_score, 1)


def price_metrics(item_code: str, current_price: int, history: dict, min_samples: int) -> dict:
    rows = history.get(item_code, [])
    prices = [int(x.get("price") or 0) for x in rows if int(x.get("price") or 0) > 0]
    recent = prices[-30:]
    required_previous = max(1, min_samples - 1)
    baseline = statistics.median(recent) if len(recent) >= required_previous else None
    previous_min = min(recent) if recent else None
    drop_pct = round((baseline - current_price) / baseline * 100, 1) if baseline else None
    return {
        "samples": len(prices) + 1,
        "baseline": round(baseline) if baseline else None,
        "previousMin": previous_min,
        "dropPct": drop_pct,
        "isNewLow": bool(previous_min and current_price < previous_min),
    }


def append_history(item_code: str, price: int, history: dict, day: str, max_days: int) -> None:
    from pipeline_common import PipelineError
    rows = history.setdefault(item_code, [])
    if rows and rows[-1]["date"] == day:
        if rows[-1]["price"] != price:
            raise PipelineError("SAME_DAY_PRICE_CONFLICT")
        return
    if rows and rows[-1]["date"] > day:
        raise PipelineError("HISTORY_DATE_REGRESSION")
    rows.append({"date": day, "price": price})
    # Never truncate historical records; archival requires an explicit retention policy.


def deal_flags(metrics: dict, review_count: int, cfg: dict) -> tuple[bool, str]:
    drop = metrics.get("dropPct")
    samples = int(metrics.get("samples") or 0)
    if drop is None or samples < int(cfg["deal_min_samples"]):
        return False, "none"
    if drop >= float(cfg["strong_deal_drop_pct"]):
        return True, "strong"
    if drop >= float(cfg["deal_drop_pct"]):
        return True, "deal"
    if metrics.get("isNewLow") and drop >= float(cfg["new_low_min_drop_pct"]) and review_count >= int(cfg["deal_min_reviews"]):
        return True, "watch"
    return False, "none"


def build_products(cfg: dict, queries: list, history: dict, requester, now=None):
    """Pure generation: caller owns validation, transport, transaction and publication."""
    history = copy.deepcopy(history)
    now = now or datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    updated_at = now.isoformat().replace("+00:00", "Z")
    metric_history = {code: [row for row in rows if row["date"] != day]
                      for code, rows in history.items()}
    max_days = int(cfg.get("history_days", 120))
    min_samples = int(cfg.get("deal_min_samples", 5))

    categories = []
    all_items: list[dict] = []

    for q in queries:
        merged: dict[str, dict] = {}
        for sort in ("-reviewCount", "+itemPrice"):
            params = {
                "format": "json",
                "formatVersion": 2,
                "keyword": q["keyword"],
                "hits": 30,
                "page": 1,
                "sort": sort,
                "availability": 1,
                "imageFlag": 1,
                "hasReviewFlag": 1,
                "minPrice": q.get("minPrice", 1),
                "maxPrice": q.get("maxPrice", 99999999),
                "elements": ",".join([
                    "itemName", "itemCode", "itemPrice", "itemUrl", "affiliateUrl",
                    "mediumImageUrls", "smallImageUrls", "availability", "postageFlag",
                    "reviewCount", "reviewAverage", "pointRate", "startTime", "endTime", "shopName"
                ]),
            }
            data = requester(params)
            for item in unwrap_items(data):
                code = str(item.get("itemCode") or "")
                if code:
                    merged[code] = item

        raw_items = list(merged.values())
        prices = [int(x.get("itemPrice") or 0) for x in raw_items if int(x.get("itemPrice") or 0) > 0]
        items = []

        for item in raw_items:
            code = str(item.get("itemCode") or "")
            price = int(item.get("itemPrice") or 0)
            if not code or price <= 0:
                continue
            review_count = int(item.get("reviewCount") or 0)
            metrics = price_metrics(code, price, metric_history, min_samples)
            is_deal, deal_level = deal_flags(metrics, review_count, cfg)
            obj = {
                "categoryId": q["id"],
                "category": q["name"],
                "name": str(item.get("itemName") or ""),
                "code": code,
                "price": price,
                "image": first_image(item),
                "url": str(item.get("affiliateUrl") or ""),
                "reviewAverage": float(item.get("reviewAverage") or 0),
                "reviewCount": review_count,
                "postageIncluded": int(item.get("postageFlag", 1)) == 0,
                "pointRate": int(item.get("pointRate") or 1),
                "shopName": str(item.get("shopName") or ""),
                "saleStart": str(item.get("startTime") or ""),
                "saleEnd": str(item.get("endTime") or ""),
                "historySamples": metrics["samples"],
                "baselinePrice": metrics["baseline"],
                "previousMin": metrics["previousMin"],
                "dropPct": metrics["dropPct"],
                "isNewLow": metrics["isNewLow"],
                "isDeal": is_deal,
                "dealLevel": deal_level,
            }
            obj["score"] = comparison_score(item, prices, metrics["dropPct"])
            items.append(obj)
            all_items.append(obj)
            append_history(code, price, history, day, max_days)

        items.sort(key=lambda x: (-x["score"], x["price"], -x["reviewCount"]))
        categories.append({"id": q["id"], "name": q["name"], "keyword": q["keyword"], "items": items[:18]})

    deal_map: dict[str, dict] = {}
    for item in all_items:
        if item["isDeal"]:
            old = deal_map.get(item["code"])
            if old is None or (item.get("dropPct") or 0, item["score"]) > (old.get("dropPct") or 0, old["score"]):
                deal_map[item["code"]] = item
    deals = list(deal_map.values())
    deals.sort(key=lambda x: (-(x.get("dropPct") or 0), -x["score"], -x["reviewCount"]))

    tracked = sum(1 for rows in history.values() if rows)
    out = {
        "updatedAt": updated_at,
        "categories": categories,
        "deals": deals[:40],
        "stats": {"products": len(all_items), "tracked": tracked, "deals": len(deals)},
        "methodology": {
            "dealMinSamples": min_samples,
            "dealDropPct": cfg.get("deal_drop_pct", 10),
            "strongDealDropPct": cfg.get("strong_deal_drop_pct", 20),
            "historyDays": max_days,
        },
    }
    return out, history


if __name__ == "__main__":
    from collection_pipeline import main
    raise SystemExit(main())
