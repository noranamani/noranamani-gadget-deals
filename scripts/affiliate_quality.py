"""Read-only display quality gate for affiliate listings.

This is intentionally separate from the Windows collection and immutable price
history. Uncertain variants can be shown with warnings, but never ranked as
verified deals. API URLs are never built or modified here.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta

from category_relevance import is_relevant

JST = timezone(timedelta(hours=9))
SIZE = re.compile(r"(?<![a-z0-9])([0-9]+(?:\.[0-9]+)?)\s*(gb|tb)\b", re.I)
WATTS = re.compile(r"(?<![a-z0-9])([0-9]{1,3})\s*w\b", re.I)
CAPACITY = re.compile(r"(?<![a-z0-9])([0-9]{3,6})\s*mah\b", re.I)
CHOICES = re.compile(r"容量選択|容量を選|タイプ選択|サイズ選択|選べる容量|各容量", re.I)
PROMOTION = re.compile(r"クーポン|セール|期間限定|本日限定|週末限定|ブラックフライデー|\d{1,2}/\d{1,2}\s*まで", re.I)


def variant_price_uncertain(item: dict) -> bool:
    """Multiple different specifications in a title do not prove a variant price."""
    category = str(item.get("categoryId") or "")
    title = str(item.get("name") or "").casefold()
    if category == "microsd":
        return bool(CHOICES.search(title) or len(set(SIZE.findall(title))) > 1)
    if category == "power-bank":
        return bool(CHOICES.search(title) or len(set(CAPACITY.findall(title))) > 1)
    if category in ("usb-charger", "car-charger"):
        return bool(CHOICES.search(title) or len(set(WATTS.findall(title))) > 1)
    return False


def _read_api_time(value) -> datetime | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        # Rakuten Search API startTime/endTime are local listing timestamps.
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M").replace(tzinfo=JST)
    except (TypeError, ValueError):
        raise ValueError("INVALID_LISTING_PERIOD") from None


def listing_period(item: dict, now: datetime) -> str:
    """Do not equate an API listing period with a guaranteed sale/coupon period."""
    if now.tzinfo is None:
        raise ValueError("NAIVE_CLOCK")
    try:
        start = _read_api_time(item.get("saleStart"))
        end = _read_api_time(item.get("saleEnd"))
    except ValueError:
        return "unverified"
    if start is not None and end is not None and end < start:
        return "unverified"
    clock = now.astimezone(JST)
    if start is not None and clock < start:
        return "upcoming"
    if end is not None and clock >= end:
        return "ended"
    return "active_or_unknown"


def displayable(item: dict) -> bool:
    return is_relevant(str(item.get("categoryId") or ""), str(item.get("name") or ""))


def recommendable(item: dict, now: datetime) -> bool:
    return (displayable(item) and not variant_price_uncertain(item)
            and listing_period(item, now) == "active_or_unknown")


def merchant_promotion_claim(item: dict) -> bool:
    return bool(PROMOTION.search(str(item.get("name") or "")))
