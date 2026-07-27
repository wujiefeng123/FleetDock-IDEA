// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

/* eslint-disable @typescript-eslint/naming-convention -- [UAV-IDE MODIFY] Preserve serialized FleetDock device contract names. */

// [UAV-IDE MODIFY] Centralize managed UAV device and simulation types so device management and orchestration share one model.

export type TelemetryProtocol = 'udp' | 'tcp';
export type TelemetryMode = 'listen' | 'dial';
export type DroneDeviceSource = 'simulation' | 'real';
// [UAV-IDE MODIFY] Keep one authoritative physical-link model for FleetDock endpoint generation.
export type PhysicalDeviceConnection =
  | {
      kind: 'udp' | 'tcp';
      mode: TelemetryMode;
      host: string;
      port: number;
    }
  | {
      kind: 'serial';
      serialPort: string;
      baudRate: number;
    };
// [UAV-IDE MODIFY] Keep the global device library generic while PX4/Project AirSim only consume supported UAV profiles.
export type ManagedDeviceCategory =
  | 'uav'
  | 'ugv'
  | 'usv'
  | 'humanoid'
  | 'quadruped';
export type ManagedDeviceRuntimeKind = 'simulation' | 'physical';
// [UAV-IDE MODIFY] Mirror FleetDock DeviceFactory builder names so generated Python runtime code follows SDK construction rules.
export type FleetDockBuilderName =
  | 'px4_generic'
  | 'dji_matrice_350_rtk'
  | 'dji_mavic_3_enterprise'
  | 'unitree_go2'
  | 'unitree_g1';
export type UavAirframeType =
  | 'quadrotor'
  | 'hexarotor'
  | 'vtol_tailsitter'
  | 'vtol_tiltrotor';
export type AllocationStrategy = 'auto' | 'manual';
export type SimulationDroneState =
  | 'not-started'
  | 'starting'
  | 'running'
  | 'failed';

// [UAV-IDE MODIFY] Keep device-owned sensor definitions backend-neutral while preserving Project AirSim's supported taxonomy.
export type ManagedSensorType =
  | 'camera'
  | 'imu'
  | 'gps'
  | 'barometer'
  | 'magnetometer'
  | 'airspeed'
  | 'lidar'
  | 'distance-sensor'
  | 'radar'
  | 'battery';
export type SensorVector3 = [number, number, number];
export type CameraCaptureType = 0 | 1 | 2 | 3 | 4 | 5 | 6;
export type ProjectAirsimLidarType =
  | 'generic_cylindrical'
  | 'generic_rosette'
  | 'livox_mid70'
  | 'livox_avia'
  | 'gpu_cylindrical'
  | 'depth_lidar';
export type BatterySimulationMode =
  | 'simple-discharge-mode'
  | 'rotor-power-discharge-mode';

export interface IManagedSensorMount {
  parentLink: string;
  xyz: SensorVector3;
  rpyDeg: SensorVector3;
}

export interface ICameraSensorConfig {
  captureInterval: number;
  imageTypes: CameraCaptureType[];
  width: number;
  height: number;
  fovDegrees: number;
  streamingEnabled: boolean;
  compress: boolean;
  targetGamma?: number;
  gimbal?: {
    lockRoll: boolean;
    lockPitch: boolean;
    lockYaw: boolean;
  };
}

export interface IImuNoiseConfig {
  randomWalk: number;
  tau: number;
  biasStability: number;
  turnOnBias: SensorVector3;
}

export interface IImuSensorConfig {
  accelerometer: IImuNoiseConfig;
  gyroscope: IImuNoiseConfig;
}

export interface IGpsSensorConfig {
  ephTimeConstant?: number;
  epvTimeConstant?: number;
  ephInitial?: number;
  epvInitial?: number;
  ephFinal?: number;
  epvFinal?: number;
}

export interface IBarometerSensorConfig {
  qnh?: number;
  pressureFactorSigma?: number;
  pressureFactorTau?: number;
  uncorrelatedNoiseSigma?: number;
  updateLatency?: number;
  updateFrequency?: number;
  startupDelay?: number;
}

export interface IMagnetometerSensorConfig {
  scaleFactor?: number;
  noiseSigma?: SensorVector3;
  noiseBias?: SensorVector3;
}

export interface IAirspeedSensorConfig {
  forwardXyz: SensorVector3;
  pressureFactorSigma?: number;
  pressureFactorTau?: number;
  uncorrelatedNoiseSigma?: number;
}

export interface ILidarSensorConfig {
  lidarType: ProjectAirsimLidarType;
  numberOfChannels: number;
  range: number;
  pointsPerSecond: number;
  reportFrequency: number;
  horizontalRotationFrequency: number;
  horizontalFovStartDeg: number;
  horizontalFovEndDeg: number;
  verticalRotationFrequency: number;
  verticalFovLowerDeg: number;
  verticalFovUpperDeg: number;
  disableSelfHits: boolean;
  drawDebugPoints: boolean;
}

export interface IDistanceSensorConfig {
  minDistance: number;
  maxDistance: number;
  drawDebugPoints: boolean;
}

