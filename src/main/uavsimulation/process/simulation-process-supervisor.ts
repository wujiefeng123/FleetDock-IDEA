// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import { ChildProcess, execFile, spawn } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import { SimulationConfig, SimulationStartResult } from '../../devices/types';
import {
  getDefaultPx4WslPath,
  windowsPathToWslPath
} from '../config/simulation-install-paths';
import { SimulationConfigGenerator } from '../config/simulation-config-generator';
import {
  SimulationEnvironmentService,
  UAV_SIMULATION_DEFAULT_WSL_DISTRO
} from '../environment/simulation-environment-service';
import {
  UAV_SIMULATION_COMMAND_ACTIONS,
  UavSimulationCommandAction,
  UavSimulationCommandRequest,
  UavSimulationCommandResult,
  UavSimulationConflictProcess,
  UavSimulationLaunchPlan,
  UavSimulationScene,
  UavSimulationStageStatus,
  UavSimulationStatus
} from '../types';
import {
  IFleetDockRtspStream,
  MediaMtxService
} from '../media/mediamtx-service';
import { SimulationTimingRecorder } from './simulation-timing-recorder';

type ExecResult = {
  stdout: string;
  stderr: string;
};

type SimulationStartOptions = {
  workingDirectory: string;
  scene: UavSimulationScene;
  px4Path?: string;
  scenePackageDirectory?: string;
  config: SimulationConfig;
  forceCleanup?: boolean;
  confirmCleanup?: (
    conflicts: UavSimulationConflictProcess[]
  ) => Promise<boolean>;
};

type SimulationCleanupTarget = {
  wslDistro?: string;
  px4WslDirectory?: string;
  scene?: UavSimulationScene | null;
  scenePackageDirectory?: string;
  workingDirectory?: string;
  clientScript?: string;
  runDirectory?: string;
};

type SimulationStopOptions = {
  silent?: boolean;
  cleanupTarget?: SimulationCleanupTarget;
};

type ClientStartupResult = {
  ok?: boolean;
  error?: string;
  protocolVersion?: number;
  capabilities?: string[];
  sensorService?: {
    available?: boolean;
    protocolVersion?: number;
    endpoint?: string;
    sdkVersion?: string;
    vehicles?: string[];
    rtspStreams?: unknown;
  };
};

type PendingSimulationCommand = {
  action: UavSimulationCommandAction;
  runId: string;
  startedAt: number;
  timeout: NodeJS.Timeout;
  resolve: (result: UavSimulationCommandResult) => void;
};

type QueuedSimulationCommand = {
  request: UavSimulationCommandRequest;
  resolve: (result: UavSimulationCommandResult) => void;
};

const PX4_READY_TIMEOUT_MS = 90000;
const PX4_PROCESS_GRACE_MS = 750;
const PROCESS_SPAWN_TIMEOUT_MS = 5000;
const SCENE_EARLY_EXIT_WINDOW_MS = 250;
const TRACKED_PROCESS_EXIT_TIMEOUT_MS = 1000;
const PX4_MAVLINK_LOCAL_PORT_BASE = 14660;
const PX4_MAVLINK_REPLACE_LOCAL_PORT_BASE = 18570;
const PX4_MAVLINK_RATE = 4000000;
const PROJECT_AIRSIM_RTSP_PORT = 8554;
const SIMULATION_COMMAND_PROTOCOL_VERSION = 1;
const SIMULATION_COMMAND_TIMEOUT_MS = 5000;
const SIMULATION_COMMAND_MAX_BYTES = 64 * 1024;
const SIMULATION_COMMAND_RESULT_PREFIX = 'SKYLAB_COMMAND_RESULT ';
const SIMULATION_SENSOR_PROTOCOL_VERSION = 1;

// [UAV-IDE MODIFY] Own the UAV simulation process tree independently from Jupyter Server/session lifecycle.
export class SimulationProcessSupervisor {
  async start(options: SimulationStartOptions): Promise<SimulationStartResult> {
    const startSignature = this._startSignatureFromOptions(options);
    if (this._startPromise) {
      // [UAV-IDE MODIFY] Duplicate start clicks should observe the active launch instead of restarting it.
      return this._toStartResult(this._status);
    }
    if (this._status.state === 'running') {
      const sameRun = this._activeStartSignature === startSignature;
      return {
        ...this._toStartResult(this._status),
        title: sameRun
          ? 'Local simulation is already running'
          : 'Local simulation is already running with different settings',
        message: sameRun
          ? 'The selected scene and devices are already running.'
          : 'Stop the current simulation before switching scene or devices.'
      };
    }
    if (this._status.state !== 'idle' && this._status.state !== 'failed') {
      return this._toStartResult(this._status);
    }

    this._startPromise = this._startInternal(options, startSignature);
    try {
      return await this._startPromise;
    } finally {
      this._startPromise = null;
    }
  }

  private async _startInternal(
    options: SimulationStartOptions,
    startSignature: string
  ): Promise<SimulationStartResult> {
    const timing = new SimulationTimingRecorder('startup');
    this._startTiming = timing;
    this._resetStatus(
      'checking',
      '\u6b63\u5728\u68c0\u6d4b\u4eff\u771f\u73af\u5883',
      '\u68c0\u67e5 WSL2\u3001PX4\u3001Project AirSim \u548c\u7aef\u53e3\u3002'
    );
    try {
      const windowsHostAddress = await timing.measure('resolve-host', () =>
        this._resolveWindowsHostAddress()
      );
      const plan = await timing.measure('generate-config', () =>
        this._generator.generate({
          workingDirectory: options.workingDirectory,
          scene: options.scene,
          config: options.config,
          scenePackageDirectory: options.scenePackageDirectory,
          windowsHostAddress,
          px4Path: options.px4Path
        })
      );
      this._plan = plan;
      this._status.runId = plan.files.runId;
      this._status.scene = options.scene;
      this._status.logPath = plan.files.logFile;
      await this._invalidateSensorManifest(
        plan.files.sensorManifestFile,
        plan.files.runId
      );
      this._status.drones = plan.drones.map(drone => ({
        droneId: drone.device.id,
        vehicleName: drone.vehicleName,
        systemId: drone.systemId,
        px4Instance: drone.px4Instance,
        simulatorPort: drone.simulatorPort,
        mavlinkPort: drone.mavlinkPort,
        connected: false
      }));

      const conflicts = await timing.measure('find-conflicts', () =>
        this.findConflicts(plan)
      );
      this._status.conflicts = conflicts;
      if (conflicts.length && !options.forceCleanup) {
        await this._runStage(
          'awaiting-confirmation',
          '\u68c0\u6d4b\u5230\u65e7\u4eff\u771f\u8fdb\u7a0b\uff0c\u7b49\u5f85\u786e\u8ba4',
          async () => {
            const confirmed = await options.confirmCleanup?.(conflicts);
            if (!confirmed) {
              throw new Error(
                'Simulation start was cancelled before stale processes were stopped.'
              );
            }
          }
        );
      }
      await this._runStage(
        'stopping-conflicts',
        '\u6b63\u5728\u6e05\u7406\u65e7\u4eff\u771f\u8fdb\u7a0b',
        () => this._cleanupBeforeStart(plan, conflicts)
      );

      const environment = await timing.measure('environment-check', () =>
        this._environment.check({
          scene: options.scene,
          ports: [
            ...this._rtspPorts(plan),
            ...plan.drones.flatMap(drone => [
              drone.simulatorPort,
              drone.mavlinkPort,
              drone.controlPortLocal
            ])
          ],
          pythonExecutable: plan.pythonExecutable,
          wslDistro: plan.wslDistro,
          px4WslDirectory: plan.px4WslDirectory
        })
      );
      if (!environment.ok) {
        throw new Error(
          environment.items
            .filter(item => !item.ok)
            .map(item => item.label + ': ' + item.message)
            .join('\n')
        );
      }
      this._finishStage('checking', 'success');
      this._finishStage('awaiting-confirmation', 'success');

      await this._runStage(
        'starting-scene',
        '\u6b63\u5728\u542f\u52a8 Project AirSim \u573a\u666f',
        () => this._startScene(plan)
      );
      // [UAV-IDE MODIFY] Report the actual WSL distro selected by the generated launch plan.
      await this._runStage(
        'starting-px4',
        '\u6b63\u5728\u542f\u52a8 ' + plan.wslDistro + ' \u4e2d\u7684 PX4',
        () => this._startPx4(plan)
      );
      await this._runStage(
        'loading-world',
        '\u6b63\u5728\u901a\u8fc7 Project AirSim \u5ba2\u6237\u7aef\u52a0\u8f7d\u573a\u666f\u4e0e\u65e0\u4eba\u673a',
        () => this._startClient(plan)
      );
      await this._runStage(
        'verifying',
        '\u6b63\u5728\u9a8c\u8bc1\u4eff\u771f\u8fde\u63a5\u72b6\u6001',
        () => this._verify(plan)
      );
      await this._runStage(
        'starting-media',
        '\u6b63\u5728\u542f\u52a8\u591a\u8def WebRTC \u89c6\u9891\u670d\u52a1',
        async () => {
          await this._mediaService.start(
            plan.files.runDirectory,
            this._mediaRtspStreams
          );
          this._status.mediaService = this._mediaService.status();
        }
      );
      await this._runStage(
        'starting-mavlink',
        '\u6b63\u5728\u914d\u7f6e PX4 MAVLink \u8f93\u51fa\u94fe\u8def',
        () => this._startPx4MavlinkOutputs(plan)
      );

      this._status.state = 'running';
      this._status.title = '\u672c\u5730\u4eff\u771f\u5df2\u542f\u52a8';
      this._status.message =
        '\u573a\u666f\u3001PX4 \u548c Project AirSim \u5ba2\u6237\u7aef\u5df2\u6309\u987a\u5e8f\u542f\u52a8\u3002';
      this._status.drones = (this._status.drones || []).map(drone => ({
        ...drone,
        connected: true
      }));
      await this._appendLog('Simulation is running.');
      this._activeStartSignature = startSignature;
      await timing.finish('success', line => this._appendLog(line));
      this._startTiming = null;
      return this._toStartResult(this._status);
    } catch (error) {
      await timing.finish('failed', line => this._appendLog(line));
      this._startTiming = null;
      await this.stop({ silent: true });
      this._activeStartSignature = null;
      this._status.state = 'failed';
      this._status.title = '\u672c\u5730\u4eff\u771f\u542f\u52a8\u5931\u8d25';
      this._status.message = this._message(error);
      await this._appendLog('Simulation failed: ' + this._status.message);
      return this._toStartResult(this._status);
    }
  }

