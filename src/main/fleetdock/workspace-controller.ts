// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import * as fs from 'fs';
import * as path from 'path';
import { ApplicationWorkspaceService } from '../application/application-workspace-service';
import type { ICreateApplicationRequest } from '../application/application-types';
import { readApplicationConfig } from '../config/application';
import type { EventManager } from '../eventmanager';
import { EventTypeMain } from '../eventtypes';
import { getBundledPythonPath } from '../utils';
import type { DeviceRegistry } from '../devices/registry';

export interface IFleetDockWorkspaceControllerOptions {
  eventManager: EventManager;
  deviceRegistry: DeviceRegistry;
  getWorkingDirectory: () => string;
  getSessionConfig: () =>
    | import('../config/sessionconfig').SessionConfig
    | undefined;
  getWorkspaceSettings: () => import('../config/settings').WorkspaceSettings;
  setWorkspaceSettings: (
    settings: import('../config/settings').WorkspaceSettings
  ) => void;
  getServerPythonPath: () => string | undefined;
  restartSessionInPythonEnvironment: (pythonPath: string) => void;
  notifySessionConfigChanged: () => void;
  openSession: (
    sessionConfig: import('../config/sessionconfig').SessionConfig
  ) => Promise<void>;
  isLabSender: (sender: Electron.WebContents) => boolean;
  isWelcomeSender: (sender: Electron.WebContents) => boolean;
  isApplicationSettingsSender: (sender: Electron.WebContents) => boolean;
}

/**
 * Owns workspace metadata IPC so application behavior stays out of
 * SessionWindow.
 */
export class FleetDockWorkspaceController {
  constructor(private _options: IFleetDockWorkspaceControllerOptions) {}

  register(): void {
    this._options.eventManager.registerSyncEventHandler(
      EventTypeMain.CreateNewApplication,
      async (event, application: ICreateApplicationRequest) => {
        if (!this._options.isWelcomeSender(event.sender)) {
          throw new Error('Application creation sender is not authorized.');
        }
        const result = this._workspaceService.createApplication(
          application,
          await this._options.deviceRegistry.listDevices()
        );
        this._options.setWorkspaceSettings(result.workspaceSettings);
        await this._options.openSession(result.sessionConfig);
        return { workingDirectory: result.workingDirectory };
      }
    );

    this._options.eventManager.registerSyncEventHandler(
      EventTypeMain.SaveApplicationSettings,
      async (event, application: ICreateApplicationRequest) => {
        if (
          !this._options.isLabSender(event.sender) &&
          !this._options.isApplicationSettingsSender(event.sender)
        ) {
          throw new Error('Application settings sender is not authorized.');
        }

        const sessionConfig = this._options.getSessionConfig();
        const workingDirectory = this._options.getWorkingDirectory();
        if (!sessionConfig || !workingDirectory || sessionConfig.isRemote) {
          throw new Error(
            'Application settings are only available for local workspaces.'
          );
        }

        const result = this._workspaceService.saveApplicationSettings({
          application,
          workingDirectory,
          workspaceSettings: this._options.getWorkspaceSettings(),
          sessionConfig,
          devices: await this._options.deviceRegistry.listDevices(),
          serverPythonPath: this._options.getServerPythonPath(),
          environmentMapSignature: environmentMapSignature,
          restartServerInPythonEnvironment: this._options
            .restartSessionInPythonEnvironment,
          emitSessionConfigChanged: this._options.notifySessionConfigChanged
        });
        this._options.setWorkspaceSettings(result.workspaceSettings);
        return true;
      }
    );

    this._options.eventManager.registerSyncEventHandler(
      EventTypeMain.SkyLabWorkspaceGetContext,
      event => {
        if (!this._options.isLabSender(event.sender)) {
          return null;
        }
        return this._getWorkspaceContext();
      }
    );
  }

  private _getWorkspaceContext() {
    const workingDirectory = this._options.getWorkingDirectory();
    const applicationConfig = workingDirectory
      ? readApplicationConfig(workingDirectory)
      : undefined;
    const entryFile = applicationConfig?.entryFile || 'main.py';
    const entryFilePath = workingDirectory
      ? path.join(workingDirectory, entryFile)
      : '';
    const requirementsPath = workingDirectory
      ? path.join(workingDirectory, 'requirements.txt')
      : '';

    return {
      appCode:
        applicationConfig?.appCode ||
        applicationConfig?.name ||
        (workingDirectory ? path.basename(workingDirectory) : ''),
      name:
        applicationConfig?.name ||
        (workingDirectory ? path.basename(workingDirectory) : ''),
      version: applicationConfig?.version || '',
      description: applicationConfig?.description || '',
      entryFile,
      maxRunSeconds: applicationConfig?.maxRunSeconds || 300,
      changeLog: applicationConfig?.changeLog || '',
      runtimeLabel: applicationConfig?.runtimeLabel || 'Python',
      pythonPath: getBundledPythonPath(),
      workingDirectory,
      entryFileExists: entryFilePath ? fs.existsSync(entryFilePath) : false,
      requirementsExists: requirementsPath
        ? fs.existsSync(requirementsPath)
        : false,
      simulator: applicationConfig?.simulator || {},
      aiGateway: applicationConfig?.aiGateway || {},
      groundStation: applicationConfig?.groundStation || {},
      envVars: Array.isArray(applicationConfig?.envVars)
        ? applicationConfig.envVars
        : []
    };
  }

  private _workspaceService = new ApplicationWorkspaceService();
}

function environmentMapSignature(
  envVars: { [key: string]: string } = {}
): string {
  return JSON.stringify(
    Object.keys(envVars)
      .sort()
      .map(key => [key, String(envVars[key] ?? '')])
  );
}
