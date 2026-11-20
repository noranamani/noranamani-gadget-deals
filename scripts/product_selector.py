"""Interactive, read-only gadget selection from the validated current snapshot.

Displays only items for which a single currently comparable product price is
available. Does not infer technical specifications or any market-wide minimum.
"""
import html
from affiliate_quality import displayable, recommendable, variant_price_uncertain

MAX_PER_CATEGORY = 8

SELECTOR_STYLE = r"""
.selector-controls {display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;align-items:end}
.selector-panel {background:var(--card,#fff);border:1px solid var(--line,#ddd);border-radius:17px;padding:19px;margin:18px 0}
.selector-panel h2 {margin:0 0 10px}
.selector-field {min-width:0;display:flex;flex-direction:column;gap:5px;font-size:.88rem;font-weight:700}
.selector-field input,.selector-field select {background:#fff;color:var(--ink,#152739);font:inherit;border:1px solid #9fb3c3;border-radius:9px;padding:10px;min-width:0;width:100%}
.selector-field input:focus-visible,.selector-field select:focus-visible,.selector-reset:focus-visible {outline:3px solid var(--accent,#2563eb);outline-offset:2px}
.selector-hint {font-size:.85rem;color:var(--muted)}
.selector-results {display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.selector-count {font-weight:850;color:#126a97}
.selector-reset {font:inherit;background:white;color:var(--accent,#2563eb);border:1px solid #98b4c9;padding:8px 15px;border-radius:9px;cursor:pointer;font-weight:750}
.selector-grid {display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:16px}
.selector-item {min-width:0}
.selector-item .product-card {height:100%}
.selector-item[hidden] {display:none!important}
.selector-empty {background:var(--card,#fff);border:1px solid var(--line,#ddd);border-radius:12px;padding:20px}
.selector-disclaimer {margin:0 0 16px;font-size:.86rem;color:var(--muted)}
@media(max-width:1000px){.selector-controls {grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:520px){.selector-controls {grid-template-columns:minmax(0,1fr)}.selector-panel{padding:15px}}
"""

SELECTOR_JS = r"""
/* Filtering stays on device. A value-free GA4 use event is optional. */
(function () {
  "use strict";
  var ALLOWED = ["score","price","reviews","drop"];
  function choose(items, filters) {
    if (!Array.isArray(items)) throw new Error("商品一覧が不正です");
    var price = filters.maxPrice;
    if (price !== null && (!Number.isFinite(price) || price < 0 || price > 10000000)) {
      throw new Error("予算上限は0〜1,000万円で入力してください。");
    }
    var reviewFloor = Number(filters.minReviews || 0);
    if (![0,10,50,100].includes(reviewFloor)) {
      throw new Error("レビュー件数の条件が不正です。");
    }
    var sort = ALLOWED.includes(filters.order) ? filters.order : "score";
    return items.filter(function (item) {
      return (filters.category === "all" || item.category === filters.category) &&
        item.price > 0 && (price === null || item.price <= price) &&
        item.reviews >= reviewFloor;
    }).sort(function (a,b) {
      var d=0;
      if (sort==="price") d=a.price-b.price;
      else if (sort==="reviews") d=b.reviews-a.reviews;
      else if (sort==="drop") d=b.drop-a.drop;
      else d=b.score-a.score;
      return d || a.index-b.index;
    });
  }
  if (typeof module!=="undefined" && module.exports) module.exports.choose=choose;
  if (typeof document==="undefined") return;
  function get(id){return document.getElementById(id);}
  var usageReported = false;
  function trackUsage() {
    if (usageReported) return;
    usageReported = true;
    // Do not send filters, prices, item IDs, search terms or category values.
    if (typeof window === "undefined" || typeof window.gtag !== "function") return;
    try { window.gtag("event", "product_selector_used"); } catch (_) {}
  }
  function init() {
    var grid=get("selector-grid");
    if (!grid) return;
    var cards=Array.prototype.slice.call(grid.querySelectorAll(".selector-item"));
    var items=cards.map(function (element,index) {
      var product=element.querySelector(".product-card");
      return {element:element,index:index,category:element.dataset.category,
        price:Number(product.dataset.price),reviews:Number(product.dataset.reviews),
        score:Number(product.dataset.score),drop:Number(product.dataset.drop)};
    });
    function update() {
      var raw=get("selector-price").value.trim();
      var filters={category:get("selector-category").value,
        maxPrice:raw===""?null:Number(raw),
        minReviews:Number(get("selector-reviews").value),
        order:get("selector-order").value};
      try {
        var chosen=choose(items,filters);
        cards.forEach(function (c){c.hidden=true;});
        chosen.forEach(function (row){row.element.hidden=false;grid.appendChild(row.element);});
        get("selector-count").textContent=chosen.length+"件を表示中（掲載候補"+items.length+"件）";
        get("selector-empty").hidden=chosen.length!==0;
      } catch(e) {
        cards.forEach(function(c){c.hidden=true;});
        get("selector-empty").hidden=false;
        get("selector-count").textContent=e.message;
      }
    }
    ["selector-category","selector-price","selector-reviews","selector-order"].forEach(function(id){
      get(id).addEventListener("change",function(){trackUsage();update();});
      get(id).addEventListener("input",function(){trackUsage();update();});
    });
    get("selector-reset").addEventListener("click",function(){
      trackUsage();
      get("selector-category").value="all";get("selector-price").value="";
      get("selector-reviews").value="0";get("selector-order").value="score";update();
    });
    update();
  }
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",init);
  else init();
}());
"""


