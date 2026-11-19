"""Fail-closed QA of rendered affiliate links, without API calls or URL disclosure.

Checks the actual site HTML just before Pages publication. The upstream
authenticated Rakuten response and output policy remain the source of truth;
this module only ensures static markup has not lost or swapped an affiliate
tracking URL during generation.
"""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from pipeline_common import PipelineError


class _OutboundParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        row = dict(attrs)
        href = row.get("href") or ""
        if "data-affiliate-code" in row or "hb.afl.rakuten.co.jp" in href:
            self.links.append(row)


def audit_affiliate_links(products: dict, pages: list[Path]) -> dict:
    """Check exact source URL equality and sponsored semantics in rendered pages.

    Never report identifiers, tracking URLs, or page HTML in an error. This is
    a presentation-only check: it does not claim or test that a sale occurred.
    """
    if not isinstance(products, dict) or not isinstance(pages, list):
        raise PipelineError("AFFILIATE_AUDIT_INPUT_INVALID")

    allowed = set()
    for cat in products.get("categories", []):
        for item in cat.get("items", []):
            code = str(item.get("code") or "")
            category = str(item.get("categoryId") or "")
            url = str(item.get("url") or "")
            if code and category and url:
                allowed.add((code, category, url))

    inspected_pages = 0
    inspected_links = 0
    for page in pages:
        path = Path(page)
        if not path.is_file():
            raise PipelineError("AFFILIATE_RENDERED_PAGE_MISSING")
        parser = _OutboundParser()
        try:
            parser.feed(path.read_text(encoding="utf-8"))
            parser.close()
        except (OSError, UnicodeError, ValueError):
            raise PipelineError("AFFILIATE_RENDERED_HTML_INVALID") from None
        inspected_pages += 1
        for anchor in parser.links:
            href = anchor.get("href") or ""
            code = anchor.get("data-affiliate-code") or ""
            category = anchor.get("data-affiliate-category") or ""
            placement = anchor.get("data-affiliate-placement") or ""
            rel = set((anchor.get("rel") or "").split())
            try:
                parts = urlsplit(href)
                valid_host = (parts.scheme == "https"
                              and parts.hostname == "hb.afl.rakuten.co.jp"
                              and parts.port in (None, 443)
                              and parts.path.startswith("/hgc/")
                              and not parts.username and not parts.password
                              and not parts.fragment)
            except ValueError:
                valid_host = False
            if not (valid_host and (code, category, href) in allowed
                    and placement and not any(c.isspace() for c in placement)
                    and {"sponsored", "nofollow", "noopener"} <= rel
                    and anchor.get("target") == "_blank"):
                raise PipelineError("AFFILIATE_RENDERED_LINK_INVALID")
            inspected_links += 1
    return {"pagesChecked": inspected_pages, "linksChecked": inspected_links}
