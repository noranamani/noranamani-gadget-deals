#!/usr/bin/env python3
"""Smoke-test cloud Chromium and optional unauthenticated note reachability.

No account credentials, cookies, notes, drafts, or publishing are used.
"""
from __future__ import annotations

import argparse
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def smoke(check_note: bool) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto("about:blank")
            page.set_content("<h1>Browser ready</h1>")
            if page.locator("h1").text_content() != "Browser ready":
                raise RuntimeError("CHROMIUM_RENDER_FAILED")
            print("CHROMIUM_READY: ephemeral browser launches and renders")
            if check_note:
                response = page.goto(
                    "https://note.com/new",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                status = response.status if response is not None else None
                hostname = urlparse(page.url).hostname
                # Report only the hostname: never print OAuth codes or URL parameters.
                print(f"NOTE_UNAUTHENTICATED_CHECK: host={hostname} http_status={status}")
                if hostname not in {"note.com", "www.note.com"}:
                    raise RuntimeError("NOTE_UNEXPECTED_REDIRECT")
                if status in {401, 403, 429} or (status is not None and status >= 500):
                    raise RuntimeError("NOTE_ACCESS_UNAVAILABLE")
                print("NOTE_REACHABLE: no login or posting attempted")
        finally:
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-note", action="store_true")
    args = parser.parse_args()
    try:
        smoke(args.check_note)
        return 0
    except (OSError, RuntimeError, PlaywrightTimeoutError) as exc:
        print(f"CLOUD_BROWSER_SMOKE_FAILED: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
