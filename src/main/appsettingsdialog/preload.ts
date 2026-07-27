import { EventTypeMain } from '../fleetdock/eventtypes';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  // [UAV-IDE MODIFY] Save the edited application profile back to the active workspace.
  saveApplicationSettings: (application: any) => {
    return ipcRenderer.invoke(
      EventTypeMain.SaveApplicationSettings,
      application
    );
  },
  // [UAV-IDE MODIFY] Open the dedicated waypoint picker so waypoint values stay structured.
  pickWaypoint: (initialValue?: string) => {
    return ipcRenderer.invoke(EventTypeMain.ShowWaypointPicker, initialValue);
  },
  // [UAV-IDE MODIFY] Open the dedicated route picker so ordered route values stay structured.
  pickRoute: (initialValue?: string) => {
    return ipcRenderer.invoke(EventTypeMain.ShowRoutePicker, initialValue);
  },
  // [UAV-IDE MODIFY] Let Drone-type environment variables select from the global device library.
  loadDroneDevices: () => {
    return ipcRenderer.invoke(EventTypeMain.DeviceManagerLoad);
  },
  selectFilePath: (currentPath?: string) => {
    return ipcRenderer.invoke(EventTypeMain.SelectFilePath, currentPath);
  }
});

export {};
