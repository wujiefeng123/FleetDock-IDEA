// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

/* eslint-disable camelcase -- [UAV-IDE MODIFY] Persist FleetDock SDK parameter names without changing the stored data contract. */

// [UAV-IDE MODIFY] Persist the global UAV device registry in a dedicated JSON store so device management stays separate from per-app settings.

import * as fs from 'fs';
import * as path from 'path';
import * as crypto from 'crypto';
import { app } from 'electron';
import {
  allocatePort,
  allocateUniqueSimulationMavlinkPort
} from '../port-allocator';
import {
  DroneDevice,
  DroneDeviceSource,
  FleetDockBuilderName,
  FleetDockDeviceConfig,
  ManagedDeviceCategory,
  ManagedDeviceRuntimeKind,
  PhysicalDeviceConnection
} from '../types';
import {
  buildPhysicalConnectionURI,
  normalizePhysicalDeviceConnection
} from '../connection-uri';
import {
  getUavAirframeProfile,
  normalizeUavAirframeType
} from '../uav-airframes';
import {
  createDefaultProjectAirsimSensors,
  normalizeManagedSensors
} from '../sensors/project-airsim-sensors';

type DeviceFile = {
  version: 4;
  devices: DroneDevice[];
  // [UAV-IDE MODIFY] Read legacy drone-only registries without forcing users to recreate devices.
  drones: DroneDevice[];
};

function nowIso(): string {
  return new Date().toISOString();
}

function createDeviceId(): string {
  // [UAV-IDE MODIFY] Generate global UAV device ids in the store so every entry point gets the same uniqueness guarantee.
  const randomId =
    typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  return `drone-${randomId}`;
}

function resolveDeviceId(
  value: Partial<DroneDevice>,
  devices: DroneDevice[]
): string {
  const requestedId = String(value.id || '').trim();
  if (!requestedId) {
    return createDeviceId();
  }

  const duplicateCount = devices.filter(device => device.id === requestedId)
    .length;
  return duplicateCount > 1 ? createDeviceId() : requestedId;
}

function nextEnvIndex(devices: DroneDevice[], currentId?: string): number {
  const indexes = devices
    .filter(device => device.id !== currentId)
    .map(device => {
      const match = String(device.envVarName || '').match(
        /^DEVICE_DRONE_(\d+)$/i
      );
      return match ? Number(match[1]) : 0;
    })
    .filter(index => Number.isFinite(index) && index > 0)
    .sort((left, right) => left - right);

  let next = 1;
  for (const index of indexes) {
    if (index === next) {
      next += 1;
    } else if (index > next) {
      break;
    }
  }
  return next;
}

function nextSystemId(devices: DroneDevice[], currentId?: string): number {
  const used = new Set(
    devices
      .filter(device => device.id !== currentId)
      .map(device => Number(device.uav?.systemId || device.systemId))
      .filter(id => Number.isInteger(id) && id > 0 && id < 256)
  );
  for (let id = 1; id < 256; id += 1) {
    if (!used.has(id)) {
      return id;
    }
  }
  return 1;
}

function resolveSystemId(
  value: Partial<DroneDevice>,
  devices: DroneDevice[]
): number {
  const currentId = value.id;
  const requested = Number(value.uav?.systemId || value.systemId);
  const duplicate = devices.some(
    device =>
      device.id !== currentId &&
      Number(device.uav?.systemId || device.systemId) === requested
  );
  if (
    Number.isInteger(requested) &&
    requested > 0 &&
    requested < 256 &&
    !duplicate
  ) {
    return requested;
  }
  return nextSystemId(devices, currentId);
}

function simulationVehicleName(value: Partial<DroneDevice>): string {
  return String(
    value.uav?.vehicleName ||
      value.simulation?.sceneActorName ||
      value.simulation?.airsimVehicleName ||
      value.airsimVehicleName ||
      ''
  )
    .trim()
    .toLowerCase();
}

