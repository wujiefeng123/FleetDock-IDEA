// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import { BrowserWindow, dialog } from 'electron';
import { ApplicationSettingsDialog } from '../appsettingsdialog/appsettingsdialog';
import { CodeAppCenterSettings } from '../codeappcenter/settings';
import { CodeAppCenterDialog } from '../codeappcenterdialog/codeappcenterdialog';
import { EventManager } from '../eventmanager';
import { EventTypeMain } from '../eventtypes';
import { RoutePickerDialog } from '../routepicker/routepicker';
import { SessionConfig } from '../config/sessionconfig';
import { WaypointPickerDialog } from '../waypointpicker/waypointpicker';

export interface ISessionDialogControllerOptions {
  eventManager: EventManager;
  parent: BrowserWindow;
  isDarkTheme: boolean;
  getSessionConfig: () => SessionConfig | undefined;
  getLabWebContents: () => Electron.WebContents | undefined;
  getWelcomeWebContents: () => Electron.WebContents | undefined;
  codeAppCenterSettings: CodeAppCenterSettings;
}

// [UAV-IDE MODIFY] Keep UAV application dialogs and picker routing outside SessionWindow.
export class SessionDialogController {
  constructor(private _options: ISessionDialogControllerOptions) {}

  register() {
    const evm = this._options.eventManager;

    evm.registerEventHandler(EventTypeMain.ShowApplicationSettings, event => {
      if (event.sender !== this._options.getLabWebContents()) {
        return;
      }
      this.showApplicationSettingsDialog();
    });

    evm.registerEventHandler(EventTypeMain.ShowCodeAppCenterPublish, event => {
      if (event.sender !== this._options.getLabWebContents()) {
        return;
      }
      this.showCodeAppCenterDialog();
    });

    evm.registerSyncEventHandler(
      EventTypeMain.ShowWaypointPicker,
      (_event, initialValue?: string) => {
        // [UAV-IDE MODIFY] Allow both application-settings and welcome-page env editors to open the shared waypoint picker.
        if (!this._isPickerSender(_event.sender)) {
          return Promise.resolve(null);
        }

        return this._showWaypointPicker(initialValue);
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.ShowRoutePicker,
      (_event, initialValue?: string) => {
        // [UAV-IDE MODIFY] Allow both application-settings and welcome-page env editors to open the shared route picker.
        if (!this._isPickerSender(_event.sender)) {
          return Promise.resolve(null);
        }

        return this._showRoutePicker(initialValue);
      }
    );
  }

  get applicationSettingsDialogWebContents(): Electron.WebContents | undefined {
    return this._applicationSettingsDialog?.window?.window?.webContents;
  }

  get codeAppCenterDialogWebContents(): Electron.WebContents | undefined {
    return this._codeAppCenterDialog?.window?.window?.webContents;
  }

  dispose() {
    // [UAV-IDE MODIFY] Keep owned UAV dialogs tied to the parent session window lifetime.
    if (this._waypointPickerDialog) {
      this._waypointPickerDialog.window.window.close();
      this._waypointPickerDialog = null;
    }
    if (this._routePickerDialog) {
      this._routePickerDialog.window.window.close();
      this._routePickerDialog = null;
    }
    if (this._applicationSettingsDialog) {
      this._applicationSettingsDialog.window.window.close();
      this._applicationSettingsDialog = null;
    }
    if (this._codeAppCenterDialog) {
      this._codeAppCenterDialog.window.window.close();
      this._codeAppCenterDialog = null;
    }
  }

  showApplicationSettingsDialog() {
    const sessionConfig = this._options.getSessionConfig();
    if (!sessionConfig?.resolvedWorkingDirectory) {
      return;
    }

    if (this._applicationSettingsDialog) {
      this._applicationSettingsDialog.window.window.focus();
      return;
    }

    // [UAV-IDE MODIFY] Open the workspace-bound application profile editor for the current session only.
    const dialogWindow = new ApplicationSettingsDialog({
      isDarkTheme: this._options.isDarkTheme,
      parent: this._options.parent,
      workingDirectory: sessionConfig.resolvedWorkingDirectory
    });

    this._applicationSettingsDialog = dialogWindow;
    dialogWindow.window.window.on('closed', () => {
      this._applicationSettingsDialog = null;
    });

    dialogWindow.load();
  }

  showCodeAppCenterDialog() {
    const sessionConfig = this._options.getSessionConfig();
    if (!sessionConfig?.resolvedWorkingDirectory || sessionConfig.isRemote) {
      void dialog.showMessageBox(this._options.parent, {
        type: 'warning',
        title: '无法发布',
        message: '应用中心发布只支持本地应用工作区。'
      });
      return;
    }

    if (this._codeAppCenterDialog) {
      this._codeAppCenterDialog.window.window.focus();
      return;
    }

    // [UAV-IDE MODIFY] Open CodeAppCenter publish from the existing App Center menu entry.
    const publishDialog = new CodeAppCenterDialog({
      isDarkTheme: this._options.isDarkTheme,
      parent: this._options.parent,
      workingDirectory: sessionConfig.resolvedWorkingDirectory,
      settings: this._options.codeAppCenterSettings.read()
    });

    this._codeAppCenterDialog = publishDialog;
    publishDialog.window.window.on('closed', () => {
      this._codeAppCenterDialog = null;
    });

    publishDialog.load();
  }

  private _isPickerSender(sender: Electron.WebContents): boolean {
    return (
      sender === this.applicationSettingsDialogWebContents ||
      sender === this._options.getWelcomeWebContents()
    );
  }

  private async _showWaypointPicker(
    initialValue?: string
  ): Promise<string | null> {
    if (this._waypointPickerDialog) {
      this._waypointPickerDialog.window.window.focus();
      return this._waypointPickerDialog.result;
    }

    // [UAV-IDE MODIFY] Open the dedicated waypoint picker so waypoint values can be map-selected without disturbing the settings flow.
    const dialogWindow = new WaypointPickerDialog({
      isDarkTheme: this._options.isDarkTheme,
      parent: this._options.parent,
      initialValue
    });

    this._waypointPickerDialog = dialogWindow;
    dialogWindow.window.window.on('closed', () => {
      this._waypointPickerDialog = null;
    });

    dialogWindow.load();
    return dialogWindow.result;
  }

  private async _showRoutePicker(
    initialValue?: string
  ): Promise<string | null> {
    if (this._routePickerDialog) {
      this._routePickerDialog.window.window.focus();
      return this._routePickerDialog.result;
    }

    // [UAV-IDE MODIFY] Open the dedicated route picker so ordered waypoint routes can be edited with the same map flow from both env editors.
    const dialogWindow = new RoutePickerDialog({
      isDarkTheme: this._options.isDarkTheme,
      parent: this._options.parent,
      initialValue
    });

    this._routePickerDialog = dialogWindow;
    dialogWindow.window.window.on('closed', () => {
      this._routePickerDialog = null;
    });

    dialogWindow.load();
    return dialogWindow.result;
  }

  private _applicationSettingsDialog: ApplicationSettingsDialog | null = null;
  private _codeAppCenterDialog: CodeAppCenterDialog | null = null;
  private _waypointPickerDialog: WaypointPickerDialog | null = null;
  private _routePickerDialog: RoutePickerDialog | null = null;
}
