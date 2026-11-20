#!/usr/bin/env python3
"""Independently monitor both public free tools, even if price publication stops.

This does NOT assert that today's prices were published. It only checks public
HTTPS tool availability and safe, observable page integration. It is read-only,
does not call Rakuten/GA4 APIs and never prints remote HTML or secrets.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
from pathlib import Path

from check_live_tools import FILES, SITE_URL, fetch_public, inspect_page

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ("sim-optimizer.html", "product-selector.html")
REPORT_FILES = ("index.html",) + TOOLS + ("sitemap.xml",)


def inspect_availability(get, measurement_id: str) -> dict[str, str]:
    """Allow the intentionally historic home, but require live working tools.

    The historic 11/19 homepage intentionally lacks modern canonical/GA4
    metadata. We still insist that the homepage links to both real tools.
    Full normal-publication audits remain in check_live_tools.py --require.
    """
    if not re.fullmatch(r"G-[A-Z0-9]{6,16}", measurement_id):
        raise ValueError("GA4_ID_INVALID")
    report = {}
    for name in REPORT_FILES:
        try:
            response = get(name)
            if name == "index.html":
                if response.status != 200:
                    reason = "HOME_NOT_200"
                elif response.mime not in ("text/html", "application/xhtml+xml"):
                    reason = "HOME_MIME_INVALID"
                elif len(response.body) < 300:
                    reason = "HOME_BODY_INVALID"
                elif not all('href="' + tool + '"' in response.body for tool in TOOLS):
                    reason = "HOME_LINK_MISSING"
                else:
                    reason = "READY"
            else:
                reason = inspect_page(name, response, measurement_id)
            report[name] = reason
        except (OSError, TimeoutError, ValueError, urllib.error.URLError):
            report[name] = "HTTP_UNAVAILABLE"
    return report


def config() -> str:
    analytics = json.loads((ROOT / "config" / "analytics.json").read_text(encoding="utf-8"))
    site = json.loads((ROOT / "config" / "site.json").read_text(encoding="utf-8"))
    measurement_id = str(analytics.get("ga4_measurement_id") or "")
    if (str(site.get("site_url") or "").rstrip("/") != SITE_URL
            or not re.fullmatch(r"G-[A-Z0-9]{6,16}", measurement_id)):
        raise ValueError("WATCH_CONFIG_INVALID")
    return measurement_id


def check_with_retry(get, measurement_id: str, attempts: int = 3,
                     interval: float = 20, pause=time.sleep) -> dict[str, str]:
    if not 1 <= attempts <= 8 or not 0 <= interval <= 30:
        raise ValueError("WATCH_RETRY_CONFIG_INVALID")
    report = {}
    for attempt in range(attempts):
        report = inspect_availability(get, measurement_id)
        if all(code == "READY" for code in report.values()):
            break
        if attempt + 1 < attempts:
            pause(interval)
    return report


def publish_report(report: dict[str, str]) -> bool:
    healthy = len(report) == len(REPORT_FILES) and all(
        report.get(name) == "READY" for name in REPORT_FILES)
    # The output contains only static filenames and internally defined reasons.
    for name in REPORT_FILES:
        print(f"TOOL_HEALTH_CHECK: {name} / {report.get(name, 'MISSING')}")
    print("TOOL_HEALTH_STATUS: " + ("READY" if healthy else "FAILED"))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        lines = ["## Independent free tool availability",
                 "Status: " + ("READY" if healthy else "FAILED"),
                 "The historical product snapshot is allowed; a fresh price publication is NOT inferred.",
                 "", "| Public path | Result |", "|---|---|"]
        lines += [f"| {name} | {report.get(name, 'MISSING')} |" for name in REPORT_FILES]
        with open(summary, "a", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n")
    return healthy


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--interval", type=int, default=20)
    args = parser.parse_args()
    try:
        mid = config()
        result = check_with_retry(fetch_public, mid, args.attempts, args.interval)
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        print("TOOL_HEALTH_STATUS: CONFIG_OR_IO_ERROR")
        return 2
    return 0 if publish_report(result) else 1


if __name__ == "__main__":
    raise SystemExit(main())
