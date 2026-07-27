// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import * as http from 'http';
import type { EventManager } from '../eventmanager';
import { EventTypeMain } from './eventtypes';
import { SettingType, WorkspaceSettings } from '../config/settings';
import { FleetDockHudWindowController } from './hud-window-controller';

interface IHudRuntimeEndpoint {
  host: string;
  basePort: number;
  token: string;
}

interface IDebugCommand {
  deviceId?: string;
  alias?: string;
  command?: string;
  params?: { [key: string]: unknown };
}

export interface IFleetDockHudRuntimeControllerOptions {
  eventManager: EventManager;
  hudWindows: FleetDockHudWindowController;
  getWorkspaceSettings: () => WorkspaceSettings;
  isLabSender: (sender: Electron.WebContents) => boolean;
}

/**
 * Bridges the command center to workspace-owned Python HUD endpoints. It does
 * not create a second device connection.
 */
export class FleetDockHudRuntimeController {
  constructor(private _options: IFleetDockHudRuntimeControllerOptions) {}

  register(): void {
    const eventManager = this._options.eventManager;

    eventManager.registerSyncEventHandler(
      EventTypeMain.FleetDockHudSnapshot,
      event => {
        if (
          !this._options.isLabSender(event.sender) &&
          !this._options.hudWindows.ownsWebContents(event.sender)
        ) {
          return { ok: false, online: false, devices: [] };
        }
        return this._requestSnapshot();
      }
    );

    eventManager.registerSyncEventHandler(
      EventTypeMain.FleetDockHudWindowOpen,
      (event, devices: unknown[]) => {
        if (!this._options.isLabSender(event.sender)) {
          return { ok: false };
        }
        this._options.hudWindows.openDevices(devices || []);
        return { ok: true };
      }
    );

    eventManager.registerSyncEventHandler(
      EventTypeMain.FleetDockDebugCommand,
      (event, request: IDebugCommand) => {
        if (!this._options.isLabSender(event.sender)) {
          return {
            ok: false,
            code: 'UNAUTHORIZED',
            message: 'Unauthorized real-device debugger sender.'
          };
        }
        return this._requestDebugCommand(request || {});
      }
    );
  }

  private async _requestSnapshot(): Promise<any> {
    const endpoint = this._runtimeEndpoint();
    if (!endpoint) {
      return {
        ok: false,
        online: false,
        devices: [],
        message: 'FleetDock HUD is not configured.'
      };
    }

    const results = await Promise.all(
      Array.from({ length: 32 }, (_item, index) =>
        this._requestPort(endpoint, endpoint.basePort + index)
      )
    );
    const onlineResults = results.filter(result => result?.online);
    if (!onlineResults.length) {
      return {
        ok: false,
        online: false,
        devices: [],
        message: 'FleetDock HUD runtime is offline.'
      };
    }

    const devices = new Map<string, any>();
    const commandHistory = new Map<string, any>();
    const timeline = new Map<string, any>();
    for (const result of onlineResults) {
      for (const device of result.devices || []) {
        const key = String(device.deviceId || device.alias || '');
        const previous = devices.get(key);
        if (!previous || isBetterDeviceSnapshot(device, previous)) {
          devices.set(key, device);
        }
      }
      for (const command of result.commandHistory || []) {
        const key = String(
          command.commandId ||
            `${command.deviceAlias}:${command.command}:${command.completedAt}`
        );
        commandHistory.set(key, command);
      }
      for (const item of result.timeline || []) {
        const key = String(
          item.id || `${item.deviceAlias}:${item.type}:${item.timestamp}`
        );
        timeline.set(key, item);
      }
    }

    return {
      ok: true,
      online: true,
      devices: Array.from(devices.values()),
      commandHistory: Array.from(commandHistory.values())
        .sort(
          (left, right) =>
            Date.parse(left.completedAt || left.startedAt || '') -
            Date.parse(right.completedAt || right.startedAt || '')
        )
        .slice(-100),
      timeline: Array.from(timeline.values())
        .sort(
          (left, right) =>
            Date.parse(left.timestamp || '') - Date.parse(right.timestamp || '')
        )
        .slice(-240),
      updatedAt: new Date().toISOString()
    };
  }

