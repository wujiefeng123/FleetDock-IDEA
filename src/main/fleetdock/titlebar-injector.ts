// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import type { WebContents } from 'electron';
import * as fs from 'fs';
import * as path from 'path';
import { readFleetDockCss } from './styles';
import log from 'electron-log';

// [UAV-IDE MODIFY] Keep FleetDock desktop chrome outside the upstream titlebar document.
export class FleetDockTitleBarInjector {
  setDarkTheme(dark: boolean): void {
    this._dark = dark;
    const webContents = this._webContents;
    if (webContents && !webContents.isDestroyed()) {
      void this._applyTheme(webContents).catch(() => {
        if (!webContents.isDestroyed()) {
          log.warn(
            'FleetDock titlebar theme could not be updated. Reopen the window to retry.'
          );
        }
      });
    }
  }

  attach(webContents: WebContents): void {
    if (this._webContents === webContents) {
      return;
    }
    this.dispose();
    this._webContents = webContents;
    this._inject = async () => {
      if (webContents.isDestroyed()) {
        return;
      }
      if (!this._cssKey) {
        this._cssKey = await webContents.insertCSS(
          this._readAsset('fleetdock-titlebar-extension.css')
        );
      }
      await webContents.executeJavaScript(
        this._readAsset('fleetdock-titlebar-extension.js'),
        true
      );
      await this._applyTheme(webContents);
    };
    webContents.on('dom-ready', this._inject);
    if (!webContents.isLoadingMainFrame()) {
      void this._inject();
    }
    webContents.once('destroyed', () => {
      this._webContents = undefined;
      this._inject = undefined;
      this._cssKey = undefined;
    });
  }

  dispose(): void {
    if (!this._webContents || !this._inject) {
      return;
    }
    this._webContents.removeListener('dom-ready', this._inject);
    if (!this._webContents.isDestroyed() && this._cssKey) {
      void this._webContents.removeInsertedCSS(this._cssKey);
    }
    this._webContents = undefined;
    this._inject = undefined;
    this._cssKey = undefined;
  }

  private _readAsset(fileName: string): string {
    if (fileName.endsWith('.css')) {
      return readFleetDockCss(fileName);
    }
    return fs.readFileSync(
      path.resolve(__dirname, '..', '..', '..', 'app-assets', fileName),
      'utf8'
    );
  }

  private async _applyTheme(webContents: WebContents): Promise<void> {
    if (this._dark === undefined || webContents.isDestroyed()) {
      return;
    }
    await webContents.executeJavaScript(
      `document.body.classList.toggle('app-ui-dark', ${this._dark});`,
      true
    );
  }

  private _dark?: boolean;
  private _webContents?: WebContents;
  private _inject?: () => Promise<void>;
  private _cssKey?: string;
}
