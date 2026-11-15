'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { parseReady, terminalURL, localRequest, externalSource, downloadName, confirmExit } = require('../boundaries.cjs');
const url = 'http://127.0.0.1:12345/#token=' + 'A'.repeat(43) + '&role=crew';
const ready = parseReady(JSON.stringify({ type: 'ready', version: 1, url }));

test('readiness accepts only exact bounded loopback token contract', () => {
  for (const bad of [url.replace('127.0.0.1', 'localhost'), url.replace('http:', 'https:'), url.replace(':12345', ':80'),
    url.replace('/#', '/api/#'), url + '&token=other', url.replace('role=crew', 'role=doctor'), url + '&other=value',
    url.replace('127.0.0.1', 'user@127.0.0.1'), url.replace('/#', '/?token=bad#')]) {
    assert.throws(() => parseReady(JSON.stringify({ type: 'ready', version: 1, url: bad })));
  }
  assert.throws(() => parseReady('x'.repeat(1025)));
  assert.throws(() => parseReady(JSON.stringify({ type: 'ready', version: 1, url, extra: true })));
});
test('terminal navigation retains private token and approved role only', () => {
  assert.equal(terminalURL(url, ready), true);
  assert.equal(terminalURL(url.replace('role=crew', 'role=doctor'), ready), true);
  for (const bad of ['file:///C:/a', 'javascript:alert(1)', 'https://example.com', url.replace('A', 'B'),
    url.replace('role=crew', 'role=admin'), url.replace('/#', '/app.js#')]) assert.equal(terminalURL(bad, ready), false);
});
test('all remote requests and unapproved external links are blocked', () => {
  assert.equal(localRequest(ready.origin + '/api/state', ready), true);
  for (const bad of ['http://localhost:12345/', 'http://127.0.0.1:12346/', 'https://example.com', 'file:///x',
    'http://user@127.0.0.1:12345/', 'data:text/html,test']) assert.equal(localRequest(bad, ready), false);
  assert.equal(externalSource('https://doi.org/10.1038/s41597-025-05336-7'), true);
  for (const bad of ['http://doi.org/10.1038/s41597-025-05336-7', 'https://doi.org.evil.com',
    'https://doi.org/10.1038/s41597-025-05336-7?x=1', 'file:///C:/x']) assert.equal(externalSource(bad), false);
});
test('exact pinned public source catalog URLs open, near-miss URLs cannot', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const catalog = JSON.parse(fs.readFileSync(path.join(__dirname, '../../..', 'assets/desktop/assistant-protocols-v1.json'), 'utf8'));
  const sources = require('../external-links.json');
  assert.deepEqual(sources, ['https://doi.org/10.1038/s41597-025-05336-7', ...catalog.sources.map(s => s.url)]);
  for (const url of sources) {
    assert.equal(externalSource(url), true);
    for (const bad of [url + '?token=private', url + '#fragment', url.replace('https:', 'http:'),
      url.replace('://', '://user@'), url.replace('://', '://evil.')]) assert.equal(externalSource(bad), false);
  }
});
test('download filename cannot suggest arbitrary filesystem path', () => {
  assert.equal(downloadName('../../a\\file.json'), '.._.._a_file.json');
  assert.equal(downloadName('...'), 'BioTwin-export.json');
  assert.equal(downloadName('a'.repeat(200)).length, 96);
});
test('healthy app exit keeps host alive until explicit discard approval', () => {
  const healthy = { healthy: true, windowCount: 1, approved: false, closing: false, failed: false };
  assert.equal(confirmExit(healthy), true);
  assert.equal(confirmExit({ ...healthy, windowCount: 2 }), true);
  for (const change of [{ healthy: false }, { windowCount: 0 }, { approved: true }, { closing: true }, { failed: true }]) {
    assert.equal(confirmExit({ ...healthy, ...change }), false);
  }
});
