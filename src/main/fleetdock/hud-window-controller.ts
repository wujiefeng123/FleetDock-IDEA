// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import { app, BrowserWindow, screen, WebContents } from 'electron';
import * as fs from 'fs';
import * as path from 'path';
import { DarkThemeBGColor, LightThemeBGColor } from '../utils';

type FleetDockHudDevice = {
  id?: string;
  deviceId?: string;
  name?: string;
  envVarName?: string;
  airframeType?: string;
  airsimVehicleName?: string;
  uav?: {
    airframeType?: string;
  };
  simulation?: {
    airsimVehicleName?: string;
    model?: string;
  };
  metadata?: {
    airframeType?: string;
  };
};

const HUD_CONTENT_WIDTH = 330;
const HUD_CONTENT_HEIGHT = 340;
const HUD_WINDOW_GAP = 12;

// [UAV-IDE MODIFY] Own standalone FleetDock HUD BrowserWindows outside the JupyterLab page DOM.
export class FleetDockHudWindowController {
  constructor(options: FleetDockHudWindowController.IOptions) {
    this._owner = options.owner;
    this._isDarkTheme = options.isDarkTheme;
  }

  openDevices(devices: FleetDockHudDevice[]): void {
    const normalized = (Array.isArray(devices) ? devices : []).filter(Boolean);
    const activeKeys = new Set(
      normalized.map((device, index) => this._deviceKey(device, index))
    );
    for (const [key, window] of Array.from(this._windows.entries())) {
      if (!activeKeys.has(key)) {
        window.close();
      }
    }
    normalized.forEach((device, index) => {
      const key = this._deviceKey(device, index);
      const existing = this._windows.get(key);
      if (existing && !existing.isDestroyed()) {
        existing.loadFile(this._pageFilePath(key, device));
        existing.setResizable(false);
        existing.setContentSize(HUD_CONTENT_WIDTH, HUD_CONTENT_HEIGHT);
        this._positionWindow(existing, index);
        if (existing.isMinimized()) {
          existing.restore();
        }
        existing.show();
        existing.moveTop();
        existing.focus();
        return;
      }
      this._createWindow(key, device, index);
    });
  }

  ownsWebContents(webContents: WebContents): boolean {
    return Array.from(this._windows.values()).some(
      window => !window.isDestroyed() && window.webContents === webContents
    );
  }

  dispose(): void {
    for (const window of Array.from(this._windows.values())) {
      if (!window.isDestroyed()) {
        window.close();
      }
    }
    this._windows.clear();
  }

  private _createWindow(
    key: string,
    device: FleetDockHudDevice,
    index: number
  ): void {
    const bounds = this._hudBounds(index);
    const window = new BrowserWindow({
      x: bounds.x,
      y: bounds.y,
      width: HUD_CONTENT_WIDTH,
      height: HUD_CONTENT_HEIGHT,
      useContentSize: true,
      parent: this._owner,
      minWidth: HUD_CONTENT_WIDTH,
      minHeight: HUD_CONTENT_HEIGHT,
      maxWidth: HUD_CONTENT_WIDTH,
      maxHeight: HUD_CONTENT_HEIGHT,
      title: `FleetDock HUD - ${this._deviceName(device, index)}`,
      show: false,
      frame: true,
      resizable: false,
      minimizable: true,
      maximizable: false,
      alwaysOnTop: false,
      closable: true,
      autoHideMenuBar: true,
      backgroundColor: this._isDarkTheme()
        ? DarkThemeBGColor
        : LightThemeBGColor,
      webPreferences: {
        preload: path.join(__dirname, './hud-window-preload.js'),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: false
      }
    });
    // [UAV-IDE MODIFY] Keep each HUD owned by the IDE window instead of globally pinning it above every Windows app.
    window.setMenuBarVisibility(false);
    window.setResizable(false);
    window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    window.webContents.on('did-finish-load', () => {
      if (!window.isDestroyed()) {
        window.setContentSize(HUD_CONTENT_WIDTH, HUD_CONTENT_HEIGHT);
        this._positionWindow(window, index);
      }
    });
    window.once('ready-to-show', () => {
      window.setContentSize(HUD_CONTENT_WIDTH, HUD_CONTENT_HEIGHT);
      this._positionWindow(window, index);
      window.show();
      window.moveTop();
      window.focus();
    });
    window.on('closed', () => {
      if (this._windows.get(key) === window) {
        this._windows.delete(key);
      }
    });
    this._windows.set(key, window);
    window.loadFile(this._pageFilePath(key, device));
  }

