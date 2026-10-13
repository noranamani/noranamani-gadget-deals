#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
ASSETS = DOCS / "assets"
DATA = ROOT / "data"
CONFIG = ROOT / "config"


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def esc(v) -> str:
    return html.escape(str(v or ""), quote=True)


def yen(v) -> str:
    try:
        return f"¥{int(v):,}"
    except Exception:
        return "-"


def pct(v) -> str:
    if v is None:
        return ""
    return f"{float(v):.1f}%"


def category_filename(category_id: str) -> str:
    return f"category-{category_id}.html"


def product_card(item: dict, editorial_reason: str = "") -> str:
    img = esc(item.get("image"))
    image_html = f'<img src="{img}" alt="" loading="lazy">' if img else '<div class="noimg">No image</div>'
    badges = []
    if item.get("dealLevel") == "strong":
        badges.append('<span class="badge strong">大幅値下げ候補</span>')
    elif item.get("isDeal"):
        badges.append('<span class="badge deal">特価候補</span>')
    if item.get("postageIncluded"):
        badges.append('<span class="badge">送料無料</span>')
    if int(item.get("pointRate") or 1) > 1:
        badges.append(f'<span class="badge">ポイント{int(item["pointRate"])}倍</span>')
    drop = item.get("dropPct")
    history_line = ""
    if drop is not None and item.get("baselinePrice"):
        if float(drop) >= 0:
            change_text = f"{pct(drop)}安"
        else:
            change_text = f"{pct(abs(float(drop)))}高"
        history_line = f'<div class="history">履歴中央値 {yen(item["baselinePrice"])} → 現在 {yen(item["price"])}（{change_text}）</div>'
    reason = f'<p class="reason">{esc(editorial_reason)}</p>' if editorial_reason else ""
    return f'''<article class="product-card">
      <a class="product-image" href="{esc(item.get('url'))}" rel="sponsored nofollow" target="_blank">{image_html}</a>
      <div class="product-body">
        <div class="badges">{''.join(badges)}</div>
        <h3>{esc(item.get('name'))}</h3>
        <div class="price">{yen(item.get('price'))}</div>
        <div class="meta">★ {float(item.get('reviewAverage') or 0):.2f} / {int(item.get('reviewCount') or 0):,}件 ・ 比較スコア {float(item.get('score') or 0):.1f}</div>
        {history_line}
        {reason}
        <div class="shop">{esc(item.get('shopName'))}</div>
        <a class="cta" href="{esc(item.get('url'))}" rel="sponsored nofollow" target="_blank">楽天市場で確認</a>
      </div>
    </article>'''


def layout(site: dict, title: str, body: str, description: str = "") -> str:
    site_name = esc(site.get("site_name", "noranamani GADGET DEALS"))
    desc = esc(description or site.get("tagline", "ガジェットの価格比較と特価候補"))
    category_queries = load(CONFIG / "queries.json", [])
    category_nav = "".join(
        f'<a href="{esc(category_filename(str(row.get("id") or "")))}">{esc(row.get("name"))}</a>'
        for row in category_queries
        if isinstance(row, dict)
    )
    sidebar = f'''<aside class="side-nav" aria-label="カテゴリナビゲーション">
      <div class="side-nav-title">カテゴリ</div>
      <a href="index.html">ホーム</a>
      {category_nav}
      <a href="deals.html">今日の特価候補</a>
    </aside>'''
    return f'''<!doctype html>
<html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} | {site_name}</title>
<meta name="description" content="{desc}">
<meta name="google-site-verification" content="SOlcKHA0_jr9rUKvudZcu9H27wc2si9Kq6B4anfCE6U" />
<link rel="stylesheet" href="assets/style.css">
</head><body>
<header><div class="wrap header-inner"><a class="brand" href="index.html">{site_name}</a><nav><a href="deals.html">今日の特価候補</a><a href="methodology.html">比較方法</a><a href="{esc(site.get('note_url'))}" target="_blank" rel="noopener">実体験note</a></nav></div></header>
<div class="site-shell">{sidebar}<main class="main-content">{body}</main></div>
<footer><div class="wrap"><p>このサイトには楽天アフィリエイトのリンクが含まれます。価格・在庫等は取得時点の情報です。購入前に販売ページをご確認ください。</p><p><a href="privacy.html">運営・プライバシー</a> ・ Supported by Rakuten Developers</p></div></footer>
</body></html>'''