export interface IRadarSensorConfig {
  rangeMax: number;
  rangeResolution: number;
  velocityMax: number;
  velocityResolution: number;
  detectionInterval: number;
  trackInterval: number;
  rcsAdjustFactor: number;
  drawDebugPoints: boolean;
  fov: {
    azimuthMin: number;
    azimuthMax: number;
    elevationMin: number;
    elevationMax: number;
    azimuthResolution: number;
    elevationResolution: number;
  };
}

export interface IBatterySensorConfig {
  mode: BatterySimulationMode;
  totalCapacity: number;
  capacityOnStart: number;
  drainRateOnStart?: number;
  rotorPowerCoefficient?: number;
}

export type ManagedSensorConfigByType = {
  camera: ICameraSensorConfig;
  imu: IImuSensorConfig;
  gps: IGpsSensorConfig;
  barometer: IBarometerSensorConfig;
  magnetometer: IMagnetometerSensorConfig;
  airspeed: IAirspeedSensorConfig;
  lidar: ILidarSensorConfig;
  'distance-sensor': IDistanceSensorConfig;
  radar: IRadarSensorConfig;
  battery: IBatterySensorConfig;
};

type ManagedSensorBase = {
  id: string;
  name: string;
  enabled: boolean;
  mount: IManagedSensorMount;
};

export type ManagedSensorDefinition = {
  [K in ManagedSensorType]: ManagedSensorBase & {
    type: K;
    config: ManagedSensorConfigByType[K];
  };
}[ManagedSensorType];

export type TelemetryConfig = {
  protocol: TelemetryProtocol;
  mode: TelemetryMode;
  bindHost?: string;
  bindPort?: number;
  remoteHost?: string;
  remotePort?: number;
};

export type FleetDockDeviceConfig = {
  product: string;
  builder: FleetDockBuilderName;
  firmware?: string;
  params: { [key: string]: string | number | boolean | null | undefined };
};

export interface DroneDevice {
  id: string;
  name: string;
  envVarName: string;
  // [UAV-IDE MODIFY] Legacy fields are kept for existing IPC; new fields model the global device library.
  deviceType: 'drone';
  deviceCategory: ManagedDeviceCategory;
  runtimeKind: ManagedDeviceRuntimeKind;
  enabled: boolean;
  description?: string;
  source: DroneDeviceSource;
  accessType: 'virtual' | 'physical';
  sensors: ManagedSensorDefinition[];
  uav: {
    airframeType: UavAirframeType;
    px4AutostartId: number;
    projectAirsimAirframe: string;
    robotTemplate: string;
    vehicleName: string;
    systemId: number;
    px4Instance?: number | null;
    simulatorPort?: number | null;
    mavlinkPort?: number | null;
    controlPortLocal?: number | null;
  };
  management: {
    host: string;
    sshPort?: number;
    username?: string;
  };
  px4: {
    startMode: 'manual' | 'ide-managed';
    startCommand?: string;
    workdir?: string;
  };
  telemetry: TelemetryConfig;
  systemId: number;
  airsimVehicleName: string;
  simulation: {
    model: string;
    engine: 'project-airsim-px4' | 'px4-sitl' | 'airsim' | 'gazebo' | string;
    airsimVehicleName: string;
    sceneActorName?: string;
    startLanded?: boolean;
    originX?: number;
    originY?: number;
    originZ?: number;
    rollDeg?: number;
    pitchDeg?: number;
    yawDeg?: number;
    systemIdStrategy: AllocationStrategy;
    mavlinkPortStrategy: AllocationStrategy;
    px4InstanceStrategy: AllocationStrategy;
    px4StartMode: 'ide-managed' | 'manual-connect';
    px4CommandOverride?: string;
    runtime: {
      state: SimulationDroneState;
      connectionUri?: string;
      systemId?: number;
      mavlinkPort?: number;
      px4Instance?: number;
      lastStartedAt?: string;
      lastError?: string;
    };
  };
  real: {
    connection: PhysicalDeviceConnection;
    connectionKind: 'udp' | 'tcp' | 'serial';
    hardwareModel?: string;
    serialNumber?: string;
    host?: string;
    port?: number;
    serialPort?: string;
    baudRate?: number;
    readOnly: boolean;
    allowCommand: boolean;
    heartbeatCheck: boolean;
  };
  fleetdock: FleetDockDeviceConfig;
  createdAt: string;
  updatedAt: string;
}

export type AirSimConfig = {
  mode: 'local' | 'remote';
  host?: string;
  port?: number;
  executablePath?: string;
  startCommand?: string;
  workdir?: string;
  autoStart?: boolean;
};

export type SimulationConfig = {
  airsim: AirSimConfig;
  drones: DroneDevice[];
};

export type DroneSimulationStatus = {
  droneId: string;
  px4Online: boolean;
  connected: boolean;
  connectionURI: string;
  airsimVehicleName: string;
  systemId: number;
  error?: string;
};

export type SimulationStartResult = {
  state: string;
  airsimOnline: boolean;
  drones: DroneSimulationStatus[];
  // [UAV-IDE MODIFY] Return local simulation pipeline metadata so all launch surfaces can show the exact failing stage.
  title?: string;
  message?: string;
  logPath?: string;
  stages?: Array<{
    stage: string;
    state: string;
    message: string;
    detail?: string;
    startedAt?: string;
    finishedAt?: string;
  }>;
};
