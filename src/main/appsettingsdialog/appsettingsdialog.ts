// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Workspace-scoped application settings editor for the UAV IDE shell.

import { BrowserWindow } from 'electron';
import * as path from 'path';
import {
  APPLICATION_ENV_TYPES,
  createDefaultApplicationConfig,
  getApplicationEnvValueHint,
  getApplicationEnvValuePlaceholder,
  normalizeApplicationConfig,
  readApplicationConfig
} from '../config/application';
import { ThemedWindow } from '../dialog/themedwindow';

const ENV_VAR_TYPES = [...APPLICATION_ENV_TYPES];
export class ApplicationSettingsDialog {
  constructor(options: ApplicationSettingsDialog.IOptions) {
    this._workingDirectory = path.resolve(options.workingDirectory);
    this._window = new ThemedWindow({
      isDarkTheme: options.isDarkTheme,
      modal: !!options.parent,
      parent: options.parent,
      title: '\u5e94\u7528\u8bbe\u7f6e',
      width: 980,
      height: 760,
      preload: path.join(__dirname, './preload.js')
    });

    const config = normalizeApplicationConfig(
      readApplicationConfig(this._workingDirectory) ||
        createDefaultApplicationConfig(this._workingDirectory),
      this._workingDirectory
    );

    this._pageBody = this._buildPageSource(config);
  }

  get window(): ThemedWindow {
    return this._window;
  }

  load() {
    this._window.loadDialogContent(this._pageBody);
  }

