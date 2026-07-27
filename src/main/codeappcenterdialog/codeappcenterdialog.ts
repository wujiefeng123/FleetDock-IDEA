// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] CodeAppCenter publish dialog for the active JupyterLab workspace.

import { BrowserWindow } from 'electron';
import * as path from 'path';
import {
  createDefaultApplicationConfig,
  normalizeApplicationConfig,
  readApplicationConfig
} from '../config/application';
import { ThemedWindow } from '../dialog/themedwindow';
import { CodeAppCenterSettingsData } from '../codeappcenter/types';
import { PRODUCT_NAME } from '../branding';

export class CodeAppCenterDialog {
  constructor(options: CodeAppCenterDialog.IOptions) {
    this._workingDirectory = path.resolve(options.workingDirectory);
    this._window = new ThemedWindow({
      isDarkTheme: options.isDarkTheme,
      modal: !!options.parent,
      parent: options.parent,
      // [UAV-IDE MODIFY] Keep the FleetDock IDEA publish dialog title readable.
      title: '应用中心发布',
      width: 1040,
      height: 780,
      preload: path.join(__dirname, './preload.js')
    });

    const config = normalizeApplicationConfig(
      readApplicationConfig(this._workingDirectory) ||
        createDefaultApplicationConfig(this._workingDirectory),
      this._workingDirectory
    );

    this._pageBody = this._buildPageSource(config, options.settings);
  }

  get window(): ThemedWindow {
    return this._window;
  }

  load() {
    this._window.loadDialogContent(this._pageBody);
  }

  // [UAV-IDE MODIFY] Derive a usable default appCode from the current workspace name.
  private _createDefaultAppCode(): string {
    return (
      path
        .basename(this._workingDirectory)
        .toLowerCase()
        .replace(/[^a-z0-9-]+/g, '-')
        .replace(/-+/g, '-')
        .replace(/^-+|-+$/g, '') || 'app'
    );
  }

