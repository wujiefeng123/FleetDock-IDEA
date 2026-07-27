// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import type { WebContents } from 'electron';
import * as fs from 'fs';
import * as path from 'path';

export class FleetDockWelcomeInjector {
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
      this._cssKey = await webContents.insertCSS(
        this._readAsset('fleetdock-welcome-extension.css')
      );
      await webContents.executeJavaScript(
        this._readAsset('fleetdock-welcome-extension.js'),
        true
      );
    };
    webContents.on('dom-ready', this._inject);
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
    return fs.readFileSync(
      path.resolve(__dirname, '..', '..', '..', 'app-assets', fileName),
      'utf8'
    );
  }

  private _webContents?: WebContents;
  private _inject?: () => Promise<void>;
  private _cssKey?: string;
}