def main() -> None:
    site = load(CONFIG / "site.json", {})
    products = load(DATA / "products.json", {"categories": [], "deals": [], "stats": {}})
    editorial = load(DATA / "editorial.json", {})
    DOCS.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)

    featured_reasons = {x.get("code"): x.get("reason", "") for x in editorial.get("featuredDeals", []) if isinstance(x, dict)}
    notes = {x.get("categoryId"): x for x in editorial.get("categoryNotes", []) if isinstance(x, dict)}
    all_by_code = {}
    for cat in products.get("categories", []):
        for item in cat.get("items", []):
            all_by_code[item.get("code")] = item

    featured = []
    for code, reason in featured_reasons.items():
        if code in all_by_code:
            featured.append(product_card(all_by_code[code], reason))

    hero = f'''<section class="hero"><p class="eyebrow">毎日自動更新</p><h1>{esc(editorial.get('dailyHeadline') or site.get('tagline'))}</h1><p>{esc(editorial.get('dailySummary'))}</p><div class="stats"><span>取得商品 {int(products.get('stats',{}).get('products',0))}</span><span>追跡商品 {int(products.get('stats',{}).get('tracked',0))}</span><span>特価候補 {int(products.get('stats',{}).get('deals',0))}</span></div></section>'''
    sections = [hero]
    if featured:
        sections.append('<section><div class="section-head"><h2>注目候補</h2><a href="deals.html">特価候補をすべて見る</a></div><div class="grid">' + ''.join(featured) + '</div></section>')

    for cat in products.get("categories", []):
        note = notes.get(cat.get("id"), {})
        summary = f'<p class="category-summary">{esc(note.get("summary"))}</p>' if note.get("summary") else ""
        watchouts = note.get("watchouts") or []
        watch_html = '<ul class="watchouts">' + ''.join(f'<li>{esc(x)}</li>' for x in watchouts[:3]) + '</ul>' if watchouts else ""
        top_code = note.get("topPickCode") or ""
        cards = []
        ordered = list(cat.get("items", []))
        if top_code:
            ordered.sort(key=lambda x: (0 if x.get("code") == top_code else 1, -float(x.get("score") or 0)))
        for item in ordered[:6]:
            reason = "比較ピック" if item.get("code") == top_code else ""
            cards.append(product_card(item, reason))
        category_url = category_filename(str(cat.get("id") or ""))
        sections.append(f'''<section id="{esc(cat.get('id'))}"><div class="section-head"><h2><a href="{esc(category_url)}">{esc(cat.get('name'))}</a></h2><div><span>{esc(cat.get('keyword'))}</span> ・ <a href="{esc(category_url)}">一覧を見る</a></div></div>{summary}{watch_html}<div class="grid">{''.join(cards)}</div></section>''')

    updated = products.get("updatedAt") or "未更新"
    sections.append(f'<p class="updated">最終データ更新: {esc(updated)}</p>')
    (DOCS / "index.html").write_text(layout(site, "ホーム", ''.join(sections)), encoding="utf-8")

    category_links = []
    category_cards = []
    for cat in products.get("categories", []):
        category_url = category_filename(str(cat.get("id") or ""))
        note = notes.get(cat.get("id"), {})
        summary_text = str(note.get("summary") or f"{cat.get('keyword')} の検索結果を、価格・レビュー・送料・ポイント・保存した価格履歴で比較しています。")
        item_count = len(cat.get("items", []))
        category_links.append(f'<a class="category-chip" href="{esc(category_url)}">{esc(cat.get("name"))}</a>')
        category_cards.append(f'''<article class="category-nav-card">
          <p class="eyebrow">比較カテゴリ</p>
          <h2><a href="{esc(category_url)}">{esc(cat.get("name"))}</a></h2>
          <p>{esc(summary_text)}</p>
          <div class="meta">{esc(cat.get("keyword"))} ・ 掲載 {item_count}件</div>
          <a class="cta" href="{esc(category_url)}">このカテゴリを見る</a>
        </article>''')

    categories_body = f'''<section class="hero compact"><p class="eyebrow">カテゴリ目次</p><h1>カテゴリ一覧</h1><p>比較したいジャンルへすぐ移動できます。</p><div class="category-chip-row">{''.join(category_links)}</div></section><div class="category-nav-grid">{''.join(category_cards)}</div>'''
    (DOCS / "categories.html").write_text(
        layout(site, "カテゴリ一覧", categories_body, "ガジェット比較カテゴリの一覧。USB-C充電器、ワイヤレス充電、microSD、車載ガジェット、モバイルルーターへすぐ移動できます。"),
        encoding="utf-8",
    )

    for cat in products.get("categories", []):
        note = notes.get(cat.get("id"), {})
        summary_text = str(note.get("summary") or f"{cat.get('keyword')} の検索結果を、価格・レビュー・送料・ポイント・保存した価格履歴で比較しています。")
        watchouts = note.get("watchouts") or []
        watch_html = '<ul class="watchouts">' + ''.join(f'<li>{esc(x)}</li>' for x in watchouts[:4]) + '</ul>' if watchouts else ""
        top_code = note.get("topPickCode") or ""
        ordered = list(cat.get("items", []))
        if top_code:
            ordered.sort(key=lambda x: (0 if x.get("code") == top_code else 1, -float(x.get("score") or 0)))
        cards = []
        for item in ordered:
            reason = "比較ピック" if item.get("code") == top_code else ""
            cards.append(product_card(item, reason))
        category_body = f'''<section class="hero compact"><p class="eyebrow">毎日自動更新</p><h1>{esc(cat.get('name'))}の価格比較</h1><p>{esc(summary_text)}</p><div class="stats"><span>掲載商品 {len(ordered)}</span><span>検索条件 {esc(cat.get('keyword'))}</span></div></section>{watch_html}<div class="grid">{''.join(cards)}</div><p class="updated">最終データ更新: {esc(updated)}</p>'''
        description = f"{cat.get('name')}の価格、レビュー、送料、ポイント、価格履歴を比較。毎日更新。"
        (DOCS / category_filename(str(cat.get("id") or ""))).write_text(
            layout(site, f"{cat.get('name')}の価格比較", category_body, description),
            encoding="utf-8",
        )

    deals = products.get("deals", [])
    deal_body = '<section class="hero compact"><p class="eyebrow">価格履歴ベース</p><h1>今日の特価候補</h1><p>このサイト自身が保存した価格履歴との比較です。市場全体の最安値を意味しません。</p></section>'
    if deals:
        deal_body += '<div class="grid">' + ''.join(product_card(x, featured_reasons.get(x.get("code"), "")) for x in deals) + '</div>'
    else:
        deal_body += '<div class="empty">価格履歴が十分にたまるまで、特価候補は表示しません。</div>'
    (DOCS / "deals.html").write_text(layout(site, "今日の特価候補", deal_body), encoding="utf-8")

    method_body = '''<section class="hero compact"><h1>比較方法</h1><p>価格だけに偏らない比較スコアと、保存した価格履歴を組み合わせています。</p></section>
<section class="prose"><h2>比較スコア</h2><p>レビュー平均、レビュー件数、同一検索カテゴリ内の相対価格、送料無料、ポイント倍率、価格履歴上の値下がりを組み合わせた参考値です。製品性能そのものを表す点数ではありません。</p>
<h2>特価候補</h2><p>最低5回の価格記録がある商品について、直近価格履歴の中央値から原則10%以上安い場合などを候補にします。市場全体の最安値を保証するものではありません。</p>
<h2>比較コメント</h2><p>楽天APIから取得した事実と、このサイトの価格履歴を根拠に短い比較コメントを掲載します。APIにない仕様や実測性能は掲載しません。</p></section>'''
    (DOCS / "methodology.html").write_text(layout(site, "比較方法", method_body), encoding="utf-8")

    privacy_body = '''<section class="hero compact"><h1>運営・プライバシー</h1></section><section class="prose"><h2>運営</h2><p>noranamani が、ガジェット選びの比較材料を提供する目的で運営しています。</p><h2>広告について</h2><p>当サイトは楽天アフィリエイトを利用しています。リンク経由の購入により運営者が紹介料を受け取る場合があります。</p><h2>データについて</h2><p>価格、レビュー、送料等は更新時点の楽天市場APIデータです。正確な最新情報は販売ページをご確認ください。</p></section>'''
    (DOCS / "privacy.html").write_text(layout(site, "運営・プライバシー", privacy_body), encoding="utf-8")

    (DOCS / "feed.json").write_text(json.dumps({"updatedAt": products.get("updatedAt"), "deals": deals[:20]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    site_url = str(site.get("site_url") or "").rstrip("/")
    robots = "User-agent: *\nAllow: /\n"
    if site_url:
        robots += f"Sitemap: {site_url}/sitemap.xml\n"
    (DOCS / "robots.txt").write_text(robots, encoding="utf-8")

    if site_url:
        pages = ["index.html", "categories.html", "deals.html", "methodology.html", "privacy.html"]
        pages += [category_filename(str(cat.get("id") or "")) for cat in products.get("categories", [])]
        urls = "".join(f"<url><loc>{esc(site_url + '/' + p)}</loc></url>" for p in pages)
        sitemap = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
        (DOCS / "sitemap.xml").write_text(sitemap, encoding="utf-8")

    print("built docs/")


if __name__ == "__main__":
    main()
