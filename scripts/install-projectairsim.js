// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

// [UAV-IDE MODIFY] Install the Project AirSim Python client into the bundled FleetDock IDEA runtime during env packaging.
const envPath = path.resolve(__dirname, '..', 'env_installer', 'jlab_server');
const pythonPath =
  process.platform === 'win32'
    ? path.join(envPath, 'python.exe')
    : path.join(envPath, 'bin', 'python');

if (!fs.existsSync(pythonPath)) {
  throw new Error(`Bundled Python was not found at ${pythonPath}`);
}

execFileSync(
  pythonPath,
  ['-m', 'pip', 'install', 'projectairsim', 'cryptography<46'],
  { stdio: 'inherit' }
);
