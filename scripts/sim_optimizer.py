"""Offline mobile-plan comparison tool: all prices and capacity are user supplied.

No carrier pricing, legal discount entitlement, or access to cellular/GA data
is inferred. Optional aggregate GA4 tool-use events have no form values and
are emitted only after a real user action (never on page initialization).
"""
import html

SIM_STYLE = r"""
.sim-intro {max-width:78ch;margin:0 0 20px;color:var(--muted)}
.sim-panel,.sim-result,.sim-line {background:var(--card,#fff);border:1px solid var(--line,#ddd);border-radius:16px;padding:20px;min-width:0}
.sim-panel {margin:16px 0}
.sim-panel h2 {margin-top:0}
.sim-layout {display:grid;grid-template-columns:minmax(0,1.8fr) minmax(260px,.9fr);gap:18px;align-items:start}
.sim-inputs {min-width:0}
.sim-lines {display:grid;gap:14px}
.sim-line {margin:0}
.sim-line legend {padding:0 7px;font-weight:800;font-size:1.05rem}
.sim-grid {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.sim-field {display:flex;flex-direction:column;gap:4px;font-size:.89rem;font-weight:650;min-width:0}
.sim-field input,.sim-count {width:100%;min-width:0;padding:9px 10px;border:1px solid #a9b9c9;border-radius:9px;background:#fff;color:var(--ink,#182836);font:inherit}
.sim-field input:focus-visible,.sim-count:focus-visible,.sim-toggle:focus-visible {outline:3px solid var(--accent,#2563eb);outline-offset:2px}
.sim-group {grid-column:1/-1;border-top:1px solid var(--line,#ddd);margin-top:6px;padding-top:10px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.sim-group-title {grid-column:1/-1;font-size:.94rem;font-weight:850;margin:0}
.sim-unlimited {display:flex;gap:8px;align-items:center;min-height:35px;grid-column:1/-1;font-size:.84rem;font-weight:650}
.sim-unlimited input {width:19px;height:19px;accent-color:var(--accent,#2563eb)}
.sim-muted {font-size:.84rem;color:var(--muted)}
.sim-helper {margin:5px 0 12px}
.sim-count-wrap {max-width:230px}
.sim-extra {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.sim-result {position:sticky;top:90px;background:#f3f9ff}
.sim-result h2 {margin-top:0}
.sim-best {font-size:clamp(1.25rem,2.5vw,1.7rem);font-weight:850;color:#125a83}
.sim-results {display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px 10px;margin:14px 0}
.sim-results dt {color:var(--muted)}
.sim-results dd {font-weight:750;text-align:right;margin:0;font-variant-numeric:tabular-nums}
.sim-results .sim-sep {border-top:1px solid #c3d9e9;padding-top:9px}
.sim-message {font-size:.85rem;min-height:28px}
.sim-message[data-error="true"] {color:#9c2b28;font-weight:750}
.sim-tools {margin-top:22px}
.sim-related {margin-top:22px}
.sim-actions {display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.sim-reset {border:1px solid #809bb2;background:#fff;border-radius:9px;padding:10px 16px;font:inherit;font-weight:750;cursor:pointer;color:#104c78}
.sim-reset:focus-visible {outline:3px solid var(--accent,#2563eb)}
@media (max-width:900px) {.sim-layout {grid-template-columns:minmax(0,1fr)}.sim-result {position:static}}
@media (max-width:480px) {.sim-panel,.sim-result,.sim-line {padding:15px}.sim-grid,.sim-extra,.sim-group {grid-template-columns:minmax(0,1fr)}}
"""