  private _buildPageSource(
    config: ReturnType<typeof normalizeApplicationConfig>
  ) {
    return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta http-equiv="Content-Security-Policy" content="default-src 'self' data: 'unsafe-inline' 'unsafe-eval'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline' 'unsafe-eval'">
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>\u5e94\u7528\u8bbe\u7f6e</title>
<style>
/* [UAV-IDE MODIFY] Align this dialog with the FleetDock IDEA blue/cyan design system. */
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#f6f8fb;color:#111827;font:13px/1.4 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Helvetica,Arial,sans-serif}
*{box-sizing:border-box}button,input,textarea,select{font:inherit}
.shell{position:relative;width:100%;height:100%;display:flex;flex-direction:column}.body{flex:1;min-height:0;display:grid;grid-template-columns:360px 1fr}
.nav{padding:16px 14px 18px 12px;border-right:1px solid #e2e8f0;overflow:auto;background:#fff}.panel{padding:24px 30px 20px;overflow:auto}
.moduleSearch{width:100%;height:52px;margin-bottom:18px;padding:0 16px;border:1px solid #e2e8f0;border-radius:18px;background:#fff;outline:none;font-size:14px}
.group{color:#64748b;font-size:14px;margin:18px 0 10px;padding-left:2px}.item{height:68px;display:flex;align-items:center;gap:14px;padding:0 14px;margin-bottom:10px;border-radius:16px;cursor:pointer;border:1px solid transparent}.item:hover,.item.active{background:rgba(37,99,235,.08);border-color:rgba(37,99,235,.15)}.icon{width:48px;height:48px;border-radius:14px;border:1px solid rgba(37,99,235,.35);color:#2563eb;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:15px;background:rgba(255,255,255,.76)}.itemName{font-size:16px;font-weight:700}.panelTitle{font-size:24px;font-weight:700;margin-bottom:18px}.cardBox{max-width:960px;padding-right:24px}.sub{font-size:14px;color:#64748b;margin-bottom:12px}.fieldRow{margin-bottom:16px}.label{display:block;font-size:14px;color:#64748b;margin-bottom:8px}.field,.textarea,.select{width:100%;min-height:48px;padding:12px 14px;border:1px solid #e2e8f0;border-radius:12px;background:#fff;color:inherit;outline:none}.textarea{min-height:144px;resize:vertical}.row{display:flex;align-items:center;gap:10px}.row .field{flex:1}.smallBtn,.footerBtn,.modalBtn{height:46px;padding:0 18px;border:1px solid #e2e8f0;border-radius:12px;background:#fff;color:inherit;cursor:pointer}.footer{height:84px;padding:0 24px;border-top:1px solid #e2e8f0;display:flex;align-items:center;justify-content:flex-end;gap:12px;background:#fff}.footerBtn{min-width:126px}.footerBtn.primary,.modalBtn.primary{background:#2563eb;border-color:#2563eb;color:#fff}.envList{display:flex;flex-direction:column;gap:12px}.envRow{display:grid;grid-template-columns:112px 220px 1fr 40px;align-items:center;gap:12px;padding:14px;border:1px solid #e2e8f0;border-radius:14px;background:#fff;cursor:pointer}.pill{width:96px;height:34px;border-radius:999px;background:rgba(37,99,235,.1);color:#2563eb;display:flex;align-items:center;justify-content:center;font-weight:700}.del{width:34px;height:34px;border:0;border-radius:10px;background:transparent;color:inherit;cursor:pointer;font-size:16px}.hint{margin-top:12px;color:#64748b;font-size:13px}.empty{padding:30px 0;color:#64748b;text-align:center}.modalBackdrop{position:absolute;inset:0;background:rgba(0,0,0,.28);display:flex;align-items:center;justify-content:center;opacity:0;pointer-events:none;transition:opacity .18s ease;z-index:20}.modalBackdrop.show{opacity:1;pointer-events:auto}.modal{width:min(640px,calc(100vw - 40px));border-radius:20px;background:#fff;box-shadow:0 20px 60px rgba(15,23,42,.18);overflow:hidden}.modalHeader{height:76px;padding:0 24px;border-bottom:1px solid #e2e8f0;display:flex;align-items:center;justify-content:space-between}.modalTitle{font-size:20px;font-weight:700}.modalClose{width:32px;height:32px;border:0;border-radius:8px;background:transparent;color:inherit;cursor:pointer;font-size:22px;line-height:32px}.modalBody{padding:22px 24px 18px}.modalFooter{padding:18px 24px 24px;border-top:1px solid #e2e8f0;display:flex;justify-content:flex-end;gap:12px}.toast{position:absolute;right:18px;bottom:98px;min-width:260px;max-width:min(480px,calc(100vw - 36px));padding:0 14px;border-radius:14px;background:rgba(15,23,42,.96);color:#fff;opacity:0;transform:translateY(8px);pointer-events:none;transition:opacity .18s ease,transform .18s ease;z-index:30}.toast.show{opacity:1;transform:translateY(0);pointer-events:auto}.toastRow{min-height:48px;display:flex;align-items:center;justify-content:space-between;gap:16px}.toastClose{width:28px;height:28px;border:0;border-radius:8px;background:rgba(255,255,255,.08);color:#fff;cursor:pointer}
/* [UAV-IDE MODIFY] Add second-stage micro-interactions and accessible focus states without changing settings logic. */
.smallBtn,.footerBtn,.modalBtn,.item,.envRow,.del,.modalClose,.toastClose{transition:border-color .18s ease,box-shadow .18s ease,background-color .18s ease,color .18s ease}.smallBtn:hover,.footerBtn:hover,.modalBtn:hover,.envRow:hover{border-color:#93c5fd;box-shadow:0 6px 18px rgba(37,99,235,.08)}.smallBtn:active,.footerBtn:active,.modalBtn:active{background:#eff6ff}.field:focus-visible,.textarea:focus-visible,.select:focus-visible,.smallBtn:focus-visible,.footerBtn:focus-visible,.modalBtn:focus-visible,.item:focus-visible,.envRow:focus-visible,.del:focus-visible,.modalClose:focus-visible,.toastClose:focus-visible{outline:2px solid rgba(6,182,212,.55);outline-offset:2px}.smallBtn:disabled,.footerBtn:disabled,.modalBtn:disabled{opacity:.55;cursor:not-allowed;box-shadow:none}.footerBtn.primary:hover,.modalBtn.primary:hover{background:#1d4ed8;border-color:#1d4ed8}.del:hover{background:#fee2e2;color:#dc2626}@media (prefers-reduced-motion: reduce){*{transition:none!important;animation:none!important}}
</style>
</head>
<body>
<div class="shell">
  <div class="body">
    <div class="nav" id="nav"></div>
    <div class="panel" id="panel"></div>
  </div>
  <div class="footer">
    <button class="footerBtn" id="cancel-application-settings" data-action="cancel">\u53d6\u6d88</button>
    <button class="footerBtn primary" id="save-application-settings" data-action="save">\u4fdd\u5b58</button>
  </div>
  <div id="modalHost"></div>
  <div id="toast" class="toast"></div>
</div>
<script>
(function () {
  const api = window.electronAPI || {};
  const envVarTypes = ${JSON.stringify(ENV_VAR_TYPES)};
  const envTypeLabels = { Text: '文本', Drone: '设备-无人机', DronePoint: '航点', DroneRoute: '航线' };
  const envValuePlaceholders = ${JSON.stringify(
    Object.fromEntries(
      APPLICATION_ENV_TYPES.map(type => [
        type,
        getApplicationEnvValuePlaceholder(type)
      ])
    )
  )};
  const envValueHints = ${JSON.stringify(
    Object.fromEntries(
      APPLICATION_ENV_TYPES.map(type => [
        type,
        getApplicationEnvValueHint(type)
      ])
    )
  )};
  const state = {
    section: 'basic',
    draft: ${JSON.stringify(config)},
    envDraft: defaultEnvDraft(),
    envEditIndex: -1,
    envModalVisible: false,
    toast: '',
    toastClosable: false,
    saving: false,
    bootstrapped: false,
    devices: [],
    loadingDevices: false,
    deviceLoadStarted: false
  };

  const droneEnvType = 'Drone';
  const waypointEnvType = 'DronePoint';
  const routeEnvType = 'DroneRoute';
  function defaultEnvDraft(){ return { type: envVarTypes[0] || '\u822a\u70b9', name: '', value: '' }; }
  function esc(v){ return String(v === undefined || v === null ? '' : v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
  function pathParts(p){ return String(p || '').split('.').map(s=>s.trim()).filter(Boolean); }
  function setDeep(target, p, value){ const parts = pathParts(p); if (!parts.length) return; let cur = target; for (let i=0;i<parts.length-1;i++){ const key = parts[i]; if (!cur[key] || typeof cur[key] !== 'object') cur[key] = {}; cur = cur[key]; } cur[parts[parts.length-1]] = value; }
  function normalizeEnvType(value){ return envVarTypes.indexOf(value) >= 0 ? value : (envVarTypes[0] || ''); }
  function envTypeLabel(value){ return envTypeLabels[normalizeEnvType(value)] || value || ''; }
  function setSection(section){ state.section = section; render(); }
  function setField(path, value){ setDeep(state.draft, path, value); }
  function setEnvField(field, value){ state.envDraft[field] = value; if (field === 'type') render(); }
  function toast(message, closable){ state.toast = message || ''; state.toastClosable = !!closable; renderToast(); }
  function openPython(){ if (typeof api.selectFilePath !== 'function'){ toast('\u6587\u4ef6\u9009\u62e9\u6865\u63a5\u672a\u52a0\u8f7d', true); return; } api.selectFilePath(state.draft.pythonPath).then(result => { if (result !== undefined && result !== null){ state.draft.pythonPath = String(result || ''); render(); } }); }
  function openEnv(index){ if (index >= 0 && state.draft.envVars[index]){ const item = state.draft.envVars[index]; state.envDraft = { type: normalizeEnvType(item.type), name: item.name || '', required: !!item.required, description: item.description || '', defaultValue: item.defaultValue || '', sensitive: !!item.sensitive, options: Array.isArray(item.options) ? item.options : [], value: item.value || item.defaultValue || '' }; state.envEditIndex = index; } else { state.envDraft = defaultEnvDraft(); state.envEditIndex = -1; } state.envModalVisible = true; render(); }
  function closeEnv(){ state.envModalVisible = false; render(); }
  function isDuplicateEnvName(envVars, name, ignoreIndex){ const target = String(name || '').trim().toLowerCase(); if (!target) return false; return (envVars || []).some((item, index) => index !== ignoreIndex && String(item && item.name ? item.name : '').trim().toLowerCase() === target); }
  function parseWaypointValue(value){ try { const parsed = JSON.parse(String(value || '')); const lat = Number(parsed.lat); const lon = Number(parsed.lon); const alt = parsed.alt === undefined || parsed.alt === null || parsed.alt === '' ? 20 : Number(parsed.alt); if (!Number.isFinite(lat) || !Number.isFinite(lon) || !Number.isFinite(alt)) { return null; } return { lat: lat.toFixed(6), lon: lon.toFixed(6), alt: alt.toFixed(2) }; } catch (error) { return null; } }
  function formatWaypointValue(value){ const waypoint = parseWaypointValue(value); if (!waypoint) return ''; return '纬度 ' + waypoint.lat + '，经度 ' + waypoint.lon + '，相对 Home 高度 ' + waypoint.alt + ' m'; }
  // [UAV-IDE MODIFY] Launch the map picker so waypoint-type environment values stay structured and user-friendly.
  function currentWaypointText(){ return formatWaypointValue(state.envDraft.value) || '点击地图选择一个坐标点'; }
  function openWaypointPicker(){ if (typeof api.pickWaypoint !== 'function'){ toast('航点选择桥接未加载', true); return; } api.pickWaypoint(state.envDraft.value).then(result => { if (result !== undefined && result !== null){ state.envDraft.value = String(result || ''); render(); } }); }
  function parseRouteValue(value){ try { const parsed = JSON.parse(String(value || '')); if (!Array.isArray(parsed)) { return null; } const route = []; for (const item of parsed) { const lat = Number(item && item.lat); const lon = Number(item && item.lon); const alt = item && item.alt !== undefined && item.alt !== null && item.alt !== '' ? Number(item.alt) : 20; if (!Number.isFinite(lat) || !Number.isFinite(lon) || !Number.isFinite(alt)) { return null; } route.push({ lat: lat.toFixed(6), lon: lon.toFixed(6), alt: alt.toFixed(2) }); } return route; } catch (error) { return null; } }
  function formatRouteValue(value){ const route = parseRouteValue(value); if (!route) return ''; return route.length ? route.length + ' 个航点' : ''; }
  function currentRouteText(){ return formatRouteValue(state.envDraft.value) || '点击地图创建航线'; }
  function openRoutePicker(){ if (typeof api.pickRoute !== 'function'){ toast('航线选择桥接未加载', true); return; } api.pickRoute(state.envDraft.value).then(result => { if (result !== undefined && result !== null){ state.envDraft.value = String(result || ''); render(); } }); }
  function routeValueField(){ return '<div class="fieldRow"><div class="label">变量值</div><div class="row"><input class="field" readonly value="' + esc(currentRouteText()) + '" placeholder="点击地图创建航线" data-action="openRoutePicker" /><button class="smallBtn" data-action="openRoutePicker">' + (formatRouteValue(state.envDraft.value) ? '编辑航线' : '创建航线') + '</button></div><div class="hint">航线会以 JSON 数组方式保存，保留每个航点的 lat、lon、alt 字段。</div></div>'; }
  function parseDroneDeviceValue(value){ let current = value; for (let i = 0; i < 2; i += 1){ try { const parsed = JSON.parse(String(current || '')); if (typeof parsed === 'string'){ current = parsed; continue; } return parsed && (parsed.id || parsed.name) ? parsed : null; } catch (error) { return null; } } return null; }
  // [UAV-IDE MODIFY] New Drone bindings persist only the global device id so runtime generation always resolves the latest endpoint.
  function droneDeviceValue(device){ return String(device && device.id ? device.id : ''); }
  function resolveDroneDeviceValue(value){ const select = document.querySelector('select[data-path="value"][data-scope="env"]'); const selectedId = select && select.value ? String(select.value) : String(value || ''); const device = state.devices.find(item => String(item.id) === selectedId); return device ? droneDeviceValue(device) : ''; }
  function selectedDroneDeviceId(){ const parsed = parseDroneDeviceValue(state.envDraft.value || state.envDraft.defaultValue); return parsed ? String(parsed.id || '') : String(state.envDraft.value || state.envDraft.defaultValue || ''); }
  function extractDroneDeviceName(value){ const raw = String(value || ''); const nameMatch = raw.match(/["']name["']\\s*:\\s*["']([^"']+)["']/); if (nameMatch && nameMatch[1]) return nameMatch[1]; const idMatch = raw.match(/["']id["']\\s*:\\s*["']([^"']+)["']/); return idMatch && idMatch[1] ? idMatch[1] : ''; }
  function droneDeviceText(value){ const parsed = parseDroneDeviceValue(value); if (parsed) return parsed.name || parsed.id || '未命名设备'; const device = state.devices.find(item => String(item.id) === String(value || '')); if (device) return device.name || device.id; const extractedName = extractDroneDeviceName(value); if (extractedName) return extractedName; const raw = String(value || '').trim(); return raw ? '设备已失效（' + raw + '）' : ''; }
  function droneDeviceValueField(){ const selectedId = selectedDroneDeviceId(); const found = state.devices.some(device => selectedId === String(device.id)); const invalid = selectedId && !found ? '<option value="' + esc(selectedId) + '" selected>设备已失效 - ' + esc(selectedId) + '</option>' : ''; const options = state.devices.map(function(device){ return '<option value="' + esc(device.id || '') + '" ' + (selectedId === String(device.id) ? 'selected' : '') + '>' + esc((device.name || device.id || '未命名设备') + ' - ' + (device.source === 'real' ? '真实设备' : '仿真设备')) + '</option>'; }).join(''); return '<div class="fieldRow"><div class="label">变量值</div><select class="select" data-path="value" data-scope="env"><option value="">' + (state.loadingDevices ? '正在加载设备...' : '请选择无人机设备') + '</option>' + invalid + options + '</select><div class="hint">应用仅保存全局设备 ID；运行时会读取设备库最新连接配置，并生成 FleetDock.devices.' + esc(state.envDraft.name || 'drone1') + '。</div></div>'; }
  function syncEnvDraftFromModal(){ Array.from(document.querySelectorAll('[data-scope="env"][data-path]')).forEach(function(fieldEl){ const pathValue = fieldEl.getAttribute('data-path') || ''; if (!pathValue) return; const value = fieldEl instanceof HTMLInputElement && fieldEl.type === 'checkbox' ? fieldEl.checked : fieldEl.value; state.envDraft[pathValue] = value; }); }
  function isPythonIdentifier(value){ const name = String(value || '').trim(); const keywords = new Set(['False','None','True','and','as','assert','async','await','break','class','continue','def','del','elif','else','except','finally','for','from','global','if','import','in','is','lambda','nonlocal','not','or','pass','raise','return','try','while','with','yield']); return /^[A-Za-z_][A-Za-z0-9_]*$/.test(name) && !keywords.has(name); }
  function saveEnv(){ syncEnvDraftFromModal(); const normalizedType = normalizeEnvType(state.envDraft.type); if (!state.envDraft.name.trim()){ toast('\u8bf7\u5148\u586b\u5199\u53d8\u91cf\u540d', true); return; } if (isDuplicateEnvName(state.draft.envVars, state.envDraft.name, state.envEditIndex)){ toast('\u53d8\u91cf\u540d\u5df2\u5b58\u5728\uff0c\u8bf7\u91cd\u65b0\u547d\u540d', true); return; } if (normalizedType === waypointEnvType && !parseWaypointValue(state.envDraft.value)){ toast('请先选择有效航点', true); return; } if (normalizedType === routeEnvType && (!parseRouteValue(state.envDraft.value) || !parseRouteValue(state.envDraft.value).length)){ toast('请先创建有效航线', true); return; } if (normalizedType === droneEnvType){ if (!isPythonIdentifier(state.envDraft.name)){ toast('无人机别名必须是有效且非关键字的 Python 标识符，例如 drone1', true); return; } const deviceValue = resolveDroneDeviceValue(state.envDraft.value); if (!deviceValue){ toast('请先选择有效的无人机设备', true); return; } state.envDraft.value = deviceValue; } const payload = { type: normalizedType, name: state.envDraft.name.trim(), required: !!state.envDraft.required, description: state.envDraft.description || '', defaultValue: state.envDraft.value || state.envDraft.defaultValue || '', sensitive: normalizedType === 'SecretText' || !!state.envDraft.sensitive, options: Array.isArray(state.envDraft.options) ? state.envDraft.options : [], value: state.envDraft.value }; if (state.envEditIndex >= 0) { state.draft.envVars[state.envEditIndex] = payload; } else { state.draft.envVars.push(payload); } state.envModalVisible = false; render(); }
  // [UAV-IDE MODIFY] Confirm destructive env-var removal to prevent accidental configuration loss.
  function removeEnv(index){ if (!window.confirm('\u786e\u8ba4\u5220\u9664\u8fd9\u4e2a\u73af\u5883\u53d8\u91cf\u5417\uff1f')) return; state.draft.envVars.splice(index, 1); render(); }
  function cancel(){ window.close(); }
  function syncSaveButton(){ const saveButton = document.getElementById('save-application-settings'); if (!saveButton) return; saveButton.disabled = !!state.saving; saveButton.textContent = state.saving ? '\u6b63\u5728\u4fdd\u5b58...' : '\u4fdd\u5b58'; }
  // [UAV-IDE MODIFY] Avoid regex escaping inside the generated inline script when detecting exclusive endpoints.
  function validateDroneBindings(){ const endpoints = new Map(); for (const item of state.draft.envVars || []){ if (normalizeEnvType(item.type) !== droneEnvType) continue; if (!isPythonIdentifier(item.name)) return '无人机别名“' + item.name + '”不是有效的 Python 标识符'; const raw = item.value || item.defaultValue || ''; const snapshot = parseDroneDeviceValue(raw); const device = snapshot || state.devices.find(entry => String(entry.id) === String(raw)); if (!device) return '设备绑定已失效：' + raw; const endpoint = String(device.fleetdock?.params?.endpoint || '').trim(); const exclusiveEndpoint = endpoint.startsWith('udpin://') || endpoint.startsWith('tcpin://') || endpoint.startsWith('serial://'); if (!exclusiveEndpoint) continue; const endpointKey = endpoint.toLowerCase(); if (endpoints.has(endpointKey)) return '同一应用不能重复占用连接地址：' + endpoint + '（' + endpoints.get(endpointKey) + ' / ' + item.name + '）'; endpoints.set(endpointKey, item.name); } return ''; }
  function save(){ if (!state.draft.name.trim()){ toast('\u8bf7\u5148\u586b\u5199\u5e94\u7528\u540d\u79f0', true); return; } const bindingError = validateDroneBindings(); if (bindingError){ toast(bindingError, true); return; } if (typeof api.saveApplicationSettings !== 'function'){ toast('\u5e94\u7528\u8bbe\u7f6e\u4fdd\u5b58\u6865\u63a5\u672a\u52a0\u8f7d', true); return; } state.saving = true; syncSaveButton(); api.saveApplicationSettings(state.draft).then(() => { toast('\u5df2\u4fdd\u5b58\u5e94\u7528\u914d\u7f6e', false); setTimeout(() => window.close(), 180); }).catch(error => { state.saving = false; syncSaveButton(); toast(String(error && error.message ? error.message : error), true); }); }
  function inputField(label, path, value, placeholder, readonly, kind){ return '<div class="fieldRow"><div class="label">' + esc(label) + '</div><input class="field" data-path="' + esc(path) + '" data-scope="draft" data-kind="' + esc(kind || 'text') + '"' + (readonly ? ' readonly' : '') + (kind === 'number' ? ' type="number"' : '') + ' value="' + esc(value) + '" placeholder="' + esc(placeholder || '') + '" /></div>'; }
  function textareaField(label, path, value, placeholder){ return '<div class="fieldRow"><div class="label">' + esc(label) + '</div><textarea class="textarea" data-path="' + esc(path) + '" data-scope="draft" placeholder="' + esc(placeholder || '') + '">' + esc(value) + '</textarea></div>'; }
  function numberField(label, path, value, placeholder){ return inputField(label, path, value, placeholder, false, 'number'); }
  function checkboxField(label, path, checked){ return '<div class="fieldRow"><label class="label" style="display:flex;gap:10px;align-items:center;cursor:pointer;"><input type="checkbox" data-path="' + esc(path) + '" data-scope="draft" ' + (checked ? 'checked' : '') + ' /><span>' + esc(label) + '</span></label></div>'; }
  function waypointValueField(){ return '<div class="fieldRow"><div class="label">变量值</div><div class="row"><input class="field" readonly value="' + esc(currentWaypointText()) + '" placeholder="点击地图选择一个坐标点" data-action="openWaypointPicker" /><button class="smallBtn" data-action="openWaypointPicker">选择航点</button></div><div class="hint">航点会以 JSON 方式保存，运行时可直接通过 os.getenv("KEY") 读取后再解析。</div></div>'; }
  function navItem(section, icon, label, group){ const active = state.section === section ? ' active' : ''; return (group ? '<div class="group">' + esc(group) + '</div>' : '') + '<div class="item' + active + '" data-section="' + esc(section) + '"><div class="icon">' + esc(icon) + '</div><div class="itemName">' + esc(label) + '</div></div>'; }
  function basicPanel(){ const d = state.draft; return '<div class="cardBox"><div class="sub">\u5e94\u7528\u57fa\u7840</div>' + inputField('\u5e94\u7528\u6807\u8bc6 appCode', 'appCode', d.appCode, 'demo-app') + inputField('\u7248\u672c\u53f7 version', 'version', d.version, '1.0.0') + inputField('\u5e94\u7528\u540d\u79f0', 'name', d.name, 'fire-guard') + textareaField('\u5e94\u7528\u63cf\u8ff0', 'description', d.description, '\u591a\u8bbe\u5907\u5de1\u68c0\u4e0e\u8bc6\u522b\u5e94\u7528') + inputField('\u5165\u53e3\u6587\u4ef6', 'entryFile', d.entryFile || 'main.py', 'main.py') + numberField('\u6700\u5927\u8fd0\u884c\u79d2\u6570', 'maxRunSeconds', d.maxRunSeconds || 300, '300') + inputField('\u8fd0\u884c\u65f6', 'runtimeLabel', d.runtimeLabel, 'Python 3.12') + textareaField('\u53d8\u66f4\u8bf4\u660e', 'changeLog', d.changeLog || '', '\u672c\u6b21\u7248\u672c\u8bf4\u660e') + '<div class="fieldRow"><div class="label">Python \u8def\u5f84</div><div class="row"><input class="field" data-path="pythonPath" data-scope="draft" value="' + esc(d.pythonPath) + '" /><button class="smallBtn" data-action="choosePythonPath">\u9009\u62e9</button></div></div>' + inputField('\u7ed1\u5b9a\u5de5\u4f5c\u7a7a\u95f4', 'workingDirectory', d.workingDirectory, '', true) + '<div class="hint">\u5de5\u4f5c\u7a7a\u95f4\u76ee\u5f55\u7531\u65b0\u5efa\u5e94\u7528\u65f6\u521b\u5efa\u5e76\u7ed1\u5b9a\uff0c\u8fd9\u91cc\u4ec5\u5c55\u793a\uff0c\u4e0d\u5efa\u8bae\u4fee\u6539\u3002</div></div>'; }
  function envPanel(){ const rows = state.draft.envVars.length ? state.draft.envVars.map(function(item, index){ const normalizedType = normalizeEnvType(item.type); const rawValue = item.value || item.defaultValue || ''; const displayValue = normalizedType === waypointEnvType ? (formatWaypointValue(rawValue) || rawValue) : (normalizedType === routeEnvType ? (formatRouteValue(rawValue) || rawValue) : (normalizedType === droneEnvType ? (droneDeviceText(rawValue) || '未选择设备') : rawValue)); return '<div class="envRow" data-action="openEnv" data-index="' + index + '"><div class="pill">' + esc(envTypeLabel(normalizedType)) + '</div><div>' + esc(item.name) + '</div><div>' + esc(displayValue) + '</div><button class="del" title="\u5220\u9664" aria-label="\u5220\u9664" data-action="removeEnv" data-index="' + index + '">&times;</button></div>'; }).join('') : '<div class="empty">\u8fd8\u6ca1\u6709\u914d\u7f6e\u73af\u5883\u53d8\u91cf\uff0c\u70b9\u51fb\u53f3\u4fa7\u6309\u94ae\u6dfb\u52a0\u3002</div>'; return '<div class="cardBox"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;"><div class="sub">\u73af\u5883\u53d8\u91cf\u5217\u8868</div><button class="smallBtn" style="min-width:120px;height:40px;" data-action="openEnvNew">\u6dfb\u52a0\u53d8\u91cf</button></div><div class="envList">' + rows + '</div></div>'; }
  function gatewayPanel(){ const g = state.draft.aiGateway; return '<div class="cardBox"><div class="sub">AI \u7f51\u5173</div>' + inputField('\u7f51\u5173\u5730\u5740', 'aiGateway.gatewayUrl', g.gatewayUrl, 'https://api.openai.com/v1') + inputField('\u9ed8\u8ba4\u6a21\u578b', 'aiGateway.defaultModel', g.defaultModel, 'gpt-4.1-mini') + inputField('\u8def\u7531\u7b56\u7565', 'aiGateway.routeStrategy', g.routeStrategy, '\u6309\u6a21\u578b') + numberField('\u8d85\u65f6\uff08ms\uff09', 'aiGateway.timeoutMs', g.timeoutMs, '15000') + inputField('API Key', 'aiGateway.apiKey', g.apiKey, 'sk-***') + inputField('AK', 'aiGateway.ak', g.ak, '') + inputField('SK', 'aiGateway.sk', g.sk, '') + '</div>'; }
  function simPanel(){ const s = state.draft.simulator; return '<div class="cardBox"><div class="sub">\u4eff\u771f\u5668</div>' + inputField('\u4eff\u771f\u5f15\u64ce', 'simulator.engine', s.engine, 'Gazebo') + inputField('\u4e16\u754c\u6587\u4ef6', 'simulator.worldFile', s.worldFile, 'worlds/default.world') + inputField('\u5b9e\u65f6\u500d\u7387', 'simulator.realTimeFactor', s.realTimeFactor, '1.0') + '</div>'; }
  function gsPanel(){ const g = state.draft.groundStation; return '<div class="cardBox"><div class="sub">\u5730\u9762\u7ad9</div>' + inputField('\u5730\u9762\u7ad9\u7c7b\u578b', 'groundStation.stationType', g.stationType, 'QGroundControl') + inputField('\u8fde\u63a5\u5730\u5740', 'groundStation.connectionUrl', g.connectionUrl, 'udp://127.0.0.1:14550') + checkboxField('\u542f\u52a8\u540e\u81ea\u52a8\u8fde\u63a5', 'groundStation.autoConnect', g.autoConnect) + '</div>'; }
  function modalView(){ if (!state.envModalVisible) return ''; const options = envVarTypes.map(function(item){ return '<option value="' + esc(item) + '" ' + (state.envDraft.type === item ? 'selected' : '') + '>' + esc(envTypeLabel(item)) + '</option>'; }).join(''); const normalizedType = normalizeEnvType(state.envDraft.type); const valueField = normalizedType === waypointEnvType ? waypointValueField() : (normalizedType === routeEnvType ? routeValueField() : (normalizedType === droneEnvType ? droneDeviceValueField() : inputField('\u53d8\u91cf\u9ed8\u8ba4\u503c', 'value', state.envDraft.value || state.envDraft.defaultValue || '', envValuePlaceholders[normalizedType] || '\u8bf7\u8f93\u5165\u53d8\u91cf\u503c', false, 'text').replace('data-scope="draft"', 'data-scope="env"'))); const nameField = inputField('\u53d8\u91cf\u540d', 'name', state.envDraft.name, '\u8bf7\u8f93\u5165\u53d8\u91cf\u540d', false, 'text').replace('data-scope="draft"', 'data-scope="env"'); const descField = inputField('\u914d\u7f6e\u8bf4\u660e', 'description', state.envDraft.description || '', '\u7ed9\u90e8\u7f72\u7528\u6237\u7684\u586b\u5199\u8bf4\u660e', false, 'text').replace('data-scope="draft"', 'data-scope="env"'); const requiredField = '<div class="fieldRow"><label class="label" style="display:flex;gap:10px;align-items:center;cursor:pointer;"><input type="checkbox" data-path="required" data-scope="env" ' + (state.envDraft.required ? 'checked' : '') + ' /><span>\u5fc5\u586b</span></label></div>'; return '<div class="modalBackdrop show" data-action="closeEnvModal"><div class="modal"><div class="modalHeader"><div class="modalTitle">\u73af\u5883\u53d8\u91cf</div><button class="modalClose" data-action="closeEnvModal">×</button></div><div class="modalBody"><div class="fieldRow"><div class="label">\u53d8\u91cf\u7c7b\u578b</div><select class="select" data-path="type" data-scope="env">' + options + '</select></div>' + nameField + descField + requiredField + valueField + '</div><div class="modalFooter"><button class="modalBtn" data-action="closeEnvModal">\u53d6\u6d88</button><button class="modalBtn primary" data-action="saveEnv">\u786e\u8ba4' + (state.envEditIndex >= 0 ? '\u4fee\u6539' : '\u6dfb\u52a0') + '</button></div></div></div>'; }
  function renderToast(){ const el = document.getElementById('toast'); if (!el) return; if (!state.toast){ el.className = 'toast'; el.innerHTML = ''; return; } el.className = 'toast show'; el.innerHTML = '<div class="toastRow"><div>' + esc(state.toast) + '</div>' + (state.toastClosable ? '<button class="toastClose" data-action="closeToast">×</button>' : '') + '</div>'; }
  function renderFallback(error){ const nav = document.getElementById('nav'); const panel = document.getElementById('panel'); const modalHost = document.getElementById('modalHost'); if (nav){ nav.innerHTML = '<div class="group">\u5e94\u7528\u57fa\u7840</div><div class="item active"><div class="icon">AB</div><div class="itemName">\u5e94\u7528\u57fa\u7840</div></div><div class="group">\u5e94\u7528\u8fd0\u884c\u53c2\u6570</div><div class="item"><div class="icon">EV</div><div class="itemName">\u73af\u5883\u53d8\u91cf</div></div><div class="group">\u80fd\u529b\u96c6\u6210</div><div class="item"><div class="icon">AI</div><div class="itemName">AI\u7f51\u5173</div></div><div class="group">\u8c03\u8bd5\u5de5\u5177</div><div class="item"><div class="icon">SM</div><div class="itemName">\u4eff\u771f\u5668</div></div><div class="item"><div class="icon">GS</div><div class="itemName">\u5730\u9762\u7ad9</div></div>'; } if (panel){ panel.innerHTML = '<div class="panelTitle">\u5e94\u7528\u8bbe\u7f6e</div><div class="cardBox"><div class="sub">\u9875\u9762\u52a0\u8f7d\u5f02\u5e38</div><div class="hint">' + esc(error && error.message ? error.message : error) + '</div><div class="hint">\u8bf7\u91cd\u8bd5\u6253\u5f00\u5e94\u7528\u8bbe\u7f6e\uff0c\u6216\u628a\u62a5\u9519\u4fe1\u606f\u53d1\u7ed9\u6211\u7ee7\u7eed\u6392\u67e5\u3002</div></div>'; } if (modalHost){ modalHost.innerHTML = ''; } renderToast(); }
  function render(){ const nav = document.getElementById('nav'); const panel = document.getElementById('panel'); const modalHost = document.getElementById('modalHost'); if (!nav || !panel || !modalHost) return; nav.innerHTML = navItem('basic', 'AB', '\u5e94\u7528\u57fa\u7840', '\u5e94\u7528\u57fa\u7840') + navItem('env', 'EV', '\u73af\u5883\u53d8\u91cf', '\u5e94\u7528\u8fd0\u884c\u53c2\u6570') + navItem('gateway', 'AI', 'AI\u7f51\u5173', '\u80fd\u529b\u96c6\u6210') + navItem('simulator', 'SM', '\u4eff\u771f\u5668', '\u8c03\u8bd5\u5de5\u5177') + navItem('groundstation', 'GS', '\u5730\u9762\u7ad9', ''); panel.innerHTML = '<div class="panelTitle">\u5e94\u7528\u8bbe\u7f6e</div>' + (state.section === 'basic' ? basicPanel() : state.section === 'env' ? envPanel() : state.section === 'gateway' ? gatewayPanel() : state.section === 'simulator' ? simPanel() : gsPanel()); modalHost.innerHTML = modalView(); syncSaveButton(); renderToast(); }
  function handleClick(event){ const target = event.target instanceof Element ? event.target : null; if (!target) return; const actionEl = target.closest('[data-action]'); if (actionEl){ const action = actionEl.getAttribute('data-action') || ''; const index = Number(actionEl.getAttribute('data-index') || '-1'); if (action === 'closeEnvModal' && actionEl !== target){ return; } switch (action){ case 'save': save(); return; case 'cancel': cancel(); return; case 'choosePythonPath': openPython(); return; case 'openEnv': openEnv(index); return; case 'openEnvNew': openEnv(-1); return; case 'removeEnv': event.stopPropagation(); removeEnv(index); return; case 'saveEnv': saveEnv(); return; case 'openWaypointPicker': openWaypointPicker(); return; case 'openRoutePicker': openRoutePicker(); return; case 'closeEnvModal': closeEnv(); return; case 'closeToast': state.toast = ''; renderToast(); return; default: return; } } const sectionEl = target.closest('[data-section]'); if (sectionEl){ setSection(sectionEl.getAttribute('data-section') || 'basic'); } }
  function handleInput(event){ const target = event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement ? event.target : null; if (!target) return; const fieldEl = target.closest('[data-path]'); if (!fieldEl) return; const scope = fieldEl.getAttribute('data-scope') || 'draft'; const pathValue = fieldEl.getAttribute('data-path') || ''; const kind = fieldEl.getAttribute('data-kind') || 'text'; const value = kind === 'number' ? (target.value === '' ? '' : Number(target.value)) : (target instanceof HTMLInputElement && target.type === 'checkbox' ? target.checked : target.value); if (scope === 'env'){ setEnvField(pathValue, value); } else { setField(pathValue, value); } }
  function bindEvents(){ const shell = document.querySelector('.shell'); if (!shell) return; shell.addEventListener('click', handleClick); shell.addEventListener('input', handleInput); shell.addEventListener('change', handleInput); }
  function loadDroneDevices(){ if (state.deviceLoadStarted || typeof api.loadDroneDevices !== 'function') return; state.deviceLoadStarted = true; state.loadingDevices = true; api.loadDroneDevices().then(function(devices){ state.devices = Array.isArray(devices) ? devices : []; }).catch(function(){ state.devices = []; }).finally(function(){ state.loadingDevices = false; render(); }); }
  function bootstrap(){
    if (state.bootstrapped) {
      return;
    }

    const nav = document.getElementById('nav');
    const panel = document.getElementById('panel');
    const modalHost = document.getElementById('modalHost');
    if (!nav || !panel || !modalHost) {
      window.requestAnimationFrame(bootstrap);
      return;
    }

    state.bootstrapped = true;
    try {
      bindEvents();
      render();
      loadDroneDevices();
    } catch (error) {
      console.error('Application settings render failed:', error);
      renderFallback(error);
    }
  }
  window.addEventListener('error', function (event) { renderFallback(event && event.error ? event.error : event.message); });
  window.addEventListener('unhandledrejection', function (event) { renderFallback(event && event.reason ? event.reason : '\u9875\u9762\u52a0\u8f7d\u5f02\u5e38'); });
  bootstrap();
  window.addEventListener('DOMContentLoaded', bootstrap, { once: true });
  window.addEventListener('load', bootstrap, { once: true });
})();
</script>
</body>
</html>`;
  }

  private _window: ThemedWindow;
  private _pageBody: string;
  private _workingDirectory: string;
}

export namespace ApplicationSettingsDialog {
  export interface IOptions {
    isDarkTheme: boolean;
    workingDirectory: string;
    parent?: BrowserWindow;
  }
}
