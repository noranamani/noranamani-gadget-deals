#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from datetime import datetime, timedelta, timezone
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


def format_updated_at(value) -> str:
    if not value:
        return "未更新"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        jst = dt.astimezone(timezone(timedelta(hours=9)))
        return jst.strftime("%Y/%m/%d %H:%M 更新")
    except (TypeError, ValueError):
        return str(value)


def category_filename(category_id: str) -> str:
    return f"category-{category_id}.html"


def unique_current_items(products: dict) -> list[dict]:
    by_code = {}
    for cat in products.get("categories", []):
        for item in cat.get("items", []):
            code = str(item.get("code") or "")
            if not code:
                continue
            current = by_code.get(code)
            if current is None or float(item.get("dropPct") or -999) > float(current.get("dropPct") or -999):
                by_code[code] = item
    return list(by_code.values())


def product_card(item: dict, editorial_reason: str = "", sort_order: int = 0) -> str:
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
    data_price = int(item.get("price") or 0)
    data_score = float(item.get("score") or 0)
    data_reviews = int(item.get("reviewCount") or 0)
    data_drop = float(drop) if drop is not None else -999.0
    return f'''<article class="product-card" data-order="{sort_order}" data-price="{data_price}" data-score="{data_score}" data-reviews="{data_reviews}" data-drop="{data_drop}">
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


def layout(site: dict, title: str, body: str, description: str = "", active_category_id: str = "", active_page: str = "") -> str:
    site_name = esc(site.get("site_name", "noranamani GADGET DEALS"))
    desc = esc(description or site.get("tagline", "ガジェットの価格比較と特価候補"))
    category_queries = load(CONFIG / "queries.json", [])
    category_nav_parts = []
    for row in category_queries:
        if not isinstance(row, dict):
            continue
        category_id = str(row.get("id") or "")
        active_class = ' class="active"' if category_id == active_category_id else ""
        category_nav_parts.append(
            f'<a{active_class} href="{esc(category_filename(category_id))}">{esc(row.get("name"))}</a>'
        )
    category_nav = "".join(category_nav_parts)
    home_class = ' class="active"' if active_page == "home" else ""
    drops_class = ' class="active"' if active_page == "price-drops" else ""
    deals_class = ' class="active"' if active_page == "deals" else ""
    sidebar = f'''<aside class="side-nav" aria-label="カテゴリナビゲーション">
      <div class="side-nav-title">カテゴリ</div>
      <a{home_class} href="index.html">ホーム</a>
      {category_nav}
      <a{drops_class} href="price-drops.html">値下がりランキング</a>
      <a{deals_class} href="deals.html">今日の特価候補</a>
    </aside>'''
    return f'''<!doctype html>
