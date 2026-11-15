'use strict';
const EXTERNAL_SOURCES = Object.freeze(require('./external-links.json'));

function parseReady(line) {
  if (typeof line !== 'string' || Buffer.byteLength(line) > 1024) throw Error('Invalid host readiness.');
  const value = JSON.parse(line);
  if (!value || Object.keys(value).sort().join() !== 'type,url,version' || value.type !== 'ready' || value.version !== 1) throw Error('Invalid host readiness.');
  const url = new URL(value.url);
  const fragment = new URLSearchParams(url.hash.slice(1));
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !/^\d{1,5}$/.test(url.port) || +url.port < 1024 || +url.port > 65535 || url.pathname !== '/' || url.search || url.username || url.password ||
      [...fragment.keys()].sort().join() !== 'role,token' || fragment.get('role') !== 'crew' || !/^[A-Za-z0-9_-]{43}$/.test(fragment.get('token') || '')) throw Error('Invalid local host address.');
  return { url: url.href, origin: url.origin, token: fragment.get('token') };
}
function terminalURL(raw, ready) {
  try {
    const url = new URL(raw);
    const f = new URLSearchParams(url.hash.slice(1));
    return url.origin === ready.origin && url.pathname === '/' && !url.search && !url.username && !url.password &&
      [...f.keys()].sort().join() === 'role,token' && f.get('token') === ready.token && ['crew', 'doctor'].includes(f.get('role'));
  } catch { return false; }
}
function localRequest(raw, ready) {
  try { const url = new URL(raw); return url.origin === ready.origin && !url.username && !url.password && url.protocol === 'http:'; }
  catch { return false; }
}
function externalSource(raw) { return typeof raw === 'string' && EXTERNAL_SOURCES.includes(raw); }
function downloadName(raw) {
  const value = String(raw).replace(/[^A-Za-z0-9._ -]/g, '_').slice(0, 96);
  return value && !/^\.+$/.test(value) ? value : 'BioTwin-export.json';
}
function confirmExit({ healthy, windowCount, approved, closing, failed }) {
  return Boolean(healthy && windowCount > 0 && !approved && !closing && !failed);
}
module.exports = { parseReady, terminalURL, localRequest, externalSource, downloadName, confirmExit };