def _safe(v):
    return html.escape(str(v or ""), quote=True)


def selected_products(products, now):
    """Select at most eight reliable, distinct offers per existing category."""
    seen = set()
    selected = []
    for category in products.get("categories", []):
        label = str(category.get("name") or "")
        cat_id = str(category.get("id") or "")
        if not cat_id or not label:
            continue
        unique = {}
        for item in category.get("items", []):
            code = str(item.get("code") or "")
            if (not code or not displayable(item) or not recommendable(item, now)
                    or variant_price_uncertain(item)):
                continue
            if code not in unique or float(item.get("score") or 0) > float(unique[code].get("score") or 0):
                unique[code] = item
        ordered = sorted(unique.values(), key=lambda x: (
            -float(x.get("score") or 0), -int(x.get("reviewCount") or 0),
            int(x.get("price") or 0), str(x.get("code") or "")))
        for item in ordered:
            code = str(item.get("code") or "")
            if code in seen:
                continue
            selected.append((cat_id, label, item))
            seen.add(code)
            if sum(x[0] == cat_id for x in selected) >= MAX_PER_CATEGORY:
                break
    return selected


def selector_page(products, now, card_renderer):
    rows = selected_products(products, now)
    categories = []
    for cat_id, name, _ in rows:
        if cat_id not in [x[0] for x in categories]:
            categories.append((cat_id, name))
    options = "".join(
        f'<option value="{_safe(cat_id)}">{_safe(name)}</option>'
        for cat_id, name in categories
    )
    cards = "".join(
        f'<div class="selector-item" data-category="{_safe(cat_id)}">'
        + card_renderer(item, sort_order=index, placement="selector")
        + "</div>"
        for index, (cat_id, _name, item) in enumerate(rows)
    )
    return (
        '<nav class="breadcrumb" aria-label="パンくず"><a href="index.html">ホーム</a>'
        '<span>›</span><span>ガジェット選定</span></nav>'
        '<section class="hero compact"><p class="eyebrow">無料ツール / 条件で探す</p>'
        '<h1>ガジェット選定・価格比較ツール</h1>'
        '<p>予算とレビュー件数で商品を絞り込み、価格・比較スコア・値下がり率を見比べられます。</p>'
        '<p class="selector-disclaimer">楽天市場APIから収集した掲載商品のうち、'
        f'条件を確認できる候補を各カテゴリ最大{MAX_PER_CATEGORY}件表示します。'
        '当サイト内での比較であり市場全体の最安値ではありません。'
        '商品仕様・クーポン条件・表示価格・送料は、購入前に販売ページで確認してください。'
        'このページには楽天アフィリエイトリンクが含まれます。</p></section>'
        '<section class="selector-panel" aria-labelledby="selector-title"><h2 id="selector-title">絞り込み条件</h2>'
        '<div class="selector-controls">'
        '<label class="selector-field" for="selector-category">カテゴリ'
        '<select id="selector-category"><option value="all">すべて</option>' + options + '</select></label>'
        '<label class="selector-field" for="selector-price">予算上限（税込・円）'
        '<input id="selector-price" type="number" min="0" max="10000000" step="1" '
        'inputmode="numeric" placeholder="上限なし"></label>'
        '<label class="selector-field" for="selector-reviews">最低レビュー件数'
        '<select id="selector-reviews"><option value="0">指定なし</option>'
        '<option value="10">10件以上</option><option value="50">50件以上</option>'
        '<option value="100">100件以上</option></select></label>'
        '<label class="selector-field" for="selector-order">並び順'
        '<select id="selector-order"><option value="score">比較スコアが高い順</option>'
        '<option value="price">価格が安い順</option><option value="reviews">レビュー件数順</option>'
        '<option value="drop">保存価格履歴の値下がり率順</option></select></label>'
        '</div><p class="selector-hint">絞り込み条件はこの端末内で処理し、送信・保存しません。'
        'カテゴリや製品の機能・性能は商品名だけから推測しません。</p>'
        '<div class="selector-results"><p id="selector-count" class="selector-count" '
        'role="status" aria-live="polite">候補を読み込み中</p>'
        '<button id="selector-reset" class="selector-reset" type="button">条件を初期化</button></div></section>'
        '<div id="selector-grid" class="selector-grid">'
        + cards + '</div>'
        '<p id="selector-empty" class="selector-empty" hidden>条件に一致する比較候補がありません。'
        '価格の上限やレビュー件数を変更してみてください。</p>'
        '<script>' + SELECTOR_JS + '</script>'
    )
