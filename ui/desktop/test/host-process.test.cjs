'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const { launchHost } = require('../host-process.cjs');

function fake() {
  const child = new EventEmitter();
  child.stdout = new PassThrough(); child.stderr = new PassThrough(); child.stdin = new PassThrough();
  child.exitCode = null; child.signalCode = null; child.kills = 0;
  child.kill = () => { child.kills++; child.exitCode = 1; child.emit('exit', 1); };
  return child;
}
const url = 'http://127.0.0.1:12345/#token=' + 'A'.repeat(43) + '&role=crew';
test('pipe readiness stays in memory, launch hides Windows console, stop only owns child', async () => {
  const child = fake(); let received; let input = '';
  child.stdin.on('data', bytes => { input += bytes.toString(); });
  const host = launchHost('host.exe', [], { spawn: (_cmd, _args, options) => { received = options; return child; } });
  child.stdout.write(JSON.stringify({ type: 'ready', version: 1, url }) + '\n');
  assert.equal((await host.ready).url, url);
  assert.equal(received.windowsHide, true); assert.equal(received.shell, false);
  assert.deepEqual(received.stdio, ['pipe', 'pipe', 'pipe']);
  host.stop(); host.stop();
  assert.equal(input, 'stop\n');
  child.exitCode = 0; child.emit('exit', 0);
  assert.equal(child.kills, 0);
});
test('oversized, malformed and extra host stdout are rejected and stopped', async () => {
  for (const output of ['x'.repeat(1026), 'not-json\n', JSON.stringify({ type: 'ready', version: 1, url }) + '\nextra']) {
    const child = fake();
    const host = launchHost('host.exe', [], { spawn: () => child });
    child.stdout.write(output);
    await assert.rejects(host.ready, /Invalid host response/);
    assert.equal(child.stdin.writableEnded, true);
    child.exitCode = 0; child.emit('exit', 0);
  }
});
test('early exit, startup timeout and host crash are observable', async () => {
  let child = fake(); let host = launchHost('host.exe', [], { spawn: () => child });
  child.exitCode = 1; child.emit('exit', 1); await assert.rejects(host.ready, /before readiness/);
  child = fake(); host = launchHost('host.exe', [], { spawn: () => child, timeout: 10 });
  await assert.rejects(host.ready, /timed out/); child.exitCode = 0; child.emit('exit', 0);
  child = fake(); let failures = 0;
  host = launchHost('host.exe', [], { spawn: () => child, onFailure: () => failures++ });
  child.stdout.write(JSON.stringify({ type: 'ready', version: 1, url }) + '\n');
  await host.ready; child.exitCode = 1; child.emit('exit', 1); assert.equal(failures, 1);
});
