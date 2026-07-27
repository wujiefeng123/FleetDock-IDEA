// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Dedicated waypoint picker backed by Leaflet with TianDiTu tiles for stable desktop rendering.

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
const DEFAULT_WAYPOINT = { lat: 39.9042, lon: 116.4074, alt: 100 };

type WaypointDraft = { lat: string; lon: string; alt: string };

const TXT = {
  title: '\u9009\u62e9\u822a\u70b9',
  loadingMap: '\u6b63\u5728\u52a0\u8f7d\u5929\u5730\u56fe...',
  clickMap: '\u70b9\u51fb\u5730\u56fe\u9009\u62e9\u822a\u70b9',
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
  lat: '\u7eac\u5ea6',
  lon: '\u7ecf\u5ea6',
  alt: '\u9ad8\u5ea6',
  reset: '\u91cd\u7f6e',
  retry: '\u91cd\u65b0\u52a0\u8f7d',
  cancel: '\u53d6\u6d88',
  confirm: '\u786e\u8ba4\u9009\u62e9',
  preview: '\u7eac\u5ea6 ',
  lonSep: '\uff0c\u7ecf\u5ea6 ',
  altSep: '\uff0c\u9ad8\u5ea6 ',
  status:
    '\u5728\u5730\u56fe\u4e0a\u5355\u51fb\u9009\u62e9\u822a\u70b9\uff0c\u652f\u6301\u62d6\u52a8\u548c\u624b\u52a8\u5fae\u8c03\u3002',
  invalidCoords:
    '\u8bf7\u5148\u586b\u5199\u6709\u6548\u7684\u822a\u70b9\u5750\u6807\u548c\u9ad8\u5ea6',
  scriptFailed:
    '\u5730\u56fe\u811a\u672c\u52a0\u8f7d\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u7f51\u7edc\u3002',
  geocoderFailed:
    '\u641c\u7d22\u670d\u52a1\u52a0\u8f7d\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u7f51\u7edc\u540e\u91cd\u8bd5\u3002'
} as const;

