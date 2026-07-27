import { EventTypeMain } from '../fleetdock/eventtypes';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  // [UAV-IDE MODIFY] Confirming or canceling a waypoint selection is handled by the main-process modal.
  confirmWaypoint: (waypoint: any) => {
    return ipcRenderer.invoke(EventTypeMain.WaypointPickerConfirm, waypoint);
  },
  cancelWaypoint: () => {
    return ipcRenderer.invoke(EventTypeMain.WaypointPickerCancel);
  }
});

export {};
