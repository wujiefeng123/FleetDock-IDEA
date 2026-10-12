// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import {
  ISessionExtension,
  ISessionExtensionContext,
  ISessionExtensionFactory
} from '../extensions/session-extension';
import { CodeAppCenterController } from '../codeappcenter/codeappcenter-controller';
import { CodeAppCenterSettings } from '../codeappcenter/settings';
import { DeviceManagerController } from '../devices/device-manager-controller';
import { syncManagedDroneEnvVars } from '../devices/device-env-sync';
import { getBundledPythonPath } from '../utils';
import { SessionDialogController } from '../sessionwindow/session-dialog-controller';
import { SimulationController } from '../simulation/simulation-controller';
import { FleetDockHudWindowController } from './hud-window-controller';
import { FleetDockLabInjector } from './lab-injector';
import { FleetDockWorkspaceController } from './workspace-controller';
import { FleetDockHudRuntimeController } from './hud-runtime-controller';
import { FleetDockWelcomeInjector } from './welcome-injector';
import { FleetDockTitleBarInjector } from './titlebar-injector';
import { PRODUCT_NAME } from '../branding';
import type { JupyterServer } from '../server';
import { SettingType } from '../config/settings';
import { QgcController } from '../groundstation/qgc-controller';

/**
 * FleetDock features are attached here as migration layers are enabled.
 * Keeping the adapter valid while initially empty lets every layer compile and
 * be reviewed independently.
 */
class FleetDockSessionExtension implements ISessionExtension {
  readonly id = 'fleetdock';

  constructor(private _context: ISessionExtensionContext) {
    this._context.window.setTitle(PRODUCT_NAME);
    this._hudWindowController = new FleetDockHudWindowController({
      owner: this._context.window,
      isDarkTheme: () => this._context.isDarkTheme
    });
    this._dialogController = new SessionDialogController({
      eventManager: this._context.eventManager,
      parent: this._context.window,
      isDarkTheme: this._context.isDarkTheme,
      getSessionConfig: this._context.getSessionConfig,
      getLabWebContents: this._context.getLabWebContents,
      getWelcomeWebContents: this._context.getWelcomeWebContents,
      codeAppCenterSettings: this._codeAppCenterSettings
    });
    this._codeAppCenterController = new CodeAppCenterController({
      eventManager: this._context.eventManager,
      settings: this._codeAppCenterSettings,
      getDialogWebContents: () =>
        this._dialogController.codeAppCenterDialogWebContents,
      getWorkingDirectory: () => this._workingDirectory()
    });
    this._deviceManagerController = new DeviceManagerController({
      eventManager: this._context.eventManager,
      parent: this._context.window,
      isDarkTheme: this._context.isDarkTheme,
      getLabWebContents: this._context.getLabWebContents,
      getWelcomeWebContents: this._context.getWelcomeWebContents,
      getApplicationSettingsWebContents: () =>
        this._dialogController.applicationSettingsDialogWebContents,
      getWorkingDirectory: () => this._workingDirectory(),
      getWorkspaceSettings: this._context.getWorkspaceSettings
    });
    this._simulationController = new SimulationController({
      eventManager: this._context.eventManager,
      parent: this._context.window,
      isDarkTheme: this._context.isDarkTheme,
      getWorkingDirectory: () => this._workingDirectory(),
      isRemoteSession: () => !!this._context.getSessionConfig()?.isRemote,
      isUavManagerSender: sender =>
        this._deviceManagerController.isUavManagerSender(sender),
      onStop: () => this._hudWindowController.dispose()
    });
    this._workspaceController = new FleetDockWorkspaceController({
      eventManager: this._context.eventManager,
      deviceRegistry: this._deviceManagerController.registry,
      getWorkingDirectory: () => this._workingDirectory(),
      getSessionConfig: this._context.getSessionConfig,
      getWorkspaceSettings: this._context.getWorkspaceSettings,
      setWorkspaceSettings: this._context.setWorkspaceSettings,
      getServerPythonPath: this._context.getServerPythonPath,
      restartSessionInPythonEnvironment: this._context
        .restartSessionInPythonEnvironment,
      notifySessionConfigChanged: this._context.notifySessionConfigChanged,
      openSession: this._context.openSession,
      isLabSender: sender => sender === this._context.getLabWebContents(),
      isWelcomeSender: sender =>
        sender === this._context.getWelcomeWebContents(),
      isApplicationSettingsSender: sender =>
        sender === this._dialogController.applicationSettingsDialogWebContents
    });
    this._hudRuntimeController = new FleetDockHudRuntimeController({
      eventManager: this._context.eventManager,
      hudWindows: this._hudWindowController,
      getWorkspaceSettings: this._context.getWorkspaceSettings,
      isLabSender: sender => sender === this._context.getLabWebContents()
    });
    this._qgcController = new QgcController(
      this._context.eventManager,
      this._context.window,
      this._context.getLabWebContents
    );
  }