export class WaypointPickerDialog {
  constructor(options: WaypointPickerDialog.IOptions) {
    this._window = new ThemedWindow({
      isDarkTheme: options.isDarkTheme,
      modal: true,
      parent: options.parent,
      title: TXT.title,
      width: 920,
      height: 680,
      preload: path.join(__dirname, './preload.js')
    });

    this._draft =
      this._parseWaypoint(options.initialValue) || this._defaultWaypoint();
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
    const draft = this._draft;
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
.layout{display:grid;grid-template-columns:minmax(0,1fr) 280px;gap:16px;height:100%}
.mapWrap{position:relative;min-height:480px;border:1px solid rgba(0,0,0,.08);border-radius:16px;overflow:hidden;background:#f4f4f4}
.map{position:absolute;inset:0}
.map :global(.leaflet-container){background:#f4f4f4}
.mapToolbar{position:absolute;right:16px;top:16px;display:flex;gap:10px;align-items:flex-start;z-index:600;max-width:calc(100% - 96px)}
.searchPanel{position:relative;width:min(320px,calc(100vw - 460px))}
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
.side{display:flex;flex-direction:column;gap:12px}
.field{width:100%;height:48px;padding:0 14px;border:1px solid rgba(0,0,0,.1);border-radius:12px;background:#fff;outline:none}
.label{font-size:14px;color:#8a8a8a;margin-bottom:8px}
.fieldRow{margin-bottom:6px}
.preview{padding:14px;border:1px solid rgba(0,0,0,.08);border-radius:14px;background:#fafafa;min-height:92px;line-height:1.7;word-break:break-all}
.status{font-size:13px;line-height:1.6;color:#666;min-height:42px}
.footer{height:78px;padding:0 20px;border-top:1px solid rgba(0,0,0,.08);display:flex;align-items:center;justify-content:flex-end;gap:12px;background:#fff}
.btn{height:42px;padding:0 18px;border:1px solid rgba(0,0,0,.12);border-radius:12px;background:#fff;cursor:pointer}
.btn.primary{background:#2563eb;border-color:#2563eb;color:#fff}
@media (max-width: 860px){.layout{grid-template-columns:1fr}.mapWrap{min-height:320px}}
/* [UAV-IDE MODIFY] Align picker shell controls with FleetDock IDEA second-stage interaction rules. */
.btn,.close,.field,.searchInput,.searchItem,.preview{transition:border-color .18s ease,box-shadow .18s ease,background-color .18s ease,color .18s ease}.btn:hover,.searchInput:hover,.field:hover{border-color:#93c5fd;box-shadow:0 6px 18px rgba(37,99,235,.08)}.btn:active,.close:active{background:#eff6ff}.btn:focus-visible,.close:focus-visible,.field:focus-visible,.searchInput:focus-visible,.searchItem:focus-visible{outline:2px solid rgba(6,182,212,.55);outline-offset:2px}.btn:disabled{opacity:.55;cursor:not-allowed;box-shadow:none}.btn.primary:hover{background:#1d4ed8;border-color:#1d4ed8}.overlay{backdrop-filter:blur(2px)}@media (prefers-reduced-motion: reduce){*{transition:none!important;animation:none!important}}
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
        <div class="mapToolbar">
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
      </div>
      <div class="side">
        <div class="fieldRow">
          <div class="label">${TXT.lat}</div>
          <input class="field" data-field="lat" type="number" step="0.000001" value="${
            draft.lat
          }" />
        </div>
        <div class="fieldRow">
          <div class="label">${TXT.lon}</div>
          <input class="field" data-field="lon" type="number" step="0.000001" value="${
            draft.lon
          }" />
        </div>
        <div class="fieldRow">
          <div class="label">${TXT.alt}</div>
          <input class="field" data-field="alt" type="number" step="0.1" value="${
            draft.alt
          }" />
        </div>
        <div class="preview" id="preview"></div>
        <div class="status" id="status"></div>
        <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:auto;">
          <button class="btn" data-action="reset">${TXT.reset}</button>
          <button class="btn" data-action="retry">${TXT.retry}</button>
        </div>
      </div>
    </div>
  </div>
  <div class="footer">
    <button class="btn" data-action="cancel">${TXT.cancel}</button>
    <button class="btn primary" data-action="confirm">${TXT.confirm}</button>
  </div>
</div>
<script src="${LEAFLET_JS_URL}"></script>
<script src="${TIANDITU_JS_API_URL}"></script>
<script src="${TIANDITU_SERVICE_JS_URL}"></script>
<script>
(function () {
  const api = window.electronAPI || {};
  const defaultWaypoint = ${JSON.stringify(DEFAULT_WAYPOINT)};
  const vectorTileUrl = 'https://t{s}.tianditu.gov.cn/DataServer?T=vec_w&x={x}&y={y}&l={z}&tk=${TIANDITU_API_KEY}';
  const annotationTileUrl = 'https://t{s}.tianditu.gov.cn/DataServer?T=cva_w&x={x}&y={y}&l={z}&tk=${TIANDITU_API_KEY}';
  let draft = ${draftJson};
  let map = null;
  let marker = null;
  let geocoder = null;
  let loading = true;
  let searching = false;
  let suggestionLoading = false;
  let errorText = '';
  let finished = false;
  let searchSuggestions = [];
  let searchActiveIndex = -1;
  let searchDebounceTimer = null;
  let searchRequestToken = 0;

  function previewText() {
    return '${TXT.preview}' + draft.lat + '${TXT.lonSep}' + draft.lon + '${
      TXT.altSep
    }' + draft.alt + ' m';
  }

  function syncPreview() {
    const preview = document.getElementById('preview');
    const status = document.getElementById('status');
    const overlay = document.getElementById('overlay');
    if (preview) {
      preview.textContent = previewText();
    }
    if (status) {
      status.textContent = errorText || (searching ? '${TXT.searching}' : '${
      TXT.status
    }');
    }
    if (overlay) {
      overlay.textContent = loading ? '${TXT.loadingMap}' : '${TXT.clickMap}';
      overlay.classList.toggle('show', loading || !!errorText);
    }
    syncSearchUi();
  }

  function syncSearchUi() {
    const button = document.querySelector('[data-action="search"]');
    if (button instanceof HTMLButtonElement) {
      button.disabled = loading || searching;
      button.textContent = searching ? '${TXT.searching}' : '${
      TXT.searchButton
    }';
    }
    renderSearchDropdown();
  }

  function syncFields() {
    const lat = document.querySelector('[data-field="lat"]');
    const lon = document.querySelector('[data-field="lon"]');
    const alt = document.querySelector('[data-field="alt"]');
    if (lat instanceof HTMLInputElement) lat.value = draft.lat;
    if (lon instanceof HTMLInputElement) lon.value = draft.lon;
    if (alt instanceof HTMLInputElement) alt.value = draft.alt;
    syncPreview();
  }

  function parseLatLng() {
    const lat = Number(draft.lat);
    const lon = Number(draft.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
      return null;
    }
    return [lat, lon];
  }

  function ensureMarker(latLng, recenter) {
    if (!map || !window.L || !latLng) {
      return;
    }
    if (!marker) {
      marker = window.L.circleMarker(latLng, {
        radius: 12,
        color: '#ffffff',
        weight: 3,
        fillColor: '#ff4d4f',
        fillOpacity: 1
      }).addTo(map);
    } else {
      marker.setLatLng(latLng);
    }
    if (recenter) {
      map.setView(latLng, map.getZoom(), { animate: false });
    }
  }

  function updateFromPoint(lat, lon, recenter) {
    draft.lat = Number(lat).toFixed(6);
    draft.lon = Number(lon).toFixed(6);
    syncFields();
    ensureMarker([Number(draft.lat), Number(draft.lon)], recenter);
  }

  function buildSearchAdapter() {
    const fallbackLat = Number(draft.lat) || defaultWaypoint.lat;
    const fallbackLon = Number(draft.lon) || defaultWaypoint.lon;
    return {
      getZoom: function () {
        return map ? Math.round(map.getZoom() || 12) : 12;
      },
      getBounds: function () {
        const bounds = map ? map.getBounds() : null;
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
    updateFromPoint(item.lat, item.lon, false);
    if (map) {
      map.setView([item.lat, item.lon], Math.max(map.getZoom() || 12, 14), { animate: false });
    }
    errorText = '';
    clearSuggestions();
    syncPreview();
    return true;
  }

  // [UAV-IDE MODIFY] Add lightweight place search so a searched result can directly update the current waypoint.
  async function searchLocation() {
    const input = document.querySelector('[data-role="map-search-input"]');
    const keyword = input instanceof HTMLInputElement ? input.value.trim() : '';
    if (!keyword) {
      errorText = '${TXT.emptySearch}';
      syncPreview();
      return;
    }
    searching = true;
    errorText = '';
    syncPreview();
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
      updateFromPoint(lat, lon, false);
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
      syncPreview();
    }
  }

  // [UAV-IDE MODIFY] Leaflet is used here instead of TianDiTu's JS map object to keep desktop dialog sizing stable.
  function initMap(forceRecreate) {
    loading = true;
    errorText = '';
    syncPreview();
    try {
      if (!window.L) {
        throw new Error('${TXT.scriptFailed}');
      }
      const latLng = parseLatLng() || [defaultWaypoint.lat, defaultWaypoint.lon];
      if (map && forceRecreate) {
        map.remove();
        map = null;
        marker = null;
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
          updateFromPoint(event.latlng.lat, event.latlng.lng, false);
        });
      }
      map.setView(latLng, map.getZoom() || 12, { animate: false });
      ensureMarker(latLng, false);
      window.setTimeout(function () {
        if (map) {
          map.invalidateSize(false);
          ensureMarker(parseLatLng() || latLng, false);
        }
      }, 0);
      window.setTimeout(function () {
        if (map) {
          map.invalidateSize(false);
          ensureMarker(parseLatLng() || latLng, false);
        }
      }, 120);
      loading = false;
      errorText = '';
      syncPreview();
    } catch (error) {
      loading = false;
      errorText = String(error && error.message ? error.message : error);
      syncPreview();
    }
  }

  function serializeWaypoint() {
    const lat = Number(draft.lat);
    const lon = Number(draft.lon);
    const alt =
      draft.alt === '' || draft.alt === null || draft.alt === undefined
        ? defaultWaypoint.alt
        : Number(draft.alt);
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || !Number.isFinite(alt)) {
      throw new Error('${TXT.invalidCoords}');
    }
    return JSON.stringify({
      lat: Number(lat.toFixed(6)),
      lon: Number(lon.toFixed(6)),
      alt: Number(alt.toFixed(2))
    });
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
      const payload = serializeWaypoint();
      const result = api.confirmWaypoint ? api.confirmWaypoint(payload) : Promise.resolve(payload);
      Promise.resolve(result)
        .then(function () {
          finish(payload);
        })
        .catch(function (err) {
          errorText = String(err && err.message ? err.message : err);
          syncPreview();
        });
    } catch (error) {
      errorText = String(error && error.message ? error.message : error);
      syncPreview();
    }
  }

  function cancel() {
    if (api.cancelWaypoint) {
      Promise.resolve(api.cancelWaypoint()).finally(function () {
        finish(null);
      });
    } else {
      finish(null);
    }
  }

  function reset() {
    draft = {
      lat: String(defaultWaypoint.lat),
      lon: String(defaultWaypoint.lon),
      alt: String(defaultWaypoint.alt)
    };
    syncFields();
    const latLng = parseLatLng();
    if (latLng) {
      ensureMarker(latLng, true);
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
    if (action === 'confirm') {
      confirm();
    } else if (action === 'cancel') {
      cancel();
    } else if (action === 'reset') {
      reset();
    } else if (action === 'retry') {
      initMap(true);
    } else if (action === 'search') {
      searchLocation();
    } else if (action === 'pick-suggestion') {
      const index = Number(actionEl.getAttribute('data-index') || '-1');
      applySuggestion(index);
    }
  });

  document.querySelector('.shell').addEventListener('input', function (event) {
    const target = event.target instanceof HTMLInputElement ? event.target : null;
    if (!target) {
      return;
    }
    const field = target.getAttribute('data-field');
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
    if (!field) {
      return;
    }
    draft[field] = target.value;
    syncPreview();
    const latLng = parseLatLng();
    if (latLng) {
      ensureMarker(latLng, false);
    }
  });

  window.addEventListener('resize', function () {
    if (map) {
      map.invalidateSize(false);
      const latLng = parseLatLng();
      if (latLng) {
        ensureMarker(latLng, false);
      }
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

  syncFields();
  window.addEventListener('load', function () {
    initMap(false);
  }, { once: true });
})();
</script>
</body>
</html>`;
  }

  private _parseWaypoint(raw?: string): WaypointDraft | null {
    if (!raw) {
      return null;
    }

    try {
      const value = JSON.parse(raw);
      const lat = Number(value.lat);
      const lon = Number(value.lon);
      const alt =
        value.alt === undefined || value.alt === null || value.alt === ''
          ? DEFAULT_WAYPOINT.alt
          : Number(value.alt);

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
    } catch (error) {
      return null;
    }
  }

  private _defaultWaypoint(): WaypointDraft {
    return {
      lat: String(DEFAULT_WAYPOINT.lat),
      lon: String(DEFAULT_WAYPOINT.lon),
      alt: String(DEFAULT_WAYPOINT.alt)
    };
  }

  private _registerHandlers() {
    const confirmHandler = (
      _event: Electron.IpcMainEvent,
      waypoint: string
    ): string => {
      this._finish(waypoint);
      return waypoint;
    };

    const cancelHandler = (_event: Electron.IpcMainEvent): null => {
      this._finish(null);
      return null;
    };

    this._evm.registerSyncEventHandler(
      EventTypeMain.WaypointPickerConfirm,
      confirmHandler
    );
    this._evm.registerSyncEventHandler(
      EventTypeMain.WaypointPickerCancel,
      cancelHandler
    );

    this._window.window.on('closed', () => {
      this._evm.unregisterSyncEventHandler(
        EventTypeMain.WaypointPickerConfirm,
        confirmHandler
      );
      this._evm.unregisterSyncEventHandler(
        EventTypeMain.WaypointPickerCancel,
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
  private _draft: WaypointDraft;
  private _evm = new EventManager();
  private _result: Promise<string | null>;
  private _resolve: (value: string | null) => void = () => {};
  private _settled = false;
}

export namespace WaypointPickerDialog {
  export interface IOptions {
    isDarkTheme: boolean;
    parent?: BrowserWindow;
    initialValue?: string;
  }
}
