// [UAV-IDE MODIFY] Focused checks for Windows COM discovery and parser normalization.

const assert = require('assert');
const path = require('path');

const discovery = require(path.resolve(
  __dirname,
  '..',
  'build',
  'out',
  'main',
  'devices',
  'serial-port-discovery.js'
));

const parsed = discovery.parseSerialPortDiscoveryOutput(
  JSON.stringify([
    { port: 'COM10', name: 'COM10' },
    { port: 'com2', name: 'USB Serial Device (COM2)', manufacturer: 'Test' },
    { port: 'COM2', name: 'COM2' },
    { port: 'LPT1', name: 'Not a serial port' }
  ])
);

assert.deepStrictEqual(
  parsed.map(item => item.port),
  ['COM2', 'COM10']
);
assert.strictEqual(parsed[0].name, 'USB Serial Device (COM2)');
assert.deepStrictEqual(discovery.parseSerialPortDiscoveryOutput(''), []);
assert.deepStrictEqual(discovery.parseSerialPortDiscoveryOutput('invalid'), []);

discovery
  .discoverWindowsSerialPorts()
  .then(result => {
    assert(Array.isArray(result.ports));
    for (const port of result.ports) {
      assert(/^COM[1-9]\d*$/.test(port.port));
    }
    console.log(
      `Serial-port discovery checks passed (${result.ports.length} detected).`
    );
  })
  .catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
