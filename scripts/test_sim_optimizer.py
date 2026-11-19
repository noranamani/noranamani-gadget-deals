"""Offline regression tests for the client-side multi-line SIM cost tool."""
import shutil
import subprocess
import unittest
from html.parser import HTMLParser

from sim_optimizer import SIM_JS, SIM_STYLE, sim_page


class Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.duplicates = set()
    def handle_starttag(self, tag, attrs):
        ident = dict(attrs).get("id")
        if ident:
            if ident in self.ids:
                self.duplicates.add(ident)
            self.ids.add(ident)


class SimOptimizerTests(unittest.TestCase):
    def test_six_line_form_and_privacy(self):
        html = sim_page()
        doc = Markup()
        doc.feed(html)
        self.assertFalse(doc.duplicates)
        for i in range(1, 7):
            for field in ("sim-line", "sim-usage", "sim-current", "sim-a-fee",
                          "sim-a-cap", "sim-a-unlimited", "sim-b-fee", "sim-b-cap",
                          "sim-b-unlimited"):
                self.assertIn(field + "-" + str(i), doc.ids)
        self.assertIn('id="sim-best"', html)
        self.assertIn("入力内容をサーバーへ送信・保存しません", html)
        self.assertIn("公式", html)
        self.assertIn("初年度", html)
        self.assertIn("容量が不足", SIM_JS)
        self.assertNotIn("fetch(", SIM_JS)
        self.assertNotIn("localStorage", SIM_JS)
        self.assertNotIn("XMLHttpRequest", SIM_JS)
        self.assertIn("@media (max-width:480px)", SIM_STYLE)
        self.assertIn('href="category-mobile-router.html"', html)
        self.assertNotIn('href="category-mobile-router.html"', sim_page(show_router=False))

    @unittest.skipUnless(shutil.which("node"), "Node.js not installed")
    def test_real_javascript_calculation_and_eligibility(self):
        case = r"""
const assert = require('node:assert/strict');
const calc = module.exports.calculate;
function data() {
 return {
   lines: [
     {usage:2,current:1000,a:{fee:800,cap:3,unlimited:false},b:{fee:500,cap:1,unlimited:false}},
     {usage:10,current:2000,a:{fee:1800,cap:12,unlimited:false},b:{fee:1000,cap:20,unlimited:false}}
   ],
   currentExtra:500,aExtra:300,aSetup:1200,bExtra:100,bSetup:0
 };
}
let r=calc(data());
assert.equal(r.current.monthly,3500);
assert.equal(r.current.firstYear,42000);
assert.equal(r.a.eligible,true);
assert.equal(r.a.monthly,2900);
assert.equal(r.a.firstYear,36000);
assert.equal(r.a.monthlySaved,600);
assert.equal(r.a.firstYearSaved,6000);
assert.equal(r.a.breakEvenMonths,2);
assert.equal(r.b.eligible,false);
assert.match(r.b.reason,/容量が不足/);
assert.equal(r.best,'a');
let x=data(); x.lines[0].b.unlimited=true;
r=calc(x); assert.equal(r.b.monthly,1600); assert.equal(r.best,'b');
x=data(); x.aSetup=10000;
r=calc(x); assert.equal(r.best,'current');
x=data(); x.lines[0].a.fee=null;
r=calc(x); assert.equal(r.a.eligible,false);
x=data(); x.lines[0].current=null;
assert.throws(()=>calc(x),/現状月額/);
x=data(); x.lines[0].usage=-1;
assert.throws(()=>calc(x),/通信量/);
x=data(); x.lines[0].a.fee=-1;
assert.throws(()=>calc(x),/候補月額/);
x=data(); x.lines[0].a.fee=0; x.lines[0].a.cap=30;
r=calc(x); assert.equal(r.a.eligible,true);
x=data(); x.lines=Array(7).fill(x.lines[0]);
assert.throws(()=>calc(x),/回線数/);
console.log('SIM optimizer arithmetic and eligibility passed');
"""
        proc = subprocess.run(["node", "-e", SIM_JS + "\n" + case],
                              capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
