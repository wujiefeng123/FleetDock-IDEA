// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Provide an IDE-styled confirmation dialog for stopping simulation-owned MAVSDK links.

import { BrowserWindow, ipcMain } from 'electron';
import * as path from 'path';
import { ThemedWindow } from '../../dialog/themedwindow';
import { EventTypeMain } from '../../fleetdock/eventtypes';
import { IFleetDockMavsdkServerProcess } from '../../fleetdock/mavsdk-cleanup';

const TXT = {
  title: '\u505c\u6b62\u4eff\u771f\u4f1a\u65ad\u5f00\u8bbe\u5907\u8fde\u63a5',
  message:
    '\u68c0\u6d4b\u5230\u5f53\u524d\u5e94\u7528\u4ecd\u6709 MAVSDK \u8bbe\u5907\u8fde\u63a5\u3002\u505c\u6b62\u4eff\u771f\u4f1a\u540c\u65f6\u65ad\u5f00\u8fd9\u4e9b\u8bbe\u5907\u8fde\u63a5\u3002',
  endpoints: '\u5c06\u65ad\u5f00\u7684\u8fde\u63a5',
  cancel: '\u53d6\u6d88',
  confirm: '\u505c\u6b62\u4eff\u771f\u5e76\u65ad\u5f00\u8bbe\u5907',
  warning: '\u8b66\u544a'
} as const;

export interface IUavSimulationDisconnectDialogOptions {
  parent: BrowserWindow;
  isDarkTheme: boolean;
  servers: IFleetDockMavsdkServerProcess[];
}

export function confirmUavSimulationDisconnect(
  options: IUavSimulationDisconnectDialogOptions
): Promise<boolean> {
  const themedWindow = new ThemedWindow({
    isDarkTheme: options.isDarkTheme,
    parent: options.parent,
    modal: true,
    title: TXT.title,
    width: 560,
    height: 352,
    resizable: false,
    preload: path.join(__dirname, './uav-simulation-disconnect-preload.js')
  });
  const dialogWindow = themedWindow.window;

  return new Promise<boolean>(resolve => {
    let finished = false;

    const finish = (confirmed: boolean) => {
      if (finished) {
        return;
      }
      finished = true;
      ipcMain.removeListener(
        EventTypeMain.UavSimulationDisconnectDialogChoice,
        handleChoice
      );
      if (!dialogWindow.isDestroyed()) {
        themedWindow.close();
      }
      resolve(confirmed);
    };

    const handleChoice = (event: Electron.IpcMainEvent, confirmed: boolean) => {
      if (event.sender !== dialogWindow.webContents) {
        return;
      }
      finish(confirmed === true);
    };

    ipcMain.on(EventTypeMain.UavSimulationDisconnectDialogChoice, handleChoice);
    dialogWindow.on('closed', () => finish(false));
    themedWindow.loadDialogContent(buildPageBody(options.servers));
  });
}

