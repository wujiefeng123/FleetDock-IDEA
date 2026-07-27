// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import { ipcRenderer } from 'electron';
import { EventTypeMain } from './eventtypes';

export const fleetDockWelcomePreloadApi = {
  createNewApplication: (application: unknown) =>
    ipcRenderer.invoke(EventTypeMain.CreateNewApplication, application),
  pickWaypoint: (initialValue?: string) =>
    ipcRenderer.invoke(EventTypeMain.ShowWaypointPicker, initialValue),
  pickRoute: (initialValue?: string) =>
    ipcRenderer.invoke(EventTypeMain.ShowRoutePicker, initialValue),
  loadDroneDevices: () => ipcRenderer.invoke(EventTypeMain.DeviceManagerLoad),
  selectDirectoryPath: (currentPath?: string) =>
    ipcRenderer.invoke(EventTypeMain.SelectDirectoryPath, currentPath),
  selectFilePath: (currentPath?: string) =>
    ipcRenderer.invoke(EventTypeMain.SelectFilePath, currentPath)
};