SIM_JS = r"""
/* Form values stay on this device. Only a value-free GA4 use event is optional. */
(function () {
  "use strict";
  var COUNT = 6, MAX_PRICE = 10000000, MAX_GB = 10000;
  function parseNumber(raw, label, max, optional, allowFraction) {
    if (raw === null || raw === undefined || raw === "") {
      if (optional) return null;
      throw new Error(label + "を入力してください。");
    }
    var value = Number(raw);
    if (!Number.isFinite(value) || value < 0 || value > max ||
        (!allowFraction && !Number.isInteger(value))) {
      throw new Error(label + "は0以上の適切な数値で入力してください。");
    }
    return value;
  }
  function valueOrZero(raw, label) {
    var v = parseNumber(raw, label, MAX_PRICE, true, false);
    return v === null ? 0 : v;
  }
  function evaluatePlan(lines, key, extra, setup) {
    var fees = extra;
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i], p = line[key];
      if (!p || p.fee === null || (!p.unlimited && p.cap === null)) {
        return {eligible: false, reason: "回線" + (i+1) + "の月額・容量が未入力"};
      }
      var fee = parseNumber(p.fee, "回線" + (i+1) + "の候補月額", MAX_PRICE, false, false);
      var cap = p.unlimited ? null : parseNumber(p.cap, "回線" + (i+1) + "の候補容量", MAX_GB, false, true);
      if (cap !== null && cap < line.usage) {
        return {eligible: false, reason: "回線" + (i+1) + "の容量が不足"};
      }
      fees += fee;
    }
    return {eligible: true, monthly: fees, setup: setup, firstYear: fees*12 + setup};
  }
  function calculate(input) {
    if (!input || !Array.isArray(input.lines) || input.lines.length < 1 || input.lines.length > COUNT) {
      throw new Error("回線数は1〜6本で指定してください。");
    }
    var totalCurrent = valueOrZero(input.currentExtra, "現状の共通月額");
    input.lines.forEach(function (line, i) {
      var usage = parseNumber(line.usage, "回線" + (i+1) + "の通信量", MAX_GB, false, true);
      var current = parseNumber(line.current, "回線" + (i+1) + "の現状月額", MAX_PRICE, false, false);
      line.usage = usage;
      totalCurrent += current;
    });
    var result = {
      current: {eligible: true, monthly: totalCurrent, firstYear: totalCurrent*12, setup: 0},
      a: evaluatePlan(input.lines, "a",
        valueOrZero(input.aExtra, "候補Aの共通月額"), valueOrZero(input.aSetup, "候補Aの初期費用")),
      b: evaluatePlan(input.lines, "b",
        valueOrZero(input.bExtra, "候補Bの共通月額"), valueOrZero(input.bSetup, "候補Bの初期費用"))
    };
    var cheapest = "current";
    ["a", "b"].forEach(function (key) {
      if (result[key].eligible && result[key].firstYear < result[cheapest].firstYear) cheapest = key;
    });
    result.best = cheapest;
    ["a","b"].forEach(function (key) {
      var plan = result[key];
      if (plan.eligible) {
        plan.monthlySaved = totalCurrent - plan.monthly;
        plan.firstYearSaved = result.current.firstYear - plan.firstYear;
        plan.breakEvenMonths = plan.monthlySaved > 0 ? Math.ceil(plan.setup / plan.monthlySaved) : null;
      }
    });
    return result;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports.calculate = calculate;
  }
  if (typeof document === "undefined") return;
  var get = function (id) {return document.getElementById(id);};
  var usageReported = false;
  function trackUsage() {
    if (usageReported) return;
    usageReported = true;
    // Never pass prices, usage, carrier names, line counts or form values.
    if (typeof window === "undefined" || typeof window.gtag !== "function") return;
    try { window.gtag("event", "sim_tool_used"); } catch (_) {}
  }
  function read(id) {
    var text = get(id).value.trim();
    return text === "" ? null : Number(text);
  }
  function money(n) {return Math.round(n).toLocaleString("ja-JP") + "円";}
  function activeLines() {
    var num = Number(get("sim-count").value), rows = [];
    for (var i=1;i<=num;i++) {
      rows.push({
        usage:read("sim-usage-"+i), current:read("sim-current-"+i),
        a:{fee:read("sim-a-fee-"+i),cap:read("sim-a-cap-"+i),unlimited:get("sim-a-unlimited-"+i).checked},
        b:{fee:read("sim-b-fee-"+i),cap:read("sim-b-cap-"+i),unlimited:get("sim-b-unlimited-"+i).checked}
      });
    }
    return rows;
  }
  function form() {
    return {lines:activeLines(),currentExtra:read("sim-extra-current"),
      aExtra:read("sim-extra-a"),aSetup:read("sim-setup-a"),
      bExtra:read("sim-extra-b"),bSetup:read("sim-setup-b")};
  }
  function write(id, content) {get(id).textContent = content;}
  function update() {
    var status = get("sim-status");
    try {
      var result = calculate(form());
      write("sim-best", {current:"現在の契約",a:"候補A",b:"候補B"}[result.best]);
      write("sim-current-result", money(result.current.monthly));
      write("sim-a-result", result.a.eligible ? money(result.a.monthly) : result.a.reason);
      write("sim-b-result", result.b.eligible ? money(result.b.monthly) : result.b.reason);
      write("sim-a-annual", result.a.eligible ? money(result.a.firstYear) : "比較不可");
      write("sim-b-annual", result.b.eligible ? money(result.b.firstYear) : "比較不可");
      write("sim-a-save", result.a.eligible ? money(result.a.firstYearSaved) : "—");
      write("sim-b-save", result.b.eligible ? money(result.b.firstYearSaved) : "—");
      write("sim-a-break", result.a.eligible ? (result.a.breakEvenMonths === null ? "回収不可" : result.a.breakEvenMonths + "か月") : "—");
      write("sim-b-break", result.b.eligible ? (result.b.breakEvenMonths === null ? "回収不可" : result.b.breakEvenMonths + "か月") : "—");
      status.dataset.error="false";
      status.textContent="初年度の総額（12か月分＋乗換初期費用）の比較です。実際の適用条件は公式案内で確認してください。";
    } catch(e) {
      ["sim-current-result","sim-a-result","sim-b-result","sim-a-annual",
       "sim-b-annual","sim-a-save","sim-b-save","sim-a-break","sim-b-break"].forEach(function(id){write(id,"—");});
      write("sim-best","入力待ち");
      status.dataset.error="true";
      status.textContent=e.message;
    }
  }
  function configureLines() {
    var count=Number(get("sim-count").value);
    for (var i=1;i<=COUNT;i++) {
      var field=get("sim-line-"+i);
      field.hidden=i>count;
      field.disabled=i>count;
    }
    update();
  }
  function init() {
    if (!get("sim-count")) return;
    get("sim-count").addEventListener("change",function(){trackUsage();configureLines();});
    document.querySelectorAll("#sim-tool input").forEach(function(el){
      el.addEventListener("input",function(){trackUsage();update();});
      el.addEventListener("change",function(){trackUsage();update();});
    });
    get("sim-reset").addEventListener("click",function(){
      trackUsage();
      document.querySelectorAll("#sim-tool input[type=number]").forEach(function(el){el.value=el.defaultValue;});
      document.querySelectorAll("#sim-tool input[type=checkbox]").forEach(function(el){el.checked=false;});
      get("sim-count").value="1";
      configureLines();
    });
    configureLines();
  }
  if (document.readyState==="loading") document.addEventListener("DOMContentLoaded",init);
  else init();
}());
"""


