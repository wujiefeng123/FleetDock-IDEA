// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

/* eslint-disable @typescript-eslint/naming-convention -- [UAV-IDE MODIFY] Preserve serialized UAV simulation IPC contract names. */

import { DroneDevice, SimulationConfig } from '../devices/types';

// [UAV-IDE MODIFY] Keep the independent UAV simulation subsystem contract outside legacy simulation modules.
export type UavSimulationEngine = 'project-airsim';

export type UavSimulationRunState =
  | 'idle'
  | 'checking'
  | 'awaiting-confirmation'
  | 'stopping-conflicts'
  | 'starting-scene'
  | 'starting-px4'
  | 'loading-world'
  | 'verifying'
  | 'starting-media'
  | 'starting-mavlink'
  | 'running'
  | 'stopping'
  | 'failed';

export interface UavSimulationScene {
  id: string;
  name: string;
  rootDirectory: string;
  executablePath: string;
  workingDirectory: string;
  engine: UavSimulationEngine;
  createdAt: string;
  updatedAt: string;
}

export interface UavSimulationSceneState {
  scenes: UavSimulationScene[];
  selectedSceneId: string;
  // [UAV-IDE MODIFY] Keep simulation setup global so all applications share one PX4 and scene package configuration.
  px4Path: string;
  scenePackageDirectory: string;
}

export interface UavSimulationCheckItem {
  id: string;
  label: string;
  ok: boolean;
  message: string;
  detail?: string;
  suggestion?: string;
}

export interface UavSimulationEnvironmentReport {
  ok: boolean;
  items: UavSimulationCheckItem[];
  checkedAt: string;
}

export interface UavSimulationConflictProcess {
  pid: number;
  name: string;
  source: 'scene' | 'project-airsim-client' | 'px4' | 'port';
  detail?: string;
}

export interface UavSimulationStageStatus {
  stage: UavSimulationRunState;
  state: 'pending' | 'running' | 'success' | 'failed';
  message: string;
  detail?: string;
  startedAt?: string;
  finishedAt?: string;
}

export interface UavSimulationStartRequest {
  sceneId?: string;
  scene?: UavSimulationScene;
  config?: SimulationConfig;
  drones?: DroneDevice[];
  forceCleanup?: boolean;
}

// [UAV-IDE MODIFY] Keep Project AirSim runtime interactions behind a small audited action set.
export const UAV_SIMULATION_COMMAND_ACTIONS = [
  'view.next',
  'simulation.pause.toggle',
  'weather.set',
  // [UAV-IDE MODIFY] Retain legacy debug actions for existing clients without exposing them in the primary UI.
  'trace.toggle',
  'markers.clear',
  'simulation.snapshot'
] as const;

export type UavSimulationCommandAction = typeof UAV_SIMULATION_COMMAND_ACTIONS[number];

export interface UavSimulationCommandRequest {
  action: UavSimulationCommandAction;
  params?: Record<string, unknown>;
}

export interface UavSimulationCommandResult {
  id: string;
  runId?: string;
  action: string;
  ok: boolean;
  code: string;
  message: string;
  data?: unknown;
  durationMs?: number;
}

export interface UavSimulationCommandChannelStatus {
  available: boolean;
  protocolVersion?: number;
  capabilities: UavSimulationCommandAction[];
  message?: string;
  lastError?: string;
}

// [UAV-IDE MODIFY] Publish health only; endpoint credentials remain in the main-process runtime manifest.
export interface UavSimulationSensorServiceStatus {
  available: boolean;
  protocolVersion?: number;
  sdkVersion?: string;
  vehicles?: string[];
  rtspVehicles?: string[];
  message?: string;
  lastError?: string;
}

// [UAV-IDE MODIFY] Model every FleetDock camera independently so multiple vehicles and cameras can play concurrently.
export type UavSimulationCameraStream = {
  id: string;
  vehicleName: string;
  sensorId: string;
  label: string;
};

export type UavSimulationMediaServiceStatus = {
  available: boolean;
  state: 'idle' | 'starting' | 'running' | 'failed';
  version?: string;
  streams: UavSimulationCameraStream[];
  message?: string;
  lastError?: string;
};

export type UavSimulationWebRtcOfferRequest = {
  streamId: string;
  offerSdp: string;
};

export type UavSimulationWebRtcSessionResult = {
  ok: boolean;
  streamId: string;
  sessionId?: string;
  answerSdp?: string;
  message?: string;
};

export interface UavSimulationRunFileSet {
  runId: string;
  runDirectory: string;
  sceneFile: string;
  launchFile: string;
  clientResultFile: string;
  sensorManifestFile: string;
  logFile: string;
  robotFiles: string[];
}

export interface UavSimulationStatus {
  state: UavSimulationRunState;
  title: string;
  message: string;
  runId?: string;
  scene?: UavSimulationScene;
  stages: UavSimulationStageStatus[];
  conflicts?: UavSimulationConflictProcess[];
  logPath?: string;
  commandChannel?: UavSimulationCommandChannelStatus;
  sensorService?: UavSimulationSensorServiceStatus;
  mediaService?: UavSimulationMediaServiceStatus;
  drones?: Array<{
    droneId: string;
    vehicleName: string;
    systemId: number;
    px4Instance: number;
    simulatorPort: number;
    mavlinkPort: number;
    connected: boolean;
    error?: string;
  }>;
}

export interface UavSimulationGeneratedDrone {
  device: DroneDevice;
  index: number;
  vehicleName: string;
  systemId: number;
  px4AutostartId: number;
  projectAirsimAirframe: string;
  robotTemplate: string;
  px4Instance: number;
  simulatorPort: number;
  mavlinkPort: number;
  cameraStreamUri: string;
  cameraStreams: Array<{
    id: string;
    sensorId: string;
    label: string;
    streamUri: string;
  }>;
  controlPortRemote: number;
  controlPortLocal: number;
  robotFile: string;
  origin: {
    xyz: string;
    rpy: string;
  };
}

export interface UavSimulationLaunchPlan {
  scene: UavSimulationScene;
  files: UavSimulationRunFileSet;
  drones: UavSimulationGeneratedDrone[];
  wslDistro: string;
  px4WslDirectory: string;
  windowsHostAddress: string;
  clientScript: string;
  runtimePythonRoot: string;
  pythonExecutable: string;
}
