"""One-time static-only GitHub Pages recovery of two 404 free tools.

Source is the immutable artifact from the LAST verified Pages publication.
Preserve every previously published product page/price/affiliate link byte-for-byte.
Never generate a new product snapshot, modify the collector, or extend its proof.
Any unexpected live change or artifact mismatch fails closed before deployment.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from xml.etree import ElementTree as ET

from build_site import layout
from product_selector import SELECTOR_JS, SELECTOR_STYLE
from sim_optimizer import SIM_STYLE, sim_page

SITE_URL = "https://noranamani.github.io/noranamani-gadget-deals"
SOURCE_COMMIT = "48f41584d3a55b1f27e96cc99edd5e94efc360b8"
INDEX_SHA256 = "70ff767e2646a1e6e08cdcf12dba65af06141d160095f2b8d8bae6fa0fa9b7ae"
SITEMAP_SHA256 = "96a9825f4d3679ca99ba2fc0d2650f99b638178cfae5a5fff4155c61d014b458"
OLD_SNAPSHOT_LABEL = "2026年11月19日06:10時点"
CARD = re.compile(r'<article class="product-card"[^>]*>.*?</article>', re.S)
TAG = re.compile(r'<[^>]*>')
CAT = re.compile(r'^[a-z0-9-]{1,80}$')
TOOLS = ("sim-optimizer.html", "product-selector.html")
ROOT = Path(__file__).resolve().parents[1]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_archive(source: Path) -> Path:
    """The artifact must be the exact already-publicly-published page generation."""
    manifest_file = source / "publication-manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if (manifest.get("schemaVersion") != 1
            or manifest.get("sourceCommit") != SOURCE_COMMIT
            or manifest.get("includeNote") is not False):
        raise ValueError("ARCHIVE_SOURCE_MISMATCH")
    hashes = manifest.get("sha256")
    if not isinstance(hashes, dict) or len(hashes) != 27:
        raise ValueError("ARCHIVE_MANIFEST_INVALID")
    docs = source / "docs"
    files = set()
    for path in docs.rglob("*"):
        if path.is_symlink():
            raise ValueError("ARCHIVE_SYMLINK_DENIED")
        if path.is_file():
            files.add(path.relative_to(source).as_posix())
    if files != set(hashes):
        raise ValueError("ARCHIVE_FILE_SET_MISMATCH")
    for rel, expected in hashes.items():
        if (not rel.startswith("docs/") or "../" in rel or "/./" in rel
                or not re.fullmatch("[0-9a-f]{64}", str(expected))
                or sha256((source / rel).read_bytes()) != expected):
            raise ValueError("ARCHIVE_DIGEST_MISMATCH")
    if (hashes.get("docs/index.html") != INDEX_SHA256
            or hashes.get("docs/sitemap.xml") != SITEMAP_SHA256):
        raise ValueError("ARCHIVE_PIN_MISMATCH")
    if any((docs / name).exists() for name in TOOLS):
        raise ValueError("ARCHIVE_ALREADY_CONTAINS_TOOLS")
    return docs


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch_live(path: str) -> bytes:
    if path not in ("index.html", "sitemap.xml"):
        raise ValueError("LIVE_PATH_NOT_ALLOWED")
    url = SITE_URL + ("/" if path == "index.html" else "/" + path)
    request = urllib.request.Request(url, method="GET",
                                     headers={"User-Agent":"static-tool-recovery/1.0"})
    opener = urllib.request.build_opener(
        NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    with opener.open(request, timeout=15) as response:
        if response.status != 200:
            raise ValueError("LIVE_SOURCE_NOT_200")
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("LIVE_SOURCE_TOO_LARGE")
    return raw


def require_public_match(fetch=fetch_live) -> None:
    # Before re-deploying any old page, verify it really IS the current live page.
    # If a different publication won the race, abort rather than overwrite it.
    for name, expected in (("index.html", INDEX_SHA256),
                           ("sitemap.xml", SITEMAP_SHA256)):
        if sha256(fetch(name)) != expected:
            raise ValueError("LIVE_HAS_NEWER_OR_DIFFERENT_PUBLICATION")


def safe_index_patch(page: str) -> str:
    marker = "</aside>"
    if page.count(marker) != 1 or any('href="' + name + '"' in page for name in TOOLS):
        raise ValueError("INDEX_STRUCTURE_CHANGED")
    links = (
        '<div class="side-nav-title">無料Webツール</div>'
        '<a href="sim-optimizer.html">通信費・格安SIM診断</a>'
        '<a href="product-selector.html">ガジェット選定・価格比較</a>'
    )
    # Make historical prices explicit; do not change product pages.
    result = page.replace(marker, links + marker, 1)
    main = '<main class="main-content">'
    if result.count(main) != 1:
        raise ValueError("INDEX_STRUCTURE_CHANGED")
    note = ('<section class="prose"><p><strong>無料ツールを臨時公開しました。</strong>'
            '商品価格・ランキングの最終取得は2026/11/19 06:10です。'
            '最新価格や在庫は販売ページでご確認ください。</p></section>')
    return result.replace(main, main + note, 1)


def safe_sitemap_patch(sitemap: str) -> str:
    if sitemap.count("</urlset>") != 1:
        raise ValueError("SITEMAP_STRUCTURE_CHANGED")
    for name in TOOLS:
        if SITE_URL + "/" + name in sitemap:
            raise ValueError("SITEMAP_ALREADY_CONTAINS_TOOL")
    extra = ''.join(
        '<url><loc>' + SITE_URL + '/' + name + '</loc></url>'
        for name in TOOLS)
    updated = sitemap.replace("</urlset>", extra + "</urlset>", 1)
    ET.fromstring(updated)
    return updated


def cards_from_archive(docs: Path, queries: list) -> tuple[str, int]:
    """Use ONLY historical already-public category data; add no outbound links."""
    selected, seen = [], set()
    for category in queries:
        cat_id = str(category.get("id") or "")
        if not CAT.fullmatch(cat_id):
            raise ValueError("CATEGORY_ID_INVALID")
        path = docs / ("category-" + cat_id + ".html")
        if not path.is_file():
            raise ValueError("ARCHIVE_CATEGORY_MISSING")
        page = path.read_text(encoding="utf-8")
        for raw in CARD.findall(page):
            start = raw.split(">", 1)[0]
            if 'data-variant="0"' not in start:
                continue
            numbers = {}
            for name in ("price", "score", "reviews", "drop"):
                signed = "-?" if name == "drop" else ""
                match = re.search(r'data-' + name + r'="(' + signed + r'[0-9]+(?:\.[0-9]+)?)"', start)
                if not match:
                    raise ValueError("CARD_FORMAT_INVALID")
                numbers[name] = match.group(1)
            if float(numbers["price"]) <= 0:
                continue
            title = re.search(r'<h3[^>]*>(.*?)</h3>', raw, re.S)
            if not title:
                raise ValueError("CARD_TITLE_MISSING")
            title = html.unescape(TAG.sub("", title.group(1))).strip()
            if not title:
                raise ValueError("CARD_TITLE_EMPTY")
            key = (cat_id, title, numbers["price"])
            if key in seen:
                continue
            seen.add(key)
            escaped = html.escape(title, quote=True)
            cat_title = html.escape(str(category.get("name") or cat_id), quote=True)
            attrs = ' '.join('data-' + k + '="' + v + '"' for k, v in numbers.items())
            selected.append(
                '<div class="selector-item" data-category="' + cat_id + '">'
                '<article class="product-card" ' + attrs + '>'
                '<div class="product-body"><div class="badges"><span class="badge">過去の掲載情報</span></div>'
                '<h3>' + escaped + '</h3><div class="price">過去の掲載価格 ¥' +
                format(int(float(numbers["price"])), ',') + '</div>'
                '<div class="meta">' + cat_title + ' / レビュー ' + numbers["reviews"] +
                '件 / 比較スコア ' + numbers["score"] + '</div>'
                '<p class="variant-note">最新価格・在庫は保証できません。購入前に公式販売ページでご確認ください。</p>'
                '<a class="cta" href="category-' + cat_id + '.html">既存のカテゴリ一覧を見る</a>'
                '</div></article></div>'
            )
    if not selected:
        raise ValueError("ARCHIVE_NO_COMPARABLE_CARDS")
    return '\n'.join(selected), len(selected)


def selector_recovery_page(docs: Path, queries: list) -> str:
    cards, count = cards_from_archive(docs, queries)
    choices = ''.join(
        '<option value="' + html.escape(q["id"], quote=True) + '">' +
        html.escape(str(q.get("name") or q["id"])) + '</option>'
        for q in queries
    )
    return (
        '<section class="hero compact"><p class="eyebrow">無料ツール</p>'
        '<h1>ガジェット選定・価格比較</h1>'
        '<p><strong>臨時版：</strong>既存サイトで公開済みの' + OLD_SNAPSHOT_LABEL +
        'の掲載価格を絞り込みます。最新価格・在庫・最安値を示すものではありません。'
        '商品データの次回正常更新後は自動的に通常版へ切り替わります。</p></section>'
        '<section class="sim-panel"><h2>絞り込み条件</h2><div class="sim-grid">'
        '<label class="sim-field">カテゴリ<select id="selector-category">'
        '<option value="all">すべてのカテゴリ</option>' + choices + '</select></label>'
        '<label class="sim-field">過去の掲載価格の上限（円）'
        '<input id="selector-price" type="number" min="0" max="10000000" placeholder="指定なし"></label>'
        '<label class="sim-field">最低レビュー件数<select id="selector-reviews">'
        '<option value="0">指定なし</option><option value="10">10件以上</option>'
        '<option value="50">50件以上</option><option value="100">100件以上</option>'
        '</select></label>'
        '<label class="sim-field">並び替え<select id="selector-order">'
        '<option value="score">比較スコア順</option><option value="price">過去掲載価格が安い順</option>'
        '<option value="reviews">レビュー件数順</option><option value="drop">値下がり率順</option>'
        '</select></label></div>'
        '<button type="button" id="selector-reset">条件をリセット</button></section>'
        '<p id="selector-count" aria-live="polite">過去の掲載候補 ' + str(count) + '件</p>'
        '<div id="selector-empty" hidden>条件に一致する商品はありません。</div>'
        '<section class="grid" id="selector-grid">' + cards + '</section>'
        '<p class="category-summary">リンク先は既存のカテゴリページです。新しい楽天アフィリエイトリンクは生成していません。</p>'
        '<script>' + SELECTOR_JS + '</script>'
    )


def recover_archive(source: Path, output: Path, fetch=fetch_live) -> dict:
    old_docs = validate_archive(source)
    require_public_match(fetch)
    if output.exists():
        raise ValueError("OUTPUT_MUST_NOT_EXIST")
    site = json.loads((ROOT / "config/site.json").read_text(encoding="utf-8"))
    queries = json.loads((ROOT / "config/queries.json").read_text(encoding="utf-8"))
    analytics = json.loads((ROOT / "config/analytics.json").read_text(encoding="utf-8"))
    if site.get("site_url", "").rstrip("/") != SITE_URL:
        raise ValueError("SITE_CONFIG_URL_MISMATCH")
    if not re.fullmatch(r'G-[A-Z0-9]{6,16}', str(analytics.get("ga4_measurement_id") or "")):
        raise ValueError("GA4_REQUIRED")
    if not isinstance(queries, list) or len(queries) < 8:
        raise ValueError("CATEGORIES_UNEXPECTED")
    # Old product pages remain byte-for-byte identical. Only the index,
    # sitemap and existing privacy declaration are minimally amended.
    shutil.copytree(old_docs, output, symlinks=False)
    home = safe_index_patch((output / "index.html").read_text(encoding="utf-8"))
    (output / "index.html").write_text(home, encoding="utf-8")
    sitemap = safe_sitemap_patch((output / "sitemap.xml").read_text(encoding="utf-8"))
    (output / "sitemap.xml").write_text(sitemap, encoding="utf-8")

    privacy = (output / "privacy.html").read_text(encoding="utf-8")
    if privacy.count("</main>") != 1:
        raise ValueError("PRIVACY_PAGE_STRUCTURE_CHANGED")
    disclaimer = (
        '<section class="prose"><h2>無料ツールの利用状況計測</h2>'
        '<p>新設の通信費診断・商品選定ツールはGoogle Analytics 4を利用し、'
        'ページ閲覧と初回操作の有無を計測します。'
        '通信量・料金・予算・絞り込み条件などの入力値をGA4へ送信しません。</p></section>'
    )
    (output / "privacy.html").write_text(
        privacy.replace("</main>", disclaimer + "</main>", 1), encoding="utf-8")

    sim_body = (
        '<section class="hero compact"><p class="eyebrow">無料Webツール</p>'
        '<h1>家族の通信費・格安SIM見直し診断</h1>'
        '<p>料金・通信量は端末内で計算し、入力した値はサーバーやGA4へ送りません。</p></section>'
        + sim_page(show_router=True)
    )
    (output / "sim-optimizer.html").write_text(
        layout(site, "家族の通信費・格安SIM見直し診断", sim_body,
               "料金と使用容量から初年度費用を比較する無料計算機。",
               active_page="sim-optimizer", canonical_file="sim-optimizer.html",
               page_css=SIM_STYLE),
        encoding="utf-8",
    )
    (output / "product-selector.html").write_text(
        layout(site, "ガジェット選定・価格比較ツール",
               selector_recovery_page(old_docs, queries),
               "既存の掲載商品の過去価格と評価を絞り込む臨時版の無料ツール。",
               active_page="product-selector", canonical_file="product-selector.html",
               page_css=SIM_STYLE + SELECTOR_STYLE),
        encoding="utf-8",
    )
    # Strict preservation check: no existing affiliate, price, or product page
    # is rewritten by this narrow recovery, including archive feed.json.
    changes = {"docs/index.html", "docs/sitemap.xml", "docs/privacy.html"}
    for rel, digest in json.loads((source / "publication-manifest.json").read_text())["sha256"].items():
        if rel in changes:
            continue
        if sha256((output / rel.removeprefix("docs/")).read_bytes()) != digest:
            raise ValueError("PUBLISHED_PRODUCT_BYTES_CHANGED")
    for name in TOOLS:
        raw = (output / name).read_text(encoding="utf-8")
        if (len(raw) < 1000 or 'gtag("config","' not in raw
                or "楽天アフィリエイト" not in raw):
            raise ValueError("RECOVERY_TOOL_INCOMPLETE")
    if "href=\"category-usb-charger.html\"" not in (output / "product-selector.html").read_text():
        raise ValueError("SELECTOR_INTERNAL_LINK_MISSING")
    return {"existing_pages_preserved": len(
        json.loads((source / "publication-manifest.json").read_text())["sha256"]) - len(changes),
            "added_tools": len(TOOLS)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check-only", action="store_true",
                        help="Recheck current public baseline immediately before deployment")
    args = parser.parse_args()
    try:
        if args.check_only:
            require_public_match()
            print("STATIC_RECOVERY_SOURCE_STILL_PUBLIC")
            return 0
        if args.artifact is None or args.output is None:
            raise ValueError("MISSING_REQUIRED_INPUT")
        result = recover_archive(args.artifact, args.output)
        print("STATIC_TOOLS_RECOVERY_READY: " + json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        # Only explicitly authored fixed error codes are allowed in Actions logs.
        # Never reflect arbitrary exception strings, product names, paths or URLs.
        allowed = {
            "ARCHIVE_SOURCE_MISMATCH", "ARCHIVE_MANIFEST_INVALID",
            "ARCHIVE_FILE_SET_MISMATCH", "ARCHIVE_DIGEST_MISMATCH",
            "ARCHIVE_PIN_MISMATCH", "ARCHIVE_ALREADY_CONTAINS_TOOLS",
            "ARCHIVE_SYMLINK_DENIED", "LIVE_PATH_NOT_ALLOWED",
            "LIVE_SOURCE_NOT_200", "LIVE_SOURCE_TOO_LARGE",
            "LIVE_HAS_NEWER_OR_DIFFERENT_PUBLICATION", "INDEX_STRUCTURE_CHANGED",
            "SITEMAP_STRUCTURE_CHANGED", "SITEMAP_ALREADY_CONTAINS_TOOL",
            "CATEGORY_ID_INVALID", "ARCHIVE_CATEGORY_MISSING",
            "CARD_FORMAT_INVALID", "CARD_TITLE_MISSING", "CARD_TITLE_EMPTY",
            "ARCHIVE_NO_COMPARABLE_CARDS", "OUTPUT_MUST_NOT_EXIST",
            "PRIVACY_PAGE_STRUCTURE_CHANGED", "SITE_CONFIG_URL_MISMATCH",
            "GA4_REQUIRED", "CATEGORIES_UNEXPECTED",
            "PUBLISHED_PRODUCT_BYTES_CHANGED", "RECOVERY_TOOL_INCOMPLETE",
            "SELECTOR_INTERNAL_LINK_MISSING", "MISSING_REQUIRED_INPUT"
        }
        reason = str(exc) if type(exc) is ValueError and str(exc) in allowed else "INTERNAL"
        print("STATIC_TOOLS_RECOVERY_BLOCKED: " + reason)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
