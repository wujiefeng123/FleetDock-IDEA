// [UAV-IDE MODIFY] Exercise the main-area real-device workbench against a
// minimal JupyterLab shell contract without requiring a running server.

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { app, BrowserWindow } = require('electron');

app.whenReady().then(async () => {
  const window = new BrowserWindow({
    show: false,
    webPreferences: { contextIsolation: false, nodeIntegration: true }
  });
  await window.loadURL('data:text/html,<html><body></body></html>');
  const source = fs.readFileSync(
    path.join(
      __dirname,
      '..',
      'src',
      'assets',
      'skylab-real-device-debugger.js'
    ),
    'utf8'
  );
  await window.webContents.insertCSS(
    fs.readFileSync(
      path.join(
        __dirname,
        '..',
        'src',
        'assets',
        'skylab-real-device-debugger.css'
      ),
      'utf8'
    )
  );
  const result = await window.webContents.executeJavaScript(`
    (() => {
      const widgets = [];
      const widget = {
        id: 'mock-launcher',
        node: document.createElement('div'),
        title: {},
        isDisposed: false,
        disposed: { connect: callback => { widget.onDisposed = callback; } },
        close: () => {
          widget.isDisposed = true;
          widget.onDisposed?.();
        }
      };
      const launcherContent = document.createElement('div');
      launcherContent.className = 'jp-Launcher';
      widget.node.appendChild(launcherContent);
      document.body.appendChild(widget.node);
      window.jupyterapp = {
        commands: {
          execute: async command => {
            if (command !== 'launcher:create') throw new Error(command);
            widgets.push(widget);
            return widget;
          }
        },
        shell: {
          widgets: () => widgets.values(),
          activateById: id => { window.__activatedWidgetId = id; }
        }
      };
      window.electronAPI = {
        getFleetDockHudSnapshot: async () => ({
          ok: true,
          online: true,
          devices: [{
            deviceId: 'px4-real-1',
            alias: 'drone',
            connected: false,
            telemetryAgeMs: 43781,
            attitude: { roll: 0, pitch: 0, yaw: 0 },
            global: { lat: 0, lon: 0, relativeAltitudeM: 0 },
            velocity: { x: 0, y: 0, z: 0 },
            health: { globalPositionOk: false },
            link: { telemetryAgeMs: 43781, messageRateHz: 0 }
          }],
          timeline: []
        })
      };
      ${source}
      return window.jlabDesktopRealDeviceDebugger
        .open({
          id: 'px4-real-1',
          name: 'PX4 Real',
          source: 'real',
          runtimeKind: 'physical',
          fleetdock: { params: { endpoint: 'serial://COM3:57600' } }
        })
        .then(() => {
          const metrics = {};
          widget.node.querySelectorAll('.jld-rd-metric').forEach(metric => {
            metrics[metric.querySelector('span')?.textContent || ''] =
              metric.querySelector('strong')?.textContent || '';
          });
          return {
            activated: window.__activatedWidgetId,
            title: widget.title.label,
            hasWorkbench: !!widget.node.querySelector('.jld-real-debugger'),
            launcherHidden:
              launcherContent.dataset.jldRealDebugBackground === 'true' &&
              launcherContent.getAttribute('aria-hidden') === 'true',
            launcherDisplay: getComputedStyle(launcherContent).display,
            diagnosis: widget.node.querySelector('.jld-rd-diagnosis h2')?.textContent || '',
            roll: metrics.Roll,
            telemetryAge: metrics['遥测年龄'],
            vehicleState: metrics['状态']
          };
        });
    })()
  `);
  assert.strictEqual(result.activated, 'mock-launcher');
  assert.strictEqual(result.title, '真机调试 · PX4 Real');
  assert.strictEqual(result.hasWorkbench, true);
  assert.strictEqual(result.launcherHidden, true);
  assert.strictEqual(result.launcherDisplay, 'none');
  assert.match(result.diagnosis, /当前未连接真机/);
  assert.strictEqual(result.roll, '-');
  assert.strictEqual(result.telemetryAge, '-');
  assert.strictEqual(result.vehicleState, '-');
  window.close();
  console.log('Real-device debugger page checks passed.');
  app.quit();
});
