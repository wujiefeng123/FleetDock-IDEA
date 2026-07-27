// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import * as fs from 'fs';
import * as path from 'path';
import {
  createDefaultApplicationConfig,
  normalizeApplicationConfig,
  readApplicationConfig,
  writeApplicationConfig
} from '../config/application';
import { SettingType, WorkspaceSettings } from '../config/settings';
import { DroneDevice } from '../devices/types';
import { SessionConfig } from '../config/sessionconfig';
import {
  buildFleetDockServerEnv,
  ensureFleetDockRuntimeArtifacts
} from '../fleetdock/runtime-generator';
import { getBundledPythonPath } from '../utils';
import { ICreateApplicationRequest } from './application-types';
import { ensureApplicationProjectFiles } from './project-scaffold';

export interface ICreateApplicationResult {
  sessionConfig: SessionConfig;
  workingDirectory: string;
  workspaceSettings: WorkspaceSettings;
}

export interface ISaveApplicationSettingsOptions {
  application: ICreateApplicationRequest;
  workingDirectory: string;
  workspaceSettings: WorkspaceSettings;
  sessionConfig: SessionConfig;
  devices: DroneDevice[];
  serverPythonPath?: string;
  environmentMapSignature: (env: { [key: string]: string }) => string;
  restartServerInPythonEnvironment: (pythonPath: string) => void;
  emitSessionConfigChanged: () => void;
}

export interface ISaveApplicationSettingsResult {
  workspaceSettings: WorkspaceSettings;
}

// [UAV-IDE MODIFY] Keep application workspace creation and settings persistence separate from Electron window/session orchestration.
export class ApplicationWorkspaceService {
  createApplication(
    application: ICreateApplicationRequest,
    devices: DroneDevice[] = []
  ): ICreateApplicationResult {
    const selectedDirectory = String(
      application?.workingDirectory || ''
    ).trim();
    const applicationName = String(application?.name || '').trim();
    const appCode = String(application?.appCode || '').trim();
    if (!selectedDirectory) {
      throw new Error('请选择应用保存目录。');
    }
    if (!appCode) {
      throw new Error('请填写应用标识。');
    }
    if (!applicationName) {
      throw new Error('请填写应用名称。');
    }
    if (/[\\/]/.test(applicationName)) {
      throw new Error('应用名称不能包含路径分隔符。');
    }

    const baseDirectory = path.resolve(selectedDirectory);
    const workingDirectory = path.resolve(baseDirectory, applicationName);
    if (fs.existsSync(workingDirectory)) {
      throw new Error(
        `应用 "${applicationName}" 已存在，请更换应用名称或保存目录。`
      );
    }

    if (!fs.existsSync(baseDirectory)) {
      fs.mkdirSync(baseDirectory, { recursive: true });
    }

    if (!fs.existsSync(workingDirectory)) {
      fs.mkdirSync(workingDirectory, { recursive: true });
    }

    // [UAV-IDE MODIFY] Generate the default project scaffold at creation time so every new workspace starts with a standard Python entrypoint and dependency manifest.
    ensureApplicationProjectFiles(workingDirectory);

    const defaultConfig = createDefaultApplicationConfig(workingDirectory);
    // [UAV-IDE MODIFY] Persist application profile and workspace-scoped runtime settings.
    const config = normalizeApplicationConfig(
      {
        ...defaultConfig,
        // [UAV-IDE MODIFY] Persist only the simplified creation fields and generate hidden defaults.
        appCode,
        version: '0.0.1',
        name: application.name,
        description: '',
        entryFile: 'main.py',
        maxRunSeconds:
          Number(application.maxRunSeconds) || defaultConfig.maxRunSeconds,
        changeLog: '',
        runtimeLabel: 'Python 3.12',
        pythonPath: '',
        envVars: application.envVars || [],
        aiGateway: {
          ...application.aiGateway,
          timeoutMs: Number(application.aiGateway.timeoutMs) || 15000
        },
        simulator: {
          ...defaultConfig.simulator,
          ...application.simulator
        },
        groundStation: {
          ...defaultConfig.groundStation,
          ...application.groundStation
        }
      },
      workingDirectory
    );
    writeApplicationConfig(config);

    const workspaceSettings = new WorkspaceSettings(workingDirectory);
    const runtime = ensureFleetDockRuntimeArtifacts({
      workingDirectory,
      config,
      // [UAV-IDE MODIFY] Resolve newly selected device-id bindings immediately from the global library.
      devices
    });
    workspaceSettings.setValue(
      SettingType.serverEnvVars,
      buildFleetDockServerEnv({
        workingDirectory,
        config,
        runtimeEnv: runtime.env
      })
    );
    // [UAV-IDE MODIFY] New FleetDock workspaces always use the bundled IDE Python runtime.
    const bundledPythonPath = getBundledPythonPath();
    workspaceSettings.setValue(SettingType.pythonPath, bundledPythonPath);
    workspaceSettings.save();

    const sessionConfig = SessionConfig.createLocal(
      workingDirectory,
      [],
      bundledPythonPath
    );

    return {
      sessionConfig,
      workingDirectory,
      workspaceSettings
    };
  }

