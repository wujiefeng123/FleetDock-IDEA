// [UAV-IDE MODIFY] Verify the no-hardware probe path returns a complete,
// exportable report without starting or retaining a physical connection.

const assert = require('assert');
const { app } = require('electron');

app.whenReady().then(async () => {
  const {
    probePhysicalConnection
  } = require('../build/out/main/devices/physical-connection-probe.js');
  const report = await probePhysicalConnection({
    kind: 'serial',
    serialPort: 'COM9999',
    baudRate: 115200
  });
  assert.strictEqual(report.reportVersion, 1);
  assert.strictEqual(report.ok, false);
  assert.strictEqual(report.connected, false);
  assert.strictEqual(report.telemetryReceived, false);
  assert.strictEqual(report.released, true);
  assert.strictEqual(report.code, 'serial_not_found');
  assert(report.testedAt);
  assert(report.endpoint === 'serial://COM9999:115200');

  const endpoint = 'udpin://127.0.0.1:15999';
  const timeoutReport = await probePhysicalConnection({
    kind: 'udp',
    mode: 'listen',
    host: '127.0.0.1',
    port: 15999
  });
  assert.strictEqual(timeoutReport.ok, false);
  assert.strictEqual(timeoutReport.released, true);
  const {
    listFleetDockMavsdkServers
  } = require('../build/out/main/fleetdock/mavsdk-cleanup.js');
  assert.deepStrictEqual(listFleetDockMavsdkServers([endpoint]), []);
  console.log('Physical probe report contract checks passed.');
  app.quit();
});
