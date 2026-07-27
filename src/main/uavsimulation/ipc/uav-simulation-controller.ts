// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import { BrowserWindow, dialog } from 'electron';
import { SimulationConfig, SimulationStartResult } from '../../devices/types';
import { EventManager } from '../../eventmanager';
import { EventTypeMain } from '../../eventtypes';
import {
  cleanupFleetDockMavsdkServers,
  listFleetDockMavsdkServers
} from '../../fleetdock/mavsdk-cleanup';
import { SimulationEnvironmentService } from '../environment/simulation-environment-service';
import { SimulationProcessSupervisor } from '../process/simulation-process-supervisor';
import { UavSimulationSceneDirectoryService } from '../scenes/scene-directory-service';
import { UavSimulationSceneStore } from '../scenes/scene-store';
import {
  UavSimulationCommandRequest,
  UavSimulationCommandResult,
  UavSimulationConflictProcess,
  UavSimulationScene,
  UavSimulationSceneState,
  UavSimulationStartRequest,
  UavSimulationStatus,
  UavSimulationWebRtcOfferRequest,
  UavSimulationWebRtcSessionResult
} from '../types';
import { confirmUavSimulationDisconnect } from './uav-simulation-disconnect-dialog';

export interface IUavSimulationControllerOptions {
  eventManager: EventManager;
  parent: BrowserWindow;
  isDarkTheme: boolean;
  getWorkingDirectory: () => string | undefined;
  isRemoteSession: () => boolean;
  isUavManagerSender: (sender: Electron.WebContents) => boolean;
  onStop?: () => void;
}

// [UAV-IDE MODIFY] Keep UAV simulation IPC isolated from SessionWindow and legacy device modules.
export class UavSimulationController {
  constructor(private _options: IUavSimulationControllerOptions) {}

