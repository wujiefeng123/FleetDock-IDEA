// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Workspace-bound application profile storage aligned with CodeAppCenter desktop-app.json.

import * as fs from 'fs';
import * as path from 'path';

import { KeyValueMap } from './settings';

export const APPLICATION_CONFIG_FILE = 'desktop-app.json';
export const APPLICATION_ENV_TYPES = [
  'Text',
  'Drone',
  'DronePoint',
  'DroneRoute'
] as const;

export type ApplicationEnvType = typeof APPLICATION_ENV_TYPES[number];

export interface IApplicationEnvVar {
  type: ApplicationEnvType | string;
  name: string;
  required?: boolean;
  description?: string;
  defaultValue?: string;
  sensitive?: boolean;
  options?: string[];
  value?: string;
}

export interface IApplicationAiGatewayConfig {
  gatewayUrl: string;
  defaultModel: string;
  routeStrategy: string;
  timeoutMs: number;
  apiKey: string;
  ak: string;
  sk: string;
}

export interface IApplicationSimulatorConfig {
  engine: string;
  worldFile: string;
  realTimeFactor: string;
  wslDistro: string;
  px4WorkingDirectory: string;
  sitlCommand: string;
  airSimExecutable: string;
  airSimArgs: string[];
  autoLaunchAirSim: boolean;
  // [UAV-IDE MODIFY] Store the IDE-owned local Project AirSim scene loader contract in application config.
  sceneFile: string;
  sceneLoaderScript: string;
}

export interface IApplicationGroundStationConfig {
  stationType: string;
  connectionUrl: string;
  autoConnect: boolean;
  executablePath: string;
  launchArgs: string[];
}

export interface IApplicationConfig {
  appCode: string;
  version: string;
  name: string;
  description: string;
  entryFile: string;
  maxRunSeconds: number;
  changeLog: string;
  runtimeLabel: string;
  pythonPath: string;
  workingDirectory: string;
  envVars: IApplicationEnvVar[];
  aiGateway: IApplicationAiGatewayConfig;
  simulator: IApplicationSimulatorConfig;
  groundStation: IApplicationGroundStationConfig;
  createdAt: string;
  updatedAt: string;
}

export interface IApplicationSummary {
  name: string;
  description: string;
  runtimeLabel: string;
  pythonPath: string;
  workingDirectory: string;
  envVarCount: number;
  createdAt: string;
  updatedAt: string;
}

export function getApplicationConfigPath(workingDirectory: string): string {
  return path.join(workingDirectory, '.jupyter', APPLICATION_CONFIG_FILE);
}

export function createDefaultApplicationConfig(
  workingDirectory: string
): IApplicationConfig {
  const now = new Date().toISOString();
  return {
    appCode: '',
    // [UAV-IDE MODIFY] New FleetDock applications start at a generated default version.
    version: '0.0.1',
    name: path.basename(workingDirectory) || 'new-application',
    description: '',
    entryFile: 'main.py',
    maxRunSeconds: 300,
    changeLog: '',
    runtimeLabel: 'Python 3.12',
    pythonPath: '',
    workingDirectory,
    envVars: [],
    aiGateway: {
      gatewayUrl: '',
      defaultModel: '',
      // [UAV-IDE MODIFY] Keep the default AI route strategy readable in Chinese.
      routeStrategy: '按模型',
      timeoutMs: 15000,
      apiKey: '',
      ak: '',
      sk: ''
    },
    simulator: {
      engine: 'Gazebo',
      worldFile: 'worlds/default.world',
      realTimeFactor: '1.0',
      // [UAV-IDE MODIFY] Default local simulation to the packaged FleetDock WSL/runtime layout.
      wslDistro: 'SkyLab-Ubuntu',
      px4WorkingDirectory: '/opt/skylab/PX4-Autopilot',
      sitlCommand: 'PX4_SIM_HOST_ADDR=127.0.0.1 make px4_sitl none_iris',
      airSimExecutable: '',
      airSimArgs: [],
      autoLaunchAirSim: true,
      // [UAV-IDE MODIFY] Default scene loading to an app-relative scene file and packaged loader script.
      sceneFile: 'simulation/scene.jsonc',
      sceneLoaderScript: ''
    },
    groundStation: {
      stationType: 'QGroundControl',
      connectionUrl: 'udp://127.0.0.1:14550',
      autoConnect: true,
      executablePath: '',
      launchArgs: []
    },
    createdAt: now,
    updatedAt: now
  };
}

