'use strict';
// Sandbox preload: no contextBridge, globals, filesystem or arbitrary invoke.
// The page cannot call this private sender. Synthetic clicks are not trusted.
const { ipcRenderer } = require('electron');
window.addEventListener('click', event => {
  if (!event.isTrusted || event.button !== 0 || event.defaultPrevented) return;
  const anchor = event.target?.closest?.('a[href]');
  if (!anchor) return;
  let url;
  try { url = new URL(anchor.href); } catch { return; }
  if (!['http:', 'https:'].includes(url.protocol) || url.origin === location.origin) return;
  event.preventDefault();
  ipcRenderer.send('biotwin:open-reviewed-source', url.href);
}, true);
