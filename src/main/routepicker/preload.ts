import { EventTypeMain } from '../eventtypes';

const { contextBridge, ipcRenderer } = require('electron');

// [UAV-IDE MODIFY] Expose the route-picker bridge so the same multi-waypoint editor can be launched from multiple application env forms.
contextBridge.exposeInMainWorld('electronAPI', {
  confirmRoute: (route: string) => {
    return ipcRenderer.invoke(EventTypeMain.RoutePickerConfirm, route);
  },
  cancelRoute: () => {
    return ipcRenderer.invoke(EventTypeMain.RoutePickerCancel);
  }
});

export {};