def _field(field_id, title, *, step="1"):
    return (f'<label class="sim-field" for="{field_id}">{html.escape(title)}'
            f'<input type="number" id="{field_id}" min="0" step="{step}" '
            'placeholder="要入力" inputmode="decimal"></label>')


def _line(index):
    fields = (
        _field(f"sim-usage-{index}", "毎月使う通信量（GB）", step="any") +
        _field(f"sim-current-{index}", "現在の月額（税込・円）") +
        '<div class="sim-group"><p class="sim-group-title">候補A</p>' +
        _field(f"sim-a-fee-{index}", "割引適用後の月額（税込・円）") +
        _field(f"sim-a-cap-{index}", "利用できる通信容量（GB）", step="any") +
        f'<label class="sim-unlimited" for="sim-a-unlimited-{index}">'
        f'<input id="sim-a-unlimited-{index}" type="checkbox">容量無制限（公式条件を確認済み）</label></div>' +
        '<div class="sim-group"><p class="sim-group-title">候補B</p>' +
        _field(f"sim-b-fee-{index}", "割引適用後の月額（税込・円）") +
        _field(f"sim-b-cap-{index}", "利用できる通信容量（GB）", step="any") +
        f'<label class="sim-unlimited" for="sim-b-unlimited-{index}">'
        f'<input id="sim-b-unlimited-{index}" type="checkbox">容量無制限（公式条件を確認済み）</label></div>'
    )
    return (f'<fieldset class="sim-line" id="sim-line-{index}"><legend>回線{index}</legend>'
            f'<div class="sim-grid">{fields}</div></fieldset>')