  private _buildPageSource(config: any, settings: CodeAppCenterSettingsData) {
    const defaultAppCode = this._createDefaultAppCode();
    return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta http-equiv="Content-Security-Policy" content="default-src 'self' data: 'unsafe-inline' 'unsafe-eval'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline' 'unsafe-eval'">
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>${PRODUCT_NAME} Publish</title>
<style>
/* [UAV-IDE MODIFY] Align the publish dialog with the FleetDock IDEA blue/cyan design system. */
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#f6f8fb;color:#111827;font:13px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Helvetica,Arial,sans-serif}
*{box-sizing:border-box}button,input,textarea,select{font:inherit}.shell{position:relative;height:100%;display:flex;flex-direction:column}.body{flex:1;display:grid;grid-template-columns:280px 1fr;min-height:0}.nav{border-right:1px solid #e2e8f0;padding:18px;background:#fff}.step{height:62px;border-radius:10px;padding:10px 12px;margin-bottom:10px;display:flex;align-items:center;gap:12px;color:#64748b}.step.active{background:rgba(37,99,235,.1);color:#2563eb}.dot{width:28px;height:28px;border-radius:999px;display:flex;align-items:center;justify-content:center;border:1px solid currentColor;font-weight:700}.panel{padding:24px 30px;overflow:auto}.title{font-size:24px;font-weight:800;margin-bottom:18px}.card{max-width:860px;border:1px solid #e2e8f0;border-radius:10px;padding:18px;margin-bottom:16px;background:#fff}.sub{font-size:14px;color:#64748b;margin-bottom:12px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.fieldRow{margin-bottom:14px}.label{font-size:13px;color:#64748b;margin-bottom:7px}.field,.textarea,.select{width:100%;min-height:44px;border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px;outline:none;background:#fff;color:inherit}.field:focus,.textarea:focus,.select:focus{outline:2px solid rgba(6,182,212,.45);outline-offset:2px}.field:focus-visible,.textarea:focus-visible,.select:focus-visible,.btn:focus-visible,.linkBtn:focus-visible{outline:2px solid rgba(6,182,212,.55);outline-offset:2px}.textarea{min-height:150px;resize:vertical;font-family:Consolas,'Courier New',monospace}.row{display:flex;gap:10px;align-items:center}.row .field,.row .select{flex:1}.btn{height:40px;padding:0 16px;border:1px solid #e2e8f0;border-radius:8px;background:#fff;cursor:pointer;transition:border-color .18s ease,box-shadow .18s ease,background-color .18s ease}.btn:hover{border-color:#93c5fd;box-shadow:0 6px 18px rgba(37,99,235,.1)}.btn:active{background:#eff6ff}.btn.primary{background:#2563eb;border-color:#2563eb;color:#fff}.btn.primary:hover{background:#1d4ed8;border-color:#1d4ed8}.btn:disabled{opacity:.55;cursor:not-allowed;box-shadow:none}.mode{display:grid;grid-template-columns:1fr 1fr;gap:12px}.mode button{height:90px;text-align:left;padding:12px 14px}.mode button.active{border-color:#2563eb;background:rgba(37,99,235,.08)}.hint{font-size:12px;color:#64748b;margin-top:7px}.footer{height:76px;border-top:1px solid #e2e8f0;display:flex;align-items:center;justify-content:flex-end;gap:12px;padding:0 24px;background:#fff}.toast{position:absolute;right:18px;bottom:86px;min-width:280px;max-width:520px;background:rgba(15,23,42,.96);color:#fff;border-radius:10px;padding:14px;opacity:0;transform:translateY(8px);transition:.18s;pointer-events:none}.toast.show{opacity:1;transform:translateY(0)}.result{white-space:pre-wrap;font-family:Consolas,'Courier New',monospace;background:#f1f5f9;border-radius:8px;padding:12px;line-height:1.5;max-height:260px;overflow:auto}.topline{max-width:860px;margin-bottom:14px;color:#64748b;display:flex;justify-content:space-between;align-items:center}.linkBtn{border:0;background:transparent;color:#2563eb;cursor:pointer;padding:0}.loginMask{position:absolute;inset:0;background:rgba(246,248,251,.78);display:flex;align-items:center;justify-content:center;z-index:5}.loginBox{width:min(520px,calc(100vw - 48px));background:#fff;border:1px solid #e2e8f0;box-shadow:0 18px 60px rgba(15,23,42,.2);border-radius:12px;padding:24px}.loginTitle{font-size:22px;font-weight:800;margin-bottom:8px}.loginActions{display:flex;align-items:center;justify-content:flex-end;gap:10px;margin-top:14px}.tableBar{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}.appTable{width:100%;border-collapse:separate;border-spacing:0;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden}.appTable th,.appTable td{height:46px;padding:0 12px;border-bottom:1px solid #e2e8f0;text-align:left;white-space:nowrap}.appTable th{background:#f1f5f9;color:#64748b;font-weight:700}.appTable tr:last-child td{border-bottom:0}.appRow{cursor:pointer}.appRow:hover{background:rgba(37,99,235,.05)}.appRow.selected{background:rgba(37,99,235,.1)}.appNameCell{font-weight:700;color:#111827}.emptyList{height:120px;display:flex;align-items:center;justify-content:center;color:#64748b;border:1px dashed #cbd5e1;border-radius:8px}.field[readonly]{background:#f1f5f9;color:#64748b;cursor:default}.publishStatus{display:flex;gap:14px;align-items:flex-start;border-radius:8px;padding:14px;background:#f8fafc;border:1px solid #e2e8f0}.statusIcon{width:30px;height:30px;border-radius:999px;display:flex;align-items:center;justify-content:center;font-weight:700;background:#dbeafe;color:#2563eb;flex:0 0 auto}.publishStatus.success .statusIcon{background:#dcfce7;color:#15803d}.publishStatus.failed .statusIcon{background:#fee2e2;color:#dc2626}.statusTitle{font-weight:700;font-size:15px;margin-bottom:4px}.statusText{color:#64748b;word-break:break-word}.progressTrack{height:8px;background:#e2e8f0;border-radius:999px;overflow:hidden;margin-top:12px}.progressBar{height:100%;width:42%;border-radius:999px;background:#2563eb;animation:publishProgress 1.1s ease-in-out infinite}@keyframes publishProgress{0%{transform:translateX(-115%)}100%{transform:translateX(255%)}}@media (prefers-reduced-motion: reduce){*{transition:none!important;animation:none!important}}
</style>
</head>
<body>
<div class="shell">
  <div class="body">
    <div class="nav" id="nav"></div>
    <div class="panel" id="panel"></div>
  </div>
  <div class="footer">
    <button class="btn" data-action="close">关闭</button>
    <button class="btn" data-action="prev" id="prevBtn">上一步</button>
    <button class="btn primary" data-action="next" id="primaryBtn">下一步</button>
  </div>
  <div id="loginHost"></div>
  <div class="toast" id="toast"></div>
</div>
<script>
(function(){
  const api = window.electronAPI || {};
  const state = {
    step: 0,
    settings: Object.assign({ gatewayBaseUrl: '', lastPublishMode: 'create-app', lastAppId: '' }, ${JSON.stringify(
      settings
    )}),
    config: ${JSON.stringify(config)},
    workingDirectory: ${JSON.stringify(this._workingDirectory)},
    login: { loginType: 'Phone', loginValue: '', passWord: '' },
    loginVisible: !(${JSON.stringify(
      !!settings.accessToken && !!settings.gatewayBaseUrl
    )}),
    apps: [],
    requirementsText: '',
    scanText: '',
    publishing: false,
    publishStatus: 'idle',
    publishMessage: '',
    toast: ''
  };
  const defaultAppCode = ${JSON.stringify(defaultAppCode)};

  // [UAV-IDE MODIFY] Publish uses the workspace name as the visible appCode default.
  if (!String(state.config.appCode || '').trim()) state.config.appCode = defaultAppCode;
  if (!String(state.config.entryFile || '').trim()) state.config.entryFile = 'main.py';

  function esc(value) {
    return String(value === undefined || value === null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
  function setDeep(target, path, value) {
    const parts = String(path || '').split('.').filter(Boolean);
    if (!parts.length) return;
    let current = target;
    for (let i = 0; i < parts.length - 1; i++) {
      if (!current[parts[i]] || typeof current[parts[i]] !== 'object') current[parts[i]] = {};
      current = current[parts[i]];
    }
    current[parts[parts.length - 1]] = value;
  }
  function toast(message) {
    state.toast = message || '';
    renderToast();
    if (message) {
      clearTimeout(toast.timer);
      toast.timer = setTimeout(function(){ state.toast = ''; renderToast(); }, 3000);
    }
  }
  function renderToast() {
    const element = document.getElementById('toast');
    if (!element) return;
    element.className = state.toast ? 'toast show' : 'toast';
    element.textContent = state.toast;
  }
  function errorMessage(error) {
    return error && error.message ? error.message : String(error || '');
  }
  function isAuthError(error) {
    return /登录已失效|重新登录|未登录|token|Unauthorized|Authentication/i.test(errorMessage(error));
  }
  function handleRequestError(error) {
    const message = errorMessage(error);
    if (isAuthError(error)) {
      state.loginVisible = true;
      state.settings.accessToken = '';
      state.settings.refreshToken = '';
      render();
      toast('登录已失效，请重新登录应用中心');
      return;
    }
    toast(message);
  }
  function steps() {
    return ['发布方式', '应用信息', '依赖配置', '打包上传'];
  }
  function renderNav() {
    document.getElementById('nav').innerHTML = steps().map(function(stepName, index) {
      const hint = index < state.step ? '已完成' : (index === state.step ? '当前步骤' : '待处理');
      return '<div class="step ' + (index === state.step ? 'active' : '') + '">' +
        '<div class="dot">' + (index + 1) + '</div>' +
        '<div><div style="font-weight:700">' + esc(stepName) + '</div><div class="hint">' + hint + '</div></div>' +
        '</div>';
    }).join('');
  }
  function field(label, path, value, placeholder, type, readonly) {
    return '<div class="fieldRow"><div class="label">' + esc(label) + '</div>' +
      '<input class="field" data-path="' + esc(path) + '" ' +
      (type ? 'type="' + esc(type) + '"' : '') + ' ' +
      (readonly ? 'readonly aria-readonly="true"' : '') +
      ' value="' + esc(value) + '" placeholder="' + esc(placeholder || '') + '" /></div>';
  }
  function textarea(label, path, value, placeholder) {
    return '<div class="fieldRow"><div class="label">' + esc(label) + '</div>' +
      '<textarea class="textarea" data-path="' + esc(path) + '" placeholder="' + esc(placeholder || '') + '">' +
      esc(value) + '</textarea></div>';
  }
  function loginOverlay() {
    if (!state.loginVisible) return '';
    return '<div class="loginMask"><div class="loginBox">' +
      '<div class="loginTitle">登录应用中心</div>' +
      '<div class="hint" style="margin-bottom:18px">请先登录应用中心，登录成功后再选择发布方式。</div>' +
      // [UAV-IDE MODIFY] Ask for the platform gateway; requests keep using service-name prefixes.
      field('统一平台网关地址', 'settings.gatewayBaseUrl', state.settings.gatewayBaseUrl, '例如 http://localhost 或平台网关地址') +
      '<div class="grid">' +
      field('登录类型', 'login.loginType', state.login.loginType, 'Phone') +
      field('账号', 'login.loginValue', state.login.loginValue, '手机号/用户名') +
      '</div>' +
      field('密码', 'login.passWord', state.login.passWord, '', 'password') +
      '<div class="loginActions"><button class="btn" data-action="close">取消</button><button class="btn primary" data-action="login">登录</button></div>' +
      '</div></div>';
  }
  function headerLine() {
    return '<div class="topline"><span>当前工作目录：' + esc(state.workingDirectory) + '</span>' +
      '<button class="linkBtn" data-action="showLogin">切换登录</button></div>';
  }
  function targetPanel() {
    return '<div class="title">选择发布方式</div>' + headerLine() +
      '<div class="card"><div class="mode">' +
      '<button class="' + (state.settings.lastPublishMode === 'create-app' ? 'active' : '') + '" data-action="setMode" data-mode="create-app">' +
      '<b>创建新应用</b><div class="hint">先上传源码包，上传完成后调用 /app/create。</div></button>' +
      '<button class="' + (state.settings.lastPublishMode === 'new-version' ? 'active' : '') + '" data-action="setMode" data-mode="new-version">' +
      '<b>已有应用新增版本</b><div class="hint">先上传源码包，上传完成后调用 /app/version/create。</div></button>' +
      '</div></div>' +
      (state.settings.lastPublishMode === 'new-version' ? existingAppPanel() : '');
  }
  function formatTime(value) {
    const time = Number(value || 0);
    if (!time) return '--';
    const date = new Date(time);
    if (Number.isNaN(date.getTime())) return '--';
    const pad = function(n) { return String(n).padStart(2, '0'); };
    return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate()) + ' ' + pad(date.getHours()) + ':' + pad(date.getMinutes());
  }
  function appVersionNo(app) {
    return app.latestVersionNo || app.versionNo || app.currentVersion || app.version || (app.latestVersion && app.latestVersion.versionNo) || '--';
  }
  function appVersionCreateTime(app) {
    return app.latestVersionCreateTime || (app.latestVersion && app.latestVersion.createTime) || app.versionCreateTime || app.updateTime || app.createTime;
  }
  function existingAppPanel() {
    const rows = state.apps.map(function(app) {
      const selected = (app.id || '') === state.settings.lastAppId;
      return '<tr class="appRow ' + (selected ? 'selected' : '') + '" data-action="selectApp" data-app-id="' + esc(app.id || '') + '">' +
        '<td><span class="appNameCell">' + esc(app.appName || app.name || app.appCode || app.id || '未命名应用') + '</span></td>' +
        '<td>' + esc(appVersionNo(app)) + '</td>' +
        '<td>' + esc(formatTime(appVersionCreateTime(app))) + '</td>' +
        '<td>' + esc(app.creatorName || '--') + '</td>' +
        '</tr>';
    }).join('');
    return '<div class="card"><div class="tableBar">' +
      '<div class="sub" style="margin:0">选择要新增版本的应用</div>' +
      '<button class="btn" data-action="loadApps">刷新应用列表</button></div>' +
      (rows ?
        '<table class="appTable"><thead><tr><th>应用名称</th><th>版本号</th><th>版本创建时间</th><th>创建人名称</th></tr></thead><tbody>' + rows + '</tbody></table>' :
        '<div class="emptyList">暂无应用，点击刷新应用列表</div>') +
      '<div class="hint">点击列表行选中应用；选中后继续填写版本信息，最终发布前不会创建版本。</div></div>';
  }
  // [UAV-IDE MODIFY] Keep publish metadata minimal and prevent entry file edits.
  function infoPanel() {
    const config = state.config;
    return '<div class="title">应用和版本信息</div>' + headerLine() +
      '<div class="card"><div class="grid">' +
      field('应用标识 appCode', 'config.appCode', config.appCode, defaultAppCode) +
      field('版本号 version', 'config.version', config.version, '1.0.0') +
      field('应用名称', 'config.name', config.name, '巡检应用') +
      field('入口文件', 'config.entryFile', config.entryFile || 'main.py', 'main.py', 'text', true) +
      field('最大运行秒数', 'config.maxRunSeconds', config.maxRunSeconds || 300, '300', 'number') +
      '</div>' +
      textarea('应用描述', 'config.description', config.description, '应用说明') +
      textarea('版本说明 / changeLog', 'config.changeLog', config.changeLog, '本次变更说明') +
      '</div>';
  }
  function depsPanel() {
    return '<div class="title">依赖配置</div>' + headerLine() +
      '<div class="card">' +
      '<div class="row" style="margin-bottom:12px">' +
      '<button class="btn" data-action="readReq">读取 requirements.txt</button>' +
      '<button class="btn" data-action="scanDeps">扫描 Python import</button>' +
      '<button class="btn" data-action="useScan">使用扫描结果</button></div>' +
      textarea('requirements.txt', 'requirementsText', state.requirementsText, '每行一个依赖，至少一条') +
      '<div class="hint">应用中心不支持 -r/-c/-e、本地路径、file:、find-links 等依赖声明。</div>' +
      (state.scanText ? '<div class="sub">扫描候选</div><div class="result">' + esc(state.scanText) + '</div>' : '') +
      '</div>';
  }
  // [UAV-IDE MODIFY] Show production publish progress and result instead of raw API JSON.
  function publishStatusView() {
    if (state.publishStatus === 'idle') return '';
    const className = state.publishStatus === 'success' ? ' success' : (state.publishStatus === 'failed' ? ' failed' : '');
    const icon = state.publishStatus === 'success' ? '✓' : (state.publishStatus === 'failed' ? '!' : '');
    const title = state.publishStatus === 'success' ? '发布成功' : (state.publishStatus === 'failed' ? '发布失败' : '发布中');
    return '<div class="card"><div class="publishStatus' + className + '">' +
      '<div class="statusIcon">' + esc(icon) + '</div><div style="flex:1">' +
      '<div class="statusTitle">' + title + '</div>' +
      '<div class="statusText">' + esc(state.publishMessage || '正在打包并上传应用源码包') + '</div>' +
      (state.publishStatus === 'running' ? '<div class="progressTrack"><div class="progressBar"></div></div>' : '') +
      '</div></div></div>';
  }
  function uploadPanel() {
    return '<div class="title">打包上传</div>' + headerLine() +
      '<div class="card"><div class="sub">确认后将打包当前工作目录，上传源码包，并提交到应用中心。</div>' +
      '<button class="btn primary" data-action="publish" ' + (state.publishing ? 'disabled' : '') + '>' +
      (state.publishing ? '发布中...' : '开始发布') + '</button></div>' +
      publishStatusView();
  }
  function renderPanel() {
    const panels = [targetPanel, infoPanel, depsPanel, uploadPanel];
    document.getElementById('panel').innerHTML = panels[state.step]();
    const primary = document.getElementById('primaryBtn');
    const prev = document.getElementById('prevBtn');
    primary.textContent = state.step === 3 ? '开始发布' : '下一步';
    primary.disabled = state.publishing || state.loginVisible;
    prev.disabled = state.step === 0 || state.publishing || state.loginVisible;
  }
  function renderLogin() {
    const host = document.getElementById('loginHost');
    if (host) host.innerHTML = loginOverlay();
  }
  function render() {
    renderNav();
    renderPanel();
    renderLogin();
    renderToast();
  }
  function requireLogin() {
    if (state.loginVisible || !state.settings.accessToken) {
      state.loginVisible = true;
      render();
      return false;
    }
    return true;
  }
  // [UAV-IDE MODIFY] Submit the visible default appCode when the user leaves it empty.
  function currentPublishRequest() {
    const appCode = String(state.config.appCode || '').trim() || defaultAppCode;
    state.config.appCode = appCode;
    state.config.entryFile = 'main.py';
    return {
      mode: state.settings.lastPublishMode,
      gatewayBaseUrl: state.settings.gatewayBaseUrl,
      appId: state.settings.lastAppId,
      appCode: appCode,
      appName: state.config.name,
      description: state.config.description,
      version: state.config.version,
      remark: state.config.changeLog,
      requirementsText: state.requirementsText,
      workingDirectory: state.workingDirectory
    };
  }
  function validateStep() {
    if (state.step === 0 && state.settings.lastPublishMode === 'new-version' && !state.settings.lastAppId) {
      toast('请选择要新增版本的应用');
      return false;
    }
    return true;
  }
  function handleClick(event) {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    const actionElement = target.closest('[data-action]');
    if (!actionElement) return;
    const action = actionElement.getAttribute('data-action');
    if (action === 'close') {
      window.close();
    } else if (action === 'prev') {
      state.step = Math.max(0, state.step - 1);
      render();
    } else if (action === 'next') {
      if (!requireLogin() || !validateStep()) return;
      if (state.step === 3) {
        publish();
      } else {
        state.step = Math.min(3, state.step + 1);
        render();
      }
    } else if (action === 'login') {
      login();
    } else if (action === 'showLogin') {
      state.loginVisible = true;
      render();
    } else if (action === 'setMode') {
      state.settings.lastPublishMode = actionElement.getAttribute('data-mode') || 'create-app';
      render();
      if (state.settings.lastPublishMode === 'new-version' && state.apps.length === 0) loadApps(false);
    } else if (action === 'selectApp') {
      state.settings.lastAppId = actionElement.getAttribute('data-app-id') || '';
      render();
    } else if (action === 'loadApps') {
      loadApps();
    } else if (action === 'readReq') {
      readReq();
    } else if (action === 'scanDeps') {
      scanDeps();
    } else if (action === 'useScan') {
      state.requirementsText = state.scanText || state.requirementsText;
      render();
    } else if (action === 'publish') {
      publish();
    }
  }
  function handleInput(event) {
    const target = event.target;
    if (!(target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement)) return;
    const path = target.getAttribute('data-path');
    if (!path) return;
    const value = target.type === 'number' ? (target.value === '' ? '' : Number(target.value)) : target.value;
    if (path === 'requirementsText') {
      state.requirementsText = String(value);
      return;
    }
    setDeep(state, path, value);
  }
  async function login() {
    try {
      if (!api.loginCodeAppCenter) throw new Error('应用中心登录桥接未加载');
      const token = await api.loginCodeAppCenter({
        gatewayBaseUrl: state.settings.gatewayBaseUrl,
        loginType: state.login.loginType,
        loginValue: state.login.loginValue,
        passWord: state.login.passWord
      });
      state.settings = Object.assign({}, state.settings, token, { gatewayBaseUrl: state.settings.gatewayBaseUrl });
      state.loginVisible = false;
      toast('登录成功');
      render();
      await loadApps(false);
    } catch (error) {
      toast(error.message || String(error));
    }
  }
  async function loadApps(showToast) {
    try {
      if (showToast === undefined) showToast = true;
      if (!requireLogin()) return;
      if (!api.listCodeApps) throw new Error('应用列表桥接未加载');
      state.apps = await api.listCodeApps();
      if (showToast) toast('应用列表已更新');
      render();
    } catch (error) {
      handleRequestError(error);
    }
  }
  async function readReq() {
    try {
      if (!api.readRequirements) throw new Error('依赖读取桥接未加载');
      state.requirementsText = await api.readRequirements();
      render();
      toast('已读取 requirements.txt');
    } catch (error) {
      toast(error.message || String(error));
    }
  }
  async function scanDeps() {
    try {
      if (!api.scanDependencies) throw new Error('依赖扫描桥接未加载');
      const result = await api.scanDependencies();
      state.scanText = result.requirementsText || '';
      render();
      toast('扫描完成');
    } catch (error) {
      toast(error.message || String(error));
    }
  }
  // [UAV-IDE MODIFY] Keep final publish feedback user-facing and hide transport payloads.
  async function publish() {
    try {
      if (!requireLogin()) return;
      if (!api.publishToCodeAppCenter) throw new Error('发布桥接未加载');
      state.publishing = true;
      state.publishStatus = 'running';
      state.publishMessage = '正在打包并上传应用源码包';
      render();
      await api.publishToCodeAppCenter(currentPublishRequest());
      state.publishStatus = 'success';
      state.publishMessage = '应用已成功发布到应用中心';
      toast('发布成功');
    } catch (error) {
      const message = errorMessage(error);
      state.publishStatus = 'failed';
      state.publishMessage = message;
      if (isAuthError(error)) {
        state.loginVisible = true;
        state.settings.accessToken = '';
        state.settings.refreshToken = '';
        toast('登录已失效，请重新登录应用中心');
      } else {
        toast(message);
      }
    } finally {
      state.publishing = false;
      render();
    }
  }

  document.querySelector('.shell').addEventListener('click', handleClick);
  document.querySelector('.shell').addEventListener('input', handleInput);
  document.querySelector('.shell').addEventListener('change', handleInput);
  if (api.readRequirements) {
    api.readRequirements().then(function(text) {
      state.requirementsText = text || '';
      render();
    }).catch(function() {
      render();
    });
  } else {
    render();
  }
  if (!state.loginVisible && state.settings.lastPublishMode === 'new-version') {
    loadApps(false);
  }
  render();
})();
</script>
</body>
</html>`;
  }

  private _window: ThemedWindow;
  private _pageBody: string;
  private _workingDirectory: string;
}

export namespace CodeAppCenterDialog {
  export interface IOptions {
    isDarkTheme: boolean;
    workingDirectory: string;
    settings: CodeAppCenterSettingsData;
    parent?: BrowserWindow;
  }
}
