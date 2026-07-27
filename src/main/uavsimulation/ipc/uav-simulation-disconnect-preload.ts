// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import { EventTypeMain } from '../../eventtypes';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('uavSimulationDisconnectDialog', {
  // [UAV-IDE MODIFY] Return the scoped stop-simulation confirmation from the SkyLab dialog.
  choose: (confirmed: boolean) => {
    ipcRenderer.send(
      EventTypeMain.UavSimulationDisconnectDialogChoice,
      confirmed
    );
  }
});

export {};
