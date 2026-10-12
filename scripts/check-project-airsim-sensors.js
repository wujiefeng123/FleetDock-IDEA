// [UAV-IDE MODIFY] Focused regression checks for device sensor validation and Project AirSim JSONC generation.

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const {
  createDefaultProjectAirsimSensors,
  createProjectAirsimSensorPreset,
  normalizeManagedSensors
} = require('../build/out/main/devices/sensors/project-airsim-sensors.js');
const {
  injectProjectAirsimSensors
} = require('../build/out/main/uavsimulation/config/project-airsim-sensor-config.js');

function templatePaths() {
  const directory = path.join(
    __dirname,
    '..',
    'dist-resources',
    'skylab-simulation',
    'templates'
  );
  return fs
    .readdirSync(directory)
    .filter(name => /^robot_.+_px4\.jsonc$/.test(name))
    .map(name => path.join(directory, name));
}

function checkTemplate(filePath) {
  const source = fs.readFileSync(filePath, 'utf8');
  const sensors = createDefaultProjectAirsimSensors();
  sensors.push(createProjectAirsimSensorPreset('lidar', 'LidarFront'));
  sensors.push(
    createProjectAirsimSensorPreset('distance-sensor', 'DistanceFront')
  );
  const generated = injectProjectAirsimSensors(source, sensors);

  assert(generated.includes('"id": "LidarFront"'));
  assert(generated.includes('"lidar-type": "generic_cylindrical"'));
  assert(generated.includes('"id": "DistanceFront"'));
  assert(generated.includes('"type": "distance-sensor"'));
  assert(generated.includes('"__TCP_PORT__"'));
  assert.strictEqual((generated.match(/"sensors"\s*:/g) || []).length, 1);
  assert.strictEqual((generated.match(/"id": "Chase"/g) || []).length, 1);
}

const paths = templatePaths();
assert(paths.length > 0, 'No Project AirSim robot template was found.');
paths.forEach(checkTemplate);

const clientPaths = [
  path.join(
    __dirname,
    '..',
    'dist-resources',
    'skylab-simulation',
    'client',
    'project_airsim_client.py'
  )
];
const clientSources = clientPaths.map(clientPath =>
  fs.readFileSync(clientPath, 'utf8')
);
for (const clientSource of clientSources) {
  assert.strictEqual(
    (clientSource.match(/ProjectAirSimClient\(\)/g) || []).length,
    1
  );
  assert(clientSource.includes('create_projectairsim_backends'));
  assert(clientSource.includes('projectairsim_client=client'));
  assert(clientSource.includes('cameraStreams'));
  assert(clientSource.includes('sensor_id=sensor_id'));
  assert(clientSource.includes('rtsp_streams.append'));
  assert(!clientSource.includes('_preview_handler='));
  assert(!clientSource.includes('MjpegPreviewServer'));
  assert(!clientSource.includes('FleetDockRtspPreviewConsumer'));
  assert(clientSource.includes('sensor_service.close()'));
  // [UAV-IDE MODIFY] Runtime interactions must use scene APIs with observable effects.
  assert(clientSource.includes('"simulation.pause.toggle"'));
  assert(clientSource.includes('"weather.set"'));
  assert(clientSource.includes('world.pause()'));
  assert(clientSource.includes('world.resume()'));
  assert(clientSource.includes('world.set_weather_visual_effects_param'));
  // [UAV-IDE MODIFY] AirSimNH Shipping crashes inside its time-of-day API path.
  assert(!clientSource.includes('"time.toggle"'));
  assert(!clientSource.includes('world.set_time_of_day'));
  assert(!clientSource.includes('world.set_sun_position_from_date_time'));
  assert(
    clientSource.indexOf('sensor_service.close()') <
      clientSource.indexOf('client.disconnect()')
  );
}

