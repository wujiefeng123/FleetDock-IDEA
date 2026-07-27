// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import { app } from 'electron';
import * as path from 'path';

// [UAV-IDE MODIFY] Resolve bundled UAV simulation dependencies from the IDE install lib directory.
export function getSkyLabInstallRoot(): string {
  if (app.isPackaged) {
    return path.dirname(process.execPath);
  }
  return process.cwd();
}

export function getSkyLabLibRoot(): string {
  if (app.isPackaged) {
    return path.join(getSkyLabInstallRoot(), 'lib');
  }
  return path.join(process.cwd(), 'dist-resources', 'win', 'lib');
}

export function getDefaultPx4Path(): string {
  return path.join(getSkyLabLibRoot(), 'PX4');
}

export function getDefaultSkyLabSimPath(): string {
  return path.join(getSkyLabLibRoot(), 'SkyLabSim');
}

export function getDefaultPx4WslPath(): string {
  return windowsPathToWslPath(getDefaultPx4Path());
}

export function windowsPathToWslPath(inputPath: string): string {
  const normalized = String(inputPath || '').trim();
  const match = /^([a-zA-Z]):[\\/](.*)$/.exec(normalized);
  if (!match) {
    return normalized;
  }
  const drive = match[1].toLowerCase();
  const rest = match[2].replace(/[\\/]+/g, '/');
  return `/mnt/${drive}/${rest}`;
}