  activate(): void {
    this._dialogController.register();
    this._codeAppCenterController.register();
    this._deviceManagerController.register();
    this._simulationController.register();
    this._workspaceController.register();
    this._hudRuntimeController.register();
    this._qgcController.register();
  }

  onTitleBarViewCreated(webContents: Electron.WebContents): void {
    this._titleBarInjector.attach(webContents);
  }

  onLabViewCreated(webContents: Electron.WebContents): void {
    this._labInjector.attach(webContents);
    webContents.on('page-title-updated', (_event, title) => {
      setImmediate(() => {
        if (!this._context.window.isDestroyed()) {
          this._context.window.setTitle(
            String(title || PRODUCT_NAME).replace(/JupyterLab/g, PRODUCT_NAME)
          );
        }
      });
    });
  }

  onWelcomeViewCreated(webContents: Electron.WebContents): Promise<void> {
    return this._welcomeInjector.attach(webContents);
  }

  async beforeCreateServer(options: JupyterServer.IOptions): Promise<void> {
    const sessionConfig = this._context.getSessionConfig();
    if (!sessionConfig || sessionConfig.isRemote) {
      return;
    }

    const pythonPath = getBundledPythonPath();
    // [UAV-IDE MODIFY] FleetDock local sessions must never fall back to a
    // system Conda environment when the packaged runtime was not discovered yet.
    const environment =
      this._context.registry.getEnvironmentByPath(pythonPath) ||
      this._context.registry.addEnvironment(pythonPath);
    if (!environment) {
      throw new Error(
        `${PRODUCT_NAME} 内置 Python 环境不可用：${pythonPath}。请在“管理 Python 环境”中重新安装内置环境。`
      );
    }
    this._context
      .getWorkspaceSettings()
      .setValue(SettingType.pythonPath, pythonPath);
    sessionConfig.pythonPath = pythonPath;
    options.environment = environment;

    await syncManagedDroneEnvVars({
      workingDirectory: this._workingDirectory(),
      registry: this._deviceManagerController.registry,
      workspaceSettings: this._context.getWorkspaceSettings(),
      cleanupStaleMavsdkServers: true
    });
  }

  async dispose(): Promise<void> {
    this._dialogController.dispose();
    this._deviceManagerController.dispose();
    this._hudWindowController.dispose();
    this._titleBarInjector.dispose();
    this._welcomeInjector.dispose();
    await this._labInjector.dispose();
    await this._simulationController.dispose();
  }

  private _workingDirectory(): string {
    const sessionConfig = this._context.getSessionConfig();
    return (
      sessionConfig?.resolvedWorkingDirectory ||
      sessionConfig?.workingDirectory ||
      ''
    );
  }

  private _codeAppCenterSettings = new CodeAppCenterSettings();
  private _dialogController: SessionDialogController;
  private _codeAppCenterController: CodeAppCenterController;
  private _deviceManagerController: DeviceManagerController;
  private _simulationController: SimulationController;
  private _hudWindowController: FleetDockHudWindowController;
  private _labInjector = new FleetDockLabInjector();
  private _workspaceController: FleetDockWorkspaceController;
  private _hudRuntimeController: FleetDockHudRuntimeController;
  private _qgcController: QgcController;
  private _titleBarInjector = new FleetDockTitleBarInjector();
  private _welcomeInjector = new FleetDockWelcomeInjector();
}

export const fleetDockSessionExtensionFactory: ISessionExtensionFactory = (
  context: ISessionExtensionContext
) => new FleetDockSessionExtension(context);