function assertUniqueSimulationVehicleName(
  value: DroneDevice,
  devices: DroneDevice[]
): void {
  if (value.source !== 'simulation') {
    return;
  }
  const vehicleName = simulationVehicleName(value);
  if (!vehicleName) {
    return;
  }
  const duplicate = devices.find(
    device =>
      device.id !== value.id &&
      device.source === 'simulation' &&
      simulationVehicleName(device) === vehicleName
  );
  if (duplicate) {
    throw new Error(
      `仿真设备名称重复：${value.uav.vehicleName} 已被 ${duplicate.name} 使用。`
    );
  }
}

function normalizeFleetDockBuilder(value: unknown): FleetDockBuilderName {
  if (
    value === 'dji_matrice_350_rtk' ||
    value === 'dji_mavic_3_enterprise' ||
    value === 'unitree_go2' ||
    value === 'unitree_g1'
  ) {
    return value;
  }
  return 'px4_generic';
}

function productForBuilder(builder: FleetDockBuilderName): string {
  switch (builder) {
    case 'dji_matrice_350_rtk':
      return 'dji.matrice_350_rtk';
    case 'dji_mavic_3_enterprise':
      return 'dji.mavic_3_enterprise';
    case 'unitree_go2':
      return 'unitree.go2';
    case 'unitree_g1':
      return 'unitree.g1';
    default:
      return 'px4.generic';
  }
}

function endpointForPort(port: number): string {
  return `udpin://0.0.0.0:${port}`;
}

function requestedSimulationMavlinkPort(
  value: Partial<DroneDevice>
): number | undefined {
  const candidates = [
    value.fleetdock?.params?.endpoint,
    value.uav?.mavlinkPort,
    value.telemetry?.bindPort,
    value.real?.port
  ];
  for (const candidate of candidates) {
    if (typeof candidate === 'string') {
      const match = candidate.match(/:(\d+)\s*$/);
      if (match) {
        return Number(match[1]);
      }
    }
    const port = Number(candidate);
    if (Number.isInteger(port)) {
      return port;
    }
  }
  return undefined;
}

function usedSimulationMavlinkPorts(
  devices: DroneDevice[],
  currentId?: string
): number[] {
  return devices
    .filter(device => device.id !== currentId && device.source === 'simulation')
    .map(device =>
      Number(
        device.uav?.mavlinkPort ||
          device.telemetry?.bindPort ||
          requestedSimulationMavlinkPort(device)
      )
    )
    .filter(port => Number.isInteger(port));
}

function resolveSimulationMavlinkPort(
  value: Partial<DroneDevice>,
  devices: DroneDevice[]
): number {
  const requested = requestedSimulationMavlinkPort(value);
  return allocateUniqueSimulationMavlinkPort(
    usedSimulationMavlinkPorts(devices, value.id),
    requested
  );
}

function normalizeFleetDockConfig(
  value: Partial<DroneDevice>,
  source: DroneDeviceSource,
  mavlinkPort: number,
  physicalConnection?: PhysicalDeviceConnection
): FleetDockDeviceConfig {
  const builder = normalizeFleetDockBuilder(value.fleetdock?.builder);
  const incomingParams = value.fleetdock?.params || {};
  const params: FleetDockDeviceConfig['params'] = { ...incomingParams };

  if (builder === 'px4_generic') {
    delete params.force_arm;
    params.endpoint =
      source === 'simulation'
        ? endpointForPort(mavlinkPort)
        : buildPhysicalConnectionURI(
            physicalConnection || normalizePhysicalDeviceConnection(value)
          );
    params.timeout_s =
      source === 'real' && physicalConnection?.kind === 'serial'
        ? 10
        : Number(params.timeout_s || 30);
    // [UAV-IDE MODIFY] Let the SDK request an OS-approved loopback port; Hyper-V exclusions vary by host and boot.
    delete params.mavsdk_server_port;
  } else if (
    builder === 'dji_matrice_350_rtk' ||
    builder === 'dji_mavic_3_enterprise'
  ) {
    params.sdk_mode = String(params.sdk_mode || 'msdk');
  } else if (builder === 'unitree_go2' || builder === 'unitree_g1') {
    params.network_interface = String(params.network_interface || 'eth0');
  }

  return {
    product: String(value.fleetdock?.product || productForBuilder(builder)),
    builder,
    firmware: value.fleetdock?.firmware
      ? String(value.fleetdock.firmware)
      : undefined,
    params
  };
}

