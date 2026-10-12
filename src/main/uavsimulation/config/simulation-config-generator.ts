// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import * as fs from 'fs';
import * as path from 'path';
import {
  DroneDevice,
  ManagedSensorDefinition,
  SimulationConfig
} from '../../devices/types';
import { getUavAirframeProfile } from '../../devices/uav-airframes';
import {
  createDefaultProjectAirsimSensors,
  createProjectAirsimSensorPreset
} from '../../devices/sensors/project-airsim-sensors';
import { allocatePort } from '../../devices/port-allocator';
import { getAppDir, getBundledPythonPath } from '../../utils';
import {
  UavSimulationGeneratedDrone,
  UavSimulationLaunchPlan,
  UavSimulationScene
} from '../types';
import {
  UAV_SIMULATION_DEFAULT_PX4_WSL_DIRECTORY,
  UAV_SIMULATION_DEFAULT_WSL_DISTRO
} from '../environment/simulation-environment-service';
import {
  getDefaultSkyLabSimPath,
  windowsPathToWslPath
} from './simulation-install-paths';
import { PRODUCT_NAME } from '../../branding';
import { injectProjectAirsimSensors } from './project-airsim-sensor-config';

const BASE_SIMULATOR_PORT = 4560;
const BASE_CONTROL_PORT = 14580;

// [UAV-IDE MODIFY] Generate Project AirSim scene/robot config files per run so UAV startup stays reproducible and isolated.
export class SimulationConfigGenerator {
  generate(options: {
    workingDirectory: string;
    scene: UavSimulationScene;
    config: SimulationConfig;
    scenePackageDirectory?: string;
    windowsHostAddress: string;
    px4Path?: string;
    pythonExecutable?: string;
  }): UavSimulationLaunchPlan {
    const runId = `run-${new Date().toISOString().replace(/[:.]/g, '-')}`;
    const runDirectory = path.join(
      options.workingDirectory,
      '.jupyter',
      'simulation',
      'runs',
      runId
    );
    fs.mkdirSync(runDirectory, { recursive: true });

    const drones = (options.config.drones || []).map((drone, index) =>
      this._generatedDrone(drone, index, runDirectory)
    );
    if (!drones.length) {
      throw new Error('请至少选择一架无人机。');
    }

    for (const drone of drones) {
      fs.writeFileSync(
        drone.robotFile,
        this._robotJson(
          drone,
          options.windowsHostAddress,
          options.scenePackageDirectory || getDefaultSkyLabSimPath()
        ),
        'utf8'
      );
    }

    const sceneFile = path.join(runDirectory, 'scene.jsonc');
    const launchFile = path.join(runDirectory, 'launch.json');
    const clientResultFile = path.join(runDirectory, 'client-result.json');
    const sensorManifestFile = path.join(
      options.workingDirectory,
      '.jupyter',
      'simulation',
      'runtime.json'
    );
    const logFile = path.join(runDirectory, 'simulation.log');
    fs.writeFileSync(sceneFile, this._sceneJson(options.scene, drones), 'utf8');

    const plan: UavSimulationLaunchPlan = {
      scene: options.scene,
      files: {
        runId,
        runDirectory,
        sceneFile,
        launchFile,
        clientResultFile,
        sensorManifestFile,
        logFile,
        robotFiles: drones.map(drone => drone.robotFile)
      },
      drones,
      wslDistro: UAV_SIMULATION_DEFAULT_WSL_DISTRO,
      px4WslDirectory: windowsPathToWslPath(
        String(options.px4Path || '').trim() ||
          UAV_SIMULATION_DEFAULT_PX4_WSL_DIRECTORY
      ),
      windowsHostAddress: options.windowsHostAddress,
      clientScript: path.join(
        options.scenePackageDirectory || getDefaultSkyLabSimPath(),
        'client',
        'project_airsim_client.py'
      ),
      runtimePythonRoot: this._resolveRuntimePythonRoot(),
      pythonExecutable:
        options.pythonExecutable || this._resolvePythonExecutable()
    };
    fs.writeFileSync(
      launchFile,
      JSON.stringify(this._launchJson(plan), null, 2),
      'utf8'
    );
    fs.writeFileSync(logFile, '', 'utf8');
    return plan;
  }