  register(): void {
    const evm = this._options.eventManager;

    evm.registerSyncEventHandler(
      EventTypeMain.UavSimulationEnvironmentCheck,
      async event => {
        if (!this._options.isUavManagerSender(event.sender)) {
          return { ok: false, items: [], checkedAt: new Date().toISOString() };
        }
        const scene = this._selectedScene();
        const state = this._sceneStore.load();
        return this._environment.check({
          scene: scene || undefined,
          px4WslDirectory: state.px4Path,
          // [UAV-IDE MODIFY] Match managed simulation runtime ports: Project AirSim TCP, MAVLink, and control.
          ports: [4560, 14600, 14580]
        });
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.UavSimulationSceneList,
      async event => {
        if (!this._options.isUavManagerSender(event.sender)) {
          return this._sceneStore.load();
        }
        return this.listScenes();
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.UavSimulationConfigSave,
      async (
        event,
        config: { px4Path?: string; scenePackageDirectory?: string }
      ) => {
        if (!this._options.isUavManagerSender(event.sender)) {
          return this._sceneStore.load();
        }
        return this.saveConfig(config || {});
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.UavSimulationSceneAddDirectory,
      async event => {
        if (!this._options.isUavManagerSender(event.sender)) {
          return this._sceneStore.load();
        }
        return this.addSceneDirectory();
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.UavSimulationSceneUpdate,
      async (event, scene: Partial<UavSimulationScene>) => {
        if (!this._options.isUavManagerSender(event.sender)) {
          return this._sceneStore.load();
        }
        return this.saveScene(scene || {});
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.UavSimulationSceneDelete,
      async (event, sceneId: string) => {
        if (!this._options.isUavManagerSender(event.sender)) {
          return this._sceneStore.load();
        }
        return this.deleteScene(sceneId);
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.UavSimulationStart,
      async (event, request: UavSimulationStartRequest) => {
        if (!this._options.isUavManagerSender(event.sender)) {
          return null;
        }
        return this.start(request || {});
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.UavSimulationStop,
      async event => {
        if (!this._options.isUavManagerSender(event.sender)) {
          return this.status();
        }
        const status = await this.stop({ confirmDisconnect: true });
        // [UAV-IDE MODIFY] A cancelled MAVSDK disconnect must not close the HUD or report a completed stop.
        if (status.state === 'idle') {
          this._options.onStop?.();
        }
        return status;
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.UavSimulationStatus,
      async event => {
        if (!this._options.isUavManagerSender(event.sender)) {
          return this.status();
        }
        return this.status();
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.UavSimulationCameraStreams,
      async (event, vehicleName: string) => {
        if (!this._options.isUavManagerSender(event.sender)) {
          return [];
        }
        return this._supervisor.cameraStreams(vehicleName);
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.UavSimulationWebRtcOffer,
      async (event, request: UavSimulationWebRtcOfferRequest) => {
        if (!this._options.isUavManagerSender(event.sender)) {
          return {
            ok: false,
            streamId: String(request?.streamId || ''),
            message: 'Simulation WebRTC sender is not authorized.'
          } as UavSimulationWebRtcSessionResult;
        }
        return this._supervisor.createWebRtcSession(
          String(request?.streamId || ''),
          String(request?.offerSdp || '')
        );
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.UavSimulationWebRtcClose,
      async (event, sessionId: string) => {
        if (!this._options.isUavManagerSender(event.sender)) {
          return false;
        }
        await this._supervisor.closeWebRtcSession(sessionId);
        return true;
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.UavSimulationCommand,
      async (event, request: UavSimulationCommandRequest) => {
        if (!this._options.isUavManagerSender(event.sender)) {
          return {
            id: '',
            action: String(request?.action || ''),
            ok: false,
            code: 'UNAUTHORIZED',
            message: 'Simulation command sender is not authorized.'
          } as UavSimulationCommandResult;
        }
        return this.executeCommand(request);
      }
    );
  }

  listScenes(): UavSimulationSceneState {
    return this._scanConfiguredScenePackageDirectory();
  }

  saveScene(scene: Partial<UavSimulationScene>): UavSimulationSceneState {
    return this._sceneStore.saveScene(scene);
  }

  saveConfig(config: {
    px4Path?: string;
    scenePackageDirectory?: string;
  }): UavSimulationSceneState {
    this._sceneStore.saveConfig(config);
    return this._scanConfiguredScenePackageDirectory();
  }

  deleteScene(sceneId: string): UavSimulationSceneState {
    return this._sceneStore.deleteScene(sceneId);
  }

  selectScene(sceneId: string): UavSimulationSceneState {
    return this._sceneStore.selectScene(sceneId);
  }

  async addSceneDirectory(): Promise<UavSimulationSceneState> {
    const { canceled, filePaths } = await dialog.showOpenDialog(
      this._options.parent,
      {
        title: '选择 Project AirSim 场景目录',
        defaultPath: this._sceneStore.load().scenePackageDirectory,
        properties: ['openDirectory']
      }
    );
    if (canceled || !filePaths.length) {
      return this._sceneStore.load();
    }
    const inspected = this._directoryService.scanPackageDirectory(filePaths[0]);
    return this._sceneStore.saveScannedScenes(inspected, filePaths[0]);
  }

  async start(
    request: UavSimulationStartRequest
  ): Promise<SimulationStartResult> {
    if (this._options.isRemoteSession()) {
      return {
        state: 'FAILED',
        airsimOnline: false,
        title: '本地仿真启动失败',
        message: '本地 UAV 仿真仅支持本地 Windows 工作区，远程会话暂不支持。',
        drones: []
      };
    }
    const workingDirectory = this._options.getWorkingDirectory();
    if (!workingDirectory) {
      return {
        state: 'FAILED',
        airsimOnline: false,
        title: '本地仿真启动失败',
        message: '未找到当前应用工作区。',
        drones: []
      };
    }

    const simulationState = this.listScenes();
    const scene = this._resolveScene(request, simulationState);
    if (!scene) {
      return {
        state: 'FAILED',
        airsimOnline: false,
        title: '本地仿真启动失败',
        message: '请先添加并选择 Project AirSim 场景目录。',
        drones: []
      };
    }

    const config: SimulationConfig = request.config || {
      airsim: {
        mode: 'local',
        executablePath: scene.executablePath,
        workdir: scene.workingDirectory,
        autoStart: true
      },
      drones: request.drones || []
    };

    return this._supervisor.start({
      workingDirectory,
      scene,
      px4Path: simulationState.px4Path,
      scenePackageDirectory: simulationState.scenePackageDirectory,
      config,
      forceCleanup: !!request.forceCleanup,
      confirmCleanup: conflicts => this._confirmCleanup(conflicts)
    });
  }

  async stop(
    options: {
      silent?: boolean;
      confirmDisconnect?: boolean;
    } = {}
  ): Promise<UavSimulationStatus> {
    const simulationState = this.listScenes();
    const scene = this._resolveScene({}, simulationState);
    const endpoints = this._activeMavsdkEndpoints();
    const servers = listFleetDockMavsdkServers(endpoints);
    if (
      servers.length > 0 &&
      !options.silent &&
      options.confirmDisconnect !== false
    ) {
      const confirmed = await this._confirmMavsdkDisconnect(servers);
      if (!confirmed) {
        return this.status();
      }
    }

    // [UAV-IDE MODIFY] Reuse the process list already shown in the confirmation dialog.
    cleanupFleetDockMavsdkServers(endpoints, servers);
    return this._supervisor.stop({
      silent: options.silent,
      // [UAV-IDE MODIFY] Provide configured paths so Stop can clean stale simulation processes after an IDE restart.
      cleanupTarget: {
        px4WslDirectory: simulationState.px4Path,
        scene,
        scenePackageDirectory: simulationState.scenePackageDirectory,
        workingDirectory: this._options.getWorkingDirectory()
      }
    });
  }

  status(): UavSimulationStatus {
    return this._supervisor.status();
  }

  executeCommand(
    request: UavSimulationCommandRequest
  ): Promise<UavSimulationCommandResult> {
    return this._supervisor.executeCommand(request);
  }

  async dispose(): Promise<void> {
    await this.stop({ silent: true, confirmDisconnect: false });
  }

  private _resolveScene(
    request: UavSimulationStartRequest,
    state = this.listScenes()
  ): UavSimulationScene | null {
    if (request.scene) {
      return request.scene;
    }
    const sceneId = request.sceneId || state.selectedSceneId;
    return (
      state.scenes.find(scene => scene.id === sceneId) ||
      state.scenes[0] ||
      null
    );
  }

  private _selectedScene(): UavSimulationScene | null {
    const state = this.listScenes();
    return (
      state.scenes.find(scene => scene.id === state.selectedSceneId) || null
    );
  }

  private _scanConfiguredScenePackageDirectory(): UavSimulationSceneState {
    const state = this._sceneStore.load();
    const scenePackageDirectory = String(
      state.scenePackageDirectory || ''
    ).trim();
    if (!scenePackageDirectory) {
      return this._sceneStore.saveScannedScenes([], scenePackageDirectory);
    }
    try {
      // [UAV-IDE MODIFY] Derive the scene selector from the configured SkyLabSim package directory instead of stale user-registered scene data.
      const inspected = this._directoryService.scanPackageDirectory(
        scenePackageDirectory
      );
      return this._sceneStore.saveScannedScenes(
        inspected,
        scenePackageDirectory
      );
    } catch (error) {
      console.warn('Failed to scan configured Project AirSim scenes:', error);
      return this._sceneStore.saveScannedScenes([], scenePackageDirectory);
    }
  }

  private async _confirmCleanup(
    conflicts: UavSimulationConflictProcess[]
  ): Promise<boolean> {
    const detail = conflicts
      .map(
        item =>
          `${item.name} (PID ${item.pid})${
            item.detail ? `\n${item.detail}` : ''
          }`
      )
      .join('\n\n');
    const result = await dialog.showMessageBox(this._options.parent, {
      type: 'warning',
      buttons: ['取消启动', '关闭并继续'],
      defaultId: 0,
      cancelId: 0,
      title: '发现仿真冲突进程',
      message: '启动前需要关闭以下进程。是否继续？',
      detail
    });
    return result.response === 1;
  }

  private async _confirmMavsdkDisconnect(
    servers: ReturnType<typeof listFleetDockMavsdkServers>
  ): Promise<boolean> {
    return confirmUavSimulationDisconnect({
      parent: this._options.parent,
      isDarkTheme: this._options.isDarkTheme,
      servers
    });
  }

  private _activeMavsdkEndpoints(): string[] {
    return (this._supervisor.status().drones || [])
      .map(drone => Number(drone.mavlinkPort))
      .filter(port => Number.isInteger(port))
      .map(port => `udpin://0.0.0.0:${port}`);
  }

  private _environment = new SimulationEnvironmentService();
  private _sceneStore = new UavSimulationSceneStore();
  private _directoryService = new UavSimulationSceneDirectoryService();
  private _supervisor = new SimulationProcessSupervisor();
}
