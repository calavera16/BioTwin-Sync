'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function harness() {
  let callback; const sent = [];
  const sandbox = { URL, location: { origin: 'http://127.0.0.1:12345' },
    window: { addEventListener: (name, listener, capture) => { assert.equal(name, 'click'); assert.equal(capture, true); callback = listener; } },
    require: name => { assert.equal(name, 'electron'); return { ipcRenderer: { send: (...args) => sent.push(args) } }; } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../source-links-preload.cjs'), 'utf8'), sandbox);
  function click(href, changes = {}) {
    let prevented = false;
    callback({ isTrusted: true, button: 0, defaultPrevented: false,
      target: { closest: () => ({ href }) }, preventDefault: () => { prevented = true; }, ...changes });
    return prevented;
  }
  return { click, sent, sandbox };
}
test('only trusted external anchor click can reach the narrow reviewed-source channel', () => {
  const h = harness();
  assert.equal(h.click('https://www.nhs.uk/symptoms/headaches/'), true);
  assert.deepEqual(h.sent, [['biotwin:open-reviewed-source', 'https://www.nhs.uk/symptoms/headaches/']]);
  assert.equal(Object.keys(h.sandbox.window).join(), 'addEventListener');
});
test('synthetic, local, non-http and prevented clicks expose no preload API', () => {
  const h = harness();
  for (const href of ['http://127.0.0.1:12345/#token=private', 'file:///C:/secret', 'javascript:alert(1)']) assert.equal(h.click(href), false);
  for (const changes of [{ isTrusted: false }, { button: 1 }, { defaultPrevented: true }]) assert.equal(h.click('https://example.com', changes), false);
  assert.deepEqual(h.sent, []);
});
