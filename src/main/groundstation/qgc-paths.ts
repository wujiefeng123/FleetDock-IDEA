import * as fs from 'fs';
import * as path from 'path';
import { getSkyLabLibRoot } from '../uavsimulation/config/simulation-install-paths';

// [UAV-IDE MODIFY] Keep QGC discovery tied to the same install root as other FleetDock tools.
export function getBundledQgcPath(): string {
  return path.join(
    getSkyLabLibRoot(),
    'QGroundControl',
    'bin',
    'QGroundControl.exe'
  );
}

export function findQgcExecutable(configuredPath?: string): string | undefined {
  const candidates = [
    configuredPath,
    getBundledQgcPath(),
    process.env.ProgramFiles &&
      path.join(
        process.env.ProgramFiles,
        'QGroundControl',
        'bin',
        'QGroundControl.exe'
      ),
    process.env['ProgramFiles(x86)'] &&
      path.join(
        process.env['ProgramFiles(x86)'],
        'QGroundControl',
        'bin',
        'QGroundControl.exe'
      )
  ];
  return candidates.find((candidate): candidate is string => {
    if (typeof candidate !== 'string' || !candidate) {
      return false;
    }
    try {
      return fs.statSync(candidate).isFile();
    } catch {
      return false;
    }
  });
}
