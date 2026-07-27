// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import { contextBridge, ipcRenderer } from 'electron';

const FLEETDOCK_HUD_SNAPSHOT = 'fleetdock-hud-snapshot';

// [UAV-IDE MODIFY] Expose only read-only FleetDock HUD telemetry to standalone HUD windows.
contextBridge.exposeInMainWorld('fleetDockHud', {
  getSnapshot: () => ipcRenderer.invoke(FLEETDOCK_HUD_SNAPSHOT)
});