  private async _requestDebugCommand(request: IDebugCommand): Promise<any> {
    const endpoint = this._runtimeEndpoint();
    if (!endpoint) {
      return {
        ok: false,
        code: 'RUNTIME_NOT_CONFIGURED',
        message: 'FleetDock 调试运行时尚未配置。'
      };
    }

    const snapshots = await Promise.all(
      Array.from({ length: 32 }, (_item, index) =>
        this._requestPort(endpoint, endpoint.basePort + index)
      )
    );
    const deviceId = String(request.deviceId || '');
    const alias = String(request.alias || '');
    const candidates = snapshots
      .filter(snapshot => snapshot?.online && snapshot?.__bridgePort)
      .map(snapshot => ({
        snapshot,
        device: (snapshot.devices || []).find(
          (device: any) =>
            (deviceId && String(device?.deviceId || '') === deviceId) ||
            (alias && String(device?.alias || '') === alias)
        )
      }))
      .filter(candidate => candidate.device)
      .sort(
        (left, right) =>
          Number(right.device?.connected === true) -
          Number(left.device?.connected === true)
      );
    if (!candidates.length) {
      return {
        ok: false,
        code: 'DEVICE_RUNTIME_OFFLINE',
        message:
          '当前应用运行时中未找到该设备。请先绑定设备并启动一个 FleetDock 内核。'
      };
    }

    return this._postDebugCommand(
      endpoint,
      Number(candidates[0].snapshot.__bridgePort),
      request
    );
  }

  private _requestPort(
    endpoint: IHudRuntimeEndpoint,
    port: number
  ): Promise<any> {
    return new Promise(resolve => {
      const request = http.request(
        {
          host: endpoint.host,
          port,
          path: '/snapshot',
          method: 'GET',
          timeout: 800,
          headers: { Authorization: `Bearer ${endpoint.token}` }
        },
        response => {
          let body = '';
          response.setEncoding('utf8');
          response.on('data', chunk => {
            body += chunk;
          });
          response.on('end', () => {
            try {
              resolve({ ...JSON.parse(body || '{}'), __bridgePort: port });
            } catch (_error) {
              resolve({ ok: false, online: false, devices: [] });
            }
          });
        }
      );
      request.on('timeout', () => {
        request.destroy();
        resolve({ ok: false, online: false, devices: [] });
      });
      request.on('error', () => {
        resolve({ ok: false, online: false, devices: [] });
      });
      request.end();
    });
  }

  private _postDebugCommand(
    endpoint: IHudRuntimeEndpoint,
    port: number,
    payload: IDebugCommand
  ): Promise<any> {
    return new Promise(resolve => {
      const body = JSON.stringify(payload);
      const request = http.request(
        {
          host: endpoint.host,
          port,
          path: '/diagnostic-command',
          method: 'POST',
          timeout: 15000,
          headers: {
            Authorization: `Bearer ${endpoint.token}`,
            'Content-Type': 'application/json; charset=utf-8',
            'Content-Length': Buffer.byteLength(body)
          }
        },
        response => {
          let responseBody = '';
          response.setEncoding('utf8');
          response.on('data', chunk => {
            responseBody += chunk;
          });
          response.on('end', () => {
            try {
              resolve(JSON.parse(responseBody || '{}'));
            } catch (_error) {
              resolve({
                ok: false,
                code: 'INVALID_RUNTIME_RESPONSE',
                message: 'FleetDock 调试运行时返回了无效响应。'
              });
            }
          });
        }
      );
      request.on('timeout', () => {
        request.destroy();
        resolve({
          ok: false,
          code: 'DEBUG_COMMAND_TIMEOUT',
          message: '调试操作超时，请检查遥测链路和飞控状态。'
        });
      });
      request.on('error', error => {
        resolve({
          ok: false,
          code: 'DEBUG_RUNTIME_OFFLINE',
          message: String(error?.message || 'FleetDock 调试运行时离线。')
        });
      });
      request.end(body);
    });
  }

  private _runtimeEndpoint(): IHudRuntimeEndpoint | null {
    const envVars =
      this._options
        .getWorkspaceSettings()
        .getValue(SettingType.serverEnvVars) || {};
    const host = String(envVars.FLEETDOCK_HUD_HOST || '127.0.0.1');
    const basePort = Number(envVars.FLEETDOCK_HUD_PORT || 0);
    const token = String(envVars.FLEETDOCK_HUD_TOKEN || '');
    return basePort && token ? { host, basePort, token } : null;
  }
}

function isBetterDeviceSnapshot(current: any, previous: any): boolean {
  const quality = (candidate: any) => {
    const sensors: any[] = Array.isArray(candidate?.sensors)
      ? candidate.sensors
      : [];
    return {
      sampled: sensors.filter(sensor => sensor?.sample != null).length,
      subscribed: sensors.filter(sensor => sensor?.subscribed).length,
      connected: candidate?.connected === true ? 1 : 0,
      updatedAt: Math.max(
        Date.parse(candidate?.updatedAt || '') || 0,
        ...sensors.map(
          sensor => Date.parse(sensor?.sampleReceivedAt || '') || 0
        )
      )
    };
  };
  const left = quality(current);
  const right = quality(previous);
  return (
    left.connected > right.connected ||
    (left.connected === right.connected && left.sampled > right.sampled) ||
    (left.connected === right.connected &&
      left.sampled === right.sampled &&
      left.subscribed > right.subscribed) ||
    (left.connected === right.connected &&
      left.sampled === right.sampled &&
      left.subscribed === right.subscribed &&
      left.updatedAt >= right.updatedAt)
  );
}