  saveApplicationSettings(
    options: ISaveApplicationSettingsOptions
  ): ISaveApplicationSettingsResult {
    const {
      application,
      workingDirectory,
      workspaceSettings,
      sessionConfig,
      devices,
      environmentMapSignature,
      restartServerInPythonEnvironment,
      emitSessionConfigChanged
    } = options;
    const existingConfig =
      readApplicationConfig(workingDirectory) ||
      createDefaultApplicationConfig(workingDirectory);
    const previousPythonPath = String(
      workspaceSettings.getValue(SettingType.pythonPath) || ''
    );
    const previousServerEnvVars = workspaceSettings.getValue(
      SettingType.serverEnvVars
    ) as { [key: string]: string };

    const nextConfig = normalizeApplicationConfig(
      {
        ...existingConfig,
        ...application,
        workingDirectory,
        maxRunSeconds:
          Number(application.maxRunSeconds) || existingConfig.maxRunSeconds,
        // [UAV-IDE MODIFY] Keep application settings persistence limited to explicit env vars while device features are being rebuilt.
        envVars: application.envVars,
        aiGateway: {
          ...application.aiGateway,
          timeoutMs: Number(application.aiGateway.timeoutMs) || 15000
        },
        simulator: {
          ...existingConfig.simulator,
          ...application.simulator
        },
        groundStation: {
          ...existingConfig.groundStation,
          ...application.groundStation
        },
        createdAt: existingConfig.createdAt
      },
      workingDirectory
    );

    const nextWorkspaceSettings = new WorkspaceSettings(workingDirectory);
    // [UAV-IDE MODIFY] Persist only the bundled IDE Python path even when old app configs carry a custom value.
    const bundledPythonPath = getBundledPythonPath();
    nextConfig.pythonPath = bundledPythonPath;
    // [UAV-IDE MODIFY] Persist application profile edits back into the active workspace and mirrored workspace settings.
    writeApplicationConfig(nextConfig);
    nextWorkspaceSettings.setValue(SettingType.pythonPath, bundledPythonPath);

    const runtime = ensureFleetDockRuntimeArtifacts({
      workingDirectory,
      config: nextConfig,
      devices
    });
    const nextServerEnvVars = buildFleetDockServerEnv({
      workingDirectory,
      config: nextConfig,
      runtimeEnv: runtime.env
    });
    nextWorkspaceSettings.setValue(
      SettingType.serverEnvVars,
      nextServerEnvVars
    );

    nextWorkspaceSettings.save();
    sessionConfig.pythonPath = bundledPythonPath;
    emitSessionConfigChanged();

    // [UAV-IDE MODIFY] Compare complete server environments so stable FleetDock
    // bootstrap values do not restart Jupyter on every settings save.
    const envVarsChanged =
      environmentMapSignature(previousServerEnvVars) !==
      environmentMapSignature(nextServerEnvVars);
    const pythonPathChanged =
      String(nextConfig.pythonPath || '') !== previousPythonPath;

    if (pythonPathChanged || envVarsChanged) {
      const restartPythonPath = bundledPythonPath;
      restartServerInPythonEnvironment(restartPythonPath);
    }

    return {
      workspaceSettings: nextWorkspaceSettings
    };
  }
}