def sim_page(show_router=True):
    extra = [
        ("sim-extra-current", "現在の共通月額（円）"),
        ("sim-extra-a", "候補Aの共通月額（円）"),
        ("sim-setup-a", "候補Aの乗換初期費用・合計（円）"),
        ("sim-extra-b", "候補Bの共通月額（円）"),
        ("sim-setup-b", "候補Bの乗換初期費用・合計（円）"),
    ]
    return (
        '<nav class="breadcrumb" aria-label="パンくず"><a href="index.html">ホーム</a>'
        '<span>›</span><span>通信費診断</span></nav>'
        '<section class="hero compact"><p class="eyebrow">無料ツール / 通信費を比較</p>'
        '<h1>家族の通信費・格安SIM見直し診断</h1>'
        '<p class="sim-intro">現在の料金と、公式情報で調べた乗り換え候補を入力すると、'
        '毎月の費用と初年度の総額を比較できます。6回線まで対応。</p></section>'
        '<section class="sim-layout" id="sim-tool" aria-label="通信費見直し入力">'
        '<div class="sim-inputs">'
        '<section class="sim-panel"><h2>1. 比較する回線数</h2>'
        '<p class="sim-muted">個人・家族・車載端末など、必要な回線だけ選んでください。</p>'
        '<label class="sim-field sim-count-wrap" for="sim-count">比較する回線数'
        '<select class="sim-count" id="sim-count">'
        + ''.join(f'<option value="{i}">{i}回線</option>' for i in range(1,7)) +
        '</select></label></section>'
        '<section class="sim-panel"><h2>2. 各回線の通信量と候補料金</h2>'
        '<p class="sim-muted">候補の料金は<strong>公式の税込月額と適用条件を確認して入力</strong>してください。'
        '候補A・Bの一方が不要なら空欄のままで構いません。容量不足や未入力の候補は順位から除外します。</p>'
        '<div class="sim-lines">' + ''.join(_line(i) for i in range(1,7)) + '</div></section>'
        '<section class="sim-panel"><h2>3. 追加費用（任意）</h2>'
        '<p class="sim-muted">家族割引等は各回線の割引適用後の料金に反映してください。'
        '二重に割り引かないよう注意してください。別途かかる月額・初期費用だけ入力します。</p>'
        '<div class="sim-extra">' + ''.join(_field(fid, title) for fid, title in extra) + '</div></section>'
        '<div class="sim-actions"><button type="button" id="sim-reset" class="sim-reset">入力を初期化する</button>'
        '<span class="sim-muted">入力内容をサーバーへ送信・保存しません。</span></div>'
        '</div><aside class="sim-result" aria-labelledby="sim-result-title">'
        '<h2 id="sim-result-title">診断結果</h2>'
        '<p class="sim-muted">初年度の支払総額が最も少ない案</p>'
        '<p id="sim-best" class="sim-best" aria-live="polite">入力待ち</p>'
        '<dl class="sim-results">'
        '<dt>現在の月額</dt><dd id="sim-current-result">—</dd>'
        '<dt>候補Aの月額</dt><dd id="sim-a-result">—</dd>'
        '<dt>候補Bの月額</dt><dd id="sim-b-result">—</dd>'
        '<dt class="sim-sep">候補Aの初年度総額</dt><dd class="sim-sep" id="sim-a-annual">—</dd>'
        '<dt>候補Bの初年度総額</dt><dd id="sim-b-annual">—</dd>'
        '<dt>候補Aの初年度節約額</dt><dd id="sim-a-save">—</dd>'
        '<dt>候補Bの初年度節約額</dt><dd id="sim-b-save">—</dd>'
        '<dt>候補Aの初期費用回収</dt><dd id="sim-a-break">—</dd>'
        '<dt>候補Bの初期費用回収</dt><dd id="sim-b-break">—</dd>'
        '</dl>'
        '<p id="sim-status" class="sim-message" role="status" aria-live="polite"></p>'
        '<p class="sim-muted">これは入力された料金による試算です。'
        '実際の割引適用条件、端末・通話料、速度制限、サービス提供エリア、'
        '株主優待等の適用資格・有効期限は公式サイトで確認してください。'
        '回線の乗換時にキャンペーンを利用する場合、期間終了後の料金も検討してください。</p>'
        '</aside></section>'
        + ('<section class="sim-related guide-box"><h2>通信環境に合う機器もチェック</h2>'
           '<p>SIMフリールーターの周波数・SIM形式・対応バンドは、使用する通信会社との適合を確認してください。</p>'
           '<p><a class="cta" href="category-mobile-router.html">モバイルルーターの比較を見る</a></p>'
           '<p class="sim-muted">製品比較ページには楽天アフィリエイトリンクが含まれます。</p>'
           '</section>' if show_router else '')
        + '<script>' + SIM_JS + '</script>'
    )
