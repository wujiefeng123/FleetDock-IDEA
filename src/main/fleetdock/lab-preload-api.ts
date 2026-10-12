// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import { ipcRenderer } from 'electron';
import { EventTypeMain } from './eventtypes';

/**
 * IPC exposed only to the JupyterLab renderer. Main-process controllers still
 * validate event.sender before every stateful operation.
 */
export const fleetDockLabPreloadApi = {
  showApplicationSettings: () => {
    ipcRenderer.send(EventTypeMain.ShowApplicationSettings);
  },
  showCodeAppCenterPublish: () => {
    ipcRenderer.send(EventTypeMain.ShowCodeAppCenterPublish);
  },
  openDeviceManager: (deviceId?: string) => {
    ipcRenderer.send(EventTypeMain.OpenDeviceManager, deviceId);
  },
  getSkyLabWorkspaceContext: () =>
    ipcRenderer.invoke(EventTypeMain.SkyLabWorkspaceGetContext),
  saveApplicationSettings: (application: unknown) =>
    ipcRenderer.invoke(EventTypeMain.SaveApplicationSettings, application),
  loadDroneDevices: () => ipcRenderer.invoke(EventTypeMain.DeviceManagerLoad),
  saveDroneDevice: (device: unknown) =>
    ipcRenderer.invoke(EventTypeMain.DeviceManagerSave, device),
  deleteDroneDevice: (deviceId: string) =>
    ipcRenderer.invoke(EventTypeMain.DeviceManagerDelete, deviceId),
  checkUavSimulationEnvironment: () =>
    ipcRenderer.invoke(EventTypeMain.UavSimulationEnvironmentCheck),
  listUavSimulationScenes: () =>
    ipcRenderer.invoke(EventTypeMain.UavSimulationSceneList),
  saveUavSimulationConfig: (config: unknown) =>
    ipcRenderer.invoke(EventTypeMain.UavSimulationConfigSave, config),
  selectUavSimulationDirectory: (request: unknown) =>
    ipcRenderer.invoke(EventTypeMain.UavSimulationDirectorySelect, request),
  addUavSimulationSceneDirectory: () =>
    ipcRenderer.invoke(EventTypeMain.UavSimulationSceneAddDirectory),
  updateUavSimulationScene: (scene: unknown) =>
    ipcRenderer.invoke(EventTypeMain.UavSimulationSceneUpdate, scene),
  deleteUavSimulationScene: (sceneId: string) =>
    ipcRenderer.invoke(EventTypeMain.UavSimulationSceneDelete, sceneId),
  startUavSimulation: (request: unknown) =>
    ipcRenderer.invoke(EventTypeMain.UavSimulationStart, request),
  stopUavSimulation: () => ipcRenderer.invoke(EventTypeMain.UavSimulationStop),
  getUavSimulationStatus: () =>
    ipcRenderer.invoke(EventTypeMain.UavSimulationStatus),
  getUavSimulationCameraStreams: (vehicleName: string) =>
    ipcRenderer.invoke(EventTypeMain.UavSimulationCameraStreams, vehicleName),
  createUavSimulationWebRtcSession: (request: unknown) =>
    ipcRenderer.invoke(EventTypeMain.UavSimulationWebRtcOffer, request),
  closeUavSimulationWebRtcSession: (sessionId: string) =>
    ipcRenderer.invoke(EventTypeMain.UavSimulationWebRtcClose, sessionId),
  executeUavSimulationCommand: (request: unknown) =>
    ipcRenderer.invoke(EventTypeMain.UavSimulationCommand, request),
  getFleetDockHudSnapshot: () =>
    ipcRenderer.invoke(EventTypeMain.FleetDockHudSnapshot),
  getGroundStationStatus: () =>
    ipcRenderer.invoke(EventTypeMain.GroundStationStatus),
  launchGroundStation: () =>
    ipcRenderer.invoke(EventTypeMain.GroundStationLaunch),
  closeGroundStation: () =>
    ipcRenderer.invoke(EventTypeMain.GroundStationClose),
  selectGroundStationExecutable: () =>
    ipcRenderer.invoke(EventTypeMain.GroundStationSelectExecutable),
  openGroundStationInstallDirectory: () =>
    ipcRenderer.invoke(EventTypeMain.GroundStationOpenInstallDirectory),
  runFleetDockDebugCommand: (request: unknown) =>
    ipcRenderer.invoke(EventTypeMain.FleetDockDebugCommand, request),
  openFleetDockHudWindows: (devices: unknown[]) =>
    ipcRenderer.invoke(EventTypeMain.FleetDockHudWindowOpen, devices)
};
