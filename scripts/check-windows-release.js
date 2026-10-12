// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Prevent an unsigned or prerelease FleetDock installer from
// being mistaken for a formal Windows release.

const packageJson = require('../package.json');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const failures = [];
if (packageJson.name !== 'fleetdock-idea') {
  failures.push('package name is not fleetdock-idea');
}
if (packageJson.build?.appId !== 'com.fleetdock.idea') {
  failures.push('desktop appId is not com.fleetdock.idea');
}
if (String(packageJson.version).includes('-rc.')) {
  failures.push(`package version '${packageJson.version}' is still an RC`);
}
if (packageJson.author?.name !== 'FleetDock IDEA Team') {
  failures.push('package author is not FleetDock IDEA Team');
}
if (packageJson.build?.publish !== null) {
  failures.push(
    'desktop publishing must remain disabled until FleetDock owns an update service'
  );
}
if (packageJson.bugs || packageJson.homepage || packageJson.repository) {
  failures.push(
    'package support metadata must not point FleetDock users to the upstream project'
  );
}
if (!process.env.CSC_LINK) {
  failures.push('CSC_LINK is not configured');
}
if (!process.env.CSC_KEY_PASSWORD) {
  failures.push('CSC_KEY_PASSWORD is not configured');
}

const qgcRoot = path.resolve(
  __dirname,
  '..',
  'dist-resources',
  'win',
  'lib',
  'QGroundControl'
);
const qgcExecutable = path.join(qgcRoot, 'bin', 'QGroundControl.exe');
const qgcManifestPath = path.join(qgcRoot, 'fleetdock-qgc-manifest.json');
const qgcLicensePath = path.join(qgcRoot, 'QGroundControl-LICENSE-APACHE');
const expectedQgcRelease = require('../dist-resources/qgroundcontrol-release.json');
try {
  const qgcManifest = JSON.parse(fs.readFileSync(qgcManifestPath, 'utf8'));
  const executableHash = crypto
    .createHash('sha256')
    .update(fs.readFileSync(qgcExecutable))
    .digest('hex');
  if (qgcManifest.architecture !== 'x64') {
    failures.push('staged QGroundControl architecture is not x64');
  }
  if (qgcManifest.version !== expectedQgcRelease.version) {
    failures.push(
      `staged QGroundControl version '${qgcManifest.version}' is not verified as ${expectedQgcRelease.version}`
    );
  }
  if (qgcManifest.executableSha256 !== executableHash) {
    failures.push('staged QGroundControl executable checksum does not match');
  }
  if (!fs.statSync(qgcLicensePath).isFile()) {
    failures.push('QGroundControl Apache 2.0 license is not packaged');
  }
} catch (error) {
  failures.push(`QGroundControl package is not staged: ${error.message}`);
}

if (failures.length > 0) {
  console.error(`Windows release preflight failed: ${failures.join('; ')}`);
  process.exit(1);
}

console.log(`Windows release preflight passed for ${packageJson.version}.`);
