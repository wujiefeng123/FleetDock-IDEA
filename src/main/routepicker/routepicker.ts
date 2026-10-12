// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Dedicated route picker backed by Leaflet with TianDiTu tiles for stable desktop rendering.

import { BrowserWindow } from 'electron';
import * as path from 'path';
import { EventManager } from '../eventmanager';
import { EventTypeMain } from '../fleetdock/eventtypes';
import { ThemedWindow } from '../dialog/themedwindow';

const TIANDITU_API_KEY = '4e4544783f309d1008b7312578367c19';
const TIANDITU_JS_API_URL = `https://api.tianditu.gov.cn/api?v=4.0&tk=${TIANDITU_API_KEY}`;
const TIANDITU_SERVICE_JS_URL = 'https://api.tianditu.gov.cn/v4.0/service.js';
const LEAFLET_CSS_URL = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
const LEAFLET_JS_URL = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
const DEFAULT_CENTER = { lat: 39.9042, lon: 116.4074, alt: 20 };

type RoutePointDraft = { lat: string; lon: string; alt: string };

const TXT = {
  title: '\u9009\u62e9\u822a\u7ebf',
  loadingMap: '\u6b63\u5728\u52a0\u8f7d\u5929\u5730\u56fe...',
  loadingHint:
    '\u6b63\u5728\u52a0\u8f7d\u5929\u5730\u56fe\uff0c\u8bf7\u7a0d\u5019...',
  searchPlaceholder: '\u641c\u7d22\u5730\u540d\u6216\u5730\u5740',
  searchButton: '\u641c\u7d22',
  searching: '\u6b63\u5728\u641c\u7d22\u5b9a\u4f4d...',
  searchingSuggest: '\u6b63\u5728\u83b7\u53d6\u641c\u7d22\u5efa\u8bae...',
  emptySearch: '\u8bf7\u5148\u8f93\u5165\u5730\u540d\u6216\u5730\u5740',
  searchNoResult:
    '\u672a\u627e\u5230\u5bf9\u5e94\u4f4d\u7f6e\uff0c\u8bf7\u66f4\u6362\u5173\u952e\u8bcd\u91cd\u8bd5\u3002',
  searchFailed:
    '\u641c\u7d22\u5b9a\u4f4d\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u7f51\u7edc\u540e\u91cd\u8bd5\u3002',
  suggestEmpty: '\u672a\u627e\u5230\u76f8\u5173\u5730\u70b9',
  createRoute: '\u521b\u5efa\u822a\u7ebf',
  finishRoute: '\u5b8c\u6210\u521b\u5efa',
  undo: '\u64a4\u9500\u4e0a\u4e00\u6b65',
  clear: '\u6e05\u7a7a\u822a\u7ebf',
  retry: '\u91cd\u65b0\u52a0\u8f7d',
  cancel: '\u53d6\u6d88',
  confirm: '\u786e\u8ba4\u9009\u62e9',
  pointLabel: '\u822a\u70b9 ',
  lat: '\u7eac\u5ea6',
  lon: '\u7ecf\u5ea6',
  alt: '\u9ad8\u5ea6',
  drawHint:
    '\u70b9\u51fb\u201c\u521b\u5efa\u822a\u7ebf\u201d\u540e\uff0c\u5728\u5730\u56fe\u4e0a\u6309\u987a\u5e8f\u70b9\u51fb\u6dfb\u52a0\u822a\u70b9\u3002',
  editHint:
    '\u4f60\u53ef\u4ee5\u76f4\u63a5\u4fee\u6539\u53f3\u4fa7\u8868\u683c\u91cc\u7684\u7eac\u7ecf\u5ea6\u548c\u9ad8\u5ea6\uff0c\u6216\u5728\u521b\u5efa\u6a21\u5f0f\u4e0b\u7ee7\u7eed\u6dfb\u52a0\u3002',
  emptyHint:
    '\u5148\u70b9\u51fb\u201c\u521b\u5efa\u822a\u7ebf\u201d\uff0c\u7136\u540e\u5728\u5730\u56fe\u4e0a\u70b9\u51fb\u6dfb\u52a0\u822a\u70b9\u3002',
  invalidPoint:
    '\u8bf7\u5148\u4fee\u6539\u6240\u6709\u822a\u70b9\u7684\u7eac\u7ecf\u5ea6\u548c\u9ad8\u5ea6\uff0c\u786e\u4fdd\u5b83\u4eec\u662f\u6709\u6548\u6570\u503c\u3002',
  emptyRoute:
    '\u8bf7\u81f3\u5c11\u6dfb\u52a0\u4e00\u4e2a\u822a\u70b9\uff0c\u518d\u786e\u8ba4\u9009\u62e9\u3002',
  saveSummary: '\u5df2\u6dfb\u52a0 ',
  saveSummaryTail: ' \u4e2a\u822a\u70b9',
  geocoderFailed:
    '\u641c\u7d22\u670d\u52a1\u52a0\u8f7d\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u7f51\u7edc\u540e\u91cd\u8bd5\u3002'
} as const;