export function readApplicationConfig(
  workingDirectory: string
): IApplicationConfig | undefined {
  const configPath = getApplicationConfigPath(workingDirectory);
  if (!fs.existsSync(configPath)) {
    return;
  }

  try {
    const data = fs.readFileSync(configPath);
    const jsonData = JSON.parse(data.toString());
    return normalizeApplicationConfig(jsonData, workingDirectory);
  } catch (error) {
    console.error('Failed to read application config:', error);
    return;
  }
}

export function writeApplicationConfig(config: IApplicationConfig): void {
  const configPath = getApplicationConfigPath(config.workingDirectory);
  const dirPath = path.dirname(configPath);
  if (!fs.existsSync(dirPath)) {
    fs.mkdirSync(dirPath, { recursive: true });
  }

  const normalized = normalizeApplicationConfig(
    config,
    config.workingDirectory
  );
  normalized.updatedAt = new Date().toISOString();
  if (!normalized.createdAt) {
    normalized.createdAt = normalized.updatedAt;
  }

  fs.writeFileSync(configPath, JSON.stringify(normalized, null, 2));
}

export function summarizeApplication(
  workingDirectory: string,
  fallbackName?: string
): IApplicationSummary {
  const config = readApplicationConfig(workingDirectory);
  const defaultName = fallbackName || path.basename(workingDirectory);
  const summary = config || createDefaultApplicationConfig(workingDirectory);

  return {
    name: summary.name || defaultName,
    description: summary.description || '',
    runtimeLabel: summary.runtimeLabel || 'Python',
    pythonPath: summary.pythonPath || '',
    workingDirectory,
    envVarCount: Array.isArray(summary.envVars) ? summary.envVars.length : 0,
    createdAt: summary.createdAt,
    updatedAt: summary.updatedAt
  };
}

export function toEnvironmentMap(
  envVars: IApplicationEnvVar[] | undefined
): KeyValueMap {
  const envMap: KeyValueMap = {};
  for (const envVar of envVars || []) {
    if (envVar?.name) {
      envMap[envVar.name] = envVar.value || envVar.defaultValue || '';
    }
  }
  return envMap;
}

export function normalizeApplicationEnvType(type?: string): string {
  const normalized = String(type || '').trim();
  const legacyMap: { [key: string]: string } = {
    // [UAV-IDE MODIFY] Preserve legacy env-type aliases while replacing DroneAddr with Drone.
    航点: 'DronePoint',
    航路: 'DroneRoute',
    航线: 'DroneRoute',
    航線: 'DroneRoute',
    URL: 'Text',
    '设备-无人机': 'Drone',
    '设备-机器人': 'Text',
    DroneRout: 'DroneRoute',
    DroneAddr: 'Drone',
    SecretText: 'Text',
    Select: 'Text'
  };

  if (legacyMap[normalized]) {
    return legacyMap[normalized];
  }
  if (!normalized) {
    return APPLICATION_ENV_TYPES[0];
  }

  return APPLICATION_ENV_TYPES.includes(normalized as ApplicationEnvType)
    ? normalized
    : APPLICATION_ENV_TYPES[0];
}

export function getApplicationEnvValuePlaceholder(type?: string): string {
  switch (normalizeApplicationEnvType(type)) {
    case 'Drone':
      return '请选择无人机设备';
    case 'DronePoint':
      return '点击地图选择一个坐标点';
    case 'DroneRoute':
      return '在地图上选择多个坐标点';
    default:
      return '';
  }
}

