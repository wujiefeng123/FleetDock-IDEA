import { afterEach, describe, expect, it } from 'vitest';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { app } from '../setup/electron-stub';
import {
  QGC_MAVLINK_LOCAL_PORT_BASE,
  QGC_MAVLINK_REMOTE_PORT
} from '../../src/main/groundstation/qgc-link';
import {
  findQgcExecutable,
  getBundledQgcPath
} from '../../src/main/groundstation/qgc-paths';

const temporaryDirectories: string[] = [];

afterEach(() => {
  for (const directory of temporaryDirectories.splice(0)) {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

describe('QGroundControl integration contracts', () => {
  it('keeps the QGC target separate from SDK listener ports', () => {
    expect(QGC_MAVLINK_REMOTE_PORT).toBe(14550);
    expect(QGC_MAVLINK_LOCAL_PORT_BASE).toBeGreaterThan(14699);
  });

  it('resolves the bundled executable below the shared FleetDock lib root', () => {
    expect(getBundledQgcPath()).toBe(
      path.join(
        process.cwd(),
        'dist-resources',
        'win',
        'lib',
        'QGroundControl',
        'bin',
        'QGroundControl.exe'
      )
    );
  });

  it('resolves the installed executable beside the packaged IDE', () => {
    app.isPackaged = true;
    try {
      expect(getBundledQgcPath()).toBe(
        path.join(
          path.dirname(process.execPath),
          'lib',
          'QGroundControl',
          'bin',
          'QGroundControl.exe'
        )
      );
    } finally {
      app.isPackaged = false;
    }
  });

  it('accepts an explicitly selected QGroundControl executable', () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'fleetdock-qgc-'));
    temporaryDirectories.push(directory);
    const executablePath = path.join(directory, 'QGroundControl.exe');
    fs.writeFileSync(executablePath, 'test', 'utf8');
    expect(findQgcExecutable(executablePath)).toBe(executablePath);
  });

  it('uses the PX4 default Normal stream mode for the QGC output', () => {
    const supervisorSource = fs.readFileSync(
      path.join(
        process.cwd(),
        'src',
        'main',
        'uavsimulation',
        'process',
        'simulation-process-supervisor.ts'
      ),
      'utf8'
    );
    expect(supervisorSource).toContain('`-o ${QGC_MAVLINK_REMOTE_PORT}`');
    expect(supervisorSource).not.toContain("'-m normal'");
  });
});
