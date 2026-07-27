// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import { BrowserWindow, clipboard, dialog } from 'electron';
import * as fs from 'fs';
import * as path from 'path';
import { WorkspaceSettings } from '../config/settings';
import { DeviceManagerDialog } from '../devicemanagerdialog/devicemanagerdialog';
import { EventManager } from '../eventmanager';
import { EventTypeMain } from '../fleetdock/eventtypes';
import { DroneDevice, PhysicalDeviceConnection } from './types';
import { DeviceRegistry } from './registry';
import { DeviceStore } from './storage/device-store';
import { syncManagedDroneEnvVars } from './device-env-sync';
import { normalizePhysicalDeviceConnection } from './connection-uri';
import { probePhysicalConnection } from './physical-connection-probe';
import { discoverWindowsSerialPorts } from './serial-port-discovery';
import { bindDeviceToApplication } from '../application/application-device-binding-service';

export interface IDeviceManagerControllerOptions {
  eventManager: EventManager;
  parent: BrowserWindow;
  isDarkTheme: boolean;
  getLabWebContents: () => Electron.WebContents | undefined;
  getWelcomeWebContents?: () => Electron.WebContents | undefined;
  getApplicationSettingsWebContents?: () => Electron.WebContents | undefined;
  getWorkingDirectory: () => string | undefined;
  getWorkspaceSettings: () => WorkspaceSettings;
}

// [UAV-IDE MODIFY] Keep device management IPC and dialog ownership outside SessionWindow.
export class DeviceManagerController {
  constructor(private _options: IDeviceManagerControllerOptions) {}

