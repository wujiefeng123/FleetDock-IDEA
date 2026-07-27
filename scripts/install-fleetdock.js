// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

// [UAV-IDE MODIFY] Install FleetDock from the packaged source tree into the bundled FleetDock IDEA Python runtime.
const envPath = path.resolve(__dirname, '..', 'env_installer', 'jlab_server');
const sourcePath = path.resolve(
  __dirname,
  '..',
  'dist-resources',
  'skylab-runtime',
  'fleetdock-src'
);
const pythonPath =
  process.platform === 'win32'
    ? path.join(envPath, 'python.exe')
    : path.join(envPath, 'bin', 'python');

if (!fs.existsSync(pythonPath)) {
  throw new Error(`Bundled Python was not found at ${pythonPath}`);
}

if (!fs.existsSync(path.join(sourcePath, 'pyproject.toml'))) {
  throw new Error(`FleetDock source package was not found at ${sourcePath}`);
}

// [UAV-IDE MODIFY] Bundle every implemented FleetDock runtime integration, including Project AirSim RTSP/GStreamer.
execFileSync(pythonPath, ['-m', 'pip', 'install', `${sourcePath}[full-prod]`], {
  stdio: 'inherit'
});
