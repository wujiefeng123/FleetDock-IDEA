// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import { app } from 'electron';
import * as path from 'path';

import { APP_DATA_NAME } from '../branding';

// [UAV-IDE MODIFY] Fix persistent FleetDock data to a product-owned path before configuration modules are loaded.
export function configureFleetDockApplicationPaths(): void {
  const isSnap = process.platform === 'linux' && process.env.SNAP !== undefined;

  if (isSnap) {
    const userHome = process.env.HOME;
    process.env.XDG_CONFIG_HOME = path.posix.join(userHome, '.config');
    // Jupyter uses this path (.local/share).
    process.env.XDG_DATA_HOME = path.posix.join(userHome, '.local', 'share');
    app.setPath('home', userHome);
    app.setPath('appData', process.env.XDG_CONFIG_HOME);

    const userDataDir = path.posix.join(
      process.env.XDG_CONFIG_HOME,
      APP_DATA_NAME
    );
    app.setPath('userData', userDataDir);
    app.setAppLogsPath(path.posix.join(userDataDir, 'logs'));
    return;
  }

  // Preserve explicit test and command-line isolation instead of overriding it.
  if (app.commandLine.hasSwitch('user-data-dir')) {
    return;
  }

  app.setPath('userData', path.join(app.getPath('appData'), APP_DATA_NAME));
}
