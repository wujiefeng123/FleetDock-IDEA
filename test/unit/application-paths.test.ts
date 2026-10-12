import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as path from 'path';
import { app } from 'electron';

import { APP_DATA_NAME } from '../../src/main/branding';
import { configureFleetDockApplicationPaths } from '../../src/main/fleetdock/application-paths';

describe('FleetDock application paths', () => {
  const originalPlatform = process.platform;
  const originalHome = process.env.HOME;
  const originalSnap = process.env.SNAP;
  const originalXdgConfigHome = process.env.XDG_CONFIG_HOME;
  const originalXdgDataHome = process.env.XDG_DATA_HOME;

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(app.getPath).mockImplementation(name =>
      path.join('C:\\profile', name)
    );
    vi.mocked(app.commandLine.hasSwitch).mockReturnValue(false);
    delete process.env.SNAP;
  });

  afterEach(() => {
    Object.defineProperty(process, 'platform', { value: originalPlatform });
    restoreEnvironment('HOME', originalHome);
    restoreEnvironment('SNAP', originalSnap);
    restoreEnvironment('XDG_CONFIG_HOME', originalXdgConfigHome);
    restoreEnvironment('XDG_DATA_HOME', originalXdgDataHome);
  });

  it('uses the fixed FleetDock data directory on a regular installation', () => {
    Object.defineProperty(process, 'platform', { value: 'win32' });

    configureFleetDockApplicationPaths();

    expect(APP_DATA_NAME).toBe('fleetdock-idea');
    expect(app.setPath).toHaveBeenCalledWith(
      'userData',
      path.join('C:\\profile', 'appData', 'fleetdock-idea')
    );
  });

  it('preserves an explicit user-data-dir override', () => {
    vi.mocked(app.commandLine.hasSwitch).mockReturnValue(true);

    configureFleetDockApplicationPaths();

    expect(app.setPath).not.toHaveBeenCalled();
  });

  it('uses a stable non-versioned FleetDock path in Snap', () => {
    Object.defineProperty(process, 'platform', { value: 'linux' });
    process.env.SNAP = '/snap/fleetdock-idea/current';
    process.env.HOME = '/home/fleetdock';

    configureFleetDockApplicationPaths();

    expect(app.setPath).toHaveBeenCalledWith(
      'userData',
      '/home/fleetdock/.config/fleetdock-idea'
    );
    expect(app.setAppLogsPath).toHaveBeenCalledWith(
      '/home/fleetdock/.config/fleetdock-idea/logs'
    );
  });
});

function restoreEnvironment(name: string, value: string | undefined): void {
  if (value === undefined) {
    delete process.env[name];
  } else {
    process.env[name] = value;
  }
}