// [UAV-IDE MODIFY] Every camera must negotiate WebRTC through the managed MediaMTX service.
const mediaServiceSource = fs.readFileSync(
  path.join(
    __dirname,
    '..',
    'src',
    'main',
    'uavsimulation',
    'media',
    'mediamtx-service.ts'
  ),
  'utf8'
);
const commandCenterSource = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'assets', 'skylab-command-center.js'),
  'utf8'
);
const primaryCommandsStart = commandCenterSource.indexOf(
  '  const SIMULATION_PRIMARY_COMMANDS'
);
const primaryCommandsEnd = commandCenterSource.indexOf(
  '  const SIMULATION_COMMANDS',
  primaryCommandsStart
);
assert(primaryCommandsStart >= 0 && primaryCommandsEnd > primaryCommandsStart);
const primaryCommands = commandCenterSource.slice(
  primaryCommandsStart,
  primaryCommandsEnd
);
assert(primaryCommands.includes("'simulation.pause.toggle'"));
assert(primaryCommands.includes("'weather.set'"));
assert(primaryCommands.includes("'simulation.snapshot'"));
assert(!primaryCommands.includes("'time.toggle'"));
assert(!primaryCommands.includes("'trace.toggle'"));
assert(!primaryCommands.includes("'markers.clear'"));
const hudSpeedStart = commandCenterSource.indexOf('  function hudSpeed');
const hudSpeedEnd = commandCenterSource.indexOf(
  '  function hudTelemetryAge',
  hudSpeedStart
);
assert(hudSpeedStart >= 0 && hudSpeedEnd > hudSpeedStart);
const hudSpeedHelpers = new Function(
  `${commandCenterSource.slice(
    hudSpeedStart,
    hudSpeedEnd
  )}\nreturn { hudSpeed, hudVerticalSpeed };`
)();
assert.strictEqual(
  hudSpeedHelpers.hudSpeed({ velocity: { x: null, y: null } }),
  '--'
);
assert.strictEqual(
  hudSpeedHelpers.hudVerticalSpeed({ velocity: { z: null } }),
  '--'
);
assert.strictEqual(
  hudSpeedHelpers.hudSpeed({ velocity: { x: 0, y: 0 } }),
  '0.0'
);
const fleetDockRtspSource = fs.readFileSync(
  path.join(
    __dirname,
    '..',
    'dist-resources',
    'skylab-runtime',
    'fleetdock-src',
    'fleetdock',
    'simulation',
    'projectairsim_rtsp.py'
  ),
  'utf8'
);
assert(mediaServiceSource.includes("const MEDIAMTX_VERSION = '1.19.2'"));
assert(mediaServiceSource.includes("'POST'"));
assert(mediaServiceSource.includes('/whep'));
assert(commandCenterSource.includes('getUavSimulationCameraStreams'));
assert(commandCenterSource.includes('createUavSimulationWebRtcSession'));
assert(commandCenterSource.includes('new RTCPeerConnection()'));
assert(commandCenterSource.includes("normalizedId.startsWith('px4.')"));
assert(
  commandCenterSource.includes(
    'await api.getUavSimulationCameraStreams(vehicleName)'
  )
);
// [UAV-IDE MODIFY] A later disconnected PX4 proxy must not replace a live same-kind Project AirSim sample.
const inventoryStart = commandCenterSource.indexOf(
  '  function hudConfiguredDevice'
);
const inventoryEnd = commandCenterSource.indexOf(
  '  function hudVehicleName',
  inventoryStart
);
assert(inventoryStart >= 0 && inventoryEnd > inventoryStart);
const hudSensorInventory = new Function(
  'state',
  'ensureArray',
  `${commandCenterSource.slice(
    inventoryStart,
    inventoryEnd
  )}\nreturn hudSensorInventory;`
)(
  {
    devices: [
      {
        id: 'sim-one',
        sensors: [
          {
            id: 'configured-imu',
            name: 'IMU',
            type: 'imu',
            enabled: true
          }
        ]
      }
    ]
  },
  value => (Array.isArray(value) ? value : [])
);
const mergedHudSensors = hudSensorInventory({
  deviceId: 'sim-one',
  sensors: [
    {
      id: 'IMU1',
      type: 'imu',
      source: 'simulated',
      discovered: true,
      subscribed: true,
      sample: { sequence: 1 }
    },
    {
      id: 'px4.imu',
      type: 'imu',
      source: 'unknown',
      discovered: true,
      subscribed: false,
      sample: null
    }
  ]
});
assert.strictEqual(mergedHudSensors.length, 1);
assert.strictEqual(mergedHudSensors[0].id, 'configured-imu');
assert.strictEqual(mergedHudSensors[0].runtimeSensorId, 'IMU1');
assert.deepStrictEqual(mergedHudSensors[0].sample, { sequence: 1 });
assert(!commandCenterSource.includes('multipart/x-mixed-replace'));
assert(!fleetDockRtspSource.includes('_preview_handler'));
assert(
  !fs.existsSync(
    path.join(
      __dirname,
      '..',
      'dist-resources',
      'skylab-runtime',
      'fleetdock_ide_runtime',
      'mjpeg_preview.py'
    )
  )
);

assert.throws(
  () =>
    normalizeManagedSensors(
      [
        createProjectAirsimSensorPreset('gps', 'GPS'),
        createProjectAirsimSensorPreset('imu', 'gps')
      ],
      { includeDefaults: false, strict: true }
    ),
  /ID 重复/
);

const invalidDistance = createProjectAirsimSensorPreset(
  'distance-sensor',
  'DistanceInvalid'
);
invalidDistance.config.minDistance = 10;
invalidDistance.config.maxDistance = 5;
assert.throws(
  () =>
    normalizeManagedSensors([invalidDistance], {
      includeDefaults: false,
      strict: true
    }),
  /最大距离必须大于最小距离/
);

assert.throws(
  () => injectProjectAirsimSensors('{ "links": [] }', []),
  /缺少顶层 sensors 数组/
);

console.log(
  `Project AirSim sensor checks passed for ${paths.length} robot template(s).`
);
