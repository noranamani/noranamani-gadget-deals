"""Read-only post-deploy checks for the NARROW one-time static recovery.

This does NOT certify a fresh daily price update. It verifies restored 404
tool pages and preservation of the archived public product snapshot only.
"""
from __future__ import annotations

import os
import ssl
import time
import urllib.error
import urllib.request
from hashlib import sha256

from check_live_tools import (SITE_URL, fetch_public, inspect_page, FILES)
from recover_static_tool_pages import OLD_SNAPSHOT_LABEL

FEED_SHA = "59cfebb3fb2d05b9a8facb70f5e4ba0dab4d67237ad1a75a29f8dfb703fae7c6"
CATEGORY_SHA = "89bf9d63b4582f6e5b4b053ed079a9a559b5bb320142aaa2408ffa4f94428638"
GA4_ID = "G-1273VEC9JB"


def _fetch_preserved(path: str) -> bytes:
    if path not in ("feed.json", "category-usb-charger.html"):
        raise ValueError("PATH_NOT_ALLOWED")
    request = urllib.request.Request(
        SITE_URL + "/" + path, method="GET",
        headers={"User-Agent": "static-tool-recovery-verify/1.0"})
    with urllib.request.urlopen(request, timeout=15, context=ssl.create_default_context()) as response:
        if response.status != 200:
            raise ValueError("PRESERVED_FILE_HTTP_ERROR")
        data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise ValueError("BODY_TOO_BIG")
        return data


def check() -> dict:
    report = {}
    for name in FILES:
        try:
            response = fetch_public(name)
            if name == "index.html":
                report[name] = (
                    "READY" if response.status == 200
                    and all('href="' + path + '"' in response.body for path in FILES[1:3])
                    and OLD_SNAPSHOT_LABEL.split("年")[0] + "/11/19" not in response.body
                    else "HOME_TOOL_LINKS_MISSING")
                # The archived index deliberately predates GA4/canonical;
                # don't misrepresent it as a normal new-price publication.
            else:
                report[name] = inspect_page(name, response, GA4_ID)
        except Exception:
            report[name] = "HTTP_UNAVAILABLE"
    for name, digest in (("feed.json", FEED_SHA),
                         ("category-usb-charger.html", CATEGORY_SHA)):
        try:
            report[name] = ("READY" if sha256(_fetch_preserved(name)).hexdigest() == digest
                            else "ORIGINAL_PRODUCT_PAGE_CHANGED")
        except Exception:
            report[name] = "HTTP_UNAVAILABLE"
    return report


def main() -> int:
    report = {}
    for attempt in range(5):
        report = check()
        if all(v == "READY" for v in report.values()):
            break
        if attempt < 4:
            time.sleep(20)
    ok = all(v == "READY" for v in report.values())
    for name, result in report.items():
        print("STATIC_RECOVERY_HTTP: " + name + " / " + result)
    print("STATIC_RECOVERY_STATUS: " + ("READY" if ok else "PENDING"))
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("## One-time static tool recovery (not a new-price release)\n")
            for name, result in report.items():
                f.write("- " + name + ": " + result + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
