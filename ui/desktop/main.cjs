'use strict';
const { app, BrowserWindow, session, shell, dialog, ipcMain } = require('electron');
const path = require('node:path');
const { launchHost } = require('./host-process.cjs');
const { terminalURL, localRequest, externalSource, downloadName, confirmExit } = require('./boundaries.cjs');

app.setName('BioTwin Sync');
app.setAppUserModelId('org.biotwin.sync');
app.commandLine.appendSwitch('disable-background-networking');
app.commandLine.appendSwitch('disable-component-update');
app.commandLine.appendSwitch('disable-domain-reliability');
app.commandLine.appendSwitch('disable-sync');
app.commandLine.appendSwitch('no-proxy-server');
let host;
let ready;
let ownedSession;
let closing = false;
let showingError = false;
let healthy = false;
let exitApproved = false;
let askingClose = false;
const windows = new Set();

function stop() { host?.stop(); }
function needsExitConfirmation() {
  return confirmExit({ healthy, windowCount: windows.size, approved: exitApproved, closing, failed: showingError });
}
async function requestExit(owner) {
  if (askingClose || !needsExitConfirmation()) return;
  askingClose = true;
  try {
    const result = await dialog.showMessageBox(owner, { type: 'warning', title: 'Close BioTwin Sync?',
      message: 'Save your session before closing the workspace.',
      detail: 'This local workspace is temporary. Keep the app open and use Save session to save any recorded sessions you need. Closing discards the current workspace for both crew members.',
      buttons: ['Keep open', 'Discard workspace and close'], defaultId: 0, cancelId: 0,
      noLink: true });
    if (result.response === 1) { exitApproved = true; app.quit(); }
  } catch { /* Keep the healthy workspace open if the dialog cannot complete. */ }
  finally { askingClose = false; }
}
function preferences() {
  return { session: ownedSession, sandbox: true, contextIsolation: true, nodeIntegration: false,
    preload: path.join(__dirname, 'source-links-preload.cjs'),
    webSecurity: true, allowRunningInsecureContent: false, devTools: !app.isPackaged,
    spellcheck: false, navigateOnDragDrop: false };
}
function terminal(url, visible = true, opener = null) {
  if (!terminalURL(url, ready)) throw Error('Unsupported terminal address.');
  if (windows.size >= 2) { const other = [...windows].find(w => w !== opener) || [...windows][0]; other.focus(); return other; }
  const window = new BrowserWindow({ width: 1440, height: 940, minWidth: 780, minHeight: 620,
    show: false, title: 'BioTwin Sync', backgroundColor: '#101720', autoHideMenuBar: true,
    icon: path.join(__dirname, 'biotwin.ico'),
    webPreferences: preferences() });
  window.setMenu(null);
  windows.add(window);
  window.on('close', event => {
    if (windows.size === 1 && needsExitConfirmation()) { event.preventDefault(); requestExit(window); }
  });
  // Windows session termination is an OS shutdown, not an interactive discard.
  window.on('session-end', () => { healthy = false; exitApproved = true; stop(); });
  window.once('closed', () => { windows.delete(window); if (!windows.size && !showingError) app.quit(); });
  window.webContents.on('will-attach-webview', event => event.preventDefault());
  window.webContents.on('will-navigate', (event, target) => {
    if (terminalURL(target, ready)) return;
    event.preventDefault();
  });
  window.webContents.setWindowOpenHandler(({ url: target }) => {
    if (terminalURL(target, ready)) terminal(target, true, window);
    return { action: 'deny' };
  });
  window.webContents.on('did-fail-load', (_event, code, _description, _url, isMainFrame) => {
    if (isMainFrame && code !== -3 && !closing) failure();
  });
  if (visible) window.once('ready-to-show', () => window.show());
  window.loadURL(url).catch(() => { if (!closing) failure(); });
  return window;
}

function setupSession() {
  // No persist: prefix: browser session storage and the token stay in memory.
  ownedSession = session.fromPartition('biotwin-' + process.pid);
  ownedSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  ownedSession.setPermissionCheckHandler(() => false);
  ownedSession.on('will-download', (event, item, contents) => {
    if (![...windows].some(w => w.webContents === contents) ||
        !item.getURLChain().every(url => localRequest(url, ready) || url.startsWith('blob:' + ready.origin + '/'))) {
      event.preventDefault(); return;
    }
    item.setSaveDialogOptions({ title: 'Save BioTwin export', defaultPath: downloadName(item.getFilename()),
      filters: [{ name: 'JSON session', extensions: ['json'] }, { name: 'All files', extensions: ['*'] }] });
    // Electron's normal Save dialog owns selection and filesystem access.
  });
  ownedSession.webRequest.onBeforeRequest({ urls: ['<all_urls>'] }, (details, callback) => {
    const permitted = ready && (localRequest(details.url, ready) || details.url.startsWith('blob:' + ready.origin + '/'));
    callback({ cancel: !permitted });
  });
}

async function failure() {
  if (closing || showingError) return;
  showingError = true;
  healthy = false;
  stop();
  const errorWindow = new BrowserWindow({ width: 560, height: 340, show: true, title: 'BioTwin Sync — local host',
    icon: path.join(__dirname, 'biotwin.ico'),
    autoHideMenuBar: true, webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } });
  errorWindow.setMenu(null);
  errorWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  errorWindow.webContents.on('will-navigate', event => event.preventDefault());
  await errorWindow.loadFile(path.join(__dirname, 'startup-error.html'));
  const result = await dialog.showMessageBox(errorWindow, { type: 'error', title: 'BioTwin Sync',
    message: 'The local workspace could not start or has stopped.',
    detail: 'Your saved exports remain available. Retry starts a new local workspace. No Internet connection or browser configuration is required.',
    buttons: ['Retry', 'Close'], defaultId: 0, cancelId: 1 });
  for (const w of windows) w.destroy();
  windows.clear();
  errorWindow.destroy();
  showingError = false;
  if (result.response === 0) start(); else app.quit();
}

async function start() {
  const backend = app.isPackaged ? path.join(process.resourcesPath, 'backend', 'BioTwinHost.exe') : process.env.BIOTWIN_PYTHON || 'python';
  const args = app.isPackaged ? [] : [path.resolve(__dirname, '../../run_webhost.py')];
  host = launchHost(backend, args, { cwd: app.isPackaged ? process.resourcesPath : path.resolve(__dirname, '../..'), onFailure: failure });
  try {
    ready = await host.ready;
    setupSession();
    terminal(ready.url);
    healthy = true;
  } catch { failure(); }
}
app.on('before-quit', event => {
  if (needsExitConfirmation()) { event.preventDefault(); requestExit([...windows][0]); return; }
  closing = true; stop();
});
app.on('window-all-closed', () => { if (!showingError) app.quit(); });
ipcMain.on('biotwin:open-reviewed-source', (event, target) => {
  const owner = [...windows].find(window => window.webContents === event.sender);
  if (!owner || event.senderFrame !== event.sender.mainFrame || !ready ||
      !localRequest(event.senderFrame.url, ready) || !externalSource(target)) return;
  shell.openExternal(target).catch(() => {});
});
app.whenReady().then(start).catch(failure);