  async stop(
    options: SimulationStopOptions = {}
  ): Promise<UavSimulationStatus> {
    const timing = new SimulationTimingRecorder('shutdown');
    const cleanupTarget =
      options.cleanupTarget ||
      (this._plan ? this._cleanupTargetFromPlan(this._plan) : null);
    if (this._status.state === 'idle' && !this._plan && !cleanupTarget) {
      return this._status;
    }

    const pathCleanupTarget = this._plan
      ? this._cleanupTargetFromPlan(this._plan)
      : cleanupTarget;
    this._status.state = 'stopping';
    this._status.title = '\u6b63\u5728\u505c\u6b62\u4eff\u771f';
    this._status.message =
      '\u6b63\u5728\u5173\u95ed\u5ba2\u6237\u7aef\u3001PX4 \u548c\u573a\u666f\u8fdb\u7a0b\u3002';
    await this._appendLog('Stopping simulation.');

    this._cancelSimulationCommands(
      'SIMULATION_STOPPED',
      'Simulation stopped before the command completed.'
    );
    this._status.message =
      '\u6b63\u5728\u5173\u95ed\u5a92\u4f53\u670d\u52a1...';
    // [UAV-IDE MODIFY] Close all WHEP sessions before FleetDock removes the RTSP publishers.
    await timing.measure('media-service', () => this._mediaService.stop());
    let clientStopped = true;
    if (this._clientProcess) {
      this._status.message =
        '\u6b63\u5728\u7b49\u5f85 Project AirSim \u5ba2\u6237\u7aef\u9000\u51fa...';
      const clientProcess = this._clientProcess;
      await timing.measure('client', async () => {
        clientProcess.stdin?.end();
        // [UAV-IDE MODIFY] Preserve the existing camera-aware client grace period; camera shutdown is outside this optimization.
        const cameraCount =
          this._plan?.drones.reduce(
            (total, drone) => total + drone.cameraStreams.length,
            0
          ) || 0;
        const clientShutdownTimeoutMs = Math.min(
          30000,
          2000 + (this._plan?.drones.length || 1) * 2500 + cameraCount * 1500
        );
        await this._waitForExit(clientProcess, clientShutdownTimeoutMs);
        if (clientProcess.exitCode === null) {
          await this._killProcessTree(clientProcess.pid);
          await this._waitForExit(
            clientProcess,
            TRACKED_PROCESS_EXIT_TIMEOUT_MS
          );
        }
        clientStopped = clientProcess.exitCode !== null;
      });
      this._clientProcess = null;
    }
    if (this._plan) {
      await timing.measure('invalidate-manifest', () =>
        this._invalidateSensorManifest(
          this._plan!.files.sensorManifestFile,
          this._plan!.files.runId
        )
      );
    }
    let px4Stopped = true;
    let sceneStopped = true;
    if (this._plan) {
      this._status.message =
        '\u6b63\u5728\u5e76\u884c\u6e05\u7406 PX4 \u548c\u573a\u666f\u8fdb\u7a0b...';
      // [UAV-IDE MODIFY] PX4 and scene teardown are independent after the client has exited.
      await timing.measure('tracked-processes', async () => {
        [px4Stopped, sceneStopped] = await Promise.all([
          this._stopPx4(this._plan!),
          this._stopSceneProcess()
        ]);
      });
    }
    const recoveryCleanupUsed =
      (!this._plan && !!cleanupTarget) ||
      !clientStopped ||
      !px4Stopped ||
      !sceneStopped;
    await this._appendLog(
      `[simulation-timing] shutdown.recovery-scan used=${recoveryCleanupUsed}`
    );
    if (recoveryCleanupUsed && pathCleanupTarget) {
      this._status.message =
        '\u6b63\u5728\u6267\u884c\u4eff\u771f\u6b8b\u7559\u8fdb\u7a0b\u6062\u590d\u6e05\u7406...';
      // [UAV-IDE MODIFY] Expensive path scans are a recovery path, not part of every normal stop.
      await Promise.all([
        timing.measure('recovery-px4-scan', () =>
          this._stopPx4ByPath(pathCleanupTarget)
        ),
        timing.measure('recovery-windows-scan', () =>
          this._cleanupWindowsProcessesByPath(pathCleanupTarget)
        )
      ]);
    }
    this._px4Processes = [];

    this._mediaRtspStreams = [];
    this._status = {
      state: 'idle',
      title: options.silent
        ? '\u4eff\u771f\u5df2\u6e05\u7406'
        : '\u4eff\u771f\u5df2\u505c\u6b62',
      message: options.silent
        ? '\u542f\u52a8\u5931\u8d25\u540e\u7684\u8d44\u6e90\u5df2\u6e05\u7406\u3002'
        : '\u672c\u6b21\u4eff\u771f\u8d44\u6e90\u5df2\u5168\u90e8\u5173\u95ed\u3002',
      stages: this._status.stages,
      logPath: this._status.logPath
    };
    this._plan = null;
    this._activeStartSignature = null;
    await timing.finish('success', line => this._appendLog(line));
    return this._status;
  }

  status(): UavSimulationStatus {
    if (this._status.mediaService || this._plan) {
      this._status.mediaService = this._mediaService.status();
    }
    return this._status;
  }

