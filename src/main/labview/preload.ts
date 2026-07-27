import electron = require('electron');
import { EventTypeMain } from '../eventtypes';
import { fleetDockLabPreloadApi } from '../fleetdock/lab-preload-api';

const { contextBridge, ipcRenderer } = electron;

contextBridge.exposeInMainWorld('electronAPI', {
  getServerInfo: () => {
    return ipcRenderer.invoke(EventTypeMain.GetServerInfo);
  },
  // [UAV-IDE MODIFY] Keep FleetDock's renderer bridge in its extension module.
  ...fleetDockLabPreloadApi,
  broadcastLabUIReady: () => {
    ipcRenderer.send(EventTypeMain.LabUIReady);
  }
});

export {};