  private _hudBounds(index: number): Electron.Rectangle {
    const ownerBounds = this._owner.getBounds();
    const display = screen.getDisplayMatching(ownerBounds);
    const workArea = display.workArea;
    const rightLimit = workArea.x + workArea.width;
    const bottomLimit = workArea.y + workArea.height;
    const startX = Math.max(workArea.x, ownerBounds.x);
    const belowOwnerY = ownerBounds.y + ownerBounds.height + HUD_WINDOW_GAP;
    const availableWidth = Math.max(HUD_CONTENT_WIDTH, rightLimit - startX);
    const columns = Math.max(
      1,
      Math.floor(
        (availableWidth + HUD_WINDOW_GAP) / (HUD_CONTENT_WIDTH + HUD_WINDOW_GAP)
      )
    );
    const column = index % columns;
    const row = Math.floor(index / columns);
    const x = Math.min(
      startX + column * (HUD_CONTENT_WIDTH + HUD_WINDOW_GAP),
      rightLimit - HUD_CONTENT_WIDTH
    );
    const y = Math.min(
      belowOwnerY + row * (HUD_CONTENT_HEIGHT + HUD_WINDOW_GAP),
      bottomLimit - HUD_CONTENT_HEIGHT
    );
    return {
      x: Math.max(workArea.x, x),
      y: Math.max(workArea.y, y),
      width: HUD_CONTENT_WIDTH,
      height: HUD_CONTENT_HEIGHT
    };
  }

  private _positionWindow(window: BrowserWindow, index: number): void {
    window.setBounds(this._hudBounds(index));
  }

  private _pageFilePath(key: string, device: FleetDockHudDevice): string {
    const directory = path.join(app.getPath('userData'), 'skylab-hud-windows');
    fs.mkdirSync(directory, { recursive: true });
    const filePath = path.join(directory, `${this._safeFileName(key)}.html`);
    fs.writeFileSync(filePath, this._pageSource(device), 'utf8');
    return filePath;
  }

