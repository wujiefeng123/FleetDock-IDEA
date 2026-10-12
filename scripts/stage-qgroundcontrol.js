// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Stage a verified, unmodified QGC payload without committing binaries.

const crypto = require('crypto');
const { execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const repositoryRoot = path.resolve(__dirname, '..');
const stagingRoot = path.join(
  repositoryRoot,
  'dist-resources',
  'win',
  'lib',
  'QGroundControl'
);
const executableRelativePath = path.join('bin', 'QGroundControl.exe');
const manifestName = 'fleetdock-qgc-manifest.json';
const licenseName = 'QGroundControl-LICENSE-APACHE';
const release = JSON.parse(
  fs.readFileSync(
    path.join(repositoryRoot, 'dist-resources', 'qgroundcontrol-release.json'),
    'utf8'
  )
);

function sha256(filePath) {
  const hash = crypto.createHash('sha256');
  hash.update(fs.readFileSync(filePath));
  return hash.digest('hex');
}

function isQgcRoot(candidate) {
  try {
    return fs.statSync(path.join(candidate, executableRelativePath)).isFile();
  } catch {
    return false;
  }
}

function isVerifiedStage() {
  try {
    const manifest = JSON.parse(
      fs.readFileSync(path.join(stagingRoot, manifestName), 'utf8')
    );
    return (
      manifest.version === release.version &&
      manifest.upstreamInstallerSha256 === release.installerSha256 &&
      manifest.executableSha256 ===
        sha256(path.join(stagingRoot, executableRelativePath)) &&
      fs.statSync(path.join(stagingRoot, licenseName)).isFile()
    );
  } catch {
    return false;
  }
}

function download(url, destination, expectedHash) {
  // curl keeps Windows certificate validation while avoiding a broken revocation service.
  execFileSync(
    'curl.exe',
    [
      '--ssl-no-revoke',
      '--location',
      '--retry',
      '8',
      '--retry-all-errors',
      '--retry-delay',
      '2',
      '--continue-at',
      '-',
      '--fail',
      '--silent',
      '--show-error',
      '--output',
      destination,
      url
    ],
    { stdio: 'inherit' }
  );
  if (sha256(destination) !== expectedHash) {
    throw new Error(`Downloaded file SHA-256 mismatch: ${url}`);
  }
}

function windowsPathToWsl(filePath) {
  const resolved = path.resolve(filePath);
  const match = /^([A-Za-z]):[\\/](.*)$/.exec(resolved);
  if (!match) {
    throw new Error(`WSL cannot access path: ${resolved}`);
  }
  return `/mnt/${match[1].toLowerCase()}/${match[2].replace(/\\/g, '/')}`;
}

function bashQuote(value) {
  return `'${String(value).replace(/'/g, "'\\''")}'`;
}

async function extractOfficialInstaller(temporaryRoot) {
  const installerPath = path.join(temporaryRoot, release.installerFileName);
  const extractorArchive = path.join(temporaryRoot, '7zip-linux.tar.xz');
  const extractorPath = path.join(temporaryRoot, '7zz');
  const sourceRoot = path.join(temporaryRoot, 'QGroundControl');
  download(release.downloadUrl, installerPath, release.installerSha256);
  download(release.extractorUrl, extractorArchive, release.extractorSha256);
  execFileSync('tar.exe', [
    '-xf',
    extractorArchive,
    '-C',
    temporaryRoot,
    '7zz'
  ]);
  fs.mkdirSync(sourceRoot);
  const script = [
    `chmod +x ${bashQuote(windowsPathToWsl(extractorPath))}`,
    `${bashQuote(windowsPathToWsl(extractorPath))} x -y -o${bashQuote(
      windowsPathToWsl(sourceRoot)
    )} ${bashQuote(windowsPathToWsl(installerPath))}`
  ].join(' && ');
  execFileSync(
    'wsl.exe',
    ['-d', 'FleetDock-Ubuntu', '--', 'bash', '-lc', script],
    { stdio: 'inherit' }
  );
  if (!isQgcRoot(sourceRoot)) {
    throw new Error(
      'Official installer extraction did not contain QGroundControl.exe.'
    );
  }
  return sourceRoot;
}

function stage(sourceRoot) {
  const resolvedSource = path.resolve(sourceRoot);
  const resolvedStaging = path.resolve(stagingRoot);
  const allowedParent = path.resolve(
    repositoryRoot,
    'dist-resources',
    'win',
    'lib'
  );
  if (
    resolvedSource === resolvedStaging ||
    !resolvedStaging.startsWith(`${allowedParent}${path.sep}`) ||
    !isQgcRoot(resolvedSource)
  ) {
    throw new Error('QGroundControl staging paths are invalid.');
  }
  const executableSha256 = sha256(
    path.join(resolvedSource, executableRelativePath)
  );
  fs.rmSync(resolvedStaging, { recursive: true, force: true });
  fs.mkdirSync(path.dirname(resolvedStaging), { recursive: true });
  fs.cpSync(resolvedSource, resolvedStaging, {
    recursive: true,
    // NSIS extraction exposes a temporary plugin payload beside the product files.
    filter: sourcePath =>
      path.basename(sourcePath).toUpperCase() !== '$PLUGINSDIR'
  });
  fs.copyFileSync(
    path.join(
      repositoryRoot,
      'dist-resources',
      'qgroundcontrol-LICENSE-APACHE'
    ),
    path.join(resolvedStaging, licenseName)
  );
  fs.writeFileSync(
    path.join(resolvedStaging, manifestName),
    `${JSON.stringify(
      {
        version: release.version,
        tag: release.tag,
        architecture: release.architecture,
        upstreamDownloadUrl: release.downloadUrl,
        upstreamInstallerSha256: release.installerSha256,
        sourceUrl: release.sourceUrl,
        license: release.license,
        licenseUrl: release.licenseUrl,
        executableSha256
      },
      null,
      2
    )}\n`,
    'utf8'
  );
  console.log(`Staged QGroundControl ${release.version}.`);
}

async function main() {
  if (!process.env.QGC_SOURCE_DIR && isVerifiedStage()) {
    console.log(`QGroundControl ${release.version} is already staged.`);
    return;
  }
  const temporaryRoot = fs.mkdtempSync(
    path.join(os.tmpdir(), 'fleetdock-qgc-stage-')
  );
  try {
    let sourceRoot = process.env.QGC_SOURCE_DIR;
    if (sourceRoot) {
      if (
        process.env.QGC_SOURCE_VERSION !== release.version ||
        !process.env.QGC_INSTALLER_PATH ||
        sha256(process.env.QGC_INSTALLER_PATH) !== release.installerSha256
      ) {
        throw new Error(
          'Custom QGC_SOURCE_DIR requires the pinned QGC_SOURCE_VERSION and verified QGC_INSTALLER_PATH.'
        );
      }
    } else {
      sourceRoot = await extractOfficialInstaller(temporaryRoot);
    }
    stage(sourceRoot);
  } finally {
    // The path is created by mkdtempSync under the system temp directory above.
    fs.rmSync(temporaryRoot, { recursive: true, force: true });
  }
}

main().catch(error => {
  console.error(`QGroundControl staging failed: ${error.message}`);
  process.exitCode = 1;
});
