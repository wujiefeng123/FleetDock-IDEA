import { BrowserWindow } from 'electron';
import { EventManager } from '../eventmanager';
import { UavSimulationController } from '../uavsimulation/ipc/uav-simulation-controller';

export interface ISimulationControllerOptions {
  eventManager: EventManager;
  parent: BrowserWindow;
  isDarkTheme: boolean;
  getWorkingDirectory: () => string | undefined;
  isRemoteSession: () => boolean;
  isUavManagerSender: (sender: Electron.WebContents) => boolean;
  onStop?: () => void;
}

// [UAV-IDE MODIFY] Keep sidebar simulation IPC isolated from removed legacy launcher/menu entrypoints.
export class SimulationController {
  constructor(private _options: ISimulationControllerOptions) {}

  register() {
    // [UAV-IDE MODIFY] Register UAV scene/env/start/stop/status IPC for the sidebar module only.
    this._uavSimulationController.register();
  }

  async dispose(): Promise<void> {
    this._options.onStop?.();
    // [UAV-IDE MODIFY] Ensure IDE/session close tears down UAV simulation resources owned by the sidebar module.
    await this._uavSimulationController.dispose();
  }

  private _uavSimulationController = new UavSimulationController({
    eventManager: this._options.eventManager,
    parent: this._options.parent,
    isDarkTheme: this._options.isDarkTheme,
    getWorkingDirectory: this._options.getWorkingDirectory,
    isRemoteSession: this._options.isRemoteSession,
    isUavManagerSender: this._options.isUavManagerSender,
    onStop: this._options.onStop
  });
}