function buildPageBody(servers: IFleetDockMavsdkServerProcess[]): string {
  const endpointItems = servers
    .map(
      item =>
        `<li><code>${escapeHtml(item.endpoint)} (PID ${escapeHtml(
          String(item.pid)
        )})</code></li>`
    )
    .join('');

  return `
<style>
#jlab-dialog-body{padding:0!important;overflow:hidden!important}
.uav-disconnect-dialog{height:100%;box-sizing:border-box;display:flex;flex-direction:column;background:#f6f8fb;color:#172033;font:13px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Helvetica,Arial,sans-serif}
.uav-disconnect-main{flex:1;min-height:0;padding:24px 24px 16px;display:flex;flex-direction:column;gap:16px}
.uav-disconnect-hero{display:grid;grid-template-columns:44px 1fr;gap:14px;align-items:start}
.uav-disconnect-icon{width:40px;height:40px;border-radius:10px;background:#fff7ed;border:1px solid #fed7aa;color:#c2410c;display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:800;box-shadow:0 8px 22px rgba(251,146,60,.18)}
.uav-disconnect-title{margin:0;color:#0f172a;font-size:18px;font-weight:750;letter-spacing:0}
.uav-disconnect-message{margin:6px 0 0;color:#475569;font-size:14px}
.uav-disconnect-list{border:1px solid #dbe5f0;border-radius:8px;background:#fff;overflow:hidden}
.uav-disconnect-list-title{height:34px;padding:0 12px;display:flex;align-items:center;border-bottom:1px solid #eef2f7;background:#fbfdff;color:#64748b;font-weight:650}
.uav-disconnect-list ul{margin:0;padding:8px 12px 10px;max-height:92px;overflow:auto;list-style:none}
.uav-disconnect-list li+li{margin-top:6px}
.uav-disconnect-list code{font:12px/1.45 Consolas,'SFMono-Regular',Menlo,monospace;color:#1e293b;word-break:break-all}
.uav-disconnect-actions{height:64px;padding:12px 24px;border-top:1px solid #dbe5f0;background:#fff;display:flex;justify-content:flex-end;align-items:center;gap:10px}
.uav-disconnect-btn{height:36px;min-width:104px;padding:0 16px;border:1px solid #dbe5f0;border-radius:8px;background:#fff;color:#172033;cursor:pointer;font:13px/1 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Helvetica,Arial,sans-serif}
.uav-disconnect-btn:hover{border-color:#93c5fd;box-shadow:0 6px 18px rgba(37,99,235,.08)}
.uav-disconnect-btn:focus{outline:none;box-shadow:0 0 0 3px rgba(37,99,235,.16)}
.uav-disconnect-btn.primary{border-color:#2563eb;background:#2563eb;color:#fff;font-weight:650}
.uav-disconnect-btn.primary:hover{background:#1d4ed8}
body.app-ui-dark .uav-disconnect-dialog{background:#1f2937;color:#e5e7eb}
body.app-ui-dark .uav-disconnect-title{color:#f9fafb}
body.app-ui-dark .uav-disconnect-message{color:#cbd5e1}
body.app-ui-dark .uav-disconnect-list,body.app-ui-dark .uav-disconnect-actions{background:#111827;border-color:#374151}
body.app-ui-dark .uav-disconnect-list-title{background:#182230;border-color:#374151;color:#cbd5e1}
body.app-ui-dark .uav-disconnect-list code{color:#e5e7eb}
body.app-ui-dark .uav-disconnect-btn{background:#1f2937;border-color:#475569;color:#f8fafc}
body.app-ui-dark .uav-disconnect-btn.primary{background:#2563eb;border-color:#2563eb;color:#fff}
</style>
<section class="uav-disconnect-dialog" role="dialog" aria-modal="true" aria-labelledby="uav-disconnect-title">
  <div class="uav-disconnect-main">
    <div class="uav-disconnect-hero">
      <div class="uav-disconnect-icon" aria-label="${TXT.warning}">!</div>
      <div>
        <h1 id="uav-disconnect-title" class="uav-disconnect-title">${TXT.title}</h1>
        <p class="uav-disconnect-message">${TXT.message}</p>
      </div>
    </div>
    <div class="uav-disconnect-list">
      <div class="uav-disconnect-list-title">${TXT.endpoints}</div>
      <ul>${endpointItems}</ul>
    </div>
  </div>
  <div class="uav-disconnect-actions">
    <button class="uav-disconnect-btn" type="button" data-action="cancel">${TXT.cancel}</button>
    <button class="uav-disconnect-btn primary" type="button" data-action="confirm" autofocus>${TXT.confirm}</button>
  </div>
</section>
<script>
(() => {
  const api = window.uavSimulationDisconnectDialog;
  const choose = (confirmed) => {
    if (api && typeof api.choose === 'function') {
      api.choose(confirmed);
      return;
    }
    if (!confirmed) {
      window.close();
    }
  };
  document.querySelector('[data-action="cancel"]').addEventListener('click', () => choose(false));
  document.querySelector('[data-action="confirm"]').addEventListener('click', () => choose(true));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      choose(false);
    }
  });
})();
</script>`;
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, char => {
    switch (char) {
      case '&':
        return '&amp;';
      case '<':
        return '&lt;';
      case '>':
        return '&gt;';
      case '"':
        return '&quot;';
      default:
        return '&#39;';
    }
  });
}