function defaultDrone(index: number): DroneDevice {
  const now = nowIso();
  const port = allocatePort(index - 1);
  return {
    id: createDeviceId(),
    // [UAV-IDE MODIFY] Store simulation drones as templates so users do not need a running PX4 instance before adding one.
    name: `drone${index}`,
    envVarName: `DEVICE_DRONE_${index}`,
    deviceType: 'drone',
    deviceCategory: 'uav',
    runtimeKind: 'simulation',
    enabled: true,
    description: '',
    source: 'simulation',
    accessType: 'virtual',
    sensors: createDefaultProjectAirsimSensors(),
    uav: {
      airframeType: 'quadrotor',
      px4AutostartId: 10016,
      projectAirsimAirframe: 'quadrotor-x',
      robotTemplate: 'robot_quadrotor_px4.jsonc',
      vehicleName: `Drone${index}`,
      systemId: index,
      px4Instance: null,
      simulatorPort: null,
      mavlinkPort: port,
      controlPortLocal: null
    },
    management: {
      host: '127.0.0.1',
      sshPort: 22,
      username: ''
    },
    px4: {
      startMode: 'ide-managed',
      startCommand: '',
      workdir: ''
    },
    telemetry: {
      protocol: 'udp',
      mode: 'listen',
      bindHost: '0.0.0.0',
      bindPort: port
    },
    systemId: index,
    airsimVehicleName: `Drone${index}`,
    simulation: {
      model: 'quadrotor',
      engine: 'project-airsim-px4',
      airsimVehicleName: `Drone${index}`,
      systemIdStrategy: 'auto',
      mavlinkPortStrategy: 'auto',
      px4InstanceStrategy: 'auto',
      px4StartMode: 'ide-managed',
      runtime: {
        state: 'not-started'
      }
    },
    real: {
      connection: {
        kind: 'udp',
        mode: 'listen',
        host: '0.0.0.0',
        port
      },
      connectionKind: 'udp',
      host: '0.0.0.0',
      port,
      serialPort: '',
      baudRate: 57600,
      readOnly: true,
      allowCommand: false,
      heartbeatCheck: true
    },
    fleetdock: {
      product: 'px4.generic',
      builder: 'px4_generic',
      params: {
        endpoint: endpointForPort(port),
        timeout_s: 30
      }
    },
    createdAt: now,
    updatedAt: now
  };
}

function normalizeSource(value: Partial<DroneDevice>): DroneDeviceSource {
  if (
    (value as any).source === 'real' ||
    value.accessType === 'physical' ||
    value.runtimeKind === 'physical'
  ) {
    return 'real';
  }
  return 'simulation';
}

function normalizeDeviceCategory(value: unknown): ManagedDeviceCategory {
  if (
    value === 'ugv' ||
    value === 'usv' ||
    value === 'humanoid' ||
    value === 'quadruped'
  ) {
    return value;
  }
  return 'uav';
}

function normalizeRuntimeKind(
  value: Partial<DroneDevice>,
  source: DroneDeviceSource
): ManagedDeviceRuntimeKind {
  if ((value as any).runtimeKind === 'physical' || source === 'real') {
    return 'physical';
  }
  return 'simulation';
}