export function getApplicationEnvValueHint(type?: string): string {
  switch (normalizeApplicationEnvType(type)) {
    case 'DronePoint':
      return '航点会以 JSON 对象保存到变量值，运行时可从环境变量读取后解析。';
    case 'DroneRoute':
      return '航线会以 JSON 数组保存到变量值，运行时可从环境变量读取后解析。';
    case 'Drone':
      return '设备绑定由 fleetdock_runtime.py 初始化；应用环境变量仅用于普通配置、航点和航线。';
    default:
      return '';
  }
}
export function isDuplicateApplicationEnvName(
  envVars: IApplicationEnvVar[] | undefined,
  name: string,
  ignoreIndex = -1
): boolean {
  const target = String(name || '')
    .trim()
    .toLowerCase();
  if (!target) {
    return false;
  }

  return (envVars || []).some((envVar, index) => {
    if (index === ignoreIndex) {
      return false;
    }
    return (
      String(envVar?.name || '')
        .trim()
        .toLowerCase() === target
    );
  });
}

function normalizeEnvVar(
  envVar: Partial<IApplicationEnvVar>
): IApplicationEnvVar {
  const type = normalizeApplicationEnvType(envVar?.type);
  const defaultValue =
    envVar?.defaultValue !== undefined
      ? String(envVar.defaultValue || '')
      : String(envVar?.value || '');

  return {
    type,
    name: String(envVar?.name || '').trim(),
    required: envVar?.required === undefined ? false : !!envVar.required,
    description: String(envVar?.description || ''),
    defaultValue,
    sensitive: envVar?.sensitive === undefined ? false : !!envVar.sensitive,
    options: Array.isArray(envVar?.options)
      ? envVar.options.map(item => String(item || '').trim()).filter(Boolean)
      : [],
    value: String(envVar?.value || '')
  };
}

export function normalizeApplicationConfig(
  config: Partial<IApplicationConfig>,
  workingDirectory?: string
): IApplicationConfig {
  const defaultConfig = createDefaultApplicationConfig(
    workingDirectory || config.workingDirectory || ''
  );

  return {
    appCode: String((config as any).appCode || '').trim(),
    version: String(
      (config as any).version || defaultConfig.version || '0.0.1'
    ).trim(),
    name: config.name || defaultConfig.name,
    description: config.description || defaultConfig.description,
    entryFile: String(
      (config as any).entryFile || defaultConfig.entryFile || 'main.py'
    ).trim(),
    maxRunSeconds: Number.isFinite(Number((config as any).maxRunSeconds))
      ? Number((config as any).maxRunSeconds)
      : defaultConfig.maxRunSeconds,
    changeLog: String((config as any).changeLog || ''),
    runtimeLabel: config.runtimeLabel || defaultConfig.runtimeLabel,
    pythonPath: config.pythonPath || defaultConfig.pythonPath,
    workingDirectory:
      config.workingDirectory ||
      workingDirectory ||
      defaultConfig.workingDirectory,
    envVars: Array.isArray(config.envVars)
      ? config.envVars.map(normalizeEnvVar).filter(envVar => !!envVar.name)
      : [],
    aiGateway: {
      ...defaultConfig.aiGateway,
      ...(config.aiGateway || {})
    },
    simulator: {
      ...defaultConfig.simulator,
      ...(config.simulator || {})
    },
    groundStation: {
      ...defaultConfig.groundStation,
      ...(config.groundStation || {})
    },
    createdAt: config.createdAt || defaultConfig.createdAt,
    updatedAt: config.updatedAt || defaultConfig.updatedAt
  };
}

export function createPublishApplicationConfig(
  config: Partial<IApplicationConfig>,
  workingDirectory?: string
): IApplicationConfig {
  const normalized = normalizeApplicationConfig(config, workingDirectory);
  return {
    ...normalized,
    entryFile: normalized.entryFile || 'main.py',
    envVars: normalized.envVars.map(envVar => ({
      type: normalizeApplicationEnvType(envVar.type),
      name: envVar.name,
      required: !!envVar.required,
      description: envVar.description || '',
      defaultValue: envVar.sensitive
        ? ''
        : envVar.defaultValue || envVar.value || '',
      sensitive: !!envVar.sensitive,
      options: Array.isArray(envVar.options) ? envVar.options : [],
      value: envVar.value || ''
    }))
  };
}