  // [UAV-IDE MODIFY] Expose only camera identities and SDP; RTSP/WHEP endpoints remain main-process private.
  cameraStreams(vehicleName?: string) {
    const normalizedVehicleName = String(vehicleName || '')
      .trim()
      .toLowerCase();
    const streams = this._mediaService.catalog();
    return normalizedVehicleName
      ? streams.filter(
          stream => stream.vehicleName.toLowerCase() === normalizedVehicleName
        )
      : streams;
  }

  createWebRtcSession(streamId: string, offerSdp: string) {
    return this._mediaService.createSession(streamId, offerSdp);
  }

  closeWebRtcSession(sessionId: string) {
    return this._mediaService.closeSession(sessionId);
  }

  // [UAV-IDE MODIFY] Serialize allowlisted Project AirSim interactions through the IDE-owned client.
  executeCommand(
    request: UavSimulationCommandRequest
  ): Promise<UavSimulationCommandResult> {
    const action = String(request?.action || '');
    if (!this._commandActionSet.has(action)) {
      return Promise.resolve(
        this._commandFailure(
          action,
          'UNSUPPORTED_ACTION',
          'Unsupported simulation command.'
        )
      );
    }
    if (this._status.state !== 'running' || !this._plan) {
      return Promise.resolve(
        this._commandFailure(
          action,
          'NOT_RUNNING',
          'Simulation is not running.'
        )
      );
    }
    if (!this._status.commandChannel?.available) {
      return Promise.resolve(
        this._commandFailure(
          action,
          'CHANNEL_UNAVAILABLE',
          this._status.commandChannel?.message ||
            'Simulation command channel is unavailable.'
        )
      );
    }
    if (
      !this._status.commandChannel.capabilities.includes(
        action as UavSimulationCommandAction
      )
    ) {
      return Promise.resolve(
        this._commandFailure(
          action,
          'CAPABILITY_UNAVAILABLE',
          'The active Project AirSim client does not support this command.'
        )
      );
    }
    const params = request?.params;
    if (
      params !== undefined &&
      (params === null || typeof params !== 'object' || Array.isArray(params))
    ) {
      return Promise.resolve(
        this._commandFailure(
          action,
          'INVALID_PARAMS',
          'Command params must be an object.'
        )
      );
    }
    try {
      if (
        Buffer.byteLength(JSON.stringify(params || {}), 'utf8') >
        SIMULATION_COMMAND_MAX_BYTES
      ) {
        return Promise.resolve(
          this._commandFailure(
            action,
            'PAYLOAD_TOO_LARGE',
            'Command payload is too large.'
          )
        );
      }
    } catch (_error) {
      return Promise.resolve(
        this._commandFailure(
          action,
          'INVALID_PARAMS',
          'Command params must be JSON serializable.'
        )
      );
    }
    return new Promise(resolve => {
      this._commandQueue.push({
        request: {
          action: action as UavSimulationCommandAction,
          params: params || {}
        },
        resolve
      });
      void this._drainCommandQueue();
    });
  }

  async findConflicts(
    plan: Pick<UavSimulationLaunchPlan, 'scene' | 'clientScript'>
  ): Promise<UavSimulationConflictProcess[]> {
    if (process.platform !== 'win32') {
      return [];
    }
    const conflicts: UavSimulationConflictProcess[] = [];
    const clientScript = path.resolve(plan.clientScript).toLowerCase();
    // [UAV-IDE MODIFY] Enumerate Windows processes once and classify scene/client conflicts locally.
    const processResult = await this._execFileAsync('powershell.exe', [
      '-NoProfile',
      '-Command',
      'Get-CimInstance Win32_Process | Select-Object ProcessId,Name,@{Name=\'Detail\';Expression={([string]$_.ExecutablePath + "`n" + [string]$_.CommandLine)}} | ConvertTo-Json -Compress'
    ]).catch(() => ({ stdout: '', stderr: '' }));
    const sceneDirectory = plan.scene?.workingDirectory
      ? path.resolve(plan.scene.workingDirectory).toLowerCase()
      : '';
    for (const item of this._parseProcessList(processResult.stdout)) {
      if (item.pid === process.pid) {
        continue;
      }
      const processName = item.name.toLowerCase();
      if (
        (processName === 'python.exe' || processName === 'pythonw.exe') &&
        this._looksLikeClient(item.detail, clientScript)
      ) {
        conflicts.push({
          pid: item.pid,
          name: item.name,
          source: 'project-airsim-client',
          detail: item.detail
        });
        continue;
      }
      const normalizedDetail = String(item.detail || '')
        .toLowerCase()
        .replace(/[\\/]+/g, '\\');
      const normalizedSceneDirectory = sceneDirectory.replace(/[\\/]+/g, '\\');
      if (
        normalizedSceneDirectory &&
        normalizedDetail.includes(normalizedSceneDirectory)
      ) {
        conflicts.push({
          pid: item.pid,
          name: item.name,
          source: 'scene',
          detail: item.detail
        });
      }
    }
    return conflicts;
  }

  private async _startScene(plan: UavSimulationLaunchPlan): Promise<void> {
    if (!fs.existsSync(plan.scene.executablePath)) {
      throw new Error(`场景 EXE 不存在：${plan.scene.executablePath}`);
    }
    this._sceneProcess = spawn(plan.scene.executablePath, [], {
      cwd: plan.scene.workingDirectory,
      windowsHide: false,
      stdio: 'ignore'
    });
    await this._waitForSpawn(this._sceneProcess, 'Project AirSim 场景');
    await this._appendLog(`Started scene: ${plan.scene.executablePath}`);
    // [UAV-IDE MODIFY] Only retain a short early-crash window instead of an unconditional 1.5 second delay.
    await this._waitForAlive(
      this._sceneProcess,
      SCENE_EARLY_EXIT_WINDOW_MS,
      'Project AirSim 场景'
    );
  }

  private async _startPx4(plan: UavSimulationLaunchPlan): Promise<void> {
    // [UAV-IDE MODIFY] Launch independent PX4 instances concurrently and share one stability window.
    const launches = plan.drones.map(async drone => {
      const label = this._px4ProcessLabel(plan, drone);
      const command = [
        `PX4_SIM_HOST_ADDR=${plan.windowsHostAddress}`,
        `PX4_SIM_TCP_PORT=${drone.simulatorPort}`,
        `PX4_INSTANCE=${drone.px4Instance}`,
        `PX4_SYS_AUTOSTART=${drone.px4AutostartId}`,
        `SKYLAB_PX4_LABEL=${this._quoteBash(label)}`,
        `exec ./build/px4_sitl_default/bin/px4 -i ${drone.px4Instance}`
      ].join(' ');
      const child = spawn(
        'wsl.exe',
        [
          '-d',
          plan.wslDistro,
          '--cd',
          plan.px4WslDirectory,
          'bash',
          '-lc',
          command
        ],
        {
          windowsHide: true,
          stdio: ['ignore', 'pipe', 'pipe']
        }
      );
      this._px4Processes.push(child);
      this._pipeLog(child, `px4:${drone.vehicleName}`);
      await this._waitForSpawn(child, `PX4 ${drone.vehicleName}`);
      await this._waitForAlive(
        child,
        PX4_PROCESS_GRACE_MS,
        `PX4 ${drone.vehicleName}`
      );
    });
    await this._allSettledOrThrow(launches);
  }

  private async _startPx4MavlinkOutputs(
    plan: UavSimulationLaunchPlan
  ): Promise<void> {
    // [UAV-IDE MODIFY] Commander readiness and MAVLink configuration are independent per PX4 instance.
    await this._waitForPx4Commanders(plan);
    await this._startPx4MavlinkOutputsBatch(plan);
  }

