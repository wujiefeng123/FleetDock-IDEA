// [UAV-IDE MODIFY] Focused regression checks for physical PX4 endpoint normalization without a new test framework.

const assert = require('assert');
const path = require('path');

const root = path.resolve(__dirname, '..');
const connectionModule = require(path.join(
  root,
  'build',
  'out',
  'main',
  'devices',
  'connection-uri.js'
));
const portModule = require(path.join(
  root,
  'build',
  'out',
  'main',
  'devices',
  'port-allocator.js'
));

const {
  buildPhysicalConnectionURI,
  normalizePhysicalDeviceConnection
} = connectionModule;

const cases = [
  [
    { kind: 'udp', mode: 'listen', host: '0.0.0.0', port: 14560 },
    'udpin://0.0.0.0:14560'
  ],
  [
    { kind: 'udp', mode: 'dial', host: '192.168.1.10', port: 14550 },
    'udpout://192.168.1.10:14550'
  ],
  [
    { kind: 'tcp', mode: 'listen', host: '127.0.0.1', port: 5760 },
    'tcpin://127.0.0.1:5760'
  ],
  [
    { kind: 'tcp', mode: 'dial', host: '192.168.1.20', port: 5760 },
    'tcpout://192.168.1.20:5760'
  ],
  [
    { kind: 'serial', serialPort: 'com3', baudRate: 115200 },
    'serial://COM3:115200'
  ]
];

for (const [connection, expected] of cases) {
  assert.strictEqual(buildPhysicalConnectionURI(connection), expected);
}

const canonicalWins = normalizePhysicalDeviceConnection({
  source: 'real',
  real: {
    connection: { kind: 'serial', serialPort: 'COM8', baudRate: 921600 },
    connectionKind: 'udp'
  },
  telemetry: {
    protocol: 'udp',
    mode: 'listen',
    bindHost: '0.0.0.0',
    bindPort: 14560
  },
  fleetdock: { params: { endpoint: 'udpin://0.0.0.0:14550' } }
});
assert.deepStrictEqual(canonicalWins, {
  kind: 'serial',
  serialPort: 'COM8',
  baudRate: 921600
});
assert.strictEqual(
  buildPhysicalConnectionURI(canonicalWins),
  'serial://COM8:921600'
);

const legacy = {
  source: 'real',
  real: { connectionKind: 'serial', serialPort: 'com4', baudRate: 230400 },
  fleetdock: { params: { endpoint: 'udpin://127.0.0.1:14550' } }
};
const migratedOnce = normalizePhysicalDeviceConnection(legacy);
const migratedTwice = normalizePhysicalDeviceConnection({
  ...legacy,
  real: { ...legacy.real, connection: migratedOnce }
});
assert.deepStrictEqual(migratedTwice, migratedOnce);
// [UAV-IDE MODIFY] New and legacy serial drafts without an explicit baud rate use the telemetry-radio default.
assert.deepStrictEqual(
  normalizePhysicalDeviceConnection({
    source: 'real',
    real: { connectionKind: 'serial', serialPort: 'COM3' }
  }),
  { kind: 'serial', serialPort: 'COM3', baudRate: 57600 }
);
assert.deepStrictEqual(
  normalizePhysicalDeviceConnection(
    JSON.parse(JSON.stringify({ real: { connection: migratedOnce } }))
  ),
  migratedOnce
);

assert.strictEqual(portModule.SIMULATION_MAVLINK_PORT_MIN, 14600);
assert.strictEqual(portModule.SIMULATION_MAVLINK_PORT_MAX, 14699);
assert.strictEqual(portModule.SIMULATION_MAVSDK_SERVER_PORT_MIN, undefined);
assert.strictEqual(
  portModule.mavsdkServerPortForSimulationMavlinkPort,
  undefined
);

console.log('Physical PX4 connection checks passed.');
