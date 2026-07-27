import { EventTypeMain } from '../fleetdock/eventtypes';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  // [UAV-IDE MODIFY] Expose CodeAppCenter publish actions to the isolated publish dialog.
  getCodeAppCenterState: () =>
    ipcRenderer.invoke(EventTypeMain.CodeAppCenterGetState),
  loginCodeAppCenter: (data: any) =>
    ipcRenderer.invoke(EventTypeMain.CodeAppCenterLogin, data),
  listCodeApps: () => ipcRenderer.invoke(EventTypeMain.CodeAppCenterListApps),
  readRequirements: () =>
    ipcRenderer.invoke(EventTypeMain.CodeAppCenterReadRequirements),
  scanDependencies: () =>
    ipcRenderer.invoke(EventTypeMain.CodeAppCenterScanDependencies),
  publishToCodeAppCenter: (data: any) =>
    ipcRenderer.invoke(EventTypeMain.CodeAppCenterPublish, data)
});

export {};