  register() {
    const evm = this._options.eventManager;

    evm.registerEventHandler(EventTypeMain.OpenDeviceManager, event => {
      if (event.sender !== this._options.getLabWebContents()) {
        return;
      }

      // [UAV-IDE MODIFY] Open device management from the top menu as a standalone global UAV dialog.
      this._showDeviceManagerDialog();
    });

    evm.registerSyncEventHandler(
      EventTypeMain.DeviceManagerLoad,
      async event => {
        if (!this.isUavManagerSender(event.sender)) {
          return [];
        }

        // [UAV-IDE MODIFY] Keep drone device listing shared by dialogs and the in-page JupyterLab extension.
        return this._managedDeviceRegistry.listDevices();
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.DeviceManagerSave,
      async (event, device: Partial<DroneDevice>) => {
        if (!this.isUavManagerSender(event.sender)) {
          return null;
        }

        // [UAV-IDE MODIFY] Reject malformed physical links before persistence; the store regenerates the canonical endpoint.
        if (device?.runtimeKind === 'physical' || device?.source === 'real') {
          normalizePhysicalDeviceConnection(device);
        }

        // [UAV-IDE MODIFY] Save managed drones through the shared registry and regenerate active app runtime bindings.
        const saved = device?.id
          ? await this._managedDeviceRegistry.updateDevice(device)
          : await this._managedDeviceRegistry.addDevice(device);
        await syncManagedDroneEnvVars({
          workingDirectory: this._options.getWorkingDirectory(),
          registry: this._managedDeviceRegistry,
          workspaceSettings: this._options.getWorkspaceSettings()
        });
        return saved;
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.DeviceManagerDelete,
      async (event, deviceId: string) => {
        if (!this.isUavManagerSender(event.sender)) {
          return false;
        }

        // [UAV-IDE MODIFY] Delete managed drones through the shared registry and keep workspace env vars aligned.
        // [UAV-IDE MODIFY] Return a truthful deletion result so inline panels can refresh accurately.
        const removed = await this._managedDeviceRegistry.removeDevice(
          deviceId
        );
        if (!removed) {
          return false;
        }
        await syncManagedDroneEnvVars({
          workingDirectory: this._options.getWorkingDirectory(),
          registry: this._managedDeviceRegistry,
          workspaceSettings: this._options.getWorkspaceSettings()
        });
        return true;
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.DeviceManagerProbe,
      async (event, connection: PhysicalDeviceConnection) => {
        if (!this.isUavManagerSender(event.sender)) {
          return null;
        }
        return probePhysicalConnection(connection);
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.DeviceManagerSerialPorts,
      async event => {
        if (!this.isUavManagerSender(event.sender)) {
          return { ports: [], error: 'Unauthorized device-manager sender.' };
        }
        return discoverWindowsSerialPorts();
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.DeviceManagerBind,
      async (
        event,
        request: {
          deviceId?: string;
          alias?: string;
        }
      ) => {
        if (!this.isUavManagerSender(event.sender)) {
          return null;
        }
        const workingDirectory = this._options.getWorkingDirectory();
        const devices = await this._managedDeviceRegistry.listDevices();
        const device = devices.find(item => item.id === request?.deviceId);
        if (!device) {
          throw new Error('要绑定的设备不存在，请刷新设备库后重试。');
        }
        const result = bindDeviceToApplication({
          workingDirectory: workingDirectory || '',
          deviceId: device.id,
          alias: request?.alias || device.name
        });
        await syncManagedDroneEnvVars({
          workingDirectory,
          registry: this._managedDeviceRegistry,
          workspaceSettings: this._options.getWorkspaceSettings()
        });
        return result;
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.DeviceManagerCopyProbeReport,
      (event, report: unknown) => {
        if (!this.isUavManagerSender(event.sender)) {
          return false;
        }
        clipboard.writeText(formatProbeReport(report));
        return true;
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.DeviceManagerExportProbeReport,
      async (event, report: unknown) => {
        if (!this.isUavManagerSender(event.sender)) {
          return null;
        }
        const defaultDirectory =
          this._options.getWorkingDirectory() || process.cwd();
        const result = await dialog.showSaveDialog(this._options.parent, {
          title: '导出 PX4 连接测试报告',
          defaultPath: path.join(
            defaultDirectory,
            `px4-connection-report-${Date.now()}.json`
          ),
          filters: [{ name: 'JSON', extensions: ['json'] }]
        });
        if (result.canceled || !result.filePath) {
          return null;
        }
        fs.writeFileSync(result.filePath, formatProbeReport(report), 'utf8');
        return result.filePath;
      }
    );
  }

  get dialogWebContents(): Electron.WebContents | undefined {
    return this._deviceManagerDialog?.window.window.webContents;
  }

  get registry(): DeviceRegistry {
    return this._managedDeviceRegistry;
  }

  dispose() {
    // [UAV-IDE MODIFY] Keep the device manager dialog tied to the parent session window lifetime.
    if (this._deviceManagerDialog) {
      this._deviceManagerDialog.window.window.close();
      this._deviceManagerDialog = null;
    }
  }

  isUavManagerSender(sender: Electron.WebContents): boolean {
    // [UAV-IDE MODIFY] Accept UAV manager IPC from the in-page command center and active FleetDock dialogs only.
    return (
      sender === this._options.getLabWebContents() ||
      sender === this._options.getWelcomeWebContents?.() ||
      sender === this._options.getApplicationSettingsWebContents?.() ||
      sender === this.dialogWebContents
    );
  }

  private _showDeviceManagerDialog() {
    if (this._deviceManagerDialog) {
      this._deviceManagerDialog.window.window.focus();
      return;
    }

    // [UAV-IDE MODIFY] Keep device management isolated in its own dialog so it can evolve without disturbing app settings flows.
    const dialog = new DeviceManagerDialog({
      isDarkTheme: this._options.isDarkTheme,
      parent: this._options.parent,
      workingDirectory: this._options.getWorkingDirectory()
    });

    this._deviceManagerDialog = dialog;
    dialog.window.window.on('closed', () => {
      this._deviceManagerDialog = null;
    });

    dialog.load();
  }

  // [UAV-IDE MODIFY] Keep managed drone persistence centralized so dialogs and the in-page JupyterLab extension operate on the same device registry.
  private _managedDeviceRegistry = new DeviceRegistry(new DeviceStore());
  private _deviceManagerDialog: DeviceManagerDialog | null = null;
}

function formatProbeReport(report: unknown): string {
  const value =
    report && typeof report === 'object'
      ? report
      : { reportVersion: 1, ok: false, message: 'Invalid probe report.' };
  return `${JSON.stringify(value, null, 2)}\n`;
}