  private _generatedDrone(
    device: DroneDevice,
    index: number,
    runDirectory: string
  ): UavSimulationGeneratedDrone {
    const runtimeIndex = this._runtimeIndex(device, index);
    const runtimeOffset = runtimeIndex - 1;
    // [UAV-IDE MODIFY] Allocate simulation runtime identity from the device alias so FleetDock.devices.droneN stays aligned with DroneN.
    const vehicleName = `Drone${runtimeIndex}`;
    const airframe = getUavAirframeProfile(
      device.uav?.airframeType || device.simulation?.model
    );
    const systemId = runtimeIndex;
    const mavlinkPort = allocatePort(runtimeOffset);
    const cameraStreams = this._cameraStreams(device, vehicleName);
    return {
      device,
      index,
      vehicleName,
      systemId,
      // [UAV-IDE MODIFY] airframeType is authoritative; ignore stale derived fields persisted by builds that mapped every type to the quadrotor template.
      px4AutostartId: airframe.px4AutostartId,
      px4ParameterOverrides: airframe.px4ParameterOverrides,
      projectAirsimAirframe: airframe.projectAirsimAirframe,
      robotTemplate: airframe.robotTemplate,
      actuatorOrder: airframe.actuatorContract.order,
      rotorCount: airframe.actuatorContract.rotorCount,
      px4Instance: runtimeOffset,
      simulatorPort: BASE_SIMULATOR_PORT + runtimeOffset,
      mavlinkPort,
      // [UAV-IDE MODIFY] Preserve the first URI for older launch readers while publishing every configured camera separately.
      cameraStreamUri: cameraStreams[0]?.streamUri || '',
      cameraStreams,
      controlPortRemote: BASE_CONTROL_PORT + runtimeOffset,
      controlPortLocal: BASE_CONTROL_PORT + runtimeOffset,
      robotFile: path.join(runDirectory, `robot-drone-${index + 1}.jsonc`),
      origin: {
        // [UAV-IDE MODIFY] Scene placement follows selected-run order, not a persistent number such as Drone7 that can land inside map geometry.
        xyz: `${(index % 3) * 8}.0 ${Math.floor(index / 3) * 8}.0 -4.0`,
        rpy: '0 0 0'
      }
    };
  }

  private _runtimeIndex(device: DroneDevice, fallbackIndex: number): number {
    const candidates = [
      device.name,
      device.envVarName,
      device.uav?.vehicleName,
      device.simulation?.sceneActorName,
      device.airsimVehicleName
    ];
    for (const candidate of candidates) {
      const match = String(candidate || '').match(
        /(?:drone|DEVICE_DRONE_)(\d+)$/i
      );
      if (match) {
        const index = Number(match[1]);
        if (Number.isInteger(index) && index > 0) {
          return index;
        }
      }
    }
    return fallbackIndex + 1;
  }

  private _sceneJson(
    scene: UavSimulationScene,
    drones: UavSimulationGeneratedDrone[]
  ): string {
    return JSON.stringify(
      {
        id: `${scene.id}-generated`,
        // [UAV-IDE MODIFY] Packaged UE Project AirSim scenes validate as UnrealNative scenes.
        'scene-type': 'UnrealNative',
        'home-geo-point': {
          latitude: 47.641468,
          longitude: -122.140165,
          altitude: 122
        },
        clock: {
          type: 'steppable',
          'step-ns': 3000000,
          'real-time-update-rate': 3000000,
          'pause-on-start': false
        },
        segmentation: {
          // [UAV-IDE MODIFY] Keep generated scenes aligned with Project AirSim's official drone examples.
          'initialize-ids': true,
          'ignore-existing': false,
          'use-owner-name': true
        },
        actors: drones.map(drone => ({
          type: 'robot',
          name: drone.vehicleName,
          origin: drone.origin,
          'robot-config': path.basename(drone.robotFile)
        }))
      },
      null,
      2
    );
  }

