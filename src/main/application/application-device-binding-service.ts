// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Keep real-device application binding transactional and
// separate from the global device registry UI.

import {
  createDefaultApplicationConfig,
  normalizeApplicationEnvType,
  readApplicationConfig,
  writeApplicationConfig
} from '../config/application';
import { ensurePhysicalDeviceStarterNotebook } from './physical-device-starter';

export interface IApplicationDeviceBindingResult {
  alias: string;
  deviceId: string;
  notebookPath: string;
}

export function bindDeviceToApplication(options: {
  workingDirectory: string;
  deviceId: string;
  alias: string;
}): IApplicationDeviceBindingResult {
  const workingDirectory = String(options.workingDirectory || '').trim();
  const deviceId = String(options.deviceId || '').trim();
  const alias = String(options.alias || '').trim();
  if (!workingDirectory) {
    throw new Error('当前没有可绑定设备的应用工作区。');
  }
  if (!deviceId) {
    throw new Error('请先保存设备，再绑定到当前应用。');
  }
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(alias)) {
    throw new Error('设备别名必须是有效的 Python 标识符。');
  }

  const config =
    readApplicationConfig(workingDirectory) ||
    createDefaultApplicationConfig(workingDirectory);
  const sameName = config.envVars.find(
    envVar => envVar.name.toLowerCase() === alias.toLowerCase()
  );
  if (sameName && normalizeApplicationEnvType(sameName.type) !== 'Drone') {
    throw new Error(`应用变量“${alias}”已被其他类型占用。`);
  }

  // One physical device has one owner binding inside an application.
  config.envVars = config.envVars.filter(envVar => {
    if (normalizeApplicationEnvType(envVar.type) !== 'Drone') {
      return true;
    }
    const value = String(envVar.value || envVar.defaultValue || '').trim();
    return (
      value !== deviceId && envVar.name.toLowerCase() !== alias.toLowerCase()
    );
  });
  config.envVars.push({
    type: 'Drone',
    name: alias,
    required: true,
    description: 'FleetDock IDEA 绑定的真机设备',
    defaultValue: deviceId,
    value: deviceId,
    sensitive: false,
    options: []
  });
  writeApplicationConfig(config);
  const notebookPath = ensurePhysicalDeviceStarterNotebook(workingDirectory);
  return { alias, deviceId, notebookPath };
}
