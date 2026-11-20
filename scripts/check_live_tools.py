#!/usr/bin/env python3
"""Read-only verification of the actually deployed free tools on GitHub Pages.

Checks the public HTTP payload, never GitHub source or the un-deployed docs dir.
No credentials, product data, affiliate URLs, analytics API or writes to Pages.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE_URL = "https://noranamani.github.io/noranamani-gadget-deals"
FILES = ("index.html", "sim-optimizer.html", "product-selector.html", "sitemap.xml")
MAX_BODY_BYTES = 2_000_000


@dataclass(frozen=True)
class Response:
    status: int
    mime: str
    body: str


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch_public(name: str) -> Response:
    if name not in FILES:
        raise ValueError("FILE_NOT_ALLOWED")
    url = SITE_URL + ("/" if name == "index.html" else "/" + name)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "noranamani-public-tool-readiness/1.0",
                 "Accept": "text/html,application/xml,text/xml"},
        method="GET",
    )
    opener = urllib.request.build_opener(
        NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(request, timeout=12) as response:
            data = response.read(MAX_BODY_BYTES + 1)
            if len(data) > MAX_BODY_BYTES:
                return Response(413, "", "")
            try:
                body = data.decode("utf-8")
            except UnicodeDecodeError:
                return Response(422, "", "")
            return Response(response.status,
                            response.headers.get("Content-Type", "").split(";")[0].strip().lower(),
                            body)
    except urllib.error.HTTPError as exc:
        return Response(exc.code, "", "")


def inspect_page(name: str, response: Response, measurement_id: str) -> str:
    """Fixed reason codes only. Do not reflect HTTP response bodies to logs."""
    if response.status != 200:
        return "HTTP_" + str(response.status) if 100 <= response.status <= 599 else "HTTP_UNEXPECTED"
    if len(response.body) < 300:
        return "BODY_TOO_SMALL"
    if name == "sitemap.xml":
        if response.mime not in ("application/xml", "text/xml"):
            return "SITEMAP_MIME_INVALID"
        for tool in FILES[1:3]:
            if SITE_URL + "/" + tool not in response.body:
                return "SITEMAP_TOOL_MISSING"
        return "READY"
    if response.mime not in ("text/html", "application/xhtml+xml"):
        return "HTML_MIME_INVALID"
    expected_canonical = SITE_URL + "/" + name
    if '<link rel="canonical" href="' + expected_canonical + '">' not in response.body:
        return "CANONICAL_MISMATCH"
    if response.body.count("https://www.googletagmanager.com/gtag/js?id=" + measurement_id) != 1:
        return "GA4_SCRIPT_MISSING_OR_DUPLICATE"
    if response.body.count('gtag("config","' + measurement_id + '")') != 1:
        return "GA4_CONFIG_MISSING_OR_DUPLICATE"
    if 'window.gtag("event", "affiliate_click"' not in response.body:
        return "AFFILIATE_HANDLER_MISSING"
    if "楽天アフィリエイト" not in response.body:
        return "AFFILIATE_DISCLOSURE_MISSING"
    if name == "index.html":
        if ('href="sim-optimizer.html"' not in response.body or
                'href="product-selector.html"' not in response.body):
            return "HOME_TOOL_LINK_MISSING"
    if name == "sim-optimizer.html":
        if not all(marker in response.body for marker in (
                'id="sim-tool"', 'id="sim-count"', 'id="sim-best"',
                'id="sim-a-annual"', 'id="sim-b-annual"', 'function calculate(')):
            return "SIM_TOOL_CONTROLS_MISSING"
    if name == "product-selector.html":
        if not all(marker in response.body for marker in (
                'id="selector-grid"', 'id="selector-category"',
                'id="selector-price"', 'id="selector-reset"',
                'function choose(')):
            return "SELECTOR_CONTROLS_MISSING"
    return "READY"


def inspect_site(get, measurement_id: str) -> dict[str, str]:
    if not re.fullmatch(r"G-[A-Z0-9]{6,16}", measurement_id):
        raise ValueError("GA4_ID_INVALID")
    result = {}
    for name in FILES:
        try:
            result[name] = inspect_page(name, get(name), measurement_id)
        except (OSError, TimeoutError, ValueError, urllib.error.URLError):
            result[name] = "HTTP_UNAVAILABLE"
    return result


def write_summary(report: dict[str, str], ready: bool) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    lines = ["## Public free-tools verification",
             "Result: **" + ("LIVE_READY" if ready else "LIVE_PENDING") + "**",
             "",
             "| Public path | Result |", "|---|---|"]
    for name, code in report.items():
        lines.append("| " + name + " | " + code + " |")
    lines.extend(["", "Observed via public HTTPS, not inferred from CI or deploy job status.",
                  "No deployment or API write was performed."])
    with open(target, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--observe", action="store_true",
                      help="Report pending live pages without failing the PR CI")
    mode.add_argument("--require", action="store_true",
                      help="Fail the deployment verification if pages stay incomplete")
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--interval", type=int, default=0)
    args = parser.parse_args()
    if not 1 <= args.attempts <= 8 or not 0 <= args.interval <= 30:
        print("LIVE_TOOL_STATUS: CONFIG_INVALID")
        return 2
    try:
        config = json.loads((ROOT / "config" / "analytics.json").read_text(encoding="utf-8"))
        site = json.loads((ROOT / "config" / "site.json").read_text(encoding="utf-8"))
        if str(site.get("site_url") or "").rstrip("/") != SITE_URL:
            raise ValueError("SITE_URL_MISMATCH")
        measurement_id = str(config.get("ga4_measurement_id") or "")
        if not re.fullmatch(r"G-[A-Z0-9]{6,16}", measurement_id):
            raise ValueError("GA4_ID_INVALID")
    except (OSError, ValueError, TypeError, AttributeError):
        print("LIVE_TOOL_STATUS: CONFIG_INVALID")
        return 2

    result = {}
    for attempt in range(args.attempts):
        result = inspect_site(fetch_public, measurement_id)
        if all(code == "READY" for code in result.values()):
            break
        if attempt + 1 < args.attempts:
            time.sleep(args.interval)
    ready = all(code == "READY" for code in result.values())
    for name, code in result.items():
        print(f"LIVE_TOOL_CHECK: {name} / {code}")
    print("LIVE_TOOL_STATUS: " + ("READY" if ready else "PENDING"))
    write_summary(result, ready)
    return 0 if args.observe or ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
