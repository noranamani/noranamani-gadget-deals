"""Offline selector regression: only safely comparable source products are shown."""
import shutil
import subprocess
import unittest
from datetime import datetime, timezone
from html.parser import HTMLParser

from product_selector import MAX_PER_CATEGORY, SELECTOR_JS, SELECTOR_STYLE, selected_products, selector_page
from test_affiliate_build import item


class MarkerParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])


class ProductSelectorTests(unittest.TestCase):
    def snapshot(self):
        items = [item("usb-charger", "c:" + str(n), "65W USB-C充電器", price=1000+n,
                      score=float(n)) for n in range(12)]
        items.extend([
            item("usb-charger", "c:variant", "20W/65W仕様選択 USB-C充電器", price=100),
            item("usb-charger", "c:unsafe", "65W USB-C充電器", price=250,
                 reviewCount=0, score=100)
        ])
        return {"categories":[{"id":"usb-charger", "name":"USB-C充電器",
                               "items":items}]}

    def test_only_recommendable_fixed_price_offers_are_selected(self):
        rows = selected_products(self.snapshot(), datetime.now(timezone.utc))
        self.assertLessEqual(len(rows), MAX_PER_CATEGORY)
        self.assertGreater(len(rows), 0)
        self.assertTrue(all(r[0] == "usb-charger" for r in rows))
        codes = [r[2]["code"] for r in rows]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertNotIn("c:variant", codes)
        self.assertNotIn("c:unsafe", codes)
        self.assertEqual(codes[0], "c:11")

    def test_markup_and_ad_disclosure(self):
        def render(row, **kwargs):
            return ('<article class="product-card" data-price="1000" data-reviews="10" '
                    'data-score="80" data-drop="0"></article>')
        content = selector_page(self.snapshot(), datetime.now(timezone.utc), render)
        p = MarkerParser()
        p.feed(content)
        for id_ in ("selector-grid", "selector-reset", "selector-category",
                    "selector-price", "selector-reviews", "selector-order",
                    "selector-count", "selector-empty"):
            self.assertIn(id_, p.ids)
        self.assertIn('value="usb-charger"', content)
        self.assertIn("市場全体の最安値ではありません", content)
        self.assertIn("楽天アフィリエイトリンク", content)
        self.assertIn("送信・保存しません", content)
        self.assertIn("@media(max-width:520px)", SELECTOR_STYLE)
        self.assertNotIn("fetch(", SELECTOR_JS)
        self.assertNotIn("localStorage", SELECTOR_JS)
        self.assertNotIn("XMLHttpRequest", SELECTOR_JS)

    @unittest.skipUnless(shutil.which("node"), "Node not installed")
    def test_browser_filter_and_sort_logic(self):
        code = r"""
const assert = require('node:assert/strict');
const choose = module.exports.choose;
const rows = [
 {id:'one',index:0,category:'usb',price:1000,reviews:20,score:80,drop:10},
 {id:'two',index:1,category:'usb',price:500,reviews:100,score:60,drop:4},
 {id:'three',index:2,category:'mobile',price:2000,reviews:1,score:90,drop:20}
];
const filter={category:'all',maxPrice:null,minReviews:0,order:'score'};
assert.deepEqual(choose(rows,filter).map(x=>x.id),['three','one','two']);
assert.deepEqual(choose(rows,{...filter,category:'usb',maxPrice:1000,minReviews:10,order:'price'}).map(x=>x.id),['two','one']);
assert.deepEqual(choose(rows,{...filter,order:'reviews'}).map(x=>x.id),['two','one','three']);
assert.deepEqual(choose(rows,{...filter,order:'drop'}).map(x=>x.id),['three','one','two']);
assert.deepEqual(choose(rows,{...filter,maxPrice:10}).map(x=>x.id),[]);
assert.throws(()=>choose(rows,{...filter,maxPrice:-1}),/予算上限/);
console.log('Product filtering checks passed');
"""
        run = subprocess.run(["node", "-e", SELECTOR_JS + "\n" + code],
                             capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
