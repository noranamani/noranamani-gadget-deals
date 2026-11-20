"""Offline browser-event simulation for privacy-preserving GA4 usage counters.

Only a single static event name is sent on the first real interaction.
Form values and filter values must never be passed to gtag.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest

from product_selector import SELECTOR_JS
from sim_optimizer import SIM_JS

RUNNER = r"""
'use strict';
const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = process.argv[1];
const kind = process.argv[2];
const expectedEvent = process.argv[3];
const withGtag = process.argv[4] === 'with-gtag';
const events = [];
const nodes = {};
function node(id) {
  if (nodes[id]) return nodes[id];
  let value = '0';
  if (id === 'sim-count') value = '1';
  if (id === 'selector-price') value = '';
  if (id === 'selector-category') value = 'all';
  if (id === 'selector-order') value = 'score';
  const el = {
    value, defaultValue: value, checked: false,
    textContent: '', hidden: false, disabled: false, dataset: {},
    listeners: {},
    addEventListener(type, listener) {
      (this.listeners[type] ||= []).push(listener);
    },
    emit(type) {
      (this.listeners[type] || []).forEach(fn => fn());
    },
    querySelectorAll() { return []; },
    appendChild() {},
  };
  nodes[id] = el;
  return el;
}
const document = {
  readyState: 'complete',
  getElementById: node,
  querySelectorAll(selector) {
    if (selector === '#sim-tool input' ||
        selector === '#sim-tool input[type=number]') {
      return [node('sim-current-1')];
    }
    if (selector === '#sim-tool input[type=checkbox]') return [];
    return [];
  },
};
const window = withGtag ? {
  gtag(...args) { events.push(args); }
} : {};
vm.runInNewContext(source, {window, document});
assert.equal(events.length, 0, 'initial page rendering must not emit any event');
if (kind === 'sim') {
  node('sim-current-1').value = '12345';
  node('sim-current-1').emit('input');
  node('sim-current-1').emit('input');
  node('sim-count').emit('change');
  node('sim-reset').emit('click');
} else if (kind === 'selector') {
  node('selector-price').value = '12345';
  node('selector-price').emit('input');
  node('selector-category').emit('change');
  node('selector-reset').emit('click');
} else throw new Error('unsupported test kind');
if (withGtag) {
  assert.deepEqual(events, [['event', expectedEvent]],
    'exactly one value-free event is expected per tool page load');
} else {
  assert.equal(events.length, 0, 'blocked analytics should not affect tool usage');
}
console.log('tool usage analytics safe');
"""


@unittest.skipUnless(shutil.which("node"), "Node.js not installed")
class UsageEventTests(unittest.TestCase):
    def check(self, source: str, kind: str, event: str, available: bool) -> None:
        run = subprocess.run(
            ["node", "-e", RUNNER, source, kind, event,
             "with-gtag" if available else "blocked-gtag"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_sim_usage_event_once_after_real_interaction(self):
        self.check(SIM_JS, "sim", "sim_tool_used", True)

    def test_selector_usage_event_once_after_real_interaction(self):
        self.check(SELECTOR_JS, "selector", "product_selector_used", True)

    def test_sim_still_operates_when_analytics_blocked(self):
        self.check(SIM_JS, "sim", "sim_tool_used", False)

    def test_selector_still_operates_when_analytics_blocked(self):
        self.check(SELECTOR_JS, "selector", "product_selector_used", False)


if __name__ == "__main__":
    unittest.main()