  private async _waitForPx4Commanders(
    plan: UavSimulationLaunchPlan
  ): Promise<void> {
    const deadline = Date.now() + PX4_READY_TIMEOUT_MS;
    const pending = new Map(
      plan.drones.map(drone => [drone.px4Instance, drone] as const)
    );
    while (pending.size && Date.now() < deadline) {
      // [UAV-IDE MODIFY] Probe all pending commanders concurrently inside one WSL process per retry.
      const probes = Array.from(pending.keys()).map(
        instance =>
          `(timeout 2s ./build/px4_sitl_default/bin/px4-commander --instance ${instance} status >/dev/null 2>&1 && echo READY:${instance}) &`
      );
      const result = await this._execFileAsync('wsl.exe', [
        '-d',
        plan.wslDistro,
        '--cd',
        plan.px4WslDirectory,
        'bash',
        '-lc',
        [...probes, 'wait || true'].join('\n')
      ]).catch(() => ({ stdout: '', stderr: '' }));
      const readyInstances = new Set(
        String(result.stdout || '')
          .split(/\r?\n/)
          .map(line => /^READY:(\d+)$/.exec(line.trim()))
          .filter((match): match is RegExpExecArray => !!match)
          .map(match => Number(match[1]))
      );
      for (const instance of readyInstances) {
        const drone = pending.get(instance);
        if (!drone) {
          continue;
        }
        pending.delete(instance);
        await this._appendLog(`PX4 ${drone.vehicleName} commander is ready.`);
      }
      if (pending.size) {
        await this._sleep(1000);
      }
    }
    if (pending.size) {
      throw new Error(
        `PX4 Commander 未在超时时间内就绪：${Array.from(pending.values())
          .map(drone => drone.vehicleName)
          .join(', ')}。`
      );
    }
  }

  private async _startPx4MavlinkOutputsBatch(
    plan: UavSimulationLaunchPlan
  ): Promise<void> {
    // [UAV-IDE MODIFY] Configure all MAVLink outputs concurrently in one WSL process.
    const commands = plan.drones.map(drone => {
      const localPort = PX4_MAVLINK_LOCAL_PORT_BASE + drone.px4Instance;
      const replaceLocalPort =
        PX4_MAVLINK_REPLACE_LOCAL_PORT_BASE + drone.px4Instance;
      const startCommand = [
        `./build/px4_sitl_default/bin/px4-mavlink`,
        `--instance ${drone.px4Instance}`,
        'start -x',
        `-u ${localPort}`,
        `-r ${PX4_MAVLINK_RATE}`,
        `-t ${this._quoteBash(plan.windowsHostAddress)}`,
        `-o ${drone.mavlinkPort}`
      ].join(' ');
      return [
        '(',
        `timeout 5s ./build/px4_sitl_default/bin/px4-mavlink --instance ${drone.px4Instance} stop -u ${localPort} || true`,
        `timeout 5s ./build/px4_sitl_default/bin/px4-mavlink --instance ${drone.px4Instance} stop -u ${replaceLocalPort} || true`,
        `${startCommand} && echo CONFIGURED:${drone.px4Instance}`,
        ') &'
      ].join('\n');
    });
    const result = await this._execFileAsync('wsl.exe', [
      '-d',
      plan.wslDistro,
      '--cd',
      plan.px4WslDirectory,
      'bash',
      '-lc',
      [...commands, 'wait || true'].join('\n')
    ]);
    const configuredInstances = new Set(
      String(result.stdout || '')
        .split(/\r?\n/)
        .map(line => /^CONFIGURED:(\d+)$/.exec(line.trim()))
        .filter((match): match is RegExpExecArray => !!match)
        .map(match => Number(match[1]))
    );
    const failed = plan.drones.filter(
      drone => !configuredInstances.has(drone.px4Instance)
    );
    if (failed.length) {
      throw new Error(
        `PX4 MAVLink 输出配置失败：${failed
          .map(drone => drone.vehicleName)
          .join(', ')}。`
      );
    }
    for (const drone of plan.drones) {
      const localPort = PX4_MAVLINK_LOCAL_PORT_BASE + drone.px4Instance;
      await this._appendLog(
        `PX4 ${drone.vehicleName} MAVLink output -> ${plan.windowsHostAddress}:${drone.mavlinkPort} (local ${localPort}).`
      );
    }
  }

  private async _startClient(plan: UavSimulationLaunchPlan): Promise<void> {
    if (!fs.existsSync(plan.clientScript)) {
      throw new Error(`Project AirSim 客户端脚本不存在：${plan.clientScript}`);
    }
    this._commandSequence = 0;
    this._clientProcess = spawn(
      plan.pythonExecutable,
      [
        plan.clientScript,
        '--launch',
        plan.files.launchFile,
        '--result',
        plan.files.clientResultFile,
        '--runtime-root',
        plan.runtimePythonRoot
      ],
      {
        cwd: plan.files.runDirectory,
        windowsHide: true,
        stdio: ['pipe', 'pipe', 'pipe']
      }
    );
    this._clientStdoutBuffer = '';
    this._pipeClientLog(this._clientProcess, 'projectairsim-client');
    // [UAV-IDE MODIFY] Result verification already watches client exit, so only wait for OS spawn here.
    await this._waitForSpawn(
      this._clientProcess,
      'Project AirSim Python 客户端'
    );
  }

  private async _verify(plan: UavSimulationLaunchPlan): Promise<void> {
    // [UAV-IDE MODIFY] Treat the client result as the source of truth for world and actor loading.
    // [UAV-IDE MODIFY] Each SDK RTSP bridge may wait for its first frame before the client publishes readiness.
    const cameraCount = plan.drones.reduce(
      (total, drone) => total + drone.cameraStreams.length,
      0
    );
    const clientStartupTimeoutMs = Math.min(
      180000,
      15000 + plan.drones.length * 10000 + cameraCount * 20000
    );
    const clientResult = await this._waitForClientResult(
      plan.files.clientResultFile,
      clientStartupTimeoutMs
    );
    if (!clientResult?.ok) {
      throw new Error(
        clientResult?.error ||
          'Project AirSim client did not return a successful load result.'
      );
    }
    if (this._clientProcess?.exitCode !== null) {
      throw new Error('Project AirSim Python 客户端启动后已退出。');
    }
    if (this._sceneProcess?.exitCode !== null) {
      throw new Error('Project AirSim 场景启动后已退出。');
    }
    const protocolVersion = Number(clientResult.protocolVersion || 0);
    const capabilities = (
      clientResult.capabilities || []
    ).filter((capability): capability is UavSimulationCommandAction =>
      this._commandActionSet.has(capability)
    );
    const commandChannelAvailable =
      protocolVersion === SIMULATION_COMMAND_PROTOCOL_VERSION &&
      capabilities.length > 0;
    this._status.commandChannel = {
      available: commandChannelAvailable,
      protocolVersion: protocolVersion || undefined,
      capabilities,
      message: commandChannelAvailable
        ? 'Simulation command channel is ready.'
        : 'Project AirSim client does not expose the supported command protocol.'
    };
    const sensorService = clientResult.sensorService;
    const sensorServiceAvailable =
      sensorService?.available === true &&
      Number(sensorService.protocolVersion || 0) ===
        SIMULATION_SENSOR_PROTOCOL_VERSION;
    if (!sensorServiceAvailable) {
      throw new Error(
        'FleetDock SDK sensor service did not start with the supported protocol.'
      );
    }
    const manifest = await this._readSensorManifest(
      plan.files.sensorManifestFile
    );
    if (
      !manifest?.active ||
      String(manifest.runId || '') !== plan.files.runId ||
      Number(manifest.protocolVersion || 0) !==
        SIMULATION_SENSOR_PROTOCOL_VERSION
    ) {
      throw new Error('FleetDock SDK sensor runtime manifest is invalid.');
    }
    this._mediaRtspStreams = this._localRtspStreams(sensorService.rtspStreams);
    this._status.sensorService = {
      available: true,
      protocolVersion: SIMULATION_SENSOR_PROTOCOL_VERSION,
      sdkVersion: String(sensorService.sdkVersion || ''),
      vehicles: Array.isArray(sensorService.vehicles)
        ? sensorService.vehicles.map(String)
        : [],
      // [UAV-IDE MODIFY] Publish availability only; media URLs and randomized preview tokens remain main-process only.
      rtspVehicles: Array.from(
        new Set(this._mediaRtspStreams.map(stream => stream.vehicleName))
      ),
      message: 'FleetDock SDK sensor service is ready.'
    };
    await this._appendLog(`Verified run ${plan.files.runId}.`);
    await this._appendLog(
      commandChannelAvailable
        ? `Simulation command channel v${protocolVersion}: ${capabilities.join(
            ', '
          )}.`
        : 'Simulation command channel is unavailable for this client.'
    );
    await this._appendLog(
      `FleetDock SDK sensor service v${SIMULATION_SENSOR_PROTOCOL_VERSION} is ready for ${
        this._status.sensorService.vehicles?.length || 0
      } vehicle(s).`
    );
  }