export class RoutePickerDialog {
  constructor(options: RoutePickerDialog.IOptions) {
    this._window = new ThemedWindow({
      isDarkTheme: options.isDarkTheme,
      modal: true,
      parent: options.parent,
      title: TXT.title,
      width: 1080,
      height: 760,
      preload: path.join(__dirname, './preload.js')
    });

    this._draft = this._parseRoute(options.initialValue);
    this._pageBody = this._buildPageSource();

    this._result = new Promise<string | null>(resolve => {
      this._resolve = resolve;
    });

    this._registerHandlers();
  }

  get window(): ThemedWindow {
    return this._window;
  }

  get result(): Promise<string | null> {
    return this._result;
  }

  load() {
    this._window.loadDialogContent(this._pageBody);
  }

  private _buildPageSource(): string {
    const draftJson = JSON.stringify(this._draft);
    return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta http-equiv="Content-Security-Policy" content="default-src 'self' data: 'unsafe-inline' 'unsafe-eval' https://*.tianditu.gov.cn https://api.tianditu.gov.cn https://unpkg.com; img-src 'self' data: blob: https://*.tianditu.gov.cn https://api.tianditu.gov.cn https://unpkg.com; style-src 'self' 'unsafe-inline' https://unpkg.com; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com https://api.tianditu.gov.cn; connect-src 'self' https://*.tianditu.gov.cn https://api.tianditu.gov.cn https://unpkg.com;">
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>${TXT.title}</title>
<link rel="stylesheet" href="${LEAFLET_CSS_URL}" />
<style>
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#fff;color:#1f1f1f;font:13px/1.4 -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif}
.shell,.shell *{box-sizing:border-box}button,input{font:inherit}
.shell{width:100%;height:100%;display:flex;flex-direction:column}
.header{height:70px;padding:0 22px;border-bottom:1px solid rgba(0,0,0,.08);display:flex;align-items:center;justify-content:space-between}
.title{font-size:20px;font-weight:700}
.close{width:32px;height:32px;border:0;border-radius:8px;background:transparent;font-size:22px;cursor:pointer}
.body{flex:1;min-height:0;padding:16px 20px 18px}
.layout{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:16px;height:100%;min-height:0}
.mapWrap{position:relative;min-height:480px;border:1px solid rgba(0,0,0,.08);border-radius:16px;overflow:hidden;background:#f4f4f4}
.map{position:absolute;inset:0}
.toolbar{position:absolute;left:16px;top:16px;display:flex;gap:10px;flex-wrap:wrap;z-index:600;max-width:calc(100% - 360px)}
.searchToolbar{position:absolute;right:16px;top:16px;display:flex;gap:10px;align-items:center;z-index:600;max-width:320px}
.btn{height:40px;padding:0 16px;border:1px solid rgba(0,0,0,.12);border-radius:12px;background:#fff;cursor:pointer}
.btn.primary{background:#2563eb;border-color:#2563eb;color:#fff}
.btn:disabled{opacity:.45;cursor:not-allowed}
.searchPanel{position:relative;width:min(280px,calc(100vw - 520px))}
.searchInput{width:100%;height:40px;padding:0 14px;border:1px solid rgba(0,0,0,.12);border-radius:12px;background:#fff;outline:none;box-shadow:0 4px 20px rgba(0,0,0,.06)}
.searchDropdown{position:absolute;left:0;right:0;top:46px;max-height:280px;overflow:auto;border:1px solid rgba(0,0,0,.12);border-radius:12px;background:#fff;box-shadow:0 10px 30px rgba(0,0,0,.12);display:none}
.searchDropdown.show{display:block}
.searchItem{padding:10px 14px;cursor:pointer;border-bottom:1px solid rgba(0,0,0,.06)}
.searchItem:last-child{border-bottom:0}
.searchItem.active,.searchItem:hover{background:#f3f7ff}
.searchTitle{font-size:14px;color:#1f1f1f;line-height:1.5}
.searchDesc{font-size:12px;color:#8a8a8a;line-height:1.5;margin-top:2px}
.searchEmpty{padding:12px 14px;font-size:13px;color:#8a8a8a}
.overlay{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(255,255,255,.82);font-size:14px;color:#666;opacity:0;pointer-events:none;transition:opacity .15s ease;padding:16px;text-align:center;z-index:500}
.overlay.show{opacity:1}
.side{display:flex;flex-direction:column;gap:12px;min-width:0;min-height:0;overflow:hidden}
.sideHead{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
.count{font-size:14px;color:#8a8a8a}
.status{font-size:13px;line-height:1.6;color:#666;min-height:42px}
.list{flex:1;min-height:0;overflow:auto;border:1px solid rgba(0,0,0,.08);border-radius:14px;background:#fff;padding:12px 12px 16px}
.empty{padding:30px 10px;color:#8a8a8a;text-align:center;line-height:1.7}
.routeItem{border:1px solid rgba(0,0,0,.08);border-radius:14px;background:#fff;padding:12px 12px 10px;margin-bottom:12px}
.routeItem.active{border-color:rgba(37,99,235,.28);box-shadow:0 0 0 2px rgba(37,99,235,.08) inset}
.routeItemHeader{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px;cursor:pointer}
.routeTitle{display:flex;align-items:center;gap:10px;font-size:15px;font-weight:700}
.routeIndex{width:28px;height:28px;border-radius:999px;background:rgba(37,99,235,.1);color:#2563eb;display:flex;align-items:center;justify-content:center;font-weight:700}
.del{width:34px;height:34px;border:0;border-radius:10px;background:transparent;color:inherit;cursor:pointer;font-size:16px}
.del:hover{background:rgba(0,0,0,.06)}
.fields{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}
.fieldWrap{display:flex;flex-direction:column;gap:6px}
.label{font-size:12px;color:#8a8a8a}
.field{width:100%;height:40px;padding:0 12px;border:1px solid rgba(0,0,0,.1);border-radius:10px;background:#fff;outline:none}
.footer{height:78px;padding:0 20px;border-top:1px solid rgba(0,0,0,.08);display:flex;align-items:center;justify-content:flex-end;gap:12px;background:#fff;position:relative;z-index:10}
.hintBar{position:absolute;left:16px;right:16px;bottom:16px;z-index:500;padding:10px 14px;border-radius:12px;background:rgba(255,255,255,.92);box-shadow:0 6px 24px rgba(0,0,0,.10);font-size:13px;color:#666;line-height:1.5}
.footerBtn{height:42px;padding:0 18px;border:1px solid rgba(0,0,0,.12);border-radius:12px;background:#fff;cursor:pointer}
.footerBtn.primary{background:#2563eb;border-color:#2563eb;color:#fff}
.leaflet-div-icon{background:transparent;border:0}
.routeMarker{display:flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:999px;background:#2563eb;color:#fff;font-weight:700;box-shadow:0 4px 10px rgba(37,99,235,.28),0 0 0 4px rgba(37,99,235,.14)}
.routeMarker.active{background:#ff7a18;box-shadow:0 4px 10px rgba(255,122,24,.28),0 0 0 4px rgba(255,122,24,.14)}
@media (max-width: 980px){.layout{grid-template-columns:1fr}.mapWrap{min-height:320px}.side{min-height:240px}}
/* [UAV-IDE MODIFY] Align route picker controls with FleetDock IDEA second-stage interaction rules. */
.btn,.footerBtn,.close,.field,.searchInput,.searchItem,.routeItem,.del{transition:border-color .18s ease,box-shadow .18s ease,background-color .18s ease,color .18s ease}.btn:hover,.footerBtn:hover,.routeItem:hover,.searchInput:hover,.field:hover{border-color:#93c5fd;box-shadow:0 6px 18px rgba(37,99,235,.08)}.btn:active,.footerBtn:active,.close:active{background:#eff6ff}.btn:focus-visible,.footerBtn:focus-visible,.close:focus-visible,.field:focus-visible,.searchInput:focus-visible,.searchItem:focus-visible,.del:focus-visible{outline:2px solid rgba(6,182,212,.55);outline-offset:2px}.btn:disabled,.footerBtn:disabled{opacity:.55;cursor:not-allowed;box-shadow:none}.btn.primary:hover,.footerBtn.primary:hover{background:#1d4ed8;border-color:#1d4ed8}.del:hover{background:#fee2e2;color:#dc2626}.overlay{backdrop-filter:blur(2px)}@media (prefers-reduced-motion: reduce){*{transition:none!important;animation:none!important}}
</style>
</head>
<body>
<div class="shell">
  <div class="header">
    <div class="title">${TXT.title}</div>
    <button class="close" data-action="cancel">&times;</button>
  </div>
  <div class="body">
    <div class="layout">
      <div class="mapWrap">
        <div class="toolbar">
          <button class="btn primary" data-action="toggleDrawing">${
            TXT.createRoute
          }</button>
          <button class="btn" data-action="undo">${TXT.undo}</button>
          <button class="btn" data-action="clear">${TXT.clear}</button>
          <button class="btn" data-action="retry">${TXT.retry}</button>
        </div>
        <div class="searchToolbar">
          <div class="searchPanel">
            <input class="searchInput" data-role="map-search-input" type="text" placeholder="${
              TXT.searchPlaceholder
            }" autocomplete="off" />
            <div class="searchDropdown" id="search-dropdown"></div>
          </div>
          <button class="btn" data-action="search">${TXT.searchButton}</button>
        </div>
        <div class="map" id="map"></div>
        <div class="overlay show" id="overlay">${TXT.loadingMap}</div>
        <div class="hintBar" id="map-hint">${TXT.loadingHint}</div>
      </div>
      <div class="side">
        <div class="sideHead">
          <div>
            <div class="label">${TXT.title}</div>
            <div class="count" id="route-count"></div>
          </div>
        </div>
        <div class="status" id="status"></div>
        <div class="list" id="route-list"></div>
      </div>
    </div>
  </div>
  <div class="footer">
    <button class="footerBtn" data-action="cancel">${TXT.cancel}</button>
    <button class="footerBtn primary" data-action="confirm">${
      TXT.confirm
    }</button>
  </div>
</div>
<script src="${LEAFLET_JS_URL}"></script>
<script src="${TIANDITU_JS_API_URL}"></script>
<script src="${TIANDITU_SERVICE_JS_URL}"></script>
<script>
(function () {
  const api = window.electronAPI || {};
  const defaultCenter = ${JSON.stringify(DEFAULT_CENTER)};
  const vectorTileUrl = 'https://t{s}.tianditu.gov.cn/DataServer?T=vec_w&x={x}&y={y}&l={z}&tk=${TIANDITU_API_KEY}';
  const annotationTileUrl = 'https://t{s}.tianditu.gov.cn/DataServer?T=cva_w&x={x}&y={y}&l={z}&tk=${TIANDITU_API_KEY}';
  let draft = ${draftJson};
  let map = null;
  let polyline = null;
  let markers = [];
  let geocoder = null;
  let loading = true;
  let searching = false;
  let suggestionLoading = false;
  let errorText = '';
  let finished = false;
  let drawingMode = false;
  let selectedIndex = draft.length ? 0 : -1;
  let lastMapClickSignature = '';
  let lastMapClickAt = 0;
  let searchMarker = null;
  let searchSuggestions = [];
  let searchActiveIndex = -1;
  let searchDebounceTimer = null;
  let searchRequestToken = 0;

  function defaultPoint() {
    const alt = draft.length ? (getPoint(draft[draft.length - 1]) || defaultCenter).alt : defaultCenter.alt;
    return { lat: String(defaultCenter.lat), lon: String(defaultCenter.lon), alt: String(alt) };
  }

  function getPoint(point) {
    const lat = Number(point && point.lat);
    const lon = Number(point && point.lon);
    const alt =
      point && point.alt !== undefined && point.alt !== null && point.alt !== ''
        ? Number(point.alt)
        : defaultCenter.alt;
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || !Number.isFinite(alt)) {
      return null;
    }
    return { lat: lat, lon: lon, alt: alt };
  }

  function serializeRoute() {
    const route = [];
    for (let index = 0; index < draft.length; index++) {
      const point = getPoint(draft[index]);
      if (!point) {
        throw new Error('${TXT.invalidPoint}');
      }
      route.push({
        lat: Number(point.lat.toFixed(6)),
        lon: Number(point.lon.toFixed(6)),
        alt: Number(point.alt.toFixed(2))
      });
    }
    if (!route.length) {
      throw new Error('${TXT.emptyRoute}');
    }
    return JSON.stringify(route);
  }

  function pointLabel(index) {
    return '${TXT.pointLabel}' + (index + 1);
  }

  function routeSummary() {
    const valid = draft.map(getPoint).filter(Boolean);
    if (!valid.length) {
      return '';
    }
    return '${TXT.saveSummary}' + valid.length + '${TXT.saveSummaryTail}';
  }

  function updateOverlay() {
    const overlay = document.getElementById('overlay');
    const hint = document.getElementById('map-hint');
    const status = document.getElementById('status');
    const count = document.getElementById('route-count');
    if (count) {
      count.textContent = routeSummary();
    }
    const hintText = errorText || (searching
      ? '${TXT.searching}'
      : (loading
      ? '${TXT.loadingHint}'
      : drawingMode
        ? '${TXT.drawHint}'
        : (draft.length ? '${TXT.editHint}' : '${TXT.emptyHint}')));
    if (status) {
      status.textContent = errorText || (searching
        ? '${TXT.searching}'
        : (drawingMode
        ? '${TXT.drawHint}'
        : (draft.length ? '${TXT.editHint}' : '${TXT.emptyHint}')));
    }
    if (hint) {
      hint.textContent = hintText;
    }
    if (overlay) {
      overlay.textContent = loading ? '${
        TXT.loadingMap
      }' : (errorText || (!draft.length && !drawingMode && !searchMarker) ? hintText : '');
      overlay.classList.toggle('show', loading || !!errorText || (!draft.length && !drawingMode && !searchMarker));
    }
    syncToolbar();
  }

  function syncToolbar() {
    const createBtn = document.querySelector('[data-action="toggleDrawing"]');
    const undoBtn = document.querySelector('[data-action="undo"]');
    const clearBtn = document.querySelector('[data-action="clear"]');
    const searchBtn = document.querySelector('[data-action="search"]');
    if (createBtn instanceof HTMLButtonElement) {
      createBtn.textContent = drawingMode ? '${TXT.finishRoute}' : '${
      TXT.createRoute
    }';
      createBtn.classList.toggle('primary', drawingMode || !draft.length);
    }
    if (undoBtn instanceof HTMLButtonElement) {
      undoBtn.disabled = !draft.length;
    }
    if (clearBtn instanceof HTMLButtonElement) {
      clearBtn.disabled = !draft.length;
    }
    if (searchBtn instanceof HTMLButtonElement) {
      searchBtn.disabled = loading || searching;
      searchBtn.textContent = searching ? '${TXT.searching}' : '${
      TXT.searchButton
    }';
    }
    renderSearchDropdown();
  }

  function renderList() {
    const list = document.getElementById('route-list');
    if (!list) {
      return;
    }
    if (!draft.length) {
      list.innerHTML = '<div class="empty">' + '${TXT.emptyHint}' + '</div>';
      return;
    }

    list.innerHTML = draft.map(function (point, index) {
      const active = selectedIndex === index ? ' active' : '';
      return '<div class="routeItem' + active + '" data-index="' + index + '">' +
        '<div class="routeItemHeader" data-action="focus-point" data-index="' + index + '">' +
        '<div class="routeTitle"><div class="routeIndex">' + (index + 1) + '</div><div>' + pointLabel(index) + '</div></div>' +
        '<button class="del" title="${TXT.cancel}" aria-label="${
      TXT.cancel
    }" data-action="remove-point" data-index="' + index + '">&times;</button>' +
        '</div>' +
        '<div class="fields">' +
        '<div class="fieldWrap"><div class="label">${
          TXT.lat
        }</div><input class="field" data-action="point-input" data-index="' + index + '" data-field="lat" type="number" step="0.000001" value="' + String(point.lat || '') + '" /></div>' +
        '<div class="fieldWrap"><div class="label">${
          TXT.lon
        }</div><input class="field" data-action="point-input" data-index="' + index + '" data-field="lon" type="number" step="0.000001" value="' + String(point.lon || '') + '" /></div>' +
        '<div class="fieldWrap"><div class="label">${
          TXT.alt
        }</div><input class="field" data-action="point-input" data-index="' + index + '" data-field="alt" type="number" step="0.1" value="' + String(point.alt || '') + '" /></div>' +
        '</div></div>';
    }).join('');
  }

  function renderMap() {
    if (!map || !window.L) {
      return;
    }
    const points = draft.map(getPoint).filter(Boolean);
    markers.forEach(function (marker) { map.removeLayer(marker); });
    markers = [];

    points.forEach(function (point, index) {
      const marker = window.L.marker([point.lat, point.lon], {
        icon: window.L.divIcon({
          className: '',
          html: '<div class="routeMarker' + (selectedIndex === index ? ' active' : '') + '">' + (index + 1) + '</div>',
          iconSize: [28, 28],
          iconAnchor: [14, 14]
        })
      }).addTo(map);
      marker.on('click', function () {
        focusPoint(index, false);
      });
      markers.push(marker);
    });

    if (!polyline) {
      polyline = window.L.polyline([], {
        color: '#2563eb',
        weight: 4
      }).addTo(map);
    }
    polyline.setLatLngs(points.map(function (point) { return [point.lat, point.lon]; }));
    if (searchMarker) {
      searchMarker.addTo(map);
    }
  }

  function renderAll(recenterMap) {
    renderList();
    renderMap();
    updateOverlay();
    if (map) {
      map.invalidateSize(false);
      const points = draft.map(getPoint).filter(Boolean);
      if (recenterMap) {
        if (selectedIndex >= 0 && points[selectedIndex]) {
          map.setView([points[selectedIndex].lat, points[selectedIndex].lon], Math.max(13, map.getZoom() || 13), { animate: false });
        } else if (points.length === 1) {
          map.setView([points[0].lat, points[0].lon], 13, { animate: false });
        } else if (points.length > 1) {
          map.fitBounds(points.map(function (point) { return [point.lat, point.lon]; }), { padding: [40, 40] });
        } else {
          map.setView([defaultCenter.lat, defaultCenter.lon], 12, { animate: false });
        }
      }
    }
  }

  function addPointFromMap(lat, lon) {
    draft.push({
      lat: Number(lat).toFixed(6),
      lon: Number(lon).toFixed(6),
      alt: String(draft.length ? (getPoint(draft[draft.length - 1]) || defaultCenter).alt : defaultCenter.alt)
    });
    selectedIndex = draft.length - 1;
    renderAll(false);
  }

  // [UAV-IDE MODIFY] Guard against duplicate map click delivery so one user click only adds one waypoint.
  function shouldIgnoreMapClick(lat, lon) {
    const signature = Number(lat).toFixed(6) + ':' + Number(lon).toFixed(6);
    const now = Date.now();
    if (signature === lastMapClickSignature && now - lastMapClickAt < 250) {
      return true;
    }
    lastMapClickSignature = signature;
    lastMapClickAt = now;
    return false;
  }

  function focusPoint(index, recenterMap) {
    if (index < 0 || index >= draft.length) {
      return;
    }
    selectedIndex = index;
    renderAll(!!recenterMap);
  }

  function toggleDrawing() {
    drawingMode = !drawingMode;
    renderAll(false);
  }

  function undoPoint() {
    if (!draft.length) {
      return;
    }
    draft.pop();
    if (selectedIndex >= draft.length) {
      selectedIndex = draft.length - 1;
    }
    if (selectedIndex < 0 && draft.length) {
      selectedIndex = 0;
    }
    renderAll(false);
  }

  function clearRoute() {
    draft = [];
    selectedIndex = -1;
    drawingMode = false;
    lastMapClickSignature = '';
    lastMapClickAt = 0;
    renderAll(true);
  }

  function ensureSearchMarker(lat, lon) {
    if (!map || !window.L) {
      return;
    }
    const latLng = [lat, lon];
    if (!searchMarker) {
      searchMarker = window.L.circleMarker(latLng, {
        radius: 9,
        color: '#2563eb',
        weight: 3,
        fillColor: '#ffffff',
        fillOpacity: 1
      }).addTo(map);
    } else {
      searchMarker.setLatLng(latLng);
      searchMarker.addTo(map);
    }
  }

  function buildSearchAdapter() {
    return {
      getZoom: function () {
        return map ? Math.round(map.getZoom() || 12) : 12;
      },
      getBounds: function () {
        const bounds = map ? map.getBounds() : null;
        const fallbackLat = defaultCenter.lat;
        const fallbackLon = defaultCenter.lon;
        const southWest = bounds ? bounds.getSouthWest() : { lat: fallbackLat - 0.1, lng: fallbackLon - 0.1 };
        const northEast = bounds ? bounds.getNorthEast() : { lat: fallbackLat + 0.1, lng: fallbackLon + 0.1 };
        return {
          getSouthWest: function () {
            return new window.T.LngLat(southWest.lng, southWest.lat);
          },
          getNorthEast: function () {
            return new window.T.LngLat(northEast.lng, northEast.lat);
          }
        };
      }
    };
  }

  function ensureGeocoder() {
    if (geocoder) {
      return Promise.resolve(geocoder);
    }
    return new Promise(function (resolve, reject) {
      let attempts = 0;
      function tryCreate() {
        if (window.T && window.T.Geocoder) {
          // [UAV-IDE MODIFY] Use TianDiTu browser-side geocoder so the existing browser-key can search without Electron main-process proxying.
          geocoder = new window.T.Geocoder();
          resolve(geocoder);
          return;
        }
        attempts += 1;
        if (attempts >= 40) {
          reject(new Error('${TXT.geocoderFailed}'));
          return;
        }
        window.setTimeout(tryCreate, 200);
      }
      tryCreate();
    });
  }

  function ensureSearchService() {
    if (window.T && window.T.LocalSearch) {
      return Promise.resolve();
    }
    return new Promise(function (resolve, reject) {
      let attempts = 0;
      function tryReady() {
        if (window.T && window.T.LocalSearch) {
          resolve();
          return;
        }
        attempts += 1;
        if (attempts >= 40) {
          reject(new Error('${TXT.geocoderFailed}'));
          return;
        }
        window.setTimeout(tryReady, 200);
      }
      tryReady();
    });
  }

  function clearSuggestions() {
    searchSuggestions = [];
    searchActiveIndex = -1;
    renderSearchDropdown();
  }

  function normalizeSuggestion(raw) {
    const title = String(raw && (raw.name || raw.poiName || raw.address || raw.hotPointID || '')).trim();
    const desc = String(raw && (raw.address || raw.province || raw.city || '')).trim();
    let lon = Number(raw && raw.lon);
    let lat = Number(raw && raw.lat);
    if ((!Number.isFinite(lon) || !Number.isFinite(lat)) && raw && typeof raw.lonlat === 'string') {
      const parts = raw.lonlat.split(',');
      lon = Number(parts[0]);
      lat = Number(parts[1]);
    }
    if ((!Number.isFinite(lon) || !Number.isFinite(lat)) && raw && raw.location) {
      lon = Number(raw.location.lon);
      lat = Number(raw.location.lat);
    }
    if (!Number.isFinite(lon) || !Number.isFinite(lat)) {
      return null;
    }
    return {
      title: title || desc || '${TXT.searchPlaceholder}',
      desc: desc && desc !== title ? desc : '',
      lon: lon,
      lat: lat
    };
  }

  function renderSearchDropdown() {
    const dropdown = document.getElementById('search-dropdown');
    if (!dropdown) {
      return;
    }
    if (suggestionLoading) {
      dropdown.innerHTML = '<div class="searchEmpty">${
        TXT.searchingSuggest
      }</div>';
      dropdown.classList.add('show');
      return;
    }
    if (!searchSuggestions.length) {
      dropdown.innerHTML = '';
      dropdown.classList.remove('show');
      return;
    }
    dropdown.innerHTML = searchSuggestions.map(function (item, index) {
      return '<div class="searchItem' + (index === searchActiveIndex ? ' active' : '') + '" data-action="pick-suggestion" data-index="' + index + '">' +
        '<div class="searchTitle">' + item.title + '</div>' +
        (item.desc ? '<div class="searchDesc">' + item.desc + '</div>' : '') +
        '</div>';
    }).join('');
    dropdown.classList.add('show');
  }

  async function fetchSuggestions(keyword) {
    const requestToken = ++searchRequestToken;
    if (!keyword || keyword.length < 2) {
      suggestionLoading = false;
      clearSuggestions();
      return;
    }
    suggestionLoading = true;
    renderSearchDropdown();
    try {
      await ensureSearchService();
      const result = await new Promise(function (resolve, reject) {
        const searcher = new window.T.LocalSearch(buildSearchAdapter(), {
          pageCapacity: 6,
          queryType: 1,
          onSearchComplete: function (searchResult) {
            resolve(searchResult);
          }
        });
        searcher.search(keyword, 1);
        window.setTimeout(function () {
          reject(new Error('${TXT.searchFailed}'));
        }, 12000);
      });
      if (requestToken !== searchRequestToken) {
        return;
      }
      const pois = result && typeof result.getPois === 'function'
        ? result.getPois()
        : (result && result.pois ? result.pois : []);
      searchSuggestions = Array.isArray(pois)
        ? pois.map(normalizeSuggestion).filter(Boolean).slice(0, 6)
        : [];
      searchActiveIndex = searchSuggestions.length ? 0 : -1;
      suggestionLoading = false;
      renderSearchDropdown();
    } catch (error) {
      if (requestToken !== searchRequestToken) {
        return;
      }
      suggestionLoading = false;
      clearSuggestions();
    }
  }

  function applySuggestion(index) {
    const item = searchSuggestions[index];
    if (!item) {
      return false;
    }
    ensureSearchMarker(item.lat, item.lon);
    if (map) {
      map.setView([item.lat, item.lon], Math.max(map.getZoom() || 12, 14), { animate: false });
    }
    errorText = '';
    clearSuggestions();
    updateOverlay();
    return true;
  }

  // [UAV-IDE MODIFY] Add lightweight place search so users can locate an area before drawing route points.
  async function searchLocation() {
    const input = document.querySelector('[data-role="map-search-input"]');
    const keyword = input instanceof HTMLInputElement ? input.value.trim() : '';
    if (!keyword) {
      errorText = '${TXT.emptySearch}';
      updateOverlay();
      return;
    }
    searching = true;
    errorText = '';
    updateOverlay();
    try {
      if (searchSuggestions.length && searchActiveIndex >= 0 && applySuggestion(searchActiveIndex)) {
        return;
      }
      const payload = await new Promise(function (resolve, reject) {
        ensureGeocoder()
          .then(function (service) {
            let settled = false;
            service.getPoint(keyword, function (result) {
              if (settled) {
                return;
              }
              settled = true;
              resolve(result || null);
            });
            window.setTimeout(function () {
              if (settled) {
                return;
              }
              settled = true;
              reject(new Error('${TXT.searchFailed}'));
            }, 12000);
          })
          .catch(reject);
      });
      const location = payload && payload.location ? payload.location : null;
      const lat = Number(location && location.lat);
      const lon = Number(location && location.lon);
      if (String(payload && payload.status) !== '0' || !Number.isFinite(lat) || !Number.isFinite(lon)) {
        throw new Error('${TXT.searchNoResult}');
      }
      ensureSearchMarker(lat, lon);
      if (map) {
        map.setView([lat, lon], Math.max(map.getZoom() || 12, 14), { animate: false });
      }
      errorText = '';
    } catch (error) {
      errorText = String(error && error.message ? error.message : error) || '${
        TXT.searchFailed
      }';
    } finally {
      searching = false;
      updateOverlay();
    }
  }

  // [UAV-IDE MODIFY] Leaflet is used here instead of TianDiTu's JS map object to keep desktop dialog sizing stable.
  function initMap(forceRecreate) {
    loading = true;
    errorText = '';
    updateOverlay();
    try {
      if (!window.L) {
        throw new Error('${TXT.loadingMap}');
      }
      if (map && forceRecreate) {
        map.remove();
        map = null;
        polyline = null;
        markers = [];
      }
      if (!map) {
        map = window.L.map('map', {
          zoomControl: true,
          attributionControl: false
        });
        window.L.tileLayer(vectorTileUrl, {
          subdomains: ['0', '1', '2', '3', '4', '5', '6', '7'],
          minZoom: 1,
          maxZoom: 18
        }).addTo(map);
        window.L.tileLayer(annotationTileUrl, {
          subdomains: ['0', '1', '2', '3', '4', '5', '6', '7'],
          minZoom: 1,
          maxZoom: 18
        }).addTo(map);
        map.on('click', function (event) {
          if (!drawingMode) {
            return;
          }
          if (shouldIgnoreMapClick(event.latlng.lat, event.latlng.lng)) {
            return;
          }
          addPointFromMap(event.latlng.lat, event.latlng.lng);
        });
      }
      renderAll(true);
      window.setTimeout(function () {
        if (map) {
          map.invalidateSize(false);
          renderMap();
        }
      }, 0);
      window.setTimeout(function () {
        if (map) {
          map.invalidateSize(false);
          renderMap();
        }
      }, 120);
      loading = false;
      errorText = '';
      updateOverlay();
    } catch (error) {
      loading = false;
      errorText = String(error && error.message ? error.message : error);
      updateOverlay();
    }
  }

  function finish(value) {
    if (finished) {
      return;
    }
    finished = true;
    Promise.resolve(value === undefined ? null : value).finally(function () {
      window.close();
    });
  }

  function confirm() {
    try {
      const payload = serializeRoute();
      const result = api.confirmRoute ? api.confirmRoute(payload) : Promise.resolve(payload);
      Promise.resolve(result)
        .then(function () {
          finish(payload);
        })
        .catch(function (err) {
          errorText = String(err && err.message ? err.message : err);
          updateOverlay();
        });
    } catch (error) {
      errorText = String(error && error.message ? error.message : error);
      updateOverlay();
    }
  }

  function cancel() {
    if (api.cancelRoute) {
      Promise.resolve(api.cancelRoute()).finally(function () {
        finish(null);
      });
    } else {
      finish(null);
    }
  }

  document.querySelector('.shell').addEventListener('click', function (event) {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) {
      return;
    }
    const actionEl = target.closest('[data-action]');
    if (!actionEl) {
      return;
    }
    const action = actionEl.getAttribute('data-action') || '';
    const index = Number(actionEl.getAttribute('data-index') || '-1');
    if (action === 'confirm') {
      confirm();
    } else if (action === 'cancel') {
      cancel();
    } else if (action === 'toggleDrawing') {
      toggleDrawing();
    } else if (action === 'undo') {
      undoPoint();
    } else if (action === 'clear') {
      clearRoute();
    } else if (action === 'retry') {
      initMap(true);
    } else if (action === 'search') {
      searchLocation();
    } else if (action === 'pick-suggestion') {
      const suggestionIndex = Number(actionEl.getAttribute('data-index') || '-1');
      applySuggestion(suggestionIndex);
    } else if (action === 'remove-point') {
      event.stopPropagation();
      draft.splice(index, 1);
      if (selectedIndex >= draft.length) {
        selectedIndex = draft.length - 1;
      }
      if (selectedIndex < 0 && draft.length) {
        selectedIndex = 0;
      }
      renderAll(false);
    } else if (action === 'focus-point') {
      focusPoint(index, true);
    }
  });

  document.querySelector('.shell').addEventListener('input', function (event) {
    const target = event.target instanceof HTMLInputElement ? event.target : null;
    if (!target) {
      return;
    }
    if (target.getAttribute('data-role') === 'map-search-input') {
      const keyword = target.value.trim();
      if (searchDebounceTimer) {
        window.clearTimeout(searchDebounceTimer);
      }
      searchDebounceTimer = window.setTimeout(function () {
        fetchSuggestions(keyword);
      }, 280);
      return;
    }
    const actionEl = target.closest('[data-action="point-input"]');
    if (!actionEl) {
      return;
    }
    const index = Number(actionEl.getAttribute('data-index') || '-1');
    const field = actionEl.getAttribute('data-field');
    if (!field || index < 0 || !draft[index]) {
      return;
    }
    draft[index][field] = target.value;
    renderAll(false);
  });

  window.addEventListener('resize', function () {
    if (map) {
      map.invalidateSize(false);
      renderMap();
    }
  });

  window.addEventListener('beforeunload', function () {
    if (!finished) {
      cancel();
    }
  });

  document.querySelector('.shell').addEventListener('keydown', function (event) {
    const target = event.target instanceof HTMLInputElement ? event.target : null;
    if (!target || target.getAttribute('data-role') !== 'map-search-input') {
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      searchLocation();
    } else if (event.key === 'ArrowDown') {
      event.preventDefault();
      if (!searchSuggestions.length) {
        return;
      }
      searchActiveIndex = Math.min(searchSuggestions.length - 1, searchActiveIndex + 1);
      renderSearchDropdown();
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      if (!searchSuggestions.length) {
        return;
      }
      searchActiveIndex = Math.max(0, searchActiveIndex - 1);
      renderSearchDropdown();
    } else if (event.key === 'Escape') {
      clearSuggestions();
    }
  });

  updateOverlay();
  renderAll(false);
  window.addEventListener('load', function () {
    initMap(false);
  }, { once: true });
})();
</script>
</body>
</html>`;
  }

  private _parseRoute(raw?: string): RoutePointDraft[] {
    if (!raw) {
      return [];
    }

    try {
      const value = JSON.parse(raw);
      if (!Array.isArray(value)) {
        return [];
      }

      const draft: RoutePointDraft[] = [];
      for (const item of value) {
        const point = this._normalizePoint(item);
        if (point) {
          draft.push(point);
        }
      }
      return draft;
    } catch (error) {
      return [];
    }
  }

  private _normalizePoint(raw: any): RoutePointDraft | null {
    const lat = Number(raw?.lat);
    const lon = Number(raw?.lon);
    const alt =
      raw?.alt === undefined || raw?.alt === null || raw?.alt === ''
        ? DEFAULT_CENTER.alt
        : Number(raw.alt);

    if (
      !Number.isFinite(lat) ||
      !Number.isFinite(lon) ||
      !Number.isFinite(alt)
    ) {
      return null;
    }

    return {
      lat: lat.toFixed(6),
      lon: lon.toFixed(6),
      alt: alt.toFixed(2)
    };
  }

  private _registerHandlers() {
    const confirmHandler = (
      _event: Electron.IpcMainEvent,
      route: string
    ): string => {
      this._finish(route);
      return route;
    };

    const cancelHandler = (_event: Electron.IpcMainEvent): null => {
      this._finish(null);
      return null;
    };

    this._evm.registerSyncEventHandler(
      EventTypeMain.RoutePickerConfirm,
      confirmHandler
    );
    this._evm.registerSyncEventHandler(
      EventTypeMain.RoutePickerCancel,
      cancelHandler
    );

    this._window.window.on('closed', () => {
      this._evm.unregisterSyncEventHandler(
        EventTypeMain.RoutePickerConfirm,
        confirmHandler
      );
      this._evm.unregisterSyncEventHandler(
        EventTypeMain.RoutePickerCancel,
        cancelHandler
      );
      this._finish(null);
    });
  }

  private _finish(value: string | null) {
    if (this._settled) {
      return;
    }

    this._settled = true;
    this._resolve(value);
  }

  private _window: ThemedWindow;
  private _pageBody: string;
  private _draft: RoutePointDraft[];
  private _evm = new EventManager();
  private _result: Promise<string | null>;
  private _resolve: (value: string | null) => void = () => {};
  private _settled = false;
}

export namespace RoutePickerDialog {
  export interface IOptions {
    isDarkTheme: boolean;
    parent?: BrowserWindow;
    initialValue?: string;
  }
}
