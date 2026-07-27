// [UAV-IDE MODIFY] Focused Electron-main checks for FleetDock device-id bindings and generated runtime errors.

const assert = require('assert');
const { execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { app } = require('electron');

function physicalDevice(id, endpoint) {
  const serial = endpoint.startsWith('serial://');
  return {
    id,
    name: id,
    source: 'real',
    runtimeKind: 'physical',
    deviceCategory: 'uav',
    systemId: 1,
    uav: { systemId: 1 },
    real: serial
      ? {
          connection: {
            kind: 'serial',
            serialPort: endpoint.split(/[:/]/)[3],
            baudRate: Number(endpoint.split(':').pop())
          }
        }
      : {
          connection: {
            kind: 'udp',
            mode: 'listen',
            host: '0.0.0.0',
            port: Number(endpoint.split(':').pop())
          }
        },
    fleetdock: {
      product: 'px4.generic',
      builder: 'px4_generic',
      params: { endpoint, timeout_s: serial ? 10 : 30 }
    }
  };
}

function simulationDevice(id, vehicleName) {
  return {
    id,
    name: id,
    source: 'simulation',
    runtimeKind: 'simulation',
    deviceCategory: 'uav',
    systemId: 1,
    airsimVehicleName: vehicleName,
    simulation: { airsimVehicleName: vehicleName },
    uav: { systemId: 1 },
    fleetdock: {
      product: 'px4.generic',
      builder: 'px4_generic',
      params: { endpoint: 'udpin://0.0.0.0:14600', timeout_s: 30 }
    }
  };
}

function config(envVars) {
  return { envVars };
}

app
  .whenReady()
  .then(async () => {
    const {
      buildFleetDockServerEnv,
      ensureFleetDockRuntimeArtifacts
    } = require('../build/out/main/fleetdock/runtime-generator.js');
    const {
      bindDeviceToApplication
    } = require('../build/out/main/application/application-device-binding-service.js');
    const {
      readApplicationConfig
    } = require('../build/out/main/config/application.js');
    const {
      SimulationConfigGenerator
    } = require('../build/out/main/uavsimulation/config/simulation-config-generator.js');
    const workspace = fs.mkdtempSync(
      path.join(os.tmpdir(), 'skylab-runtime-check-')
    );
    try {
      const userData = path.join(workspace, 'user-data');
      fs.mkdirSync(userData, { recursive: true });
      app.setPath('userData', userData);
      const binding = bindDeviceToApplication({
        workingDirectory: workspace,
        deviceId: 'px4-bound',
        alias: 'real_drone'
      });
      assert.strictEqual(binding.alias, 'real_drone');
      assert(fs.existsSync(binding.notebookPath));
      const boundConfig = readApplicationConfig(workspace);
      assert.deepStrictEqual(
        boundConfig.envVars.map(item => ({
          type: item.type,
          name: item.name,
          value: item.value
        })),
        [{ type: 'Drone', name: 'real_drone', value: 'px4-bound' }]
      );
      assert.throws(
        () =>
          bindDeviceToApplication({
            workingDirectory: workspace,
            deviceId: 'px4-bound',
            alias: 'not-valid-alias'
          }),
        /Python/
      );
      const {
        DeviceStore
      } = require('../build/out/main/devices/storage/device-store.js');
      const store = new DeviceStore();
      const savedSerial = await store.upsert({
        name: 'serial-px4',
        source: 'real',
        runtimeKind: 'physical',
        accessType: 'physical',
        real: {
          connection: {
            kind: 'serial',
            serialPort: 'com3',
            baudRate: 115200
          },
          connectionKind: 'udp'
        },
        fleetdock: {
          product: 'px4.generic',
          builder: 'px4_generic',
          params: { endpoint: 'udpin://0.0.0.0:14550' }
        }
      });
      assert.strictEqual(savedSerial.real.connection.serialPort, 'COM3');
      assert.strictEqual(
        savedSerial.fleetdock.params.endpoint,
        'serial://COM3:115200'
      );
      assert.strictEqual(
        (await store.list())[0].fleetdock.params.endpoint,
        'serial://COM3:115200'
      );

      const updatedNetwork = await store.upsert({
        ...savedSerial,
        real: {
          ...savedSerial.real,
          connection: {
            kind: 'tcp',
            mode: 'dial',
            host: '192.168.1.8',
            port: 5760
          }
        },
        fleetdock: {
          ...savedSerial.fleetdock,
          params: {
            ...savedSerial.fleetdock.params,
            endpoint: 'serial://COM3:115200'
          }
        }
      });
      assert.strictEqual(
        updatedNetwork.fleetdock.params.endpoint,
        'tcpout://192.168.1.8:5760'
      );

      const legacyUserData = path.join(workspace, 'legacy-user-data');
      fs.mkdirSync(legacyUserData, { recursive: true });
      const legacyRegistryPath = path.join(
        legacyUserData,
        'uav-device-registry.json'
      );
      const legacyText = JSON.stringify({
        version: 2,
        devices: [
          {
            id: 'legacy-serial',
            name: 'legacy-serial',
            envVarName: 'DEVICE_DRONE_1',
            source: 'real',
            accessType: 'physical',
            real: {
              connectionKind: 'serial',
              serialPort: 'com5',
              baudRate: 57600
            },
            fleetdock: {
              product: 'px4.generic',
              builder: 'px4_generic',
              params: { endpoint: 'udpin://0.0.0.0:14550' }
            },
            createdAt: '2026-01-01T00:00:00.000Z',
            updatedAt: '2026-01-01T00:00:00.000Z'
          }
        ]
      });
      fs.writeFileSync(legacyRegistryPath, legacyText, 'utf8');
      app.setPath('userData', legacyUserData);
      const legacyStore = new DeviceStore();
      const migratedOnce = await legacyStore.list();
      const migratedTwice = await legacyStore.list();
      assert.deepStrictEqual(migratedTwice, migratedOnce);
      assert.strictEqual(
        migratedOnce[0].fleetdock.params.endpoint,
        'serial://COM5:57600'
      );
      assert.strictEqual(
        fs.readFileSync(legacyRegistryPath, 'utf8'),
        legacyText
      );

      const first = physicalDevice('px4-one', 'serial://COM7:115200');
      ensureFleetDockRuntimeArtifacts({
        workingDirectory: workspace,
        config: config([{ type: 'Drone', name: 'drone1', value: first.id }]),
        devices: [first]
      });
      const runtimePath = path.join(workspace, 'fleetdock_runtime.py');
      const runtimeStubPath = path.join(workspace, 'fleetdock_runtime.pyi');
      let runtime = fs.readFileSync(runtimePath, 'utf8');
      assert(runtime.includes('serial://COM7:115200'));
      assert(runtime.includes('"connectionOwner": "sdk-runtime" if connected'));
      assert(runtime.includes('"/diagnostic-command"'));
      assert(runtime.includes('"commandHistory": list(self._command_history)'));
      assert(runtime.includes('"packetLossPercent": None'));
      assert(runtime.includes('"safe_test_altitude"'));
      assert(runtime.includes('"AUTO_LOCK_FAILED"'));
      assert(
        runtime.includes(
          '"armed": self._value(state, "armed") if connected else None'
        )
      );
      assert(runtime.includes('telemetry_age_ms = None'));
      assert(
        runtime.includes('"alias": "drone1"') ||
          runtime.includes('\\"alias\\":\\"drone1\\"')
      );

      const updated = physicalDevice('px4-one', 'serial://COM8:921600');
      ensureFleetDockRuntimeArtifacts({
        workingDirectory: workspace,
        config: config([{ type: 'Drone', name: 'drone1', value: updated.id }]),
        devices: [updated]
      });
      runtime = fs.readFileSync(runtimePath, 'utf8');
      assert(runtime.includes('serial://COM8:921600'));
      assert(!runtime.includes('serial://COM7:115200'));

      const snapshot = physicalDevice(
        'legacy-snapshot',
        'udpin://0.0.0.0:14570'
      );
      ensureFleetDockRuntimeArtifacts({
        workingDirectory: workspace,
        config: config([
          {
            type: 'Drone',
            name: 'legacy_drone',
            value: JSON.stringify(snapshot)
          }
        ]),
        devices: []
      });
      runtime = fs.readFileSync(runtimePath, 'utf8');
      assert(runtime.includes('udpin://0.0.0.0:14570'));

      ensureFleetDockRuntimeArtifacts({
        workingDirectory: workspace,
        config: config([
          { type: 'Drone', name: 'missing_drone', value: 'deleted-device' }
        ]),
        devices: []
      });
      runtime = fs.readFileSync(runtimePath, 'utf8');
      assert(runtime.includes('deleted-device'));
      assert(runtime.includes('_BINDING_ERRORS'));

      ensureFleetDockRuntimeArtifacts({
        workingDirectory: workspace,
        config: config([
          { type: 'Drone', name: 'drone1', value: 'one' },
          { type: 'Drone', name: 'drone2', value: 'two' }
        ]),
        devices: [
          physicalDevice('one', 'udpin://0.0.0.0:14580'),
          physicalDevice('two', 'udpin://0.0.0.0:14580')
        ]
      });
      runtime = fs.readFileSync(runtimePath, 'utf8');
      assert(runtime.includes('物理连接地址冲突'));

      ensureFleetDockRuntimeArtifacts({
        workingDirectory: workspace,
        config: config([
          { type: 'Drone', name: 'drone1', value: 'one' },
          { type: 'Drone', name: 'drone1', value: 'two' },
          { type: 'Drone', name: 'not-valid-name', value: 'two' }
        ]),
        devices: [
          physicalDevice('one', 'serial://COM10:115200'),
          physicalDevice('two', 'serial://COM11:115200')
        ]
      });
      runtime = fs.readFileSync(runtimePath, 'utf8');
      assert(runtime.includes('无人机别名重复'));
      assert(runtime.includes('不是有效的 Python 标识符'));

      const simulation = simulationDevice('sim-one', 'Drone1');
      const routeValue = JSON.stringify([
        { lat: 47, lon: -122, alt: 130 },
        { lat: 47.0001, lon: -121.9999, alt: 131 }
      ]);
      const simulationConfig = config([
        { type: 'Drone', name: 'sim_drone', value: simulation.id },
        { type: 'DroneRoute', name: 'SIM_ROUTE', value: routeValue }
      ]);
      const simulationRuntime = ensureFleetDockRuntimeArtifacts({
        workingDirectory: workspace,
        config: simulationConfig,
        devices: [simulation]
      });
      runtime = fs.readFileSync(runtimePath, 'utf8');
      assert(runtime.includes('IdeSimulationSensorProxy'));
      assert(runtime.includes('airsimVehicleName'));
      assert(runtime.includes('vehicle_name=airsim_vehicle_name'));
      assert(runtime.includes('excluded_kinds=("rgb_camera",)'));
      assert(runtime.includes('class _FleetDockRoutes:'));
      assert(runtime.includes('routes = _FleetDockRoutes()'));
      assert(runtime.includes('rtsp://127.0.0.1:8554/Drone1_camera_front'));
      assert(
        runtime.includes('SensorSourceKind(params["camera_stream_source"])')
      );
      assert(
        simulationRuntime.env.FLEETDOCK_SIMULATION_MANIFEST.endsWith(
          path.join('.jupyter', 'simulation', 'runtime.json')
        )
      );
      assert(simulationRuntime.env.FLEETDOCK_IDE_RUNTIME_ROOT);
      const launchGenerator = new SimulationConfigGenerator();
      const launchPlan = launchGenerator.generate({
        workingDirectory: workspace,
        scene: {
          id: 'test-scene',
          name: 'Test Scene',
          rootDirectory: workspace,
          executablePath: path.join(workspace, 'scene.exe'),
          workingDirectory: workspace,
          engine: 'project-airsim',
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString()
        },
        config: { drones: [simulation] },
        scenePackageDirectory: path.join(
          process.cwd(),
          'dist-resources',
          'skylab-simulation'
        ),
        windowsHostAddress: '127.0.0.1',
        pythonExecutable: path.join(
          process.cwd(),
          'env_installer',
          'jlab_server',
          'python.exe'
        )
      });
      assert.strictEqual(
        launchPlan.drones[0].cameraStreamUri,
        'rtsp://127.0.0.1:8554/Drone1_camera_front'
      );
      assert(
        launchPlan.files.sensorManifestFile.endsWith(
          path.join('.jupyter', 'simulation', 'runtime.json')
        )
      );
      assert(launchPlan.runtimePythonRoot);
      ensureFleetDockRuntimeArtifacts({
        workingDirectory: workspace,
        config: simulationConfig,
        devices: [{ ...simulation, sensors: [] }]
      });
      const sensorlessRuntime = fs.readFileSync(runtimePath, 'utf8');
      assert(
        !sensorlessRuntime.includes('rtsp://127.0.0.1:8554/Drone1_camera_front')
      );
      const serverEnv = buildFleetDockServerEnv({
        workingDirectory: workspace,
        config: simulationConfig,
        runtimeEnv: simulationRuntime.env
      });
      execFileSync(
        'python',
        [
          '-c',
          'import fleetdock_runtime; from fleetdock_ide_runtime import IdeSimulationSensorProxy'
        ],
        {
          cwd: workspace,
          stdio: 'inherit',
          env: {
            ...process.env,
            ...serverEnv,
            FLEETDOCK_AUTO_INIT: '0'
          }
        }
      );
      // [UAV-IDE MODIFY] Verify the generated HUD reads the current SDK DeviceStateView through snapshot().
      execFileSync(
        'python',
        [
          '-c',
          [
            'from fleetdock_runtime import FleetDock',
            'FleetDock.init()',
            'local_route = FleetDock.routes.from_env("SIM_ROUTE", altitude_m=15.0)',
            'assert local_route.name == "SIM_ROUTE"',
            'assert len(local_route.waypoints) == 2',
            'assert local_route.waypoints[0].x_m == 0.0',
            'assert local_route.waypoints[0].y_m == 0.0',
            'assert local_route.waypoints[0].z_m == 15.0',
            'assert 11.0 < local_route.waypoints[1].x_m < 11.2',
            'assert 7.5 < local_route.waypoints[1].y_m < 7.7',
            'gps_route = FleetDock.routes.from_env("SIM_ROUTE", mode="gps")',
            'assert gps_route.waypoints[0].latitude_deg == 47.0',
            'assert gps_route.waypoints[0].longitude_deg == -122.0',
            'assert gps_route.waypoints[0].altitude_m == 130.0',
            'gps_override_route = FleetDock.routes.from_env("SIM_ROUTE", mode="gps", altitude_m=150.0)',
            'assert all(waypoint.altitude_m == 150.0 for waypoint in gps_override_route.waypoints)',
            'test_case = __import__("unittest").TestCase()',
            'test_case.assertRaisesRegex(ValueError, "mode", FleetDock.routes.from_env, "SIM_ROUTE", mode="unsupported")',
            'test_case.assertRaisesRegex(ValueError, "exceeding", FleetDock.routes.from_env, "SIM_ROUTE", max_distance_m=1.0)',
            'test_case.assertRaisesRegex(KeyError, "MISSING_ROUTE", FleetDock.routes.from_env, "MISSING_ROUTE")',
            'device = FleetDock.devices.sim_drone',
            'device.state._update(connected=True, armed=True, in_air=True, flight_mode="mission", battery_remaining_fraction=0.75, relative_altitude_m=12.5, local_z_m=-12.5, velocity_x_m_s=3.0, velocity_y_m_s=4.0, velocity_z_m_s=0.5, roll_deg=1.0, pitch_deg=2.0, yaw_deg=3.0, health_local_position_ok=True, health_global_position_ok=True, health_home_position_ok=True, health_armable=True, last_event_updated_at_s=__import__("time").monotonic())',
            'snapshot = FleetDock.hud.snapshot(FleetDock)["devices"][0]',
            'assert snapshot["connected"] is True',
            'assert snapshot["armed"] is True',
            'assert snapshot["inAir"] is True',
            'assert snapshot["flightMode"] == "mission"',
            'assert snapshot["batteryRemaining"] == 0.75',
            'assert snapshot["global"]["relativeAltitudeM"] == 12.5',
            'assert snapshot["local"]["z"] == -12.5',
            'assert snapshot["velocity"] == {"x": 3.0, "y": 4.0, "z": 0.5}',
            'assert snapshot["attitude"] == {"roll": 1.0, "pitch": 2.0, "yaw": 3.0}',
            'assert snapshot["health"] == {"localPositionOk": True, "globalPositionOk": True, "homePositionOk": True, "armable": True}',
            'assert isinstance(snapshot["telemetryAgeMs"], int)',
            'assert snapshot["sensors"] == []',
            'assert FleetDock.hud._sensor_value(float("nan")) is None',
            'strict_json_payload = FleetDock.hud._json_safe({"nan": float("nan"), "positive_infinity": float("inf"), "nested": [1.0, float("-inf")]})',
            'assert strict_json_payload == {"nan": None, "positive_infinity": None, "nested": [1.0, None]}',
            '__import__("json").dumps(strict_json_payload, allow_nan=False)',
            'types = __import__("types")',
            'descriptor = types.SimpleNamespace(sensor_id="distance.front", kind=types.SimpleNamespace(value="distance"), source=types.SimpleNamespace(value="simulated"), is_primary=True, nominal_rate_hz=10.0)',
            'fake_sensors = types.SimpleNamespace(list=lambda: (descriptor,), subscribe=lambda sensor_id, handler, **kwargs: (handler({"sensor_id": sensor_id, "distance_m": 2.5, "sequence": 7}), (lambda: None))[1])',
            // [UAV-IDE MODIFY] The connected task kernel owns HUD sensor subscriptions.
            'fake_device = types.SimpleNamespace(sensors=fake_sensors, state=types.SimpleNamespace(snapshot=lambda: types.SimpleNamespace(connected=True)))',
            'FleetDock.hud._reconcile_sensors("fake", fake_device)',
            'sensor_snapshot = FleetDock.hud._sensor_snapshot("fake", fake_device)[0]',
            'assert sensor_snapshot["sample"]["distance_m"] == 2.5',
            'assert sensor_snapshot["sample"]["sequence"] == 7',
            'assert isinstance(sensor_snapshot["sampleAgeMs"], int)',
            'assert sensor_snapshot["discovered"] is True',
            'assert sensor_snapshot["subscribed"] is True',
            'assert sensor_snapshot["actualRateHz"] is None',
            // [UAV-IDE MODIFY] A successful reconnect may precede the asynchronous state event.
            'lifecycle_subscribe_calls = []',
            'lifecycle_sensors = types.SimpleNamespace(list=lambda: (descriptor,), subscribe=lambda sensor_id, handler, **kwargs: (lifecycle_subscribe_calls.append(sensor_id), (lambda: None))[1])',
            'lifecycle_device = types.SimpleNamespace(sensors=lifecycle_sensors, state=types.SimpleNamespace(snapshot=lambda: types.SimpleNamespace(connected=None)), lifecycle_state=types.SimpleNamespace(value="connected"))',
            'FleetDock.hud._reconcile_sensors("lifecycle", lifecycle_device)',
            'assert lifecycle_subscribe_calls == ["distance.front"]',
            'assert FleetDock.hud._device_snapshot("lifecycle", {"device_id": "lifecycle", "name": "lifecycle"}, lifecycle_device)["connected"] is True',
            'idle_subscribe_calls = []',
            'idle_sensors = types.SimpleNamespace(list=lambda: (descriptor,), subscribe=lambda sensor_id, handler, **kwargs: idle_subscribe_calls.append(sensor_id))',
            'idle_device = types.SimpleNamespace(sensors=idle_sensors, state=types.SimpleNamespace(snapshot=lambda: types.SimpleNamespace(connected=None)))',
            'FleetDock.hud._reconcile_sensors("idle", idle_device)',
            'assert idle_subscribe_calls == []',
            // [UAV-IDE MODIFY] Fresh FleetDock samples may backfill only direct observations and Project AirSim ground truth.
            'fallback_descriptors = tuple(types.SimpleNamespace(sensor_id=sensor_id, kind=types.SimpleNamespace(value=kind), source=types.SimpleNamespace(value="simulated"), is_primary=True, nominal_rate_hz=10.0) for sensor_id, kind in (("GPS", "gnss"), ("IMU1", "imu"), ("Magnetometer", "magnetometer"), ("Barometer", "barometer"), ("projectairsim.actual_pose", "vendor.projectairsim.actual_pose")))',
            'fallback_samples = {"GPS": {"kind": "gnss", "captured_at_s": 1.0, "latitude_deg": 47.0, "longitude_deg": -122.0, "absolute_altitude_m": 135.0}, "IMU1": {"kind": "imu", "linear_acceleration_m_s2": {"x": 0.0, "y": 0.0, "z": -9.81}, "orientation_rpy_deg": None}, "Magnetometer": {"kind": "magnetometer", "values": {"magnetic_field_body": {"x": 1.0, "y": 0.1, "z": 0.0}}}, "Barometer": {"kind": "barometer", "values": {"altitude": 134.8}}, "projectairsim.actual_pose": {"kind": "vendor.projectairsim.actual_pose", "values": {"translation": {"x": 12.0, "y": -3.5, "z": -8.0}, "rotation": {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0}}}}',
            'fallback_handlers = {}',
            'fallback_sensors = types.SimpleNamespace(list=lambda: fallback_descriptors, subscribe=lambda sensor_id, handler, **kwargs: (fallback_handlers.setdefault(sensor_id, handler), handler(fallback_samples[sensor_id]), (lambda: None))[2])',
            'empty_state = types.SimpleNamespace(snapshot=lambda: types.SimpleNamespace(connected=True, armed=None, in_air=None, landed_state=None, flight_mode=None, failsafe=None, last_event_updated_at_s=None, battery_remaining_fraction=None, latitude_deg=None, longitude_deg=None, absolute_altitude_m=None, relative_altitude_m=None, home_latitude_deg=None, home_longitude_deg=None, home_absolute_altitude_m=None, local_x_m=None, local_y_m=None, local_z_m=None, velocity_x_m_s=None, velocity_y_m_s=None, velocity_z_m_s=None, roll_deg=None, pitch_deg=None, yaw_deg=None, health_local_position_ok=None, health_global_position_ok=None, health_home_position_ok=None, health_armable=None))',
            'fallback_device = types.SimpleNamespace(sensors=fallback_sensors, state=empty_state)',
            'FleetDock.hud._reconcile_sensors("fallback", fallback_device)',
            'fallback_snapshot = FleetDock.hud._device_snapshot("fallback", {"device_id": "fallback", "name": "fallback"}, fallback_device)',
            'assert fallback_snapshot["global"]["lat"] == 47.0',
            'assert fallback_snapshot["global"]["absoluteAltitudeM"] == 135.0',
            'assert fallback_snapshot["global"]["relativeAltitudeM"] is None',
            'assert fallback_snapshot["home"] == {"lat": None, "lon": None, "absoluteAltitudeM": None}',
            'assert fallback_snapshot["local"] == {"x": 12.0, "y": -3.5, "z": -8.0}',
            'assert abs(fallback_snapshot["attitude"]["roll"]) < 0.01',
            'assert abs(fallback_snapshot["attitude"]["pitch"]) < 0.01',
            'assert abs(fallback_snapshot["attitude"]["yaw"]) < 0.01',
            'assert fallback_snapshot["velocity"] == {"x": None, "y": None, "z": None}',
            'assert fallback_snapshot["health"]["globalPositionOk"] is True',
            'assert fallback_snapshot["batteryRemaining"] is None',
            'stale_handler = fallback_handlers["GPS"]',
            'FleetDock.close()',
            'FleetDock.init()',
            'stale_handler(fallback_samples["GPS"])',
            'assert "fallback:GPS" not in FleetDock.hud._sensor_samples',
            'FleetDock.close()'
          ].join('; ')
        ],
        {
          cwd: workspace,
          stdio: 'inherit',
          env: {
            ...process.env,
            ...serverEnv,
            FLEETDOCK_AUTO_INIT: '0',
            FLEETDOCK_HUD_ENABLED: '0'
          }
        }
      );
      execFileSync('python', ['-m', 'py_compile', runtimePath], {
        stdio: 'ignore'
      });
      execFileSync('python', ['-m', 'py_compile', runtimeStubPath], {
        stdio: 'ignore'
      });

      console.log('FleetDock runtime binding checks passed.');
    } finally {
      fs.rmSync(workspace, { recursive: true, force: true });
      app.quit();
    }
  })
  .catch(error => {
    console.error(error);
    app.exit(1);
  });