  private async _readSensorManifest(
    manifestPath: string
  ): Promise<Record<string, unknown> | null> {
    try {
      return JSON.parse(await fs.promises.readFile(manifestPath, 'utf8'));
    } catch (_error) {
      return null;
    }
  }

  private async _invalidateSensorManifest(
    manifestPath: string,
    runId: string
  ): Promise<void> {
    const temporaryPath = `${manifestPath}.${process.pid}.tmp`;
    await fs.promises.mkdir(path.dirname(manifestPath), { recursive: true });
    await fs.promises.writeFile(
      temporaryPath,
      JSON.stringify(
        {
          active: false,
          runId,
          protocolVersion: SIMULATION_SENSOR_PROTOCOL_VERSION,
          updatedAt: new Date().toISOString()
        },
        null,
        2
      ),
      'utf8'
    );
    await fs.promises.rename(temporaryPath, manifestPath);
  }

  private async _waitForClientResult(
    resultFile: string,
    timeoutMs: number
  ): Promise<ClientStartupResult | null> {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (fs.existsSync(resultFile)) {
        try {
          // [UAV-IDE MODIFY] Surface Project AirSim schema and world-load failures to startup.
          return JSON.parse(await fs.promises.readFile(resultFile, 'utf8'));
        } catch (error) {
          return { ok: false, error: this._message(error) };
        }
      }
      if (this._clientProcess?.exitCode !== null) {
        break;
      }
      await this._sleep(250);
    }
    return null;
  }

  private async _cleanupConflicts(
    conflicts: UavSimulationConflictProcess[]
  ): Promise<void> {
    // [UAV-IDE MODIFY] Independent stale Windows process trees can be closed concurrently.
    await Promise.all(
      conflicts.map(conflict => this._killProcessTree(conflict.pid))
    );
  }

  private async _cleanupBeforeStart(
    plan: UavSimulationLaunchPlan,
    conflicts: UavSimulationConflictProcess[]
  ): Promise<void> {
    if (conflicts.length) {
      await this._appendLog(
        `Cleaning stale simulation processes: ${conflicts
          .map(conflict => `${conflict.name}:${conflict.pid}`)
          .join(', ')}`
      );
      await this._cleanupConflicts(conflicts);
    }
    // [UAV-IDE MODIFY] Clear stale PX4 SITL instances only by SkyLab labels or the configured PX4 path before launching.
    await this._stopPx4(plan, { includeStaleSkyLabProcesses: true });
  }

  private async _stopPx4(
    plan: UavSimulationLaunchPlan,
    options: { includeStaleSkyLabProcesses?: boolean } = {}
  ): Promise<boolean> {
    const labels = plan.drones.map(drone => this._px4ProcessLabel(plan, drone));
    let labelsStopped = true;
    if (labels.length) {
      // [UAV-IDE MODIFY] Match environment labels without changing PX4 argv[0], which PX4 uses to locate startup assets.
      const labelChecks = labels
        .map(
          label =>
            `grep -zFqx ${this._quoteBash(
              `SKYLAB_PX4_LABEL=${label}`
            )} "$environment_file"`
        )
        .join(' || ');
      const script = [
        'targets=""',
        'for proc in /proc/[0-9]*; do',
        '  pid="${proc##*/}"',
        '  [ "$pid" = "$$" ] && continue',
        '  environment_file="$proc/environ"',
        '  [ -r "$environment_file" ] || continue',
        `  if { ${labelChecks}; }; then`,
        '    targets="$targets $pid"',
        '  fi',
        'done',
        'if [ -n "$targets" ]; then',
        '  kill -TERM $targets 2>/dev/null || true',
        '  sleep 0.2',
        '  kill -KILL $targets 2>/dev/null || true',
        'fi'
      ].join('\n');
      labelsStopped = !!(await this._execFileAsync('wsl.exe', [
        '-d',
        plan.wslDistro,
        '--cd',
        plan.px4WslDirectory,
        'bash',
        '-lc',
        this._escapeWslShellExpansion(script)
      ]).catch((): null => null));
    }
    if (options.includeStaleSkyLabProcesses) {
      await this._stopPx4ByPath(this._cleanupTargetFromPlan(plan));
    }
    const children = [...this._px4Processes];
    await Promise.all(
      children.map(async child => {
        if (child.exitCode === null) {
          await this._killProcessTree(child.pid);
          await this._waitForExit(child, TRACKED_PROCESS_EXIT_TIMEOUT_MS);
        }
      })
    );
    this._px4Processes = [];
    return labelsStopped && children.every(child => child.exitCode !== null);
  }

  private async _stopSceneProcess(): Promise<boolean> {
    const child = this._sceneProcess;
    if (!child) {
      return true;
    }
    if (child.exitCode === null) {
      await this._killProcessTree(child.pid);
      await this._waitForExit(child, TRACKED_PROCESS_EXIT_TIMEOUT_MS);
    }
    this._sceneProcess = null;
    return child.exitCode !== null;
  }

  private _cleanupTargetFromPlan(
    plan: UavSimulationLaunchPlan
  ): SimulationCleanupTarget {
    return {
      wslDistro: plan.wslDistro,
      px4WslDirectory: plan.px4WslDirectory,
      scene: plan.scene,
      clientScript: plan.clientScript,
      runDirectory: plan.files.runDirectory
    };
  }

  private async _stopPx4ByPath(target: SimulationCleanupTarget): Promise<void> {
    const wslDistro = String(
      target.wslDistro || UAV_SIMULATION_DEFAULT_WSL_DISTRO
    ).trim();
    const px4WslDirectory = this._normalizePx4WslDirectory(
      target.px4WslDirectory
    );
    if (!wslDistro || !px4WslDirectory) {
      return;
    }

    const script = [
      `PX4_DIR=${this._quoteBash(px4WslDirectory)}`,
      'targets=""',
      'for proc in /proc/[0-9]*; do',
      '  pid="${proc##*/}"',
      '  [ "$pid" = "$$" ] && continue',
      '  exe="$(readlink -f "$proc/exe" 2>/dev/null || true)"',
      '  cwd="$(readlink -f "$proc/cwd" 2>/dev/null || true)"',
      '  cmd="$(tr \'\\000\' \' \' < "$proc/cmdline" 2>/dev/null || true)"',
      '  [ -z "$cmd" ] && continue',
      '  case "$cmd" in *skylab-px4-* ) targets="$targets $pid"; continue ;; esac',
      '  case "$exe" in "$PX4_DIR"/* ) targets="$targets $pid"; continue ;; esac',
      '  case "$cmd" in *"$PX4_DIR"*px4*|*px4*"$PX4_DIR"* ) targets="$targets $pid"; continue ;; esac',
      '  case "$cwd" in "$PX4_DIR"|"$PX4_DIR"/* ) case "$cmd" in *px4* ) targets="$targets $pid";; esac ;; esac',
      'done',
      'if [ -n "$targets" ]; then',
      '  kill -TERM $targets 2>/dev/null || true',
      '  sleep 0.2',
      '  kill -KILL $targets 2>/dev/null || true',
      'fi'
    ].join('\n');
    // [UAV-IDE MODIFY] Path-scoped WSL cleanup lets Stop recover PX4 after an IDE restart without global px4 pkill.
    await this._execFileAsync('wsl.exe', [
      '-d',
      wslDistro,
      'bash',
      '-lc',
      this._escapeWslShellExpansion(script)
    ]).catch((): void => undefined);
  }

  private _normalizePx4WslDirectory(input?: string): string {
    const value = String(input || '').trim();
    return String(value ? windowsPathToWslPath(value) : getDefaultPx4WslPath())
      .replace(/\/+$/, '')
      .trim();
  }

  private async _cleanupWindowsProcessesByPath(
    target: SimulationCleanupTarget
  ): Promise<void> {
    if (process.platform !== 'win32') {
      return;
    }

    const sceneWorkingDirectory =
      target.scene?.workingDirectory || target.scene?.rootDirectory || '';
    const sceneExecutablePath = target.scene?.executablePath || '';
    const clientScript =
      target.clientScript ||
      (target.scenePackageDirectory
        ? path.join(
            target.scenePackageDirectory,
            'client',
            'project_airsim_client.py'
          )
        : '');
    const runRootDirectory =
      target.runDirectory ||
      (target.workingDirectory
        ? path.join(target.workingDirectory, '.jupyter', 'simulation', 'runs')
        : '');
    if (
      !sceneWorkingDirectory &&
      !sceneExecutablePath &&
      !clientScript &&
      !runRootDirectory
    ) {
      return;
    }

    const payload = Buffer.from(
      JSON.stringify({
        sceneWorkingDirectory,
        sceneExecutablePath,
        clientScript,
        runRootDirectory
      }),
      'utf8'
    ).toString('base64');
    const script = [
      `$payload = '${payload}'`,
      '$json = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($payload))',
      '$target = ConvertFrom-Json -InputObject $json',
      'function NormalizePath($value) {',
      "  if (-not $value) { return '' }",
      '  try {',
      '    $resolved = [System.IO.Path]::GetFullPath([string]$value)',
      '  } catch {',
      '    $resolved = [string]$value',
      '  }',
      '  return $resolved.TrimEnd([char]92).ToLowerInvariant().Replace([char]47, [char]92)',
      '}',
      'function IsUnder($pathValue, $rootValue) {',
      '  if (-not $pathValue -or -not $rootValue) { return $false }',
      '  return $pathValue -eq $rootValue -or $pathValue.StartsWith($rootValue + [string][char]92)',
      '}',
      'function ContainsPath($text, $pathValue) {',
      '  if (-not $text -or -not $pathValue) { return $false }',
      '  return ([string]$text).ToLowerInvariant().Replace([char]47, [char]92).Contains($pathValue)',
      '}',
      '$sceneRoot = NormalizePath $target.sceneWorkingDirectory',
      '$sceneExe = NormalizePath $target.sceneExecutablePath',
      '$clientScript = NormalizePath $target.clientScript',
      '$runRoot = NormalizePath $target.runRootDirectory',
      '$currentPid = $PID',
      'Get-CimInstance Win32_Process | ForEach-Object {',
      '  $processId = [int]$_.ProcessId',
      '  if ($processId -eq $currentPid) { return }',
      '  $exe = NormalizePath $_.ExecutablePath',
      '  $cmd = [string]$_.CommandLine',
      '  $matched = $false',
      '  if ($sceneExe -and $exe -eq $sceneExe) { $matched = $true }',
      '  if (-not $matched -and $sceneRoot -and (IsUnder $exe $sceneRoot)) { $matched = $true }',
      '  if (-not $matched -and $sceneExe -and (ContainsPath $cmd $sceneExe)) { $matched = $true }',
      '  if (-not $matched -and $sceneRoot -and (ContainsPath $cmd $sceneRoot)) { $matched = $true }',
      '  if (-not $matched -and $clientScript -and (ContainsPath $cmd $clientScript)) { $matched = $true }',
      '  if (-not $matched -and $runRoot -and (ContainsPath $cmd $runRoot)) { $matched = $true }',
      '  if ($matched) {',
      '    try { Stop-Process -Id $processId -Force -ErrorAction Stop } catch {}',
      '  }',
      '}'
    ].join('\n');
    // [UAV-IDE MODIFY] Recover stale Project AirSim scene/client processes by configured SkyLab paths after restart.
    await this._execFileAsync('powershell.exe', [
      '-NoProfile',
      '-ExecutionPolicy',
      'Bypass',
      '-Command',
      script
    ]).catch((): void => undefined);
  }

  private async _runStage(
    stage: UavSimulationStatus['state'],
    message: string,
    action: () => Promise<void>
  ): Promise<void> {
    this._setStage(stage, 'running', message);
    this._status.state = stage;
    this._status.title = message;
    this._status.message = message;
    try {
      if (this._startTiming) {
        await this._startTiming.measure(`stage.${stage}`, action);
      } else {
        await action();
      }
      this._finishStage(stage, 'success');
    } catch (error) {
      this._finishStage(stage, 'failed', this._message(error));
      throw error;
    }
  }

  private _resetStatus(
    state: UavSimulationStatus['state'],
    title: string,
    message: string
  ): void {
    this._mediaRtspStreams = [];
    this._status = {
      state,
      title,
      message,
      stages: [
        'checking',
        'awaiting-confirmation',
        'stopping-conflicts',
        'starting-scene',
        'starting-px4',
        'loading-world',
        'verifying',
        'starting-media',
        'starting-mavlink'
      ].map(stage => ({
        stage: stage as UavSimulationStatus['state'],
        state: 'pending',
        message: ''
      }))
    };
    this._setStage(state, 'running', message);
  }

  private _setStage(
    stage: UavSimulationStatus['state'],
    state: UavSimulationStageStatus['state'],
    message: string,
    detail?: string
  ): void {
    const item = this._status.stages.find(
      candidate => candidate.stage === stage
    );
    if (!item) {
      return;
    }
    item.state = state;
    item.message = message;
    item.detail = detail;
    if (state === 'running') {
      item.startedAt = new Date().toISOString();
    }
    if (state === 'success' || state === 'failed') {
      item.finishedAt = new Date().toISOString();
    }
  }

  private _finishStage(
    stage: UavSimulationStatus['state'],
    state: UavSimulationStageStatus['state'],
    detail?: string
  ): void {
    const item = this._status.stages.find(
      candidate => candidate.stage === stage
    );
    this._setStage(stage, state, item?.message || '', detail);
  }

  private _startSignatureFromOptions(options: SimulationStartOptions): string {
    return JSON.stringify({
      scene: {
        id: options.scene.id,
        executablePath: options.scene.executablePath,
        workingDirectory: options.scene.workingDirectory
      },
      px4Path: options.px4Path || '',
      scenePackageDirectory: options.scenePackageDirectory || '',
      drones: (options.config.drones || []).map((drone, index) => ({
        index,
        id: drone.id,
        name: drone.name,
        airframeType: drone.uav?.airframeType || drone.simulation?.model || '',
        px4AutostartId: drone.uav?.px4AutostartId || '',
        mavlinkPort:
          drone.uav?.mavlinkPort ||
          drone.simulation?.runtime?.mavlinkPort ||
          drone.telemetry?.bindPort ||
          '',
        simulatorPort: drone.uav?.simulatorPort || '',
        systemId: drone.uav?.systemId || drone.systemId || ''
      }))
    });
  }

  private _toStartResult(status: UavSimulationStatus): SimulationStartResult {
    return {
      state: this._toResultState(status.state),
      airsimOnline: status.state === 'running',
      title: status.title,
      message: status.message,
      logPath: status.logPath,
      stages: status.stages,
      drones: (status.drones || []).map(drone => ({
        droneId: drone.droneId,
        px4Online: status.state === 'running',
        connected: drone.connected,
        connectionURI: `udp://0.0.0.0:${drone.mavlinkPort}`,
        airsimVehicleName: drone.vehicleName,
        systemId: drone.systemId,
        error: drone.error
      }))
    };
  }

  private _toResultState(state: UavSimulationStatus['state']): string {
    if (state === 'running') {
      return 'RUNNING';
    }
    if (state === 'failed') {
      return 'FAILED';
    }
    return state.toUpperCase();
  }

  private async _resolveWindowsHostAddress(): Promise<string> {
    const result = await this._execFileAsync('powershell.exe', [
      '-NoProfile',
      '-Command',
      "(Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias '*WSL*' -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty IPAddress)"
    ]).catch(() => ({ stdout: '', stderr: '' }));
    return String(result.stdout || '').trim() || '127.0.0.1';
  }

  private async _drainCommandQueue(): Promise<void> {
    if (this._commandProcessing) {
      return;
    }
    this._commandProcessing = true;
    try {
      while (this._commandQueue.length) {
        const item = this._commandQueue.shift();
        if (!item) {
          continue;
        }
        if (this._status.state !== 'running') {
          item.resolve(
            this._commandFailure(
              item.request.action,
              'NOT_RUNNING',
              'Simulation is not running.'
            )
          );
          continue;
        }
        item.resolve(await this._sendCommand(item.request));
      }
    } finally {
      this._commandProcessing = false;
    }
  }

  private _sendCommand(
    request: UavSimulationCommandRequest
  ): Promise<UavSimulationCommandResult> {
    const child = this._clientProcess;
    const runId = this._plan?.files.runId || '';
    if (
      !child?.stdin ||
      child.stdin.destroyed ||
      !child.stdin.writable ||
      !runId
    ) {
      return Promise.resolve(
        this._commandFailure(
          request.action,
          'CHANNEL_UNAVAILABLE',
          'Project AirSim client input channel is unavailable.'
        )
      );
    }

    const id = `${runId}:${++this._commandSequence}`;
    const serialized = JSON.stringify({
      version: SIMULATION_COMMAND_PROTOCOL_VERSION,
      id,
      runId,
      action: request.action,
      params: request.params || {}
    });
    return new Promise(resolve => {
      const timeout = setTimeout(() => {
        this._completePendingCommand(
          id,
          this._commandFailure(
            request.action,
            'TIMEOUT',
            'Simulation command timed out.',
            id,
            runId
          )
        );
      }, SIMULATION_COMMAND_TIMEOUT_MS);
      this._pendingCommands.set(id, {
        action: request.action,
        runId,
        startedAt: Date.now(),
        timeout,
        resolve
      });
      child.stdin?.write(serialized + '\n', 'utf8', error => {
        if (!error) {
          return;
        }
        this._completePendingCommand(
          id,
          this._commandFailure(
            request.action,
            'WRITE_FAILED',
            this._message(error),
            id,
            runId
          )
        );
      });
    });
  }

  private _completePendingCommand(
    id: string,
    result: UavSimulationCommandResult
  ): void {
    const pending = this._pendingCommands.get(id);
    if (!pending) {
      return;
    }
    this._pendingCommands.delete(id);
    clearTimeout(pending.timeout);
    const normalized = {
      ...result,
      id,
      runId: pending.runId,
      action: pending.action,
      durationMs: Math.max(0, Date.now() - pending.startedAt)
    };
    if (!normalized.ok && this._status.commandChannel) {
      this._status.commandChannel.lastError = normalized.message;
    }
    pending.resolve(normalized);
  }

  private _cancelSimulationCommands(code: string, message: string): void {
    for (const item of this._commandQueue.splice(0)) {
      item.resolve(this._commandFailure(item.request.action, code, message));
    }
    for (const [id, pending] of Array.from(this._pendingCommands.entries())) {
      this._completePendingCommand(
        id,
        this._commandFailure(pending.action, code, message, id, pending.runId)
      );
    }
  }

  private _commandFailure(
    action: string,
    code: string,
    message: string,
    id = '',
    runId?: string
  ): UavSimulationCommandResult {
    return { id, runId, action, ok: false, code, message };
  }

  private _pipeClientLog(child: ChildProcess, prefix: string): void {
    child.stdout?.on('data', chunk => {
      this._clientStdoutBuffer += String(chunk).replace(/\r/g, '');
      const lines = this._clientStdoutBuffer.split('\n');
      this._clientStdoutBuffer = lines.pop() || '';
      for (const line of lines) {
        this._handleClientStdoutLine(line, prefix);
      }
    });
    child.stderr?.on('data', chunk => {
      const message = this._cleanProcessLog(prefix, chunk);
      if (message) {
        void this._appendLog(`[${prefix} stderr] ${message}`);
      }
    });
    child.once('exit', code => {
      if (this._clientStdoutBuffer) {
        this._handleClientStdoutLine(this._clientStdoutBuffer, prefix);
        this._clientStdoutBuffer = '';
      }
      this._cancelSimulationCommands(
        'CLIENT_EXITED',
        'Project AirSim client exited before the command completed.'
      );
      if (this._status.commandChannel) {
        this._status.commandChannel.available = false;
        this._status.commandChannel.lastError = 'Project AirSim client exited.';
      }
      void this._appendLog(`[${prefix}] exited with code ${code ?? 'unknown'}`);
    });
  }

  private _handleClientStdoutLine(line: string, prefix: string): void {
    const message = String(line || '').trimEnd();
    if (!message) {
      return;
    }
    if (!message.startsWith(SIMULATION_COMMAND_RESULT_PREFIX)) {
      void this._appendLog(`[${prefix} stdout] ${message}`);
      return;
    }
    try {
      const result = JSON.parse(
        message.slice(SIMULATION_COMMAND_RESULT_PREFIX.length)
      ) as UavSimulationCommandResult;
      const id = String(result?.id || '');
      if (!id || !this._pendingCommands.has(id)) {
        void this._appendLog(
          `[${prefix}] ignored stale command response ${id}.`
        );
        return;
      }
      const pending = this._pendingCommands.get(id);
      if (!pending || String(result.runId || '') !== pending.runId) {
        this._completePendingCommand(
          id,
          this._commandFailure(
            result.action || pending?.action || '',
            'RUN_MISMATCH',
            'Simulation command response belongs to a different run.',
            id,
            pending?.runId
          )
        );
        return;
      }
      this._completePendingCommand(id, result);
      void this._appendLog(
        `[${prefix}] command ${result.action || 'unknown'} ${
          result.ok ? 'completed' : `failed: ${result.code || 'UNKNOWN'}`
        }.`
      );
    } catch (error) {
      void this._appendLog(
        `[${prefix}] invalid command response: ${this._message(error)}`
      );
    }
  }

  private _pipeLog(child: ChildProcess, prefix: string): void {
    child.stdout?.on('data', chunk => {
      const message = this._cleanProcessLog(prefix, chunk);
      if (message) {
        void this._appendLog(`[${prefix} stdout] ${message}`);
      }
    });
    child.stderr?.on('data', chunk => {
      const message = this._cleanProcessLog(prefix, chunk);
      if (message) {
        void this._appendLog(`[${prefix} stderr] ${message}`);
      }
    });
    child.once('exit', code => {
      void this._appendLog(`[${prefix}] exited with code ${code ?? 'unknown'}`);
    });
  }

  private _cleanProcessLog(prefix: string, chunk: unknown): string {
    let message = String(chunk).trimEnd();
    if (!prefix.startsWith('px4:')) {
      return message;
    }
    // [UAV-IDE MODIFY] Drop empty PX4 shell prompts so idle SITL does not grow simulation.log by hundreds of MB.
    message = message
      .replace(/\x1b\[[0-9;?]*[ -/]*[@-~]/g, '')
      .replace(/\r/g, '\n')
      .split('\n')
      .map(line => line.trim())
      .map(line => line.replace(/(?:^|\s)pxh>\s*/g, '').trim())
      .filter(Boolean)
      .join('\n');
    return message.trimEnd();
  }

  private async _appendLog(line: string): Promise<void> {
    const logPath = this._status.logPath || this._plan?.files.logFile;
    if (!logPath) {
      return;
    }
    await fs.promises.appendFile(
      logPath,
      `[${new Date().toISOString()}] ${line}\n`,
      'utf8'
    );
  }

  private async _waitForAlive(
    child: ChildProcess,
    timeoutMs: number,
    label: string
  ): Promise<void> {
    await this._sleep(timeoutMs);
    if (child.exitCode !== null || child.killed) {
      throw new Error(`${label} 启动后已退出。`);
    }
  }

  private async _waitForSpawn(
    child: ChildProcess,
    label: string
  ): Promise<void> {
    if (child.pid) {
      return;
    }
    await new Promise<void>((resolve, reject) => {
      const cleanup = () => {
        clearTimeout(timeout);
        child.removeListener('spawn', onSpawn);
        child.removeListener('error', onError);
      };
      const onSpawn = () => {
        cleanup();
        resolve();
      };
      const onError = (error: Error) => {
        cleanup();
        reject(new Error(`${label} 启动失败：${error.message}`));
      };
      const timeout = setTimeout(() => {
        cleanup();
        reject(new Error(`${label} 未在超时时间内创建进程。`));
      }, PROCESS_SPAWN_TIMEOUT_MS);
      child.once('spawn', onSpawn);
      child.once('error', onError);
    });
  }

  private async _waitForExit(
    child: ChildProcess,
    timeoutMs: number
  ): Promise<void> {
    if (child.exitCode !== null) {
      return;
    }
    await new Promise<void>(resolve => {
      const onExit = () => {
        clearTimeout(timeout);
        resolve();
      };
      const timeout = setTimeout(() => {
        child.removeListener('exit', onExit);
        resolve();
      }, timeoutMs);
      child.once('exit', onExit);
    });
  }

  private async _allSettledOrThrow(actions: Promise<void>[]): Promise<void> {
    const results = await Promise.allSettled(actions);
    const failure = results.find(result => result.status === 'rejected');
    if (failure?.status === 'rejected') {
      throw failure.reason;
    }
  }

  private async _killProcessTree(pid?: number): Promise<void> {
    if (!pid) {
      return;
    }
    if (process.platform === 'win32') {
      await this._execFileAsync('taskkill.exe', [
        '/PID',
        String(pid),
        '/T',
        '/F'
      ]).catch((): void => undefined);
      return;
    }
    try {
      process.kill(pid, 'SIGTERM');
    } catch (_error) {
      /* [UAV-IDE MODIFY] Stop remains best-effort for already-exited child processes. */
    }
  }

  private _parseProcessList(
    output: string
  ): Array<{
    pid: number;
    name: string;
    detail?: string;
  }> {
    const text = String(output || '').trim();
    if (!text) {
      return [];
    }
    try {
      const parsed = JSON.parse(text);
      const items = Array.isArray(parsed) ? parsed : [parsed];
      return items
        .map(item => ({
          pid: Number(item.ProcessId || item.Id || item.id || 0),
          name: String(item.Name || item.ProcessName || item.name || ''),
          detail: String(
            item.Detail ||
              item.detail ||
              item.CommandLine ||
              item.commandLine ||
              item.ExecutablePath ||
              item.Path ||
              item.path ||
              ''
          )
        }))
        .filter(item => item.pid && item.name);
    } catch (_error) {
      return [];
    }
  }

  private _looksLikeClient(
    commandLine: string | undefined,
    clientScript: string
  ): boolean {
    const normalizedCommandLine = String(commandLine || '')
      .toLowerCase()
      .replace(/[\\/]+/g, '\\');
    const normalizedClientScript = String(clientScript || '')
      .toLowerCase()
      .replace(/[\\/]+/g, '\\');
    return (
      !!normalizedClientScript &&
      normalizedCommandLine.includes(normalizedClientScript)
    );
  }

  private _execFileAsync(command: string, args: string[]): Promise<ExecResult> {
    return new Promise((resolve, reject) => {
      execFile(
        command,
        args,
        { windowsHide: true },
        (error, stdout, stderr) => {
          if (error) {
            reject(new Error(stderr || error.message));
            return;
          }
          resolve({
            stdout: String(stdout || ''),
            stderr: String(stderr || '')
          });
        }
      );
    });
  }

  private _sleep(timeoutMs: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, timeoutMs));
  }

  private _quoteBash(value: string | number): string {
    return `'${String(value).replace(/'/g, "'\\''")}'`;
  }

  private _escapeWslShellExpansion(script: string): string {
    // [UAV-IDE MODIFY] wsl.exe expands dollar expressions once before bash -lc evaluates the script.
    return script.replace(/\$/g, '\\$');
  }

  private _localRtspStreams(value: unknown): IFleetDockRtspStream[] {
    if (!Array.isArray(value)) {
      return [];
    }
    const streams: IFleetDockRtspStream[] = [];
    const ids = new Set<string>();
    for (const candidate of value) {
      if (
        !candidate ||
        typeof candidate !== 'object' ||
        Array.isArray(candidate)
      ) {
        continue;
      }
      try {
        const item = candidate as Record<string, unknown>;
        const id = String(item.id || '').trim();
        const vehicleName = String(item.vehicleName || '').trim();
        const sensorId = String(item.sensorId || '').trim();
        const url = new URL(String(item.uri || ''));
        if (
          id &&
          vehicleName &&
          sensorId &&
          !ids.has(id) &&
          url.protocol === 'rtsp:' &&
          !url.username &&
          !url.password &&
          ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) &&
          url.pathname !== '/'
        ) {
          ids.add(id);
          streams.push({
            id,
            vehicleName,
            sensorId,
            label: String(item.label || sensorId),
            rtspUri: url.toString()
          });
        }
      } catch {
        // [UAV-IDE MODIFY] Ignore malformed, credentialed, or non-loopback RTSP addresses from simulation clients.
      }
    }
    return streams;
  }

  private _rtspPorts(plan: UavSimulationLaunchPlan): number[] {
    const ports = plan.drones
      .map(drone => String(drone.cameraStreamUri || '').trim())
      .filter(Boolean)
      .map(uri => {
        try {
          const parsed = new URL(uri);
          return Number(parsed.port || PROJECT_AIRSIM_RTSP_PORT);
        } catch (_error) {
          return PROJECT_AIRSIM_RTSP_PORT;
        }
      })
      .filter(port => Number.isInteger(port) && port > 0 && port <= 65535);
    return Array.from(new Set(ports));
  }

  private _px4ProcessLabel(
    plan: UavSimulationLaunchPlan,
    drone: UavSimulationLaunchPlan['drones'][number]
  ): string {
    return `skylab-px4-${plan.files.runId}-${drone.px4Instance}`;
  }

  private _message(error: unknown): string {
    return error instanceof Error ? error.message : String(error || '未知错误');
  }

  private _generator = new SimulationConfigGenerator();
  private _environment = new SimulationEnvironmentService();
  private _plan: UavSimulationLaunchPlan | null = null;
  private _sceneProcess: ChildProcess | null = null;
  private _clientProcess: ChildProcess | null = null;
  private _clientStdoutBuffer = '';
  private _px4Processes: ChildProcess[] = [];
  private _startPromise: Promise<SimulationStartResult> | null = null;
  private _startTiming: SimulationTimingRecorder | null = null;
  private _activeStartSignature: string | null = null;
  private _commandSequence = 0;
  private _commandProcessing = false;
  private _commandQueue: QueuedSimulationCommand[] = [];
  private _pendingCommands = new Map<string, PendingSimulationCommand>();
  private _commandActionSet = new Set<string>(UAV_SIMULATION_COMMAND_ACTIONS);
  private _mediaRtspStreams: IFleetDockRtspStream[] = [];
  private _mediaService = new MediaMtxService(message =>
    this._appendLog(message)
  );
  private _status: UavSimulationStatus = {
    state: 'idle',
    title: '未启动',
    message: '等待启动仿真。',
    stages: []
  };
}
