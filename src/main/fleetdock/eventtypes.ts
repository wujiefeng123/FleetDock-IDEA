// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import { EventTypeMain as UpstreamEventTypeMain } from '../eventtypes';

// [UAV-IDE MODIFY] Keep all FleetDock IPC channels outside the upstream event
// registry so upstream updates can replace that registry without remerging
// product-specific entries.
export enum FleetDockEventTypeMain {
  CreateNewApplication = 'create-new-application',
  ShowApplicationSettings = 'show-application-settings',
  SaveApplicationSettings = 'save-application-settings',
  SkyLabWorkspaceGetContext = 'skylab-workspace-get-context',
  ShowCodeAppCenterPublish = 'show-codeappcenter-publish',
  CodeAppCenterGetState = 'codeappcenter-get-state',
  CodeAppCenterLogin = 'codeappcenter-login',
  CodeAppCenterListApps = 'codeappcenter-list-apps',
  CodeAppCenterReadRequirements = 'codeappcenter-read-requirements',
  CodeAppCenterScanDependencies = 'codeappcenter-scan-dependencies',
  CodeAppCenterPublish = 'codeappcenter-publish',
  ShowWaypointPicker = 'show-waypoint-picker',
  WaypointPickerConfirm = 'waypoint-picker-confirm',
  WaypointPickerCancel = 'waypoint-picker-cancel',
  ShowRoutePicker = 'show-route-picker',
  RoutePickerConfirm = 'route-picker-confirm',
  RoutePickerCancel = 'route-picker-cancel',
  OpenDeviceManager = 'open-device-manager',
  DeviceManagerLoad = 'device-manager-load',
  DeviceManagerSave = 'device-manager-save',
  DeviceManagerDelete = 'device-manager-delete',
  DeviceManagerProbe = 'device-manager-probe',
  DeviceManagerSerialPorts = 'device-manager-serial-ports',
  DeviceManagerBind = 'device-manager-bind',
  DeviceManagerCopyProbeReport = 'device-manager-copy-probe-report',
  DeviceManagerExportProbeReport = 'device-manager-export-probe-report',
  UavSimulationEnvironmentCheck = 'uav-simulation-environment-check',
  UavSimulationSceneList = 'uav-simulation-scene-list',
  UavSimulationConfigSave = 'uav-simulation-config-save',
  UavSimulationDirectorySelect = 'uav-simulation-directory-select',
  UavSimulationSceneAddDirectory = 'uav-simulation-scene-add-directory',
  UavSimulationSceneUpdate = 'uav-simulation-scene-update',
  UavSimulationSceneDelete = 'uav-simulation-scene-delete',
  UavSimulationStart = 'uav-simulation-start',
  UavSimulationStop = 'uav-simulation-stop',
  UavSimulationStatus = 'uav-simulation-status',
  UavSimulationCameraStreams = 'uav-simulation-camera-streams',
  UavSimulationWebRtcOffer = 'uav-simulation-webrtc-offer',
  UavSimulationWebRtcClose = 'uav-simulation-webrtc-close',
  UavSimulationCommand = 'uav-simulation-command',
  UavSimulationDisconnectDialogChoice = 'uav-simulation-disconnect-dialog-choice',
  FleetDockHudSnapshot = 'fleetdock-hud-snapshot',
  FleetDockHudWindowOpen = 'fleetdock-hud-window-open',
  FleetDockDebugCommand = 'fleetdock-debug-command',
  GroundStationStatus = 'groundstation-status',
  GroundStationLaunch = 'groundstation-launch',
  GroundStationClose = 'groundstation-close',
  GroundStationSelectExecutable = 'groundstation-select-executable',
  GroundStationOpenInstallDirectory = 'groundstation-open-install-directory'
}

/**
 * Combined registry for FleetDock modules that also invoke a small number of
 * upstream file-picker channels.
 */
export const EventTypeMain = {
  ...UpstreamEventTypeMain,
  ...FleetDockEventTypeMain
} as const;
