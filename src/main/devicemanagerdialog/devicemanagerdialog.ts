// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Provide the standalone FleetDock device library dialog used by menu and sidebar entries.

import { BrowserWindow } from 'electron';
import * as path from 'path';
import { ThemedWindow } from '../dialog/themedwindow';
import {
  createDefaultProjectAirsimSensors,
  createProjectAirsimSensorPreset,
  PROJECT_AIRSIM_FLIGHT_SENSOR_IDS,
  PROJECT_AIRSIM_SENSOR_TYPE_OPTIONS
} from '../devices/sensors/project-airsim-sensors';

const SENSOR_UI_CATALOG = PROJECT_AIRSIM_SENSOR_TYPE_OPTIONS.map(option => ({
  ...option,
  preset: createProjectAirsimSensorPreset(option.value)
}));

const TXT = {
  title: '\u8bbe\u5907\u5e93',
  subtitle:
    '\u5168\u5c40\u8bbe\u5907\u5e93\uff1a\u8bbe\u5907\u7684\u6dfb\u52a0\u3001\u67e5\u770b\u3001\u7f16\u8f91\u548c\u5220\u9664\u90fd\u5728\u8fd9\u91cc\u5904\u7406\u3002',
  add: '\u6dfb\u52a0\u8bbe\u5907',
  save: '\u4fdd\u5b58\u8bbe\u5907',
  saving: '\u4fdd\u5b58\u4e2d...',
  cancel: '\u53d6\u6d88',
  close: '\u5173\u95ed',
  delete: '\u5220\u9664',
  edit: '\u7f16\u8f91',
  empty: '\u8fd8\u6ca1\u6709\u8bbe\u5907',
  emptyHint:
    '\u70b9\u51fb\u201c\u6dfb\u52a0\u8bbe\u5907\u201d\u521b\u5efa\u5168\u5c40\u8bbe\u5907\u3002',
  basic: '\u57fa\u7840\u4fe1\u606f',
  uav: '\u65e0\u4eba\u673a\u53c2\u6570',
  connection: '\u8fde\u63a5\u53c2\u6570',
  fleetdock: 'FleetDock SDK',
  simulation: '\u4eff\u771f\u53c2\u6570',
  unsupported:
    '\u5f53\u524d Project AirSim + PX4 \u4eff\u771f\u542f\u52a8\u94fe\u8def\u53ea\u652f\u6301\u65e0\u4eba\u673a\u4eff\u771f\u8bbe\u5907\u3002',
  nameRequired: '\u8bf7\u586b\u5199\u8bbe\u5907\u540d\u79f0',
  saveFailed: '\u4fdd\u5b58\u8bbe\u5907\u5931\u8d25',
  deleteConfirm: '\u786e\u8ba4\u5220\u9664\u8fd9\u4e2a\u8bbe\u5907\u5417\uff1f',
  deleted: '\u8bbe\u5907\u5df2\u5220\u9664',
  saved: '\u8bbe\u5907\u5df2\u4fdd\u5b58',
  listTitle: '\u8bbe\u5907\u5217\u8868',
  listHint:
    '\u5168\u5c40\u8bbe\u5907\u5e93\u4e2d\u7684\u8bbe\u5907\u4f1a\u5728\u8fd9\u91cc\u7edf\u4e00\u7ba1\u7406\u3002',
  editHint:
    '\u70b9\u51fb\u8bbe\u5907\u5361\u7247\u53ef\u67e5\u770b\u548c\u7f16\u8f91\u3002'
} as const;

export class DeviceManagerDialog {
  constructor(options: DeviceManagerDialog.IOptions) {
    this._window = new ThemedWindow({
      isDarkTheme: options.isDarkTheme,
      modal: true,
      parent: options.parent,
      title: TXT.title,
      width: 1120,
      height: 760,
      preload: path.join(__dirname, './preload.js')
    });

    this._pageBody = this._buildPageSource(options.initialDeviceId);
  }

  get window(): ThemedWindow {
    return this._window;
  }

  load() {
    this._window.loadDialogContent(this._pageBody);
  }

  selectDevice(deviceId: string) {
    const selectedId = String(deviceId || '').trim();
    if (!selectedId || this._window.window.isDestroyed()) {
      return;
    }
    void this._window.window.webContents
      .executeJavaScript(
        `window.fleetDockDeviceManager?.selectDevice(${JSON.stringify(
          selectedId
        )});`
      )
      .catch((_error: unknown): void => undefined);
  }