  private _robotJson(
    drone: UavSimulationGeneratedDrone,
    windowsHostAddress: string,
    scenePackageDirectory: string
  ): string {
    // [UAV-IDE MODIFY] Use the selected PX4/Project AirSim UAV profile and inject per-drone runtime values.
    const templatePath = this._resolveRobotTemplatePath(
      scenePackageDirectory,
      drone.robotTemplate || 'robot_quadrotor_px4.jsonc'
    );
    let robotJson = fs.readFileSync(templatePath, 'utf8');
    for (const [token, value] of [
      ['"__TCP_PORT__"', String(drone.simulatorPort)],
      ['"__MAVLINK_PORT__"', String(drone.mavlinkPort)],
      ['"__CONTROL_PORT__"', String(drone.controlPortLocal)],
      ['"__SYSTEM_ID__"', String(drone.systemId)],
      ['"__PX4_AUTOSTART__"', String(drone.px4AutostartId)],
      ['"__LOCAL_HOST_IP__"', JSON.stringify(windowsHostAddress)]
    ]) {
      robotJson = robotJson.split(token).join(value);
    }
    robotJson = robotJson.replace(
      /"SYS_AUTOSTART"\s*:\s*\d+/,
      `"SYS_AUTOSTART": ${drone.px4AutostartId}`
    );
    robotJson = robotJson.replace(
      /"airframe-setup"\s*:\s*"[^"]+"/,
      `"airframe-setup": "${drone.projectAirsimAirframe}"`
    );
    // [UAV-IDE MODIFY] Materialize each device's sensor inventory into its isolated per-run robot config.
    robotJson = injectProjectAirsimSensors(
      robotJson,
      this._projectAirsimSensors(drone)
    );
    this._validateRobotContract(robotJson, drone);
    return robotJson;
  }

  private _projectAirsimSensors(
    drone: UavSimulationGeneratedDrone
  ): ManagedSensorDefinition[] {
    const sensors = [
      ...(Array.isArray(drone.device.sensors)
        ? drone.device.sensors
        : createDefaultProjectAirsimSensors())
    ];
    const requiresAirspeed =
      drone.projectAirsimAirframe === 'vtol-quad-x-tailsitter' ||
      drone.projectAirsimAirframe === 'vtol-quad-tiltrotor';
    if (
      requiresAirspeed &&
      !sensors.some(sensor => sensor.type === 'airspeed')
    ) {
      // [UAV-IDE MODIFY] PX4 VTOL preflight requires valid airspeed data even when an older persisted device inventory predates this flight sensor.
      const airspeed = createProjectAirsimSensorPreset('airspeed', 'Airspeed');
      airspeed.name = '飞控空速计';
      sensors.push(airspeed);
    }
    return sensors;
  }

  private _resolveRobotTemplatePath(
    scenePackageDirectory: string,
    templateName: string
  ): string {
    // [UAV-IDE MODIFY] Product-owned templates ship separately from large scene packages so a stale local SkyLabSim extraction cannot restore quadrotor mappings for every airframe.
    const appDirectories: string[] = [];
    try {
      appDirectories.push(getAppDir());
    } catch (_error) {
      // [UAV-IDE MODIFY] Contract tests use a minimal Electron stub; cwd still resolves development resources.
    }
    const candidates = [
      ...appDirectories.flatMap(appDirectory => [
        path.join(
          appDirectory,
          'dist-resources',
          'skylab-simulation',
          'templates',
          templateName
        ),
        path.join(appDirectory, 'skylab-simulation', 'templates', templateName)
      ]),
      path.join(
        process.cwd(),
        'dist-resources',
        'skylab-simulation',
        'templates',
        templateName
      ),
      path.join(scenePackageDirectory, 'templates', templateName)
    ];
    const templatePath = candidates.find(candidate => fs.existsSync(candidate));
    if (!templatePath) {
      throw new Error(
        `Project AirSim 机器人模板不存在：${templateName}。请修复 FleetDock IDEA 安装资源。`
      );
    }
    return templatePath;
  }

  private _validateRobotContract(
    robotJson: string,
    drone: UavSimulationGeneratedDrone
  ): void {
    const unresolvedTokens = robotJson.match(/"__[A-Z0-9_]+__"/g) || [];
    if (unresolvedTokens.length) {
      throw new Error(
        `Project AirSim 机器人模板 ${
          drone.robotTemplate
        } 包含未替换变量：${unresolvedTokens.join(', ')}。`
      );
    }
    const actuatorNames = new Set(
      Array.from(robotJson.matchAll(/"name"\s*:\s*"([^"]+_actuator)"/g)).map(
        match => match[1]
      )
    );
    const order = Array.from(
      robotJson.matchAll(/"id"\s*:\s*"([^"]+_actuator)"/g)
    )
      .map(match => match[1])
      .filter(id => actuatorNames.has(id));
    const expected = Array.from(drone.actuatorOrder);
    if (
      order.length !== expected.length ||
      order.some((id, index) => id !== expected[index])
    ) {
      throw new Error(
        `Project AirSim 机器人模板 ${drone.robotTemplate} 的执行器顺序与 ${drone.projectAirsimAirframe} 契约不一致。`
      );
    }
    const rotorCount = Array.from(robotJson.matchAll(/"type"\s*:\s*"rotor"/g))
      .length;
    if (rotorCount !== drone.rotorCount) {
      throw new Error(
        `Project AirSim 机器人模板 ${drone.robotTemplate} 应包含 ${drone.rotorCount} 个旋翼，实际为 ${rotorCount} 个。`
      );
    }
  }

  private _launchJson(plan: UavSimulationLaunchPlan): object {
    return {
      runId: plan.files.runId,
      scene: plan.scene,
      files: plan.files,
      wslDistro: plan.wslDistro,
      px4WslDirectory: plan.px4WslDirectory,
      windowsHostAddress: plan.windowsHostAddress,
      drones: plan.drones.map(drone => ({
        droneId: drone.device.id,
        vehicleName: drone.vehicleName,
        systemId: drone.systemId,
        px4AutostartId: drone.px4AutostartId,
        px4ParameterOverrides: drone.px4ParameterOverrides,
        projectAirsimAirframe: drone.projectAirsimAirframe,
        actuatorOrder: drone.actuatorOrder,
        px4Instance: drone.px4Instance,
        simulatorPort: drone.simulatorPort,
        mavlinkPort: drone.mavlinkPort,
        cameraStreamUri: drone.cameraStreamUri,
        cameraStreams: drone.cameraStreams,
        robotFile: drone.robotFile
      }))
    };
  }

  private _resolvePythonExecutable(): string {
    const bundledPython = getBundledPythonPath();
    if (bundledPython && fs.existsSync(bundledPython)) {
      return bundledPython;
    }
    // [UAV-IDE MODIFY] FleetDock simulation must use the IDE-bundled Python runtime, not a system interpreter.
    throw new Error(`${PRODUCT_NAME} bundled Python runtime was not found.`);
  }

  private _cameraStreams(
    device: DroneDevice,
    vehicleName: string
  ): UavSimulationGeneratedDrone['cameraStreams'] {
    const sensors = Array.isArray(device.sensors)
      ? device.sensors
      : createDefaultProjectAirsimSensors();
    const cameras = sensors.filter(
      sensor => sensor.enabled !== false && sensor.type === 'camera'
    );
    const configuredUri = String(
      device.fleetdock?.params?.camera_stream_uri || ''
    ).trim();
    return cameras.map((sensor, index) => {
      const sensorId = String(sensor.id || `camera-${index + 1}`);
      const safeSensorId =
        sensorId.replace(/[^A-Za-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '') ||
        `camera_${index + 1}`;
      return {
        id: `${vehicleName}:${sensorId}`,
        sensorId,
        label: String(sensor.name || sensorId),
        streamUri:
          index === 0 && configuredUri
            ? configuredUri
            : index === 0
            ? `rtsp://127.0.0.1:8554/${vehicleName}_camera_front`
            : `rtsp://127.0.0.1:8554/${vehicleName}_camera_${
                index + 1
              }_${safeSensorId}`
      };
    });
  }

  private _resolveRuntimePythonRoot(): string {
    let appDirectory = process.cwd();
    try {
      appDirectory = getAppDir();
    } catch (_error) {
      // [UAV-IDE MODIFY] Keep configuration generation testable without a complete Electron application object.
    }
    const candidates = [
      path.join(appDirectory, 'dist-resources', 'skylab-runtime'),
      path.join(appDirectory, 'skylab-runtime'),
      path.join(process.cwd(), 'dist-resources', 'skylab-runtime')
    ];
    return (
      candidates.find(candidate =>
        fs.existsSync(path.join(candidate, 'fleetdock_ide_runtime'))
      ) || candidates[0]
    );
  }
}