function normalizeDrone(
  value: Partial<DroneDevice>,
  devices: DroneDevice[],
  strictSensors = false
): DroneDevice {
  const envIndex =
    value.envVarName && /^DEVICE_DRONE_\d+$/i.test(value.envVarName)
      ? Number(String(value.envVarName).split('_').pop())
      : nextEnvIndex(devices, value.id);
  const defaults = defaultDrone(envIndex);
  const id = resolveDeviceId(value, devices);
  const now = nowIso();
  const systemId = resolveSystemId(value, devices);
  const source = normalizeSource(value);
  const accessType = source === 'real' ? 'physical' : 'virtual';
  const mavlinkPort =
    source === 'simulation'
      ? resolveSimulationMavlinkPort(value, devices)
      : Number(
          value.uav?.mavlinkPort ||
            value.telemetry?.bindPort ||
            defaults.uav.mavlinkPort
        );
  const deviceCategory = normalizeDeviceCategory((value as any).deviceCategory);
  const runtimeKind = normalizeRuntimeKind(value, source);
  // [UAV-IDE MODIFY] Legacy simulation UAVs inherit the five official template sensors until users customize the list.
  const sensors = normalizeManagedSensors((value as any).sensors, {
    includeDefaults: deviceCategory === 'uav' && source === 'simulation',
    strict: strictSensors
  });
  const airframeType = normalizeUavAirframeType(
    value.uav?.airframeType || value.simulation?.model
  );
  const airframe = getUavAirframeProfile(airframeType);
  const simulation = {
    ...defaults.simulation,
    ...(value.simulation || {}),
    engine: value.simulation?.engine || defaults.simulation.engine,
    model: airframeType,
    runtime: {
      ...defaults.simulation.runtime,
      ...(value.simulation?.runtime || {})
    }
  };
  let physicalConnection: PhysicalDeviceConnection | undefined;
  if (source === 'real') {
    try {
      physicalConnection = normalizePhysicalDeviceConnection(value);
    } catch (error) {
      // [UAV-IDE MODIFY] Keep malformed legacy entries visible so users can repair them in the device manager.
      physicalConnection = {
        kind: 'udp',
        mode: 'listen',
        host: '0.0.0.0',
        port: 14560
      };
    }
  }
  const real = {
    ...defaults.real,
    ...(value.real || {}),
    connection: physicalConnection || defaults.real.connection,
    connectionKind:
      physicalConnection?.kind ||
      value.real?.connectionKind ||
      defaults.real.connectionKind,
    host:
      physicalConnection && physicalConnection.kind !== 'serial'
        ? physicalConnection.host
        : value.real?.host || '',
    port:
      physicalConnection && physicalConnection.kind !== 'serial'
        ? physicalConnection.port
        : value.real?.port,
    serialPort:
      physicalConnection?.kind === 'serial'
        ? physicalConnection.serialPort
        : value.real?.serialPort || '',
    baudRate:
      physicalConnection?.kind === 'serial'
        ? physicalConnection.baudRate
        : value.real?.baudRate || defaults.real.baudRate
  };
  // [UAV-IDE MODIFY] Access-mode gating was removed from the product; ignore
  // values saved by short-lived development builds.
  delete (real as any).accessMode;
  const fleetdock = normalizeFleetDockConfig(
    value,
    source,
    mavlinkPort,
    physicalConnection
  );

  return {
    ...defaults,
    ...value,
    id,
    deviceType: 'drone',
    deviceCategory,
    runtimeKind,
    enabled: (value as any).enabled !== false,
    description: String((value as any).description || ''),
    source,
    accessType,
    sensors,
    envVarName: String(value.envVarName || defaults.envVarName),
    name: String(value.name || defaults.name),
    management: {
      ...defaults.management,
      ...(value.management || {})
    },
    uav: {
      ...defaults.uav,
      ...(value.uav || {}),
      airframeType,
      // [UAV-IDE MODIFY] Recompute profile-derived values while loading so legacy quad-template mappings migrate without rewriting user-owned identity or ports.
      px4AutostartId: airframe.px4AutostartId,
      projectAirsimAirframe: airframe.projectAirsimAirframe,
      robotTemplate: airframe.robotTemplate,
      vehicleName: String(
        value.uav?.vehicleName ||
          simulation.airsimVehicleName ||
          value.airsimVehicleName ||
          defaults.uav.vehicleName
      ),
      systemId,
      mavlinkPort:
        source === 'simulation'
          ? mavlinkPort
          : value.uav?.mavlinkPort || defaults.uav.mavlinkPort
    },
    px4: {
      ...defaults.px4,
      ...(value.px4 || {}),
      startMode:
        source === 'simulation' && simulation.px4StartMode === 'ide-managed'
          ? 'ide-managed'
          : value.px4?.startMode || defaults.px4.startMode
    },
    telemetry: {
      ...defaults.telemetry,
      ...(value.telemetry || {}),
      protocol:
        physicalConnection && physicalConnection.kind !== 'serial'
          ? physicalConnection.kind
          : value.telemetry?.protocol || defaults.telemetry.protocol,
      mode:
        physicalConnection && physicalConnection.kind !== 'serial'
          ? physicalConnection.mode
          : value.telemetry?.mode || defaults.telemetry.mode,
      bindHost:
        source === 'simulation'
          ? '0.0.0.0'
          : physicalConnection &&
            physicalConnection.kind !== 'serial' &&
            physicalConnection.mode === 'listen'
          ? physicalConnection.host
          : value.telemetry?.bindHost,
      bindPort:
        source === 'simulation'
          ? mavlinkPort
          : physicalConnection &&
            physicalConnection.kind !== 'serial' &&
            physicalConnection.mode === 'listen'
          ? physicalConnection.port
          : value.telemetry?.bindPort,
      remoteHost:
        physicalConnection &&
        physicalConnection.kind !== 'serial' &&
        physicalConnection.mode === 'dial'
          ? physicalConnection.host
          : value.telemetry?.remoteHost,
      remotePort:
        physicalConnection &&
        physicalConnection.kind !== 'serial' &&
        physicalConnection.mode === 'dial'
          ? physicalConnection.port
          : value.telemetry?.remotePort
    },
    systemId,
    airsimVehicleName: String(
      value.airsimVehicleName ||
        simulation.airsimVehicleName ||
        defaults.airsimVehicleName
    ),
    simulation: {
      ...simulation,
      airsimVehicleName: String(
        simulation.airsimVehicleName ||
          value.airsimVehicleName ||
          defaults.airsimVehicleName
      ),
      sceneActorName: String(
        simulation.sceneActorName ||
          value.uav?.vehicleName ||
          simulation.airsimVehicleName ||
          defaults.airsimVehicleName
      ),
      startLanded: simulation.startLanded !== false
    },
    real,
    fleetdock,
    createdAt: String(value.createdAt || defaults.createdAt),
    updatedAt: String(value.updatedAt || now)
  };
}

