// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import {
  createDefaultApplicationConfig,
  readApplicationConfig
} from '../config/application';
import { SettingType, WorkspaceSettings } from '../config/settings';
import {
  buildFleetDockServerEnv,
  ensureFleetDockRuntimeArtifacts
} from '../fleetdock/runtime-generator';
import { cleanupFleetDockMavsdkServers } from '../fleetdock/mavsdk-cleanup';
import { DeviceRegistry } from './registry';

export interface IDeviceEnvSyncOptions {
  workingDirectory?: string;
  registry: DeviceRegistry;
  workspaceSettings: WorkspaceSettings;
  cleanupStaleMavsdkServers?: boolean;
}

// [UAV-IDE MODIFY] Generate FleetDock runtime artifacts for app device bindings without mirroring devices into user env vars.
export async function syncManagedDroneEnvVars(options: IDeviceEnvSyncOptions) {
  if (!options.workingDirectory) {
    return;
  }

  const devices = await options.registry.listDevices();
  const config =
    readApplicationConfig(options.workingDirectory) ||
    createDefaultApplicationConfig(options.workingDirectory);
  const runtime = ensureFleetDockRuntimeArtifacts({
    workingDirectory: options.workingDirectory,
    config,
    devices
  });
  if (runtime.bindingErrors.length) {
    // [UAV-IDE MODIFY] Never silently skip deleted or conflicting application bindings.
    console.error(
      'FleetDock device binding errors:',
      runtime.bindingErrors.join('; ')
    );
  }
  if (options.cleanupStaleMavsdkServers) {
    cleanupFleetDockMavsdkServers(runtime.mavsdkEndpoints);
  }

  options.workspaceSettings.setValue(
    SettingType.serverEnvVars,
    buildFleetDockServerEnv({
      workingDirectory: options.workingDirectory,
      config,
      runtimeEnv: runtime.env
    })
  );
  options.workspaceSettings.save();
}