  private _buildPageSource(initialDeviceId = ''): string {
    return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta http-equiv="Content-Security-Policy" content="default-src 'self' data: 'unsafe-inline' 'unsafe-eval'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline' 'unsafe-eval'">
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>${TXT.title}</title>
<style>
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#f6f8fb;color:#172033;font:13px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Helvetica,Arial,sans-serif}
#title-bar{display:none!important}#jlab-dialog-body{padding:0!important;overflow:hidden!important;min-height:0!important;height:100%!important;flex:1 1 0!important}.page-container{height:100vh!important;min-height:0!important;overflow:hidden!important;background:#f6f8fb!important}
*::-webkit-scrollbar{width:10px;height:10px}*::-webkit-scrollbar-track{background:transparent}*::-webkit-scrollbar-thumb{background:rgba(37,99,235,.18);border:3px solid transparent;border-radius:999px;background-clip:content-box}*::-webkit-scrollbar-thumb:hover{background:rgba(37,99,235,.34);border:2px solid transparent;background-clip:content-box}*::-webkit-scrollbar-corner{background:transparent}
*{box-sizing:border-box}button,input,select,textarea{font:inherit}
.shell{position:fixed;inset:0;width:auto;height:auto;min-height:0;display:flex;flex-direction:column;overflow:hidden;background:#f6f8fb}
.header{height:72px;flex:0 0 72px;padding:0 24px;border-bottom:1px solid #dbe5f0;background:#fff;display:flex;align-items:center;justify-content:space-between;-webkit-app-region:drag}
.title{font-size:18px;font-weight:750}.subtitle{margin-top:4px;color:#64748b}
.iconBtn{width:36px;height:36px;border:1px solid #dbe5f0;border-radius:8px;background:#fff;color:#334155;cursor:pointer;font-size:20px;-webkit-app-region:no-drag}
.body{height:calc(100% - 72px);flex:0 0 calc(100% - 72px);min-height:0;display:block;overflow:hidden}
.sidebar{border-right:1px solid #dbe5f0;background:#fff;display:flex;flex-direction:column;min-width:0}
.tools{padding:16px;display:flex;flex-direction:column;gap:10px;border-bottom:1px solid #eef2f7}
.search,.select,.field,.area{width:100%;border:1px solid #dbe5f0;border-radius:8px;background:#fff;color:#172033;outline:none}
.search,.select,.field{height:36px;padding:0 10px}.area{min-height:72px;padding:9px 10px;resize:vertical}
.btn{height:36px;padding:0 14px;border:1px solid #dbe5f0;border-radius:8px;background:#fff;color:#172033;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:6px}
.btn.primary{border-color:#2563eb;background:#2563eb;color:#fff}.btn.danger{border-color:#fecaca;background:#fff5f5;color:#dc2626}
.btn:hover,.iconBtn:hover{border-color:#93c5fd;box-shadow:0 6px 18px rgba(37,99,235,.08)}.btn.primary:hover{background:#1d4ed8}.btn:disabled{opacity:.55;cursor:not-allowed}
.list{padding:12px;overflow:auto;display:flex;flex-direction:column;gap:8px}
.item{width:100%;border:1px solid #e2e8f0;border-radius:10px;background:#fff;padding:12px;text-align:left;cursor:pointer;transition:border-color .18s ease,box-shadow .18s ease,transform .18s ease}
.item:hover{border-color:#93c5fd;box-shadow:0 8px 20px rgba(37,99,235,.08);transform:translateY(-1px)}
.item[data-active="1"]{border-color:#2563eb;box-shadow:0 0 0 2px rgba(37,99,235,.12)}
.itemTop{display:flex;justify-content:space-between;gap:8px;align-items:flex-start}.itemName{font-size:14px;font-weight:750;min-width:0;word-break:break-all}
.tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.tag{height:22px;padding:0 8px;border-radius:999px;background:#eef6ff;color:#2563eb;border:1px solid #cfe2ff;font-size:12px;display:inline-flex;align-items:center}.tag.warn{background:#fff7ed;color:#c2410c;border-color:#fed7aa}.tag.muted{background:#f1f5f9;color:#64748b;border-color:#e2e8f0}
.empty{padding:28px 16px;color:#64748b;text-align:center}
.main{width:100%;height:100%;min-width:0;min-height:0;overflow:auto;padding:18px 22px 32px;overscroll-behavior:contain}
.panel{max-width:1000px;margin:0 auto;padding-bottom:8px}.panelHead{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px}
.panelTitle{font-size:18px;font-weight:750}
.panelHint{margin-top:4px;color:#64748b}.mainList{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}.mainItemWrap{position:relative;min-width:0}.mainItem{width:100%;border:1px solid #dbe5f0;border-radius:12px;background:#fff;padding:16px 52px 16px 16px;text-align:left;cursor:pointer;min-height:104px;transition:border-color .18s ease,box-shadow .18s ease,transform .18s ease}.mainItem:hover{border-color:#93c5fd;box-shadow:0 12px 28px rgba(37,99,235,.1);transform:translateY(-2px)}.mainItemDelete{position:absolute;top:11px;right:11px;width:28px;height:28px;padding:0;border:1px solid transparent;border-radius:8px;background:transparent;color:#94a3b8;cursor:pointer;font-size:20px;line-height:24px;display:flex;align-items:center;justify-content:center;transition:border-color .16s ease,background .16s ease,color .16s ease}.mainItemDelete:hover,.mainItemDelete:focus-visible{border-color:#fecaca;background:#fef2f2;color:#dc2626;outline:none}.mainEmpty{border:1px dashed #bfd0e4;border-radius:8px;background:#fff;padding:34px 18px;text-align:center;color:#64748b}.mainEmpty strong{display:block;color:#172033;font-size:16px;margin-bottom:6px}
.deviceCard{display:flex;align-items:center;gap:12px;min-width:0}.deviceIcon{width:42px;height:42px;flex:0 0 42px;border-radius:11px;display:flex;align-items:center;justify-content:center;background:#eff6ff;color:#2563eb;border:1px solid #dbeafe}.deviceIcon[data-category="ugv"]{background:#ecfdf5;color:#059669;border-color:#a7f3d0}.deviceIcon[data-category="usv"]{background:#ecfeff;color:#0891b2;border-color:#a5f3fc}.deviceIcon[data-category="humanoid"]{background:#f5f3ff;color:#7c3aed;border-color:#ddd6fe}.deviceIcon[data-category="quadruped"]{background:#fffbeb;color:#d97706;border-color:#fde68a}.deviceIcon svg{width:26px;height:26px;display:block}.deviceCardContent{min-width:0;flex:1}.deviceCardName{font-size:14px;font-weight:750;color:#172033;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.deviceCardMeta{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:7px}.deviceMeta{height:22px;padding:0 8px;border-radius:999px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0;font-size:12px;display:inline-flex;align-items:center}.deviceMeta.source{background:#eef6ff;color:#2563eb;border-color:#cfe2ff}.deviceMeta.source.physical{background:#fff7ed;color:#c2410c;border-color:#fed7aa}.mainItem .deviceCard{gap:14px}.mainItem .deviceIcon{width:52px;height:52px;flex-basis:52px;border-radius:14px}.mainItem .deviceIcon svg{width:31px;height:31px}.mainItem .deviceCardName{font-size:16px}
.listToolbar{display:grid;grid-template-columns:minmax(220px,1fr) 180px 180px;gap:10px;margin-bottom:14px;padding:12px;border:1px solid #dbe5f0;border-radius:10px;background:#fff}
.section{border:1px solid #dbe5f0;border-radius:8px;background:#fff;margin-bottom:12px;overflow:hidden}.sectionTitle{height:42px;padding:0 14px;display:flex;align-items:center;font-weight:750;border-bottom:1px solid #eef2f7;background:#fbfdff}
.grid{padding:14px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 14px}.grid.three{grid-template-columns:repeat(3,minmax(0,1fr))}
.fieldRow{display:flex;flex-direction:column;gap:6px}.label{color:#64748b}.full{grid-column:1/-1}.note{padding:10px 12px;border-radius:8px;background:#fff7ed;color:#9a3412;border:1px solid #fed7aa}
.endpoint{min-height:36px;padding:8px 10px;border:1px solid #cfe2ff;border-radius:8px;background:#f8fbff;color:#1d4ed8;font-family:Consolas,monospace;word-break:break-all}.probe{padding:10px 12px;border-radius:8px;border:1px solid #dbe5f0;background:#f8fafc;color:#475569}.probe.ok{border-color:#bbf7d0;background:#f0fdf4;color:#166534}.probe.error{border-color:#fecaca;background:#fff5f5;color:#b91c1c}
.serialRow{display:flex;gap:8px}.serialRow .field{min-width:0;flex:1}.serialHint{color:#64748b;font-size:12px;word-break:break-word}.serialHint.error{color:#b91c1c}
.sensorToolbar{padding:12px 14px;display:flex;align-items:center;justify-content:space-between;gap:10px;border-bottom:1px solid #eef2f7}.sensorAdd{display:flex;gap:8px;align-items:center}.sensorAdd .select{width:150px}.sensorList{padding:12px 14px;display:flex;flex-direction:column;gap:8px}.sensorItemGroup{min-width:0}.sensorItem{border:1px solid #dbe5f0;border-radius:8px;padding:10px 12px;display:flex;align-items:center;justify-content:space-between;gap:12px}.sensorItemGroup[data-editing="1"] .sensorItem{border-color:#93c5fd;background:#f8fbff}.sensorInfo{min-width:0}.sensorName{font-weight:750;display:flex;align-items:center;gap:8px;flex-wrap:wrap}.sensorMeta{margin-top:4px;color:#64748b;font-size:12px}.sensorActions{display:flex;gap:8px;flex-shrink:0}.sensorEditor{margin-top:8px;border:1px solid #bfdbfe;border-radius:8px;background:#f8fbff;overflow:hidden}.sensorEditorHead{height:42px;padding:0 12px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #dbeafe;font-weight:750}.sensorEditorSection{background:#fff}.sensorEditorSection+.sensorEditorSection{border-top:1px solid #dbeafe}.sensorEditorSectionTitle{padding:10px 14px 0;color:#1d4ed8;font-size:12px;font-weight:750}.sensorEditorSection .grid{padding-top:10px;background:#fff}.sensorCaptureTypes{display:flex;gap:6px;flex-wrap:wrap}.sensorCapture{height:30px;padding:0 9px;border:1px solid #dbe5f0;border-radius:7px;background:#fff;cursor:pointer}.sensorCapture.active{border-color:#2563eb;background:#eff6ff;color:#1d4ed8}.sensorNote{margin:0 14px 12px;padding:9px 11px;border-radius:8px;background:#eff6ff;color:#1e40af;border:1px solid #bfdbfe}.sensorEmpty{padding:18px 14px;text-align:center;color:#64748b}
.footer{position:sticky;bottom:0;z-index:5;min-height:58px;display:flex;justify-content:space-between;align-items:center;gap:10px;margin-top:4px;padding:10px 0 8px;background:#f6f8fb;box-shadow:0 -10px 18px rgba(246,248,251,.96)}.actions{display:flex;gap:10px}.toast{position:fixed;right:20px;bottom:20px;display:none;max-width:420px;padding:11px 14px;border-radius:8px;background:#111827;color:#fff;box-shadow:0 16px 42px rgba(15,23,42,.22);z-index:20}.toast.show{display:block}
@media (max-width:860px){.grid,.grid.three,.listToolbar{grid-template-columns:1fr}.main{padding:14px}}
</style>
</head>
<body>
<div class="shell">
  <div class="header">
    <div><div class="title">${TXT.title}</div><div class="subtitle">${
      TXT.subtitle
    }</div></div>
    <button class="iconBtn" data-action="close" title="${
      TXT.close
    }">&times;</button>
  </div>
  <div class="body">
    <main id="main" class="main"></main>
  </div>
</div>
<div id="toast" class="toast"></div>
<script>
(function () {
  const api = window.electronAPI || {};
  const INITIAL_DEVICE_ID = ${JSON.stringify(initialDeviceId || '')};
  const TXT = ${JSON.stringify(TXT)};
  const CATEGORIES = [
    { value: 'uav', label: '\\u65e0\\u4eba\\u673a' },
    { value: 'ugv', label: '\\u65e0\\u4eba\\u8f66' },
    { value: 'usv', label: '\\u65e0\\u4eba\\u8239' },
    { value: 'humanoid', label: '\\u4eba\\u5f62\\u673a\\u5668\\u4eba' },
    { value: 'quadruped', label: '\\u56db\\u8db3\\u673a\\u5668\\u4eba' }
  ];
  const RUNTIMES = [
    { value: 'simulation', label: '\\u4eff\\u771f\\u8bbe\\u5907' },
    { value: 'physical', label: '\\u7269\\u7406\\u8bbe\\u5907' }
  ];
  const AIRFRAMES = [
    { value: 'quadrotor', label: '\\u56db\\u65cb\\u7ffc', px4AutostartId: 10016, projectAirsimAirframe: 'quadrotor-x', robotTemplate: 'robot_quadrotor_px4.jsonc' },
    { value: 'hexarotor', label: '\\u516d\\u65cb\\u7ffc', px4AutostartId: 10016, projectAirsimAirframe: 'hexarotor-x', robotTemplate: 'robot_hexarotor_px4.jsonc' },
    { value: 'vtol_tailsitter', label: '\\u5782\\u76f4\\u7ffc Tailsitter', px4AutostartId: 1041, projectAirsimAirframe: 'vtol-quad-x-tailsitter', robotTemplate: 'robot_vtol_tailsitter_px4.jsonc' },
    { value: 'vtol_tiltrotor', label: '\\u5782\\u76f4\\u7ffc Tiltrotor', px4AutostartId: 1042, projectAirsimAirframe: 'vtol-quad-tiltrotor', robotTemplate: 'robot_vtol_tiltrotor_px4.jsonc' }
  ];
  const FLEETDOCK_PRODUCTS = [
    { value: 'px4_generic', label: '通用 PX4 无人机', product: 'px4.generic', category: 'uav' },
    { value: 'dji_matrice_350_rtk', label: '大疆 Matrice 350 RTK', product: 'dji.matrice_350_rtk', category: 'uav' },
    { value: 'dji_mavic_3_enterprise', label: '大疆 Mavic 3 行业系列', product: 'dji.mavic_3_enterprise', category: 'uav' },
    { value: 'unitree_go2', label: '宇树 Go2 机器狗', product: 'unitree.go2', category: 'quadruped' },
    { value: 'unitree_g1', label: '宇树 G1 人形机器人', product: 'unitree.g1', category: 'humanoid' }
  ];
  const SENSOR_CATALOG = ${JSON.stringify(SENSOR_UI_CATALOG)};
  const DEFAULT_SENSORS = ${JSON.stringify(
    createDefaultProjectAirsimSensors()
  )};
  const FLIGHT_SENSOR_IDS = ${JSON.stringify(PROJECT_AIRSIM_FLIGHT_SENSOR_IDS)};
  // [UAV-IDE MODIFY] Open the global device library in list mode; the form is shown only after adding or editing a device.
  const state = { devices: [], selectedId: '', draft: null, sensorEditorIndex: -1, saving: false, testing: false, probeResult: null, bindingAlias: 'drone', serialPorts: [], serialPortsLoading: false, serialPortsError: '', search: '', category: 'all', runtime: 'all' };

  function esc(value) {
    return String(value == null ? '' : value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function optionLabel(options, value) {
    const item = options.find(option => option.value === value);
    return item ? item.label : value || '';
  }
  function airframe(value) {
    return AIRFRAMES.find(item => item.value === value) || AIRFRAMES[0];
  }
  function fleetdockProduct(builder) {
    return FLEETDOCK_PRODUCTS.find(item => item.value === builder) || FLEETDOCK_PRODUCTS[0];
  }
  function defaultFleetDockParams(builder, port) {
    if (builder === 'unitree_go2' || builder === 'unitree_g1') {
      return { network_interface: 'eth0', robot_ip: '' };
    }
    if (builder === 'dji_matrice_350_rtk' || builder === 'dji_mavic_3_enterprise') {
      return { sdk_mode: 'msdk', app_key: '', app_secret: '', mqtt_host: '', workspace_id: '' };
    }
    return { endpoint: 'udpin://0.0.0.0:' + (port || 14600), timeout_s: 30, mavsdk_server_port: '' };
  }
  function nextPhysicalPort() {
    const used = new Set();
    state.devices.forEach(device => {
      if ((device.runtimeKind || (device.source === 'real' ? 'physical' : 'simulation')) !== 'physical') return;
      const connection = canonicalConnection(device);
      if (connection.kind !== 'serial') used.add(Number(connection.port));
    });
    for (let port = 14560; port <= 14599; port += 1) {
      if (!used.has(port)) return port;
    }
    return 14560;
  }
  function canonicalConnection(device) {
    if (device?.connection?.protocol === 'serial') {
      return { kind: 'serial', serialPort: String(device.connection.serialPort || 'COM3').trim().toUpperCase(), baudRate: Number(device.connection.baudRate || 57600) };
    }
    if (device?.connection?.protocol === 'udp' || device?.connection?.protocol === 'tcp') {
      return { kind: device.connection.protocol, mode: device.connection.mode === 'dial' ? 'dial' : 'listen', host: String(device.connection.host || '').trim(), port: Number(device.connection.port) };
    }
    const stored = device?.real?.connection;
    if (stored?.kind === 'serial') {
      return { kind: 'serial', serialPort: String(stored.serialPort || 'COM3').trim().toUpperCase(), baudRate: Number(stored.baudRate || 57600) };
    }
    if (stored?.kind === 'udp' || stored?.kind === 'tcp') {
      return { kind: stored.kind, mode: stored.mode === 'dial' ? 'dial' : 'listen', host: String(stored.host || '').trim(), port: Number(stored.port) };
    }
    const protocol = device?.connection?.protocol || device?.real?.connectionKind || device?.telemetry?.protocol || 'udp';
    if (protocol === 'serial') {
      return { kind: 'serial', serialPort: String(device?.connection?.serialPort || device?.real?.serialPort || 'COM3').trim().toUpperCase(), baudRate: Number(device?.connection?.baudRate || device?.real?.baudRate || 57600) };
    }
    const mode = device?.connection?.mode || device?.telemetry?.mode || 'listen';
    return {
      kind: protocol === 'tcp' ? 'tcp' : 'udp',
      mode: mode === 'dial' ? 'dial' : 'listen',
      host: String(device?.connection?.host || device?.real?.host || (mode === 'listen' ? device?.telemetry?.bindHost : device?.telemetry?.remoteHost) || (mode === 'listen' ? '0.0.0.0' : '127.0.0.1')).trim(),
      port: Number(device?.connection?.port || device?.real?.port || (mode === 'listen' ? device?.telemetry?.bindPort : device?.telemetry?.remotePort) || 14560)
    };
  }
  function connectionEndpoint(connection) {
    if (connection.kind === 'serial') return 'serial://' + connection.serialPort + ':' + connection.baudRate;
    return connection.kind + (connection.mode === 'dial' ? 'out' : 'in') + '://' + connection.host + ':' + connection.port;
  }
  function validateConnection(connection) {
    if (connection.kind === 'serial') {
      if (!/^COM(?:[1-9]\d*)$/.test(connection.serialPort)) throw new Error('\u4e32\u53e3\u5fc5\u987b\u4f7f\u7528 COM1\u3001COM2 \u7b49 Windows COM \u7aef\u53e3\u683c\u5f0f\u3002');
      if (!Number.isInteger(connection.baudRate) || connection.baudRate <= 0) throw new Error('\u4e32\u53e3\u6ce2\u7279\u7387\u5fc5\u987b\u662f\u6b63\u6574\u6570\u3002');
      return;
    }
    if (!connection.host) throw new Error(connection.mode === 'listen' ? '\u8bf7\u586b\u5199\u672c\u673a\u76d1\u542c IP\u3002' : '\u8bf7\u586b\u5199\u8fdc\u7aef\u8bbe\u5907 IP\u3002');
    if (!Number.isInteger(connection.port) || connection.port < 1 || connection.port > 65535) throw new Error('\u7f51\u7edc\u7aef\u53e3\u5fc5\u987b\u662f 1 \u5230 65535 \u4e4b\u95f4\u7684\u6574\u6570\u3002');
  }
  function normalizeFleetDock(draft) {
    const builder = draft.fleetdock?.builder || 'px4_generic';
    const product = fleetdockProduct(builder);
    const port = Number(draft.telemetry?.bindPort || draft.connection?.port || 14600);
    draft.fleetdock = Object.assign({ product: product.product, builder, params: {} }, draft.fleetdock || {});
    draft.fleetdock.builder = builder;
    draft.fleetdock.product = product.product;
    draft.fleetdock.params = Object.assign(defaultFleetDockParams(builder, port), draft.fleetdock.params || {});
    if (builder === 'px4_generic' && draft.runtimeKind !== 'physical') {
      draft.fleetdock.params.endpoint = 'udpin://0.0.0.0:' + port;
      draft.fleetdock.params.timeout_s = Number(draft.fleetdock.params.timeout_s || 30);
    } else if (builder === 'px4_generic' && draft.runtimeKind === 'physical') {
      const connection = canonicalConnection(draft);
      draft.fleetdock.params.endpoint = connectionEndpoint(connection);
      draft.fleetdock.params.timeout_s = connection.kind === 'serial' ? 10 : Number(draft.fleetdock.params.timeout_s || 30);
      delete draft.fleetdock.params.mavsdk_server_port;
      delete draft.fleetdock.params.force_arm;
    }
  }
  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }
  function nowIndex() {
    return state.devices.length + 1;
  }
  function defaultDraft() {
    const index = nowIndex();
    const port = 14600 + index - 1;
    const profile = airframe('quadrotor');
    return {
      id: '',
      name: 'drone' + index,
      deviceCategory: 'uav',
      runtimeKind: 'simulation',
      enabled: true,
      description: '',
      source: 'simulation',
      accessType: 'virtual',
      sensors: clone(DEFAULT_SENSORS),
      envVarName: '',
      systemId: index,
      airsimVehicleName: 'Drone' + index,
      uav: {
        airframeType: profile.value,
        px4AutostartId: profile.px4AutostartId,
        projectAirsimAirframe: profile.projectAirsimAirframe,
        robotTemplate: profile.robotTemplate,
        vehicleName: 'Drone' + index,
        systemId: index,
        px4Instance: null,
        simulatorPort: null,
        mavlinkPort: null,
        controlPortLocal: null
      },
      telemetry: { protocol: 'udp', mode: 'listen', bindHost: '0.0.0.0', bindPort: port, remoteHost: '', remotePort: port },
      connection: { protocol: 'udp', mode: 'listen', host: '0.0.0.0', port, serialPort: '', baudRate: 57600, readOnly: true, allowCommand: false },
      simulation: { engine: 'project-airsim-px4', model: profile.value, airsimVehicleName: 'Drone' + index, sceneActorName: 'Drone' + index, startLanded: true, px4StartMode: 'ide-managed', originX: 0, originY: 0, originZ: -0.1, runtime: { state: 'not-started' } },
      real: { connectionKind: 'udp', host: '0.0.0.0', port, serialPort: '', baudRate: 57600, readOnly: true, allowCommand: false, heartbeatCheck: true },
      fleetdock: { product: 'px4.generic', builder: 'px4_generic', params: defaultFleetDockParams('px4_generic', port) }
    };
  }
  function normalizeDraft(device) {
    const draft = Object.assign(defaultDraft(), clone(device || {}));
    draft.deviceCategory = draft.deviceCategory || (draft.deviceType === 'drone' ? 'uav' : 'uav');
    draft.runtimeKind = draft.runtimeKind || (draft.source === 'real' || draft.accessType === 'physical' ? 'physical' : 'simulation');
    draft.source = draft.runtimeKind === 'physical' ? 'real' : 'simulation';
    draft.accessType = draft.runtimeKind === 'physical' ? 'physical' : 'virtual';
    draft.uav = Object.assign(defaultDraft().uav, draft.uav || {});
    draft.simulation = Object.assign(defaultDraft().simulation, draft.simulation || {});
    draft.real = Object.assign(defaultDraft().real, draft.real || {});
    draft.telemetry = Object.assign(defaultDraft().telemetry, draft.telemetry || {});
    draft.sensors = Array.isArray(draft.sensors)
      ? draft.sensors
      : (draft.deviceCategory === 'uav' && draft.runtimeKind === 'simulation' ? clone(DEFAULT_SENSORS) : []);
    const connection = canonicalConnection(draft);
    draft.connection = connection.kind === 'serial'
      ? { protocol: 'serial', mode: 'listen', host: '', port: '', serialPort: connection.serialPort, baudRate: connection.baudRate }
      : { protocol: connection.kind, mode: connection.mode, host: connection.host, port: connection.port, serialPort: 'COM3', baudRate: 57600 };
    draft.uav.airframeType = draft.uav.airframeType || draft.simulation.model || 'quadrotor';
    draft.simulation.originX = draft.simulation.originX ?? 0;
    draft.simulation.originY = draft.simulation.originY ?? 0;
    draft.simulation.originZ = draft.simulation.originZ ?? -0.1;
    syncAirframe(draft);
    normalizeFleetDock(draft);
    return draft;
  }
  function syncAirframe(draft) {
    const profile = airframe(draft.uav && draft.uav.airframeType);
    draft.uav.airframeType = profile.value;
    draft.uav.px4AutostartId = profile.px4AutostartId;
    draft.uav.projectAirsimAirframe = profile.projectAirsimAirframe;
    draft.uav.robotTemplate = profile.robotTemplate;
    draft.simulation.model = profile.value;
    draft.simulation.engine = 'project-airsim-px4';
  }
  function setDeep(target, path, value) {
    const parts = String(path).split('.');
    let cursor = target;
    for (let index = 0; index < parts.length - 1; index += 1) {
      const key = parts[index];
      if (!cursor[key] || typeof cursor[key] !== 'object') {
        cursor[key] = {};
      }
      cursor = cursor[key];
    }
    cursor[parts[parts.length - 1]] = value;
  }
  function showToast(message) {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.classList.add('show');
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => toast.classList.remove('show'), 2200);
  }
  function filteredDevices() {
    return state.devices.filter(device => {
      const category = device.deviceCategory || 'uav';
      const runtime = device.runtimeKind || (device.source === 'real' || device.accessType === 'physical' ? 'physical' : 'simulation');
      const text = [device.name, device.description, category, runtime, device.uav?.airframeType].join(' ').toLowerCase();
      return (state.category === 'all' || category === state.category) && (state.runtime === 'all' || runtime === state.runtime) && (!state.search || text.includes(state.search.toLowerCase()));
    });
  }
  function deviceCategoryIcon(category) {
    const common = 'viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
    let body = '';
    if (category === 'ugv') {
      body = '<path d="M6 19h20l-2-6a3 3 0 0 0-2.8-2H11a3 3 0 0 0-2.8 2L6 19Z"/><path d="M4 19h24v4H4z"/><circle cx="9" cy="24" r="2.5"/><circle cx="23" cy="24" r="2.5"/><path d="M11 15h10"/>';
    } else if (category === 'usv') {
      body = '<path d="M7 18h18l-3 6H10l-3-6Z"/><path d="M12 18v-7h8l3 7"/><path d="M16 11V7"/><path d="M5 27c2 1.3 4 1.3 6 0 2 1.3 4 1.3 6 0 2 1.3 4 1.3 6 0 1.3.9 2.7 1.1 4 .6"/>';
    } else if (category === 'humanoid') {
      body = '<circle cx="16" cy="7" r="3"/><path d="M12 13h8l1 8h-3l-1 7h-2l-1-7h-3l1-8Z"/><path d="m12 14-5 6M20 14l5 6"/>';
    } else if (category === 'quadruped') {
      body = '<rect x="8" y="11" width="15" height="10" rx="3"/><path d="M23 13h3l2 3-2 3h-3M8 13 5 10M11 21v6M20 21v6M9 27h4M18 27h4"/><circle cx="25" cy="15" r=".8" fill="currentColor" stroke="none"/>';
    } else {
      body = '<rect x="12" y="12" width="8" height="8" rx="2"/><path d="m12 14-5-4M20 14l5-4M12 18l-5 4M20 18l5 4"/><circle cx="6" cy="9" r="3"/><circle cx="26" cy="9" r="3"/><circle cx="6" cy="23" r="3"/><circle cx="26" cy="23" r="3"/><path d="M16 12V8M14 8h4"/>';
    }
    return '<svg ' + common + '>' + body + '</svg>';
  }
  function field(label, key, value, type) {
    return '<label class="fieldRow"><span class="label">' + label + '</span><input class="field" data-field="' + key + '" type="' + (type || 'text') + '" value="' + esc(value) + '"></label>';
  }
  function area(label, key, value) {
    return '<label class="fieldRow full"><span class="label">' + label + '</span><textarea class="area" data-field="' + key + '">' + esc(value) + '</textarea></label>';
  }
  function select(label, key, value, options) {
    return '<label class="fieldRow"><span class="label">' + label + '</span><select class="select" data-field="' + key + '">' + options.map(item => '<option value="' + esc(item.value) + '"' + (item.value === value ? ' selected' : '') + '>' + esc(item.label) + '</option>').join('') + '</select></label>';
  }
  function numberOrEmpty(value) {
    return value == null ? '' : value;
  }
  function serialPortField(connection) {
    const options = state.serialPorts.map(item => '<option value="' + esc(item.port) + '" label="' + esc(item.name || item.port) + '"></option>').join('');
    const detected = state.serialPorts.length
      ? '\u5df2\u68c0\u6d4b\u5230 ' + state.serialPorts.length + ' \u4e2a\u4e32\u53e3\uff1a' + state.serialPorts.map(item => item.port + (item.name && item.name !== item.port ? ' - ' + item.name : '')).join('\uff1b')
      : (state.serialPortsLoading ? '\u6b63\u5728\u68c0\u6d4b Windows \u4e32\u53e3...' : (state.serialPortsError || '\u672a\u68c0\u6d4b\u5230\u4e32\u53e3\uff0c\u4ecd\u53ef\u624b\u5de5\u8f93\u5165 COM \u53e3\u3002'));
    return '<label class="fieldRow"><span class="label">\u4e32\u53e3</span><div class="serialRow"><input class="field" data-field="connection.serialPort" list="detectedSerialPorts" value="' + esc(connection.serialPort || 'COM3') + '" placeholder="COM3"><button type="button" class="btn" data-action="refresh-serial-ports" ' + (state.serialPortsLoading ? 'disabled' : '') + '>' + (state.serialPortsLoading ? '\u68c0\u6d4b\u4e2d...' : '\u91cd\u65b0\u68c0\u6d4b') + '</button></div><datalist id="detectedSerialPorts">' + options + '</datalist><span class="serialHint ' + (state.serialPortsError ? 'error' : '') + '">' + esc(detected) + '</span></label>';
  }
  function boolSelect(label, key, value) {
    return '<label class="fieldRow"><span class="label">' + label + '</span><select class="select" data-field="' + key + '" data-value-type="boolean"><option value="true"' + (value ? ' selected' : '') + '>\u542f\u7528</option><option value="false"' + (!value ? ' selected' : '') + '>\u505c\u7528</option></select></label>';
  }
  function sensorCatalogItem(type) {
    return SENSOR_CATALOG.find(item => item.value === type) || SENSOR_CATALOG[0];
  }
  function nextSensorId(type) {
    const base = sensorCatalogItem(type).preset.id;
    const used = new Set((state.draft?.sensors || []).map(sensor => String(sensor.id || '').toLowerCase()));
    if (!used.has(base.toLowerCase())) return base;
    let index = 2;
    while (used.has((base + index).toLowerCase())) index += 1;
    return base + index;
  }
  function sensorPreset(type) {
    const preset = clone(sensorCatalogItem(type).preset);
    preset.id = nextSensorId(type);
    preset.name = sensorCatalogItem(type).label;
    return preset;
  }
  function sensorSummary(sensor) {
    const config = sensor.config || {};
    if (sensor.type === 'camera') return (config.width || 0) + '\u00d7' + (config.height || 0) + ' / FOV ' + (config.fovDegrees || 0) + '\u00b0';
    if (sensor.type === 'lidar') return (config.lidarType || '') + ' / ' + (config.numberOfChannels || 0) + '\u901a\u9053 / ' + (config.range || 0) + 'm';
    if (sensor.type === 'distance-sensor') return (config.minDistance || 0) + '\u2013' + (config.maxDistance || 0) + 'm';
    if (sensor.type === 'radar') return '\u91cf\u7a0b ' + (config.rangeMax || 0) + 'm';
    if (sensor.type === 'battery') return config.mode === 'simple-discharge-mode' ? '\u56fa\u5b9a\u653e\u7535' : '\u65cb\u7ffc\u529f\u7387\u653e\u7535';
    if (sensor.type === 'airspeed') return '\u524d\u5411\u8f74 ' + (config.forwardXyz || [1, 0, 0]).join(' ');
    return sensor.enabled ? '\u4f7f\u7528 Project AirSim \u9ed8\u8ba4\u53c2\u6570' : '\u5df2\u505c\u7528';
  }
  function sensorTypeFields(sensor, index) {
    const base = 'sensors.' + index + '.config.';
    const config = sensor.config || {};
    if (sensor.type === 'camera') {
      const labels = ['RGB', '\u5e73\u9762\u6df1\u5ea6', '\u900f\u89c6\u6df1\u5ea6', '\u5206\u5272', '\u6df1\u5ea6\u53ef\u89c6\u5316', '\u89c6\u5dee', '\u8868\u9762\u6cd5\u7ebf'];
      const imageTypes = Array.isArray(config.imageTypes) ? config.imageTypes : [0];
      return field('\u91c7\u96c6\u5468\u671f\uff08\u79d2\uff09', base + 'captureInterval', config.captureInterval, 'number') +
        field('\u5bbd\u5ea6', base + 'width', config.width, 'number') + field('\u9ad8\u5ea6', base + 'height', config.height, 'number') +
        field('FOV\uff08\u5ea6\uff09', base + 'fovDegrees', config.fovDegrees, 'number') + boolSelect('\u89c6\u9891\u6d41', base + 'streamingEnabled', config.streamingEnabled) + boolSelect('PNG \u538b\u7f29', base + 'compress', config.compress) +
        '<div class="fieldRow full"><span class="label">\u56fe\u50cf\u8f93\u51fa\uff08\u81f3\u5c11\u9009\u4e00\u9879\uff09</span><div class="sensorCaptureTypes">' + labels.map((label, type) => '<button type="button" class="sensorCapture ' + (imageTypes.includes(type) ? 'active' : '') + '" data-action="toggle-image-type" data-index="' + index + '" data-image-type="' + type + '">' + label + '</button>').join('') + '</div></div>';
    }
    if (sensor.type === 'imu') {
      return field('\u52a0\u901f\u5ea6\u8ba1\u968f\u673a\u6e38\u8d70', base + 'accelerometer.randomWalk', config.accelerometer?.randomWalk, 'number') + field('\u52a0\u901f\u5ea6\u8ba1\u65f6\u95f4\u5e38\u6570', base + 'accelerometer.tau', config.accelerometer?.tau, 'number') + field('\u9640\u87ba\u4eea\u968f\u673a\u6e38\u8d70', base + 'gyroscope.randomWalk', config.gyroscope?.randomWalk, 'number') + field('\u9640\u87ba\u4eea\u65f6\u95f4\u5e38\u6570', base + 'gyroscope.tau', config.gyroscope?.tau, 'number');
    }
    if (sensor.type === 'airspeed') {
      return field('\u524d\u5411 X', base + 'forwardXyz.0', config.forwardXyz?.[0], 'number') + field('\u524d\u5411 Y', base + 'forwardXyz.1', config.forwardXyz?.[1], 'number') + field('\u524d\u5411 Z', base + 'forwardXyz.2', config.forwardXyz?.[2], 'number');
    }
    if (sensor.type === 'lidar') {
      const lidarTypes = [
        { value: 'generic_cylindrical', label: 'Generic Cylindrical' }, { value: 'generic_rosette', label: 'Generic Rosette' },
        { value: 'livox_mid70', label: 'Livox Mid-70' }, { value: 'livox_avia', label: 'Livox Avia' },
        { value: 'gpu_cylindrical', label: 'GPU Cylindrical' }, { value: 'depth_lidar', label: 'Depth Lidar' }
      ];
      return select('\u6fc0\u5149\u96f7\u8fbe\u7c7b\u578b', base + 'lidarType', config.lidarType, lidarTypes) + field('\u901a\u9053\u6570', base + 'numberOfChannels', config.numberOfChannels, 'number') + field('\u91cf\u7a0b\uff08m\uff09', base + 'range', config.range, 'number') + field('\u70b9\u9891', base + 'pointsPerSecond', config.pointsPerSecond, 'number') + field('\u6c34\u5e73\u626b\u63cf\u9891\u7387', base + 'horizontalRotationFrequency', config.horizontalRotationFrequency, 'number') + field('\u4e0a\u62a5\u9891\u7387', base + 'reportFrequency', config.reportFrequency, 'number') + field('\u6c34\u5e73 FOV \u8d77\u70b9', base + 'horizontalFovStartDeg', config.horizontalFovStartDeg, 'number') + field('\u6c34\u5e73 FOV \u7ec8\u70b9', base + 'horizontalFovEndDeg', config.horizontalFovEndDeg, 'number') + field('\u5782\u76f4 FOV \u4e0b\u9650', base + 'verticalFovLowerDeg', config.verticalFovLowerDeg, 'number') + field('\u5782\u76f4 FOV \u4e0a\u9650', base + 'verticalFovUpperDeg', config.verticalFovUpperDeg, 'number');
    }
    if (sensor.type === 'distance-sensor') {
      return field('\u6700\u5c0f\u8ddd\u79bb\uff08m\uff09', base + 'minDistance', config.minDistance, 'number') + field('\u6700\u5927\u8ddd\u79bb\uff08m\uff09', base + 'maxDistance', config.maxDistance, 'number') + boolSelect('\u663e\u793a\u8c03\u8bd5\u70b9', base + 'drawDebugPoints', config.drawDebugPoints);
    }
    if (sensor.type === 'radar') {
      return field('\u6700\u5927\u8ddd\u79bb\uff08m\uff09', base + 'rangeMax', config.rangeMax, 'number') + field('\u8ddd\u79bb\u5206\u8fa8\u7387', base + 'rangeResolution', config.rangeResolution, 'number') + field('\u6700\u5927\u901f\u5ea6\uff08m/s\uff09', base + 'velocityMax', config.velocityMax, 'number') + field('\u901f\u5ea6\u5206\u8fa8\u7387', base + 'velocityResolution', config.velocityResolution, 'number') + field('\u68c0\u6d4b\u5468\u671f\uff08s\uff09', base + 'detectionInterval', config.detectionInterval, 'number') + field('\u8ddf\u8e2a\u5468\u671f\uff08s\uff09', base + 'trackInterval', config.trackInterval, 'number') + field('\u65b9\u4f4d\u89d2\u4e0b\u9650\uff08rad\uff09', base + 'fov.azimuthMin', config.fov?.azimuthMin, 'number') + field('\u65b9\u4f4d\u89d2\u4e0a\u9650\uff08rad\uff09', base + 'fov.azimuthMax', config.fov?.azimuthMax, 'number') + field('\u4fef\u4ef0\u89d2\u4e0b\u9650\uff08rad\uff09', base + 'fov.elevationMin', config.fov?.elevationMin, 'number') + field('\u4fef\u4ef0\u89d2\u4e0a\u9650\uff08rad\uff09', base + 'fov.elevationMax', config.fov?.elevationMax, 'number');
    }
    if (sensor.type === 'battery') {
      const modes = [{ value: 'rotor-power-discharge-mode', label: '\u65cb\u7ffc\u529f\u7387\u653e\u7535' }, { value: 'simple-discharge-mode', label: '\u56fa\u5b9a\u901f\u7387\u653e\u7535' }];
      return select('\u653e\u7535\u6a21\u5f0f', base + 'mode', config.mode, modes) + field('\u603b\u5bb9\u91cf', base + 'totalCapacity', config.totalCapacity, 'number') + field('\u521d\u59cb\u5bb9\u91cf', base + 'capacityOnStart', config.capacityOnStart, 'number') + (config.mode === 'simple-discharge-mode' ? field('\u521d\u59cb\u653e\u7535\u901f\u7387', base + 'drainRateOnStart', config.drainRateOnStart, 'number') : field('\u65cb\u7ffc\u529f\u7387\u7cfb\u6570', base + 'rotorPowerCoefficient', config.rotorPowerCoefficient, 'number'));
    }
    return '<div class="fieldRow full"><span class="label">\u53c2\u6570</span><div class="endpoint">\u5f53\u524d\u4f7f\u7528 Project AirSim \u9ed8\u8ba4\u4f20\u611f\u5668\u53c2\u6570\u3002</div></div>';
  }
  function renderSensorEditor(sensor, index) {
    if (!sensor) return '';
    const root = 'sensors.' + index + '.';
    const typeOptions = SENSOR_CATALOG.map(item => ({ value: item.value, label: item.label }));
    return '<div class="sensorEditor"><div class="sensorEditorHead"><span>\u7f16\u8f91\uff1a' + esc(sensor.name || sensor.id) + '</span><button type="button" class="btn" data-action="close-sensor-editor">\u6536\u8d77</button></div>' +
      '<div class="sensorEditorSection"><div class="sensorEditorSectionTitle">\u57fa\u672c\u8bbe\u7f6e</div><div class="grid">' +
        field('\u540d\u79f0', root + 'name', sensor.name) + field('ID', root + 'id', sensor.id) + select('\u7c7b\u578b', root + 'type', sensor.type, typeOptions) + boolSelect('\u72b6\u6001', root + 'enabled', sensor.enabled) +
      '</div></div><div class="sensorEditorSection"><div class="sensorEditorSectionTitle">\u5b89\u88c5\u4f4d\u59ff</div><div class="grid">' +
        field('\u7236 Link', root + 'mount.parentLink', sensor.mount?.parentLink || 'Frame') + '<div></div>' +
        field('\u4f4d\u7f6e X\uff08m\uff09', root + 'mount.xyz.0', sensor.mount?.xyz?.[0], 'number') + field('\u4f4d\u7f6e Y\uff08m\uff09', root + 'mount.xyz.1', sensor.mount?.xyz?.[1], 'number') + field('\u4f4d\u7f6e Z\uff08m\uff09', root + 'mount.xyz.2', sensor.mount?.xyz?.[2], 'number') +
        field('Roll\uff08\u5ea6\uff09', root + 'mount.rpyDeg.0', sensor.mount?.rpyDeg?.[0], 'number') + field('Pitch\uff08\u5ea6\uff09', root + 'mount.rpyDeg.1', sensor.mount?.rpyDeg?.[1], 'number') + field('Yaw\uff08\u5ea6\uff09', root + 'mount.rpyDeg.2', sensor.mount?.rpyDeg?.[2], 'number') +
      '</div></div><div class="sensorEditorSection"><div class="sensorEditorSectionTitle">' + esc(sensorCatalogItem(sensor.type).label) + '\u53c2\u6570</div><div class="grid">' + sensorTypeFields(sensor, index) + '</div></div></div>';
  }
  function renderSensorSection(draft, isPhysical) {
    const sensors = Array.isArray(draft.sensors) ? draft.sensors : [];
    const typeOptions = SENSOR_CATALOG.map(item => '<option value="' + esc(item.value) + '">' + esc(item.label) + '</option>').join('');
    const rows = sensors.length ? sensors.map((sensor, index) => {
      const critical = FLIGHT_SENSOR_IDS.includes(sensor.id);
      const editing = state.sensorEditorIndex === index;
      return '<div class="sensorItemGroup" data-editing="' + (editing ? '1' : '0') + '"><div class="sensorItem"><div class="sensorInfo"><div class="sensorName">' + esc(sensor.name || sensor.id) + '<span class="tag">' + esc(sensorCatalogItem(sensor.type).label) + '</span>' + (critical ? '<span class="tag warn">\u98de\u63a7\u57fa\u7840</span>' : '') + (!sensor.enabled ? '<span class="tag muted">\u5df2\u505c\u7528</span>' : '') + '</div><div class="sensorMeta">ID: ' + esc(sensor.id) + ' \u00b7 ' + esc(sensorSummary(sensor)) + '</div></div><div class="sensorActions"><button type="button" class="btn" data-action="edit-sensor" data-index="' + index + '">' + (editing ? '\u6536\u8d77' : '\u7f16\u8f91') + '</button><button type="button" class="btn danger" data-action="remove-sensor" data-index="' + index + '">\u5220\u9664</button></div></div>' + (editing ? renderSensorEditor(sensor, index) : '') + '</div>';
    }).join('') : '<div class="sensorEmpty">\u5f53\u524d\u8bbe\u5907\u6ca1\u6709\u914d\u7f6e\u4f20\u611f\u5668\u3002</div>';
    const note = isPhysical ? '\u7269\u7406\u8bbe\u5907\u4e2d\u7684\u4f20\u611f\u5668\u4ec5\u4f5c\u4e3a\u8bbe\u5907\u8d44\u4ea7\u6e05\u5355\uff0cIDE \u4e0d\u4f1a\u4fee\u6539\u771f\u5b9e\u786c\u4ef6\u6216\u56fa\u4ef6\u3002' : '\u4fdd\u5b58\u540e\uff0c\u8fd9\u4e9b\u4f20\u611f\u5668\u4f1a\u5728\u4e0b\u6b21\u542f\u52a8\u4eff\u771f\u65f6\u5199\u5165\u8be5\u65e0\u4eba\u673a\u7684 Project AirSim \u914d\u7f6e\u3002';
    return '<div class="section"><div class="sectionTitle">\u4f20\u611f\u5668\uff08' + sensors.length + '\uff09</div><div class="sensorToolbar"><span class="panelHint">\u7ba1\u7406\u8bbe\u5907\u81ea\u8eab\u7684\u4f20\u611f\u5668\u6e05\u5355\u548c\u5b89\u88c5\u4f4d\u59ff</span><div class="sensorAdd"><select id="newSensorType" class="select">' + typeOptions + '</select><button type="button" class="btn primary" data-action="add-sensor">\u6dfb\u52a0\u4f20\u611f\u5668</button>' + (!isPhysical ? '<button type="button" class="btn" data-action="reset-sensors">\u6062\u590d\u9ed8\u8ba4</button>' : '') + '</div></div><div class="sensorList">' + rows + '</div><div class="sensorNote">' + note + '</div></div>';
  }
  async function discoverSerialPorts(preferDetectedPort) {
    if (state.serialPortsLoading) return;
    state.serialPortsLoading = true;
    state.serialPortsError = '';
    renderMain();
    try {
      const result = api.listSerialPorts
        ? await api.listSerialPorts()
        : { ports: [], error: '\u4e32\u53e3\u68c0\u6d4b\u63a5\u53e3\u4e0d\u53ef\u7528\u3002' };
      state.serialPorts = Array.isArray(result?.ports) ? result.ports : [];
      state.serialPortsError = String(result?.error || '');
      if (preferDetectedPort && state.draft?.connection?.protocol === 'serial' && state.serialPorts.length) {
        const currentPort = String(state.draft.connection.serialPort || '').trim().toUpperCase();
        const currentDetected = state.serialPorts.some(item => item.port === currentPort);
        if (!currentPort || (currentPort === 'COM3' && !currentDetected)) {
          state.draft.connection.serialPort = state.serialPorts[0].port;
        }
      }
    } catch (error) {
      state.serialPorts = [];
      state.serialPortsError = String((error && error.message) || '\u4e32\u53e3\u68c0\u6d4b\u5931\u8d25\u3002');
    } finally {
      state.serialPortsLoading = false;
      render();
    }
  }
  function renderMain() {
    const main = document.getElementById('main');
    if (!state.draft) {
      renderDeviceListMain(main);
      return;
    }
    const draft = state.draft;
    const isUav = draft.deviceCategory === 'uav';
    const isPhysical = draft.runtimeKind === 'physical';
    const physicalConnection = isPhysical ? canonicalConnection(draft) : null;
    const isSerial = physicalConnection?.kind === 'serial';
    const endpoint = physicalConnection ? connectionEndpoint(physicalConnection) : '';
    const hostLabel = physicalConnection?.mode === 'dial' ? '\u8fdc\u7aef\u8bbe\u5907 IP' : '\u672c\u673a\u76d1\u542c IP';
    const probe = renderProbeReport(endpoint);
    main.innerHTML = '<div class="panel"><div class="panelHead"><div><div class="panelTitle">' + (draft.id ? TXT.edit : TXT.add) + '</div></div></div>' +
      // [UAV-IDE MODIFY] Keep identity, FleetDock SDK, and UAV profile fields in one compact device form.
      '<div class="section"><div class="sectionTitle">' + TXT.basic + '</div><div class="grid">' +
        field('\u8bbe\u5907\u540d\u79f0', 'name', draft.name) +
        select('\u8bbe\u5907\u7c7b\u578b', 'deviceCategory', draft.deviceCategory, CATEGORIES) +
        select('\u8bbe\u5907\u6765\u6e90', 'runtimeKind', draft.runtimeKind, RUNTIMES) +
        select('FleetDock 产品', 'fleetdock.builder', draft.fleetdock?.builder || 'px4_generic', FLEETDOCK_PRODUCTS) +
        (isUav
          ? select('\u65e0\u4eba\u673a\u673a\u578b', 'uav.airframeType', draft.uav.airframeType, AIRFRAMES)
          : '<div class="note full">' + TXT.unsupported + '</div>') +
        area('\u5907\u6ce8', 'description', draft.description || '') +
      '</div></div>' +
      (isUav ? renderSensorSection(draft, isPhysical) : '') +
      (isPhysical ? '<div class="section"><div class="sectionTitle">' + TXT.connection + '</div><div class="grid three">' +
        select('\u534f\u8bae', 'connection.protocol', draft.connection?.protocol || draft.telemetry?.protocol || 'udp', [{ value: 'udp', label: 'UDP' }, { value: 'tcp', label: 'TCP' }, { value: 'serial', label: 'Serial' }]) +
        (isSerial
          ? serialPortField(physicalConnection) + '<label class="fieldRow"><span class="label">\u6ce2\u7279\u7387</span><input class="field" data-field="connection.baudRate" type="number" min="1" list="baudRates" value="' + esc(numberOrEmpty(physicalConnection.baudRate || 57600)) + '"><datalist id="baudRates"><option value="57600"><option value="115200"><option value="230400"><option value="460800"><option value="921600"></datalist></label>'
          : select('\u6a21\u5f0f', 'connection.mode', physicalConnection.mode, [{ value: 'listen', label: '\u76d1\u542c' }, { value: 'dial', label: '\u4e3b\u52a8\u8fde\u63a5' }]) + field(hostLabel, 'connection.host', physicalConnection.host) + field('\u7aef\u53e3', 'connection.port', numberOrEmpty(physicalConnection.port), 'number')) +
        '<label class="fieldRow"><span class="label">\u5e94\u7528\u5185\u522b\u540d</span><input class="field" data-action="binding-alias" value="' + esc(state.bindingAlias) + '" placeholder="drone"></label>' +
        '<div class="fieldRow full"><span class="label">FleetDock \u6700\u7ec8\u8fde\u63a5\u5730\u5740</span><div class="endpoint">' + esc(endpoint) + '</div></div>' + probe +
      '</div></div>' : '') +
      '<div class="footer"><button class="btn danger" data-action="delete-current" ' + (!draft.id ? 'disabled' : '') + '>' + TXT.delete + '</button><div class="actions">' + (isPhysical && draft.fleetdock?.builder === 'px4_generic' ? '<button class="btn" data-action="test-connection" ' + (state.testing ? 'disabled' : '') + '>' + (state.testing ? '\u6d4b\u8bd5\u4e2d...' : '\u6d4b\u8bd5\u8fde\u63a5') + '</button>' : '') + (isPhysical && state.probeResult?.ok ? '<button class="btn primary" data-action="save-bind" ' + (state.saving ? 'disabled' : '') + '>\u4fdd\u5b58\u5e76\u7ed1\u5b9a\u5f53\u524d\u5e94\u7528</button>' : '') + '<button class="btn" data-action="back-list">' + TXT.cancel + '</button><button class="btn primary" data-action="save" ' + (state.saving ? 'disabled' : '') + '>' + (state.saving ? TXT.saving : TXT.save) + '</button></div></div></div>';
  }

  function renderProbeReport(endpoint) {
    const report = state.probeResult;
    if (!report) return '';
    const telemetry = report.telemetry || {};
    const live = report.telemetryReceived
      ? '<br>\u9065\u6d4b: \u6a21\u5f0f=' + esc(telemetry.flightMode || '-') + '\uff0c\u89e3\u9501=' + (telemetry.armed === true ? '\u662f' : telemetry.armed === false ? '\u5426' : '-') + '\uff0c\u98de\u884c\u4e2d=' + (telemetry.inAir === true ? '\u662f' : telemetry.inAir === false ? '\u5426' : '-') + '\uff0c\u7535\u91cf=' + (telemetry.batteryPercent == null ? '-' : esc(telemetry.batteryPercent) + '%') + '\uff0cGPS=' + (telemetry.gpsPositionOk === true ? '\u6b63\u5e38' : telemetry.gpsPositionOk === false ? '\u672a\u5c31\u7eea' : '-') + (telemetry.latitudeDeg == null || telemetry.longitudeDeg == null ? '' : ' (' + esc(Number(telemetry.latitudeDeg).toFixed(6)) + ', ' + esc(Number(telemetry.longitudeDeg).toFixed(6)) + ')')
      : '<br>\u6536\u5230\u9065\u6d4b: \u5426';
    return '<div class="probe full ' + (report.ok ? 'ok' : 'error') + '"><strong>' + (report.ok ? '\u914d\u7f6e\u9a8c\u8bc1\u901a\u8fc7\uff0c\u7aef\u53e3\u5df2\u91ca\u653e' : '\u8fde\u63a5\u6d4b\u8bd5\u5931\u8d25') + '</strong><br>' + esc(report.message || '') + '<br>Endpoint: ' + esc(report.endpoint || endpoint) + live + '<br>\u7aef\u53e3\u5df2\u91ca\u653e: ' + (report.released ? '\u662f' : '\u5426') + '<div class="actions" style="margin-top:10px"><button class="btn" data-action="copy-probe-report">\u590d\u5236\u62a5\u544a</button><button class="btn" data-action="export-probe-report">\u5bfc\u51fa JSON</button></div></div>';
  }

  function renderDeviceCards() {
    const devices = filteredDevices();
    return devices.length ? '<div class="mainList">' + devices.map(device => {
      const category = device.deviceCategory || 'uav';
      const runtime = device.runtimeKind || (device.source === 'real' || device.accessType === 'physical' ? 'physical' : 'simulation');
      return '<div class="mainItemWrap"><button class="mainItem" data-action="select" data-id="' + esc(device.id) + '">' +
        '<div class="deviceCard"><div class="deviceIcon" data-category="' + esc(category) + '">' + deviceCategoryIcon(category) + '</div><div class="deviceCardContent"><div class="deviceCardName">' + esc(device.name || 'device') + '</div><div class="deviceCardMeta"><span class="deviceMeta">' + esc(optionLabel(CATEGORIES, category)) + '</span><span class="deviceMeta source ' + (runtime === 'physical' ? 'physical' : '') + '">' + esc(optionLabel(RUNTIMES, runtime)) + '</span></div></div></div>' +
      '</button><button class="mainItemDelete" data-action="delete-card" data-id="' + esc(device.id) + '" title="删除设备" aria-label="删除设备 ' + esc(device.name || 'device') + '">&times;</button></div>';
    }).join('') + '</div>' : '<div class="mainEmpty"><strong>' + TXT.empty + '</strong><div>' + TXT.emptyHint + '</div></div>';
  }

  function updateMainDeviceCards() {
    const host = document.getElementById('mainDeviceCards');
    if (host) host.innerHTML = renderDeviceCards();
  }

  function renderDeviceListMain(main) {
    const categoryOptions = '<option value="all">全部类型</option>' + CATEGORIES.map(item => '<option value="' + item.value + '"' + (state.category === item.value ? ' selected' : '') + '>' + item.label + '</option>').join('');
    const runtimeOptions = '<option value="all">全部来源</option>' + RUNTIMES.map(item => '<option value="' + item.value + '"' + (state.runtime === item.value ? ' selected' : '') + '>' + item.label + '</option>').join('');
    // [UAV-IDE MODIFY] Keep one authoritative device list and move its filters into the main panel.
    main.innerHTML = '<div class="panel"><div class="panelHead"><div><div class="panelTitle">' + TXT.listTitle + '</div><div class="panelHint">' + TXT.listHint + '</div></div><button class="btn primary" data-action="new">' + TXT.add + '</button></div><div class="listToolbar"><input id="search" class="search" data-action="search" placeholder="搜索设备" value="' + esc(state.search) + '"><select id="categoryFilter" class="select" data-action="filter-category">' + categoryOptions + '</select><select id="runtimeFilter" class="select" data-action="filter-runtime">' + runtimeOptions + '</select></div><div id="mainDeviceCards">' + renderDeviceCards() + '</div><div class="panelHint" style="margin-top:12px">' + TXT.editHint + '</div></div>';
  }

  function render() {
    renderMain();
  }
  function selectDevice(id) {
    const device = state.devices.find(item => item.id === id);
    state.selectedId = id || '';
    state.draft = normalizeDraft(device);
    state.sensorEditorIndex = -1;
    state.probeResult = null;
    state.bindingAlias = /^[A-Za-z_][A-Za-z0-9_]*$/.test(device?.name || '') ? device.name : 'drone';
    render();
    if (state.draft.runtimeKind === 'physical' && state.draft.connection?.protocol === 'serial') {
      void discoverSerialPorts(false);
    }
  }
  function newDevice() {
    state.selectedId = '';
    state.draft = defaultDraft();
    state.sensorEditorIndex = -1;
    state.probeResult = null;
    state.bindingAlias = 'drone';
    render();
  }
  function buildPayload() {
    const payload = normalizeDraft(state.draft);
    const profile = airframe(payload.uav.airframeType);
    payload.name = String(payload.name || '').trim();
    payload.enabled = payload.enabled !== false;
    payload.source = payload.runtimeKind === 'physical' ? 'real' : 'simulation';
    payload.accessType = payload.runtimeKind === 'physical' ? 'physical' : 'virtual';
    payload.airsimVehicleName = payload.uav.vehicleName || payload.airsimVehicleName || payload.name || 'Drone1';
    payload.uav.px4AutostartId = profile.px4AutostartId;
    payload.uav.projectAirsimAirframe = profile.projectAirsimAirframe;
    payload.uav.robotTemplate = profile.robotTemplate;
    payload.uav.vehicleName = payload.airsimVehicleName;
    payload.simulation.engine = 'project-airsim-px4';
    payload.simulation.model = profile.value;
    payload.simulation.airsimVehicleName = payload.airsimVehicleName;
    payload.simulation.sceneActorName = payload.airsimVehicleName;
    payload.simulation.startLanded = payload.simulation.startLanded !== false && payload.simulation.startLanded !== 'false';
    payload.simulation.px4StartMode = payload.runtimeKind === 'physical' ? 'manual-connect' : (payload.simulation.px4StartMode || 'ide-managed');
    const physicalConnection = canonicalConnection(payload);
    if (payload.runtimeKind === 'physical') validateConnection(physicalConnection);
    payload.telemetry = physicalConnection.kind === 'serial' ? payload.telemetry : {
      protocol: physicalConnection.kind,
      mode: physicalConnection.mode,
      bindHost: physicalConnection.mode === 'listen' ? physicalConnection.host : undefined,
      bindPort: physicalConnection.mode === 'listen' ? physicalConnection.port : undefined,
      remoteHost: physicalConnection.mode === 'dial' ? physicalConnection.host : undefined,
      remotePort: physicalConnection.mode === 'dial' ? physicalConnection.port : undefined
    };
    payload.real = Object.assign(payload.real || {}, {
      connection: physicalConnection,
      connectionKind: physicalConnection.kind,
      host: physicalConnection.kind === 'serial' ? '' : physicalConnection.host,
      port: physicalConnection.kind === 'serial' ? undefined : physicalConnection.port,
      serialPort: physicalConnection.kind === 'serial' ? physicalConnection.serialPort : '',
      baudRate: physicalConnection.kind === 'serial' ? physicalConnection.baudRate : undefined
    });
    normalizeFleetDock(payload);
    if (payload.fleetdock.builder === 'px4_generic') {
      delete payload.fleetdock.params.force_arm;
      payload.fleetdock.params.timeout_s = payload.runtimeKind === 'physical' && physicalConnection.kind === 'serial' ? 10 : Number(payload.fleetdock.params.timeout_s || 30);
      if (payload.runtimeKind === 'physical') payload.fleetdock.params.endpoint = connectionEndpoint(physicalConnection);
      if (payload.fleetdock.params.mavsdk_server_port === '') {
        delete payload.fleetdock.params.mavsdk_server_port;
      }
      if (payload.runtimeKind === 'physical') delete payload.fleetdock.params.mavsdk_server_port;
    }
    return payload;
  }
  async function saveDevice(bindAfterSave) {
    let payload;
    try {
      payload = buildPayload();
    } catch (error) {
      showToast((error && error.message) || TXT.saveFailed);
      return;
    }
    if (!payload.name) {
      showToast(TXT.nameRequired);
      return;
    }
    if (payload.runtimeKind === 'physical' && payload.fleetdock?.builder === 'px4_generic') {
      const endpoint = payload.fleetdock.params.endpoint;
      const duplicate = state.devices.find(device => device.id !== payload.id && (device.runtimeKind === 'physical' || device.source === 'real') && connectionEndpoint(canonicalConnection(device)) === endpoint);
      if (duplicate && !window.confirm('\u8bbe\u5907\u5e93\u4e2d\u5df2\u6709\u8bbe\u5907\u201c' + duplicate.name + '\u201d\u4f7f\u7528\u76f8\u540c\u5730\u5740 ' + endpoint + '\u3002\u4e0d\u540c\u5e94\u7528\u53ef\u4ee5\u5171\u7528\u8be5\u914d\u7f6e\uff0c\u4ecd\u8981\u4fdd\u5b58\u5417\uff1f')) return;
    }
    state.saving = true;
    render();
    try {
      const saved = api.saveDroneDevice ? await api.saveDroneDevice(payload) : payload;
      const index = state.devices.findIndex(item => item.id === saved.id);
      if (index >= 0) {
        state.devices[index] = saved;
      } else {
        state.devices.unshift(saved);
      }
      state.selectedId = saved.id;
      if (bindAfterSave) {
        if (!api.bindDroneDevice) throw new Error('\u5f53\u524d\u7248\u672c\u4e0d\u652f\u6301\u5e94\u7528\u7ed1\u5b9a\u3002');
        const binding = await api.bindDroneDevice(saved.id, state.bindingAlias);
        showToast('\u8bbe\u5907\u5df2\u4fdd\u5b58\u5e76\u7ed1\u5b9a\u4e3a FleetDock.devices.' + binding.alias);
      } else {
        showToast(TXT.saved);
      }
      state.draft = null;
      state.sensorEditorIndex = -1;
    } catch (error) {
      showToast((error && error.message) || TXT.saveFailed);
    } finally {
      state.saving = false;
      render();
    }
  }
  async function testConnection() {
    let connection;
    try {
      connection = canonicalConnection(state.draft);
      validateConnection(connection);
    } catch (error) {
      showToast((error && error.message) || '\u8fde\u63a5\u914d\u7f6e\u65e0\u6548');
      return;
    }
    state.testing = true;
    state.probeResult = null;
    render();
    try {
      state.probeResult = api.testPhysicalConnection
        ? await api.testPhysicalConnection(connection)
        : { ok: false, endpoint: connectionEndpoint(connection), message: '\u8fde\u63a5\u6d4b\u8bd5\u63a5\u53e3\u4e0d\u53ef\u7528', telemetryReceived: false };
    } catch (error) {
      state.probeResult = { ok: false, endpoint: connectionEndpoint(connection), message: (error && error.message) || '\u8fde\u63a5\u6d4b\u8bd5\u5931\u8d25', telemetryReceived: false };
    } finally {
      state.testing = false;
      render();
    }
  }
  async function deleteDevice(id) {
    const deviceId = id || state.draft?.id || state.selectedId;
    if (!deviceId || !window.confirm(TXT.deleteConfirm)) {
      return;
    }
    if (api.deleteDroneDevice) {
      await api.deleteDroneDevice(deviceId);
    }
    state.devices = state.devices.filter(item => item.id !== deviceId);
    state.selectedId = '';
    state.draft = null;
    state.sensorEditorIndex = -1;
    render();
    showToast(TXT.deleted);
  }
  document.addEventListener('click', event => {
    const target = event.target instanceof Element ? event.target : null;
    const actionEl = target?.closest('[data-action]');
    if (!actionEl) {
      return;
    }
    const action = actionEl.getAttribute('data-action');
    if (action === 'close') {
      window.close();
    } else if (action === 'new') {
      newDevice();
    } else if (action === 'back-list') {
      state.selectedId = '';
      state.draft = null;
      state.sensorEditorIndex = -1;
      state.probeResult = null;
      render();
    } else if (action === 'select') {
      selectDevice(actionEl.getAttribute('data-id') || '');
    } else if (action === 'delete-card') {
      void deleteDevice(actionEl.getAttribute('data-id') || '');
    } else if (action === 'save') {
      void saveDevice(false);
    } else if (action === 'save-bind') {
      void saveDevice(true);
    } else if (action === 'test-connection') {
      void testConnection();
    } else if (action === 'copy-probe-report' && state.probeResult) {
      void api.copyPhysicalProbeReport?.(state.probeResult).then(() => showToast('\u8fde\u63a5\u6d4b\u8bd5\u62a5\u544a\u5df2\u590d\u5236'));
    } else if (action === 'export-probe-report' && state.probeResult) {
      void api.exportPhysicalProbeReport?.(state.probeResult).then(filePath => {
        if (filePath) showToast('\u62a5\u544a\u5df2\u5bfc\u51fa\uff1a' + filePath);
      });
    } else if (action === 'refresh-serial-ports') {
      void discoverSerialPorts(false);
    } else if (action === 'delete-current') {
      void deleteDevice();
    } else if (action === 'add-sensor' && state.draft) {
      const typeElement = document.getElementById('newSensorType');
      const type = typeElement instanceof HTMLSelectElement ? typeElement.value : 'camera';
      state.draft.sensors = Array.isArray(state.draft.sensors) ? state.draft.sensors : [];
      state.draft.sensors.push(sensorPreset(type));
      state.sensorEditorIndex = state.draft.sensors.length - 1;
      renderMain();
    } else if (action === 'edit-sensor' && state.draft) {
      const index = Number(actionEl.getAttribute('data-index'));
      state.sensorEditorIndex = state.sensorEditorIndex === index ? -1 : index;
      renderMain();
    } else if (action === 'close-sensor-editor') {
      state.sensorEditorIndex = -1;
      renderMain();
    } else if (action === 'remove-sensor' && state.draft) {
      const index = Number(actionEl.getAttribute('data-index'));
      const sensor = state.draft.sensors?.[index];
      if (!sensor) return;
      const critical = FLIGHT_SENSOR_IDS.includes(sensor.id);
      const message = critical
        ? '\u8fd9\u662f PX4 \u98de\u63a7\u57fa\u7840\u4f20\u611f\u5668\uff0c\u5220\u9664\u540e\u53ef\u80fd\u5bfc\u81f4\u4eff\u771f\u542f\u52a8\u6216\u5b9a\u4f4d\u5931\u8d25\u3002\u786e\u8ba4\u5220\u9664\u5417\uff1f'
        : '\u786e\u8ba4\u5220\u9664\u4f20\u611f\u5668\u201c' + sensor.name + '\u201d\u5417\uff1f';
      if (!window.confirm(message)) return;
      state.draft.sensors.splice(index, 1);
      state.sensorEditorIndex = -1;
      renderMain();
    } else if (action === 'reset-sensors' && state.draft) {
      if (!window.confirm('\u786e\u8ba4\u6062\u590d\u4e3a Chase\u3001IMU\u3001GPS\u3001\u6c14\u538b\u8ba1\u548c\u78c1\u529b\u8ba1\u8fd9 5 \u4e2a\u9ed8\u8ba4\u4f20\u611f\u5668\u5417\uff1f')) return;
      state.draft.sensors = clone(DEFAULT_SENSORS);
      state.sensorEditorIndex = -1;
      renderMain();
    } else if (action === 'toggle-image-type' && state.draft) {
      const index = Number(actionEl.getAttribute('data-index'));
      const imageType = Number(actionEl.getAttribute('data-image-type'));
      const sensor = state.draft.sensors?.[index];
      if (!sensor || sensor.type !== 'camera') return;
      const imageTypes = Array.isArray(sensor.config.imageTypes) ? sensor.config.imageTypes : [];
      const current = imageTypes.indexOf(imageType);
      if (current >= 0) {
        if (imageTypes.length === 1) {
          showToast('\u76f8\u673a\u81f3\u5c11\u9700\u8981\u4e00\u79cd\u56fe\u50cf\u8f93\u51fa\u3002');
          return;
        }
        imageTypes.splice(current, 1);
      } else {
        imageTypes.push(imageType);
        imageTypes.sort((left, right) => left - right);
      }
      sensor.config.imageTypes = imageTypes;
      renderMain();
    }
  });
  document.addEventListener('input', event => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement) && !(target instanceof HTMLTextAreaElement)) {
      return;
    }
    if (target.dataset.action === 'search') {
      state.search = target.value;
      if (!state.draft) {
        updateMainDeviceCards();
      }
      return;
    }
    if (target.dataset.action === 'binding-alias') {
      state.bindingAlias = target.value.trim();
      return;
    }
    const field = target.getAttribute('data-field');
    if (!field || !state.draft) {
      return;
    }
    const value = target instanceof HTMLInputElement && target.type === 'number' ? (target.value === '' ? '' : Number(target.value)) : target.value;
    setDeep(state.draft, field, value);
    if (field.startsWith('connection.')) {
      state.probeResult = null;
      const endpointElement = document.querySelector('.endpoint');
      if (endpointElement) endpointElement.textContent = connectionEndpoint(canonicalConnection(state.draft));
    }
  });
  document.addEventListener('change', event => {
    const target = event.target;
    if (!(target instanceof HTMLSelectElement)) {
      return;
    }
    const action = target.dataset.action;
    if (action === 'filter-category') {
      state.category = target.value;
      if (!state.draft) {
        updateMainDeviceCards();
      }
      return;
    }
    if (action === 'filter-runtime') {
      state.runtime = target.value;
      if (!state.draft) {
        updateMainDeviceCards();
      }
      return;
    }
    const field = target.getAttribute('data-field');
    if (!field || !state.draft) {
      return;
    }
    let value = target.value;
    const shouldDiscoverSerial = field === 'connection.protocol' && value === 'serial';
    if (target.dataset.valueType === 'boolean' || field === 'simulation.startLanded') {
      value = target.value === 'true';
    }
    const sensorFieldParts = field.split('.');
    if (sensorFieldParts[0] === 'sensors' && sensorFieldParts[2] === 'type') {
      const sensorIndex = Number(sensorFieldParts[1]);
      const previous = state.draft.sensors?.[sensorIndex];
      if (previous) {
        const replacement = sensorPreset(value);
        replacement.id = previous.id;
        replacement.name = previous.name;
        replacement.enabled = previous.enabled;
        replacement.mount = clone(previous.mount);
        state.draft.sensors[sensorIndex] = replacement;
        state.sensorEditorIndex = sensorIndex;
        render();
        return;
      }
    }
    setDeep(state.draft, field, value);
    if (field === 'uav.airframeType') {
      syncAirframe(state.draft);
    }
    if (field === 'fleetdock.builder') {
      state.draft.deviceCategory = fleetdockProduct(value).category;
      normalizeFleetDock(state.draft);
    }
    if (field === 'runtimeKind') {
      state.draft.source = value === 'physical' ? 'real' : 'simulation';
      state.draft.accessType = value === 'physical' ? 'physical' : 'virtual';
      state.draft.simulation.px4StartMode = value === 'physical' ? 'manual-connect' : 'ide-managed';
      if (value === 'simulation' && (!Array.isArray(state.draft.sensors) || !state.draft.sensors.length)) {
        state.draft.sensors = clone(DEFAULT_SENSORS);
      }
      if (value === 'physical') {
        state.draft.connection = { protocol: 'udp', mode: 'listen', host: '0.0.0.0', port: nextPhysicalPort(), serialPort: 'COM3', baudRate: 57600 };
      }
    }
    if (field === 'connection.protocol') {
      if (value === 'serial') {
        state.draft.connection = { protocol: 'serial', mode: 'listen', host: '', port: '', serialPort: state.draft.connection.serialPort || 'COM3', baudRate: Number(state.draft.connection.baudRate || 57600) };
      } else {
        state.draft.connection = { protocol: value, mode: state.draft.connection.mode === 'dial' ? 'dial' : 'listen', host: state.draft.connection.host || (state.draft.connection.mode === 'dial' ? '127.0.0.1' : '0.0.0.0'), port: Number(state.draft.connection.port || nextPhysicalPort()), serialPort: 'COM3', baudRate: 57600 };
      }
    }
    if (field === 'connection.mode') {
      state.draft.connection.host = value === 'dial' && state.draft.connection.host === '0.0.0.0' ? '127.0.0.1' : (value === 'listen' && state.draft.connection.host === '127.0.0.1' ? '0.0.0.0' : state.draft.connection.host);
    }
    if (field.startsWith('connection.') || field === 'runtimeKind') state.probeResult = null;
    render();
    if (shouldDiscoverSerial) void discoverSerialPorts(true);
  });
  async function init() {
    state.devices = api.loadDroneDevices ? await api.loadDroneDevices() : [];
    state.draft = null;
    state.selectedId = '';
    if (INITIAL_DEVICE_ID && state.devices.some(device => String(device.id || '') === INITIAL_DEVICE_ID)) {
      selectDevice(INITIAL_DEVICE_ID);
      return;
    }
    render();
  }
  window.fleetDockDeviceManager = {
    selectDevice: function(id) {
      const selectedId = String(id || '');
      if (state.devices.some(device => String(device.id || '') === selectedId)) {
        selectDevice(selectedId);
      }
    }
  };
  init().catch(() => {
    state.devices = [];
    state.draft = null;
    render();
  });
})();
</script>
</body>
</html>`;
  }

  private _window: ThemedWindow;
  private _pageBody: string;
}

export namespace DeviceManagerDialog {
  export interface IOptions {
    isDarkTheme: boolean;
    parent: BrowserWindow;
    workingDirectory?: string;
    initialDeviceId?: string;
  }
}