export class DeviceStore {
  private _path = path.join(
    app.getPath('userData'),
    'uav-device-registry.json'
  );

  async list(): Promise<DroneDevice[]> {
    return this._read().devices;
  }

  async upsert(device: Partial<DroneDevice>): Promise<DroneDevice> {
    const file = this._read();
    const incomingId = String(device.id || '').trim();
    const index = incomingId
      ? file.devices.findIndex(item => item.id === incomingId)
      : -1;
    const merged = normalizeDrone(
      index >= 0 ? { ...file.devices[index], ...device } : device,
      file.devices,
      true
    );
    assertUniqueSimulationVehicleName(merged, file.devices);

    if (index >= 0) {
      file.devices[index] = merged;
    } else {
      file.devices.unshift(merged);
    }
    file.drones = file.devices;

    this._write(file);
    return merged;
  }

  async remove(id: string): Promise<boolean> {
    const file = this._read();
    const beforeCount = file.devices.length;
    // [UAV-IDE MODIFY] Report whether a device was actually removed so inline UI can avoid false success states.
    file.devices = file.devices.filter(item => item.id !== id);
    file.drones = file.devices;
    this._write(file);
    return file.devices.length !== beforeCount;
  }

  private _read(): DeviceFile {
    try {
      if (!fs.existsSync(this._path)) {
        return { version: 4, devices: [], drones: [] };
      }
      const parsed = JSON.parse(fs.readFileSync(this._path, 'utf8')) as Partial<
        DeviceFile
      >;
      const sourceDrones = Array.isArray((parsed as any)?.devices)
        ? ((parsed as any).devices as DroneDevice[])
        : Array.isArray(parsed?.drones)
        ? (parsed.drones as DroneDevice[])
        : [];
      const drones = sourceDrones.map(item =>
        normalizeDrone(item, sourceDrones)
      );
      const file: DeviceFile = {
        version: 4,
        devices: drones,
        drones
      };
      return file;
    } catch (error) {
      console.error('Failed to read UAV device registry:', error);
      return { version: 4, devices: [], drones: [] };
    }
  }

  private _write(file: DeviceFile): void {
    const dirPath = path.dirname(this._path);
    if (!fs.existsSync(dirPath)) {
      fs.mkdirSync(dirPath, { recursive: true });
    }
    fs.writeFileSync(this._path, JSON.stringify(file, null, 2));
  }
}
