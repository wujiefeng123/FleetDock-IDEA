import { EventTypeMain } from '../eventtypes';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  // [UAV-IDE MODIFY] Load the global drone device library inside the standalone device-manager dialog.
  loadDroneDevices: () => {
    return ipcRenderer.invoke(EventTypeMain.DeviceManagerLoad);
  },
  // [UAV-IDE MODIFY] Save virtual or physical drone devices through the shell so the renderer never touches the filesystem directly.
  saveDroneDevice: (device: any) => {
    return ipcRenderer.invoke(EventTypeMain.DeviceManagerSave, device);
  },
  // [UAV-IDE MODIFY] Delete a managed drone device through the shell to keep persistence centralized.
  deleteDroneDevice: (deviceId: string) => {
    return ipcRenderer.invoke(EventTypeMain.DeviceManagerDelete, deviceId);
  },
  // [UAV-IDE MODIFY] Probe an unsaved physical PX4 connection in the main process with the bundled runtime.
  testPhysicalConnection: (connection: any) => {
    return ipcRenderer.invoke(EventTypeMain.DeviceManagerProbe, connection);
  },
  // [UAV-IDE MODIFY] Enumerate COM devices without opening them so users can select a telemetry radio safely.
  listSerialPorts: () => {
    return ipcRenderer.invoke(EventTypeMain.DeviceManagerSerialPorts);
  },
  // [UAV-IDE MODIFY] Bind a saved device to the active application through a
  // main-process transaction that also regenerates runtime artifacts.
  bindDroneDevice: (deviceId: string, alias: string) => {
    return ipcRenderer.invoke(EventTypeMain.DeviceManagerBind, {
      deviceId,
      alias
    });
  },
  copyPhysicalProbeReport: (report: any) => {
    return ipcRenderer.invoke(
      EventTypeMain.DeviceManagerCopyProbeReport,
      report
    );
  },
  exportPhysicalProbeReport: (report: any) => {
    return ipcRenderer.invoke(
      EventTypeMain.DeviceManagerExportProbeReport,
      report
    );
  }
});

export {};