  private _pageSource(device: FleetDockHudDevice): string {
    const payload = JSON.stringify({
      id: device.id || device.deviceId || '',
      name: device.name || '',
      envVarName: device.envVarName || '',
      airsimVehicleName:
        device.simulation?.airsimVehicleName || device.airsimVehicleName || '',
      airframeType:
        device.uav?.airframeType ||
        device.simulation?.model ||
        device.airframeType ||
        device.metadata?.airframeType ||
        ''
    }).replace(/</g, '\\u003c');
    const dark = this._isDarkTheme();
    return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'self' data: 'unsafe-inline'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FleetDock HUD</title>
<style>
:root{--bg:${dark ? '#111827' : '#eef6ff'};--panel:${
      dark ? '#111827' : '#fff'
    };--soft:${dark ? '#172033' : '#f8fbff'};--border:${
      dark ? '#334155' : '#dbe7f3'
    };--text:${dark ? '#f8fafc' : '#0f172a'};--muted:${
      dark ? '#94a3b8' : '#64748b'
    };--blue:#2563eb;--cyan:#06b6d4;--green:#16a34a;--danger:#dc2626}
*{box-sizing:border-box}
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:var(--bg);color:var(--text);font:12px/1.35 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Arial,sans-serif}
.hud{height:100%;display:flex;flex-direction:column;min-height:0;background:var(--panel)}
.head{height:37px;padding:4px 8px 4px 10px;display:flex;align-items:center;justify-content:space-between;gap:7px;border-bottom:1px solid var(--border);background:var(--soft)}
.head div{min-width:0}.head strong,.head span{display:block;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.head strong{font-size:12px;font-weight:800;line-height:1.15}.head span{color:var(--muted);font-size:10px;line-height:1.2;margin-top:2px}
.pill{flex:0 0 auto;min-width:52px;height:20px;padding:0 7px;border-radius:999px;display:inline-flex;align-items:center;justify-content:center;background:rgba(100,116,139,.12);color:var(--muted);font-size:10px}.pill[data-online="1"]{background:rgba(22,163,74,.1);color:#15803d}
.body{padding:6px;overflow:hidden;min-height:0;flex:1}.attitude{height:100%;min-height:0;display:grid;grid-template-rows:auto minmax(116px,1fr) auto auto;gap:4px;overflow:hidden;border:1px solid var(--border);border-radius:8px;background:radial-gradient(circle at 50% 48%,rgba(37,99,235,.08),transparent 48%),var(--panel);padding:6px}.attitude[data-connected="0"]{opacity:.58}
.scene{position:relative;min-height:116px;overflow:hidden;border-radius:7px;background:linear-gradient(180deg,rgba(248,251,255,.55),rgba(239,246,255,.22));perspective:420px}
.horizon{position:absolute;inset:0;pointer-events:none}.horizon span{position:absolute;left:50%;top:50%;background:rgba(37,99,235,.16);transform:translate(-50%,-50%)}.horizon span:first-child{width:74%;height:1px}.horizon span:last-child{width:1px;height:72%}
.model{position:absolute;left:50%;top:50%;width:138px;height:88px;transform-style:preserve-3d;transform:translate(-50%,-50%) rotateZ(var(--yaw)) rotateX(var(--pitch)) rotateY(var(--roll));transition:transform 420ms linear}.model i,.model b,.model em{position:absolute;display:block;box-sizing:border-box}.arm{left:50%;top:50%;width:114px;height:5px;margin-left:-57px;margin-top:-2.5px;border-radius:999px;background:linear-gradient(90deg,rgba(15,23,42,.18),var(--blue),rgba(15,23,42,.18));transform-origin:50% 50%}.arm-b{transform:rotateZ(90deg)}.body-shape{left:50%;top:50%;width:32px;height:20px;margin-left:-16px;margin-top:-10px;border:1px solid rgba(37,99,235,.36);border-radius:50% 50% 44% 44%;background:linear-gradient(180deg,rgba(255,255,255,.94),rgba(219,234,254,.86));box-shadow:0 8px 18px rgba(37,99,235,.18)}.nose{left:50%;top:50%;width:9px;height:16px;margin-left:-4.5px;margin-top:-27px;clip-path:polygon(50% 0,100% 100%,0 100%);background:var(--cyan)}.rotor{width:22px;height:22px;border:2px solid rgba(37,99,235,.5);border-radius:999px;background:radial-gradient(circle,rgba(6,182,212,.18) 0 34%,rgba(255,255,255,.7) 36% 100%);box-shadow:0 6px 14px rgba(15,23,42,.12)}.r1{left:3px;top:1px}.r2{right:3px;top:1px}.r3{left:3px;bottom:1px}.r4{right:3px;bottom:1px}
.readouts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:5px}.readout{min-width:0;padding:0 2px}.readout span,.readout strong{display:block;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.readout span{color:var(--muted);font-size:10px;line-height:1.15}.readout strong{margin-top:1px;color:var(--text);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;line-height:1.25}
body .readout[data-state="warn"] strong{color:#b91c1c}.hud-foot{display:grid;grid-template-columns:minmax(0,1fr);gap:2px}.angles{display:flex;justify-content:space-between;color:var(--muted);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:10px;line-height:1.2}
</style>
</head>
<body><div id="app" class="hud"></div>
<script>
const target=${payload};
const app=document.getElementById('app');
function esc(v){return String(v==null?'':v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;')}
function n(v,d){if(v===null||v===undefined||v==='')return'--';const x=Number(v);return Number.isFinite(x)?x.toFixed(d||0):'--'}
function b(v,y,no){return v===true?y:v===false?no:'--'}
function battery(v){if(v===null||v===undefined||v==='')return'--';const x=Number(v);return Number.isFinite(x)?(x<=1?x*100:x).toFixed(0)+'%':'--'}
function speed(d){const v=d.velocity||{};const x=Number(v.x);const y=Number(v.y);return Number.isFinite(x)&&Number.isFinite(y)?Math.sqrt(x*x+y*y).toFixed(1):'--'}
function keyValues(){return [target.id,target.envVarName,target.name,target.airsimVehicleName].map(v=>String(v||'').toLowerCase()).filter(Boolean)}
function matches(device){const keys=keyValues();return [device.deviceId,device.alias,device.name].map(v=>String(v||'').toLowerCase()).some(v=>v&&keys.includes(v))}
function shape(){return '<i class="arm"></i><i class="arm arm-b"></i><b class="body-shape"></b><b class="nose"></b><em class="rotor r1"></em><em class="rotor r2"></em><em class="rotor r3"></em><em class="rotor r4"></em>'}
function metric(label,value,state){return '<div class="readout" data-state="'+esc(state||'')+'"><span>'+esc(label)+'</span><strong>'+esc(value)+'</strong></div>'}
function render(device,status){const attitude=device.attitude||{};const global=device.global||{};const local=device.local||{};const roll=Number(attitude.roll)||0;const pitch=Number(attitude.pitch)||0;const yaw=Number(attitude.yaw)||0;const s=speed(device);const event=(device.lastEvent&&device.lastEvent.type)||'暂无事件';const top=metric('高度',n(global.relativeAltitudeM,1)+' m')+metric('水平速度',s==='--'?'--':s+' m/s')+metric('电量',battery(device.batteryRemaining));const bottom=metric('解锁',b(device.armed,'ARM','LOCK'),device.armed===false?'warn':'')+metric('模式',device.flightMode||'--')+metric('Local Z',n(local.z,1)+' m');app.innerHTML='<div class="head"><div><strong>'+esc(device.name||device.alias||target.name||'FleetDock HUD')+'</strong><span>FleetDock HUD · '+esc(status)+' · '+esc(event)+'</span></div><span class="pill" data-online="'+(device.connected?'1':'0')+'">'+b(device.connected,'已连接','未连接')+'</span></div><div class="body"><div class="attitude" data-connected="'+(device.connected?'1':'0')+'"><div class="readouts">'+top+'</div><div class="scene"><div class="horizon"><span></span><span></span></div><div class="model" style="--roll:'+roll.toFixed(1)+'deg;--pitch:'+pitch.toFixed(1)+'deg;--yaw:'+yaw.toFixed(1)+'deg">'+shape()+'</div></div><div class="readouts">'+bottom+'</div><div class="hud-foot"><div class="angles"><span>R '+n(attitude.roll,0)+'°</span><span>P '+n(attitude.pitch,0)+'°</span><span>Y '+n(attitude.yaw,0)+'°</span></div></div></div></div>'}
async function refresh(){const fallback={deviceId:target.id,name:target.name,connected:false,metadata:{airframeType:target.airframeType}};try{if(!window.fleetDockHud||typeof window.fleetDockHud.getSnapshot!=='function'){throw new Error('FleetDock HUD bridge is not ready.')}const snapshot=await window.fleetDockHud.getSnapshot();const device=(snapshot.devices||[]).find(matches)||fallback;render(device,snapshot.online?'已连接':'等待 FleetDock 运行时')}catch(error){render(fallback,String(error&&error.message?error.message:error))}}
refresh();setInterval(refresh,500);
</script></body></html>`;
  }

  private _safeFileName(value: string): string {
    return String(value || 'hud').replace(/[^a-zA-Z0-9_.-]+/g, '_');
  }

  private _deviceKey(device: FleetDockHudDevice, index: number): string {
    return String(
      device.id ||
        device.deviceId ||
        device.envVarName ||
        device.name ||
        `hud-${index}`
    );
  }

  private _deviceName(device: FleetDockHudDevice, index: number): string {
    return String(device.name || device.envVarName || `drone${index + 1}`);
  }

  private _owner: BrowserWindow;
  private _isDarkTheme: () => boolean;
  private _windows = new Map<string, BrowserWindow>();
}

export namespace FleetDockHudWindowController {
  export interface IOptions {
    owner: BrowserWindow;
    isDarkTheme: () => boolean;
  }
}