<html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} | {site_name}</title>
<meta name="description" content="{desc}">
<meta name="google-site-verification" content="SOlcKHA0_jr9rUKvudZcu9H27wc2si9Kq6B4anfCE6U" />
<link rel="stylesheet" href="assets/style.css">
</head><body>
<header><div class="wrap header-inner"><a class="brand" href="index.html">{site_name}</a><nav><a href="price-drops.html">値下がりランキング</a><a href="deals.html">今日の特価候補</a><a href="methodology.html">比較方法</a><a href="{esc(site.get('note_url'))}" target="_blank" rel="noopener">実体験note</a></nav></div></header>
<div class="site-shell">{sidebar}<main class="main-content">{body}</main></div>
<footer><div class="wrap"><p>このサイトには楽天アフィリエイトのリンクが含まれます。価格・在庫等は取得時点の情報です。購入前に販売ページをご確認ください。</p><p><a href="privacy.html">運営・プライバシー</a> ・ Supported by Rakuten Developers</p></div></footer>
<script>
document.addEventListener("click", function(event) {{
  const button = event.target.closest("[data-sort]");
  if (!button) return;
  const grid = document.getElementById("category-products");
  if (!grid) return;
  const key = button.dataset.sort;
  const items = Array.from(grid.querySelectorAll(".product-card"));
  const value = (item, name) => Number(item.dataset[name] || 0);
  items.sort((a, b) => {{
    if (key === "price") return value(a, "price") - value(b, "price");
    if (key === "reviews") return value(b, "reviews") - value(a, "reviews");
    if (key === "drop") return value(b, "drop") - value(a, "drop");
    if (key === "score") return value(b, "score") - value(a, "score");
    return value(a, "order") - value(b, "order");
  }});
  items.forEach((item) => grid.appendChild(item));
  document.querySelectorAll("[data-sort]").forEach((x) => x.classList.remove("active"));
  button.classList.add("active");
}});
</script>
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

    min_samples = int(products.get("methodology", {}).get("dealMinSamples") or site.get("deal_min_samples", 5))
    current_unique = unique_current_items(products)
    price_drop_items = [
        item for item in current_unique
        if item.get("dropPct") is not None
        and int(item.get("historySamples") or 0) >= min_samples
        and float(item.get("dropPct") or 0) > 0
    ]
    price_drop_items.sort(key=lambda x: (-float(x.get("dropPct") or 0), -float(x.get("score") or 0), -int(x.get("reviewCount") or 0)))
    watching_items = [
        item for item in current_unique
        if int(item.get("historySamples") or 0) < min_samples
    ]
    watching_items.sort(key=lambda x: (-int(x.get("historySamples") or 0), -int(x.get("reviewCount") or 0), int(x.get("price") or 0)))

    featured = []
    for code, reason in featured_reasons.items():
        if code in all_by_code:
            featured.append(product_card(all_by_code[code], reason))

    hero = f'''<section class="hero"><p class="eyebrow">毎日自動更新</p><h1>{esc(editorial.get('dailyHeadline') or site.get('tagline'))}</h1><p>{esc(editorial.get('dailySummary'))}</p><div class="stats"><span>取得商品 {int(products.get('stats',{}).get('products',0))}</span><span>追跡商品 {int(products.get('stats',{}).get('tracked',0))}</span><span>特価候補 {int(products.get('stats',{}).get('deals',0))}</span></div></section>'''
    sections = [hero]
    if price_drop_items:
        top_drop_cards = ''.join(product_card(item) for item in price_drop_items[:3])
        sections.append(f'''<section class="price-drop-home"><div class="section-head"><div><p class="eyebrow">価格履歴ベース</p><h2>今日の値下がりTOP</h2></div><a href="price-drops.html">値下がりランキングを見る</a></div><div class="grid">{top_drop_cards}</div></section>''')
    else:
        sections.append(f'''<section class="price-drop-home"><div class="section-head"><div><p class="eyebrow">価格履歴ベース</p><h2>値下がりランキング</h2></div><a href="price-drops.html">履歴の状況を見る</a></div><div class="history-progress"><strong>価格履歴を蓄積中です</strong><p>判定には最低{min_samples}回の価格記録を使用します。現在 {len(watching_items)} 商品が判定待ちです。</p></div></section>''')
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

    current_category_files = {
        category_filename(str(cat.get("id") or ""))
        for cat in products.get("categories", [])
    }
    for stale_path in DOCS.glob("category-*.html"):
        if stale_path.name not in current_category_files:
            stale_path.unlink()

    updated = format_updated_at(products.get("updatedAt"))
    sections.append(f'<p class="updated">最終データ更新: {esc(updated)}</p>')
    (DOCS / "index.html").write_text(layout(site, "ホーム", ''.join(sections), active_page="home"), encoding="utf-8")

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
        layout(site, "カテゴリ一覧", categories_body, "ガジェット比較カテゴリの一覧。充電器、ストレージ、車載ガジェット、モバイル通信機器などを比較できます。"),
        encoding="utf-8",
    )

    for cat in products.get("categories", []):
        category_id = str(cat.get("id") or "")
        note = notes.get(cat.get("id"), {})
        summary_text = str(note.get("summary") or f"{cat.get('keyword')} の検索結果を、価格・レビュー・送料・ポイント・保存した価格履歴で比較しています。")
        watchouts = note.get("watchouts") or []
        watch_html = '<ul class="watchouts">' + ''.join(f'<li>{esc(x)}</li>' for x in watchouts[:4]) + '</ul>' if watchouts else ""
        top_code = note.get("topPickCode") or ""
        ordered = list(cat.get("items", []))
        if top_code:
            ordered.sort(key=lambda x: (0 if x.get("code") == top_code else 1, -float(x.get("score") or 0)))
        cards = []
        for index, item in enumerate(ordered):
            reason = "比較ピック" if item.get("code") == top_code else ""
            cards.append(product_card(item, reason, index))

        score_top = sorted(cat.get("items", []), key=lambda x: -float(x.get("score") or 0))[:5]
        table_rows = []
        for index, item in enumerate(score_top, 1):
            deal_text = "大幅値下げ" if item.get("dealLevel") == "strong" else ("特価候補" if item.get("isDeal") else "—")
            table_rows.append(
                f'''<tr><td class="rank-cell">{index}</td><td class="compare-product"><a href="{esc(item.get('url'))}" rel="sponsored nofollow" target="_blank">{esc(item.get('name'))}</a></td><td>{yen(item.get('price'))}</td><td>{float(item.get('score') or 0):.1f}</td><td>{int(item.get('reviewCount') or 0):,}件</td><td>{esc(deal_text)}</td></tr>'''
            )
        compare_table = ""
        if table_rows:
            compare_table = f'''<section class="compare-box"><div class="section-head"><h2>比較スコア上位5件</h2><span>価格・レビュー・送料・ポイント・価格履歴をもとに比較</span></div><div class="compare-table-wrap"><table class="compare-table"><thead><tr><th>#</th><th>商品</th><th>価格</th><th>スコア</th><th>レビュー</th><th>価格状況</th></tr></thead><tbody>{''.join(table_rows)}</tbody></table></div></section>'''

        sort_toolbar = '''<div class="sort-toolbar"><span>並べ替え</span><div class="sort-buttons"><button type="button" class="active" data-sort="default">おすすめ順</button><button type="button" data-sort="score">比較スコア順</button><button type="button" data-sort="price">価格が安い順</button><button type="button" data-sort="reviews">レビュー件数順</button><button type="button" data-sort="drop">値下がり順</button></div></div>'''
        breadcrumb = f'''<nav class="breadcrumb" aria-label="パンくず"><a href="index.html">ホーム</a><span>›</span><span>{esc(cat.get('name'))}</span></nav>'''
        category_body = f'''{breadcrumb}<section class="hero compact category-hero"><p class="eyebrow">毎日自動更新</p><h1>{esc(cat.get('name'))}の価格比較</h1><p>{esc(summary_text)}</p><div class="stats"><span>掲載商品 {len(ordered)}</span><span>検索条件 {esc(cat.get('keyword'))}</span><span>最終更新 {esc(updated)}</span></div></section><section class="guide-box"><h2>選ぶときのチェックポイント</h2>{watch_html if watch_html else '<p>価格だけでなく、レビュー、送料、ポイント、価格履歴も合わせて比較してください。</p>'}</section>{compare_table}<section><div class="section-head"><h2>商品一覧</h2><span>{len(ordered)}件</span></div>{sort_toolbar}<div class="grid" id="category-products">{''.join(cards)}</div></section><p class="updated">最終データ更新: {esc(updated)}</p>'''
        description = f"{cat.get('name')}の価格、レビュー、送料、ポイント、価格履歴を比較。毎日更新。"
        (DOCS / category_filename(category_id)).write_text(
            layout(site, f"{cat.get('name')}の価格比較", category_body, description, category_id),
            encoding="utf-8",
        )

    ranking_rows = []
    for rank, item in enumerate(price_drop_items[:40], 1):
        drop_text = f'{float(item.get("dropPct") or 0):.1f}%安'
        level_text = "大幅値下げ" if item.get("dealLevel") == "strong" else ("特価候補" if item.get("isDeal") else "値下がり")
        ranking_rows.append(
            f'''<tr><td class="rank-cell">{rank}</td><td class="compare-product"><a href="{esc(item.get('url'))}" rel="sponsored nofollow" target="_blank">{esc(item.get('name'))}</a><div class="meta">{esc(item.get('category'))}</div></td><td>{yen(item.get('price'))}</td><td>{yen(item.get('baselinePrice'))}</td><td class="drop-rate">{esc(drop_text)}</td><td>{int(item.get('historySamples') or 0)}回</td><td>{esc(level_text)}</td></tr>'''
        )

    if ranking_rows:
        ranking_html = f'''<section class="compare-box"><div class="section-head"><h2>値下がり率ランキング</h2><span>履歴{min_samples}回以上・履歴中央値より安い商品</span></div><div class="compare-table-wrap"><table class="compare-table drop-table"><thead><tr><th>#</th><th>商品</th><th>現在価格</th><th>履歴中央値</th><th>値下がり</th><th>履歴</th><th>判定</th></tr></thead><tbody>{''.join(ranking_rows)}</tbody></table></div></section>'''
    else:
        ranking_html = f'''<div class="empty"><strong>まだランキング対象の商品はありません。</strong><p>最低{min_samples}回の価格記録がたまり、履歴中央値より安い商品が出ると自動的にここへ表示されます。</p></div>'''

    watching_rows = []
    for item in watching_items[:20]:
        samples = int(item.get("historySamples") or 0)
        remaining = max(min_samples - samples, 0)
        watching_rows.append(
            f'''<tr><td class="compare-product"><a href="{esc(item.get('url'))}" rel="sponsored nofollow" target="_blank">{esc(item.get('name'))}</a><div class="meta">{esc(item.get('category'))}</div></td><td>{yen(item.get('price'))}</td><td><span class="history-count">{samples}/{min_samples}</span></td><td>あと{remaining}回</td></tr>'''
        )
    watching_html = ""
    if watching_rows:
        watching_html = f'''<section class="compare-box"><div class="section-head"><h2>価格ウォッチ中</h2><span>{len(watching_items)}商品が判定待ち</span></div><p class="category-summary">価格履歴が{min_samples}回に達するまで特価判定を行いません。履歴がたまった商品から自動的にランキング対象になります。</p><div class="compare-table-wrap"><table class="compare-table watch-table"><thead><tr><th>商品</th><th>現在価格</th><th>履歴</th><th>判定まで</th></tr></thead><tbody>{''.join(watching_rows)}</tbody></table></div></section>'''

    drops_body = f'''<nav class="breadcrumb" aria-label="パンくず"><a href="index.html">ホーム</a><span>›</span><span>値下がりランキング</span></nav><section class="hero compact category-hero"><p class="eyebrow">毎日自動更新</p><h1>値下がりランキング</h1><p>このサイトが保存した価格履歴の中央値と現在価格を比較し、値下がり率の大きい順に表示します。市場全体の最安値を示すものではありません。</p><div class="stats"><span>ランキング対象 {len(price_drop_items)}</span><span>価格ウォッチ中 {len(watching_items)}</span><span>判定開始 {min_samples}回から</span><span>最終更新 {esc(updated)}</span></div></section>{ranking_html}{watching_html}'''
    (DOCS / "price-drops.html").write_text(
        layout(site, "値下がりランキング", drops_body, "保存した価格履歴から値下がり率の大きい商品を毎日ランキング。特価候補と価格ウォッチ中の商品も確認できます。", active_page="price-drops"),
        encoding="utf-8",
    )

    deals = products.get("deals", [])
    deal_body = '<section class="hero compact"><p class="eyebrow">価格履歴ベース</p><h1>今日の特価候補</h1><p>このサイト自身が保存した価格履歴との比較です。市場全体の最安値を意味しません。</p></section>'
    if deals:
        deal_body += '<div class="grid">' + ''.join(product_card(x, featured_reasons.get(x.get("code"), "")) for x in deals) + '</div>'
    else:
        deal_body += '<div class="empty">価格履歴が十分にたまるまで、特価候補は表示しません。</div>'
    (DOCS / "deals.html").write_text(layout(site, "今日の特価候補", deal_body, active_page="deals"), encoding="utf-8")

    method_body = '''<section class="hero compact"><h1>比較方法</h1><p>価格だけに偏らない比較スコアと、保存した価格履歴を組み合わせています。</p></section>
<section class="prose"><h2>比較スコア</h2><p>レビュー平均、レビュー件数、同一検索カテゴリ内の相対価格、送料無料、ポイント倍率、価格履歴上の値下がりを組み合わせた参考値です。製品性能そのものを表す点数ではありません。</p>
<h2>値下がりランキング</h2><p>最低5回の価格記録がある商品を対象に、保存した価格履歴の中央値と現在価格を比較し、値下がり率の大きい順に表示します。市場全体の最安値を保証するものではありません。</p>
<h2>特価候補</h2><p>最低5回の価格記録がある商品について、直近価格履歴の中央値から原則10%以上安い場合などを候補にします。市場全体の最安値を保証するものではありません。</p>
<h2>比較コメント</h2><p>楽天APIから取得した事実と、このサイトの価格履歴を根拠に短い比較コメントを掲載します。APIにない仕様や実測性能は掲載しません。</p></section>'''
    (DOCS / "methodology.html").write_text(layout(site, "比較方法", method_body), encoding="utf-8")

    privacy_body = '''<section class="hero compact"><h1>運営・プライバシー</h1></section><section class="prose"><h2>運営</h2><p>noranamani が、ガジェット選びの比較材料を提供する目的で運営しています。</p><h2>広告について</h2><p>当サイトは楽天アフィリエイトを利用しています。リンク経由の購入により運営者が紹介料を受け取る場合があります。</p><h2>データについて</h2><p>価格、レビュー、送料等は更新時点の楽天市場APIデータです。正確な最新情報は販売ページをご確認ください。</p></section>'''
    (DOCS / "privacy.html").write_text(layout(site, "運営・プライバシー", privacy_body), encoding="utf-8")

    (DOCS / "feed.json").write_text(json.dumps({"updatedAt": products.get("updatedAt"), "deals": deals[:20], "priceDrops": price_drop_items[:20]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    site_url = str(site.get("site_url") or "").rstrip("/")
    robots = "User-agent: *\nAllow: /\n"
    if site_url:
        robots += f"Sitemap: {site_url}/sitemap.xml\n"
    (DOCS / "robots.txt").write_text(robots, encoding="utf-8")

    if site_url:
        pages = ["index.html", "categories.html", "price-drops.html", "deals.html", "methodology.html", "privacy.html"]
        pages += [category_filename(str(cat.get("id") or "")) for cat in products.get("categories", [])]
        urls = "".join(f"<url><loc>{esc(site_url + '/' + p)}</loc></url>" for p in pages)
        sitemap = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
        (DOCS / "sitemap.xml").write_text(sitemap, encoding="utf-8")

    print("built docs/")


if __name__ == "__main__":
    main()
