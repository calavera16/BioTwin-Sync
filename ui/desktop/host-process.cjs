'use strict';
const { spawn } = require('node:child_process');
const { parseReady } = require('./boundaries.cjs');

function launchHost(command, args, options = {}) {
  const child = (options.spawn || spawn)(command, args, {
    cwd: options.cwd, windowsHide: true, shell: false, stdio: ['pipe', 'pipe', 'pipe'],
    env: options.env || process.env,
  });
  let stopped = false;
  let ready = false;
  let buffer = '';
  let rejectReady;
  const stop = () => {
    if (stopped) return;
    stopped = true;
    clearTimeout(timer);
    if (child.exitCode === null && child.signalCode === null) {
      child.stdin.end('stop\n');
      const killTimer = setTimeout(() => { if (child.exitCode === null) child.kill(); }, 2500);
      killTimer.unref();
      child.once('exit', () => clearTimeout(killTimer));
    }
  };
  const promise = new Promise((resolve, reject) => {
    rejectReady = reject;
    child.stdout.on('data', chunk => {
      if (ready) { stop(); options.onFailure?.(); return; }
      buffer += chunk.toString('utf8');
      if (Buffer.byteLength(buffer) > 1025) { reject(Error('Invalid host response.')); stop(); return; }
      if (!buffer.includes('\n')) return;
      try {
        const lines = buffer.split('\n');
        if (lines.length !== 2 || lines[1]) throw Error('Extra host response.');
        const value = parseReady(lines[0]);
        buffer = '';
        ready = true;
        clearTimeout(timer);
        resolve(value);
      } catch { reject(Error('Invalid host response.')); stop(); }
    });
    // Never collect or forward host diagnostics: tokens cannot enter shell logs.
    child.stderr.resume();
    child.stdin.on('error', () => {});
    child.once('error', () => { reject(Error('Local host could not start.')); stop(); });
    child.once('exit', () => {
      clearTimeout(timer);
      if (!ready) reject(Error('Local host closed before readiness.'));
      else if (!stopped) options.onFailure?.();
    });
  });
  const timer = setTimeout(() => { rejectReady(Error('Local host startup timed out.')); stop(); }, options.timeout || 20000);
  return { child, ready: promise, stop };
}
module.exports = { launchHost };
