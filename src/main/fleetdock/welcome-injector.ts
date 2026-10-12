// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import type { WebContents } from 'electron';
import * as fs from 'fs';
import * as path from 'path';

export class FleetDockWelcomeInjector {
  attach(webContents: WebContents): Promise<void> {
    if (this._webContents === webContents) {
      return this._readyPromise ?? Promise.resolve();
    }
    this.dispose();
    this._webContents = webContents;
    this._readyPromise = new Promise<void>((resolve, reject) => {
      this._resolveReady = resolve;
      this._rejectReady = reject;
    });
    this._inject = async () => {
      if (webContents.isDestroyed()) {
        this._settleReady();
        return;
      }
      try {
        if (!this._cssKey) {
          this._cssKey = await webContents.insertCSS(
            this._readAsset('fleetdock-welcome-extension.css')
          );
        }
        await webContents.executeJavaScript(
          this._readAsset('fleetdock-welcome-extension.js'),
          true
        );
        this._settleReady();
      } catch (error) {
        this._settleReady(error);
      }
    };
    webContents.on('dom-ready', this._inject);
    // [UAV-IDE MODIFY] WelcomeView may finish loading before SessionWindow
    // publishes the extension hook, so inject immediately when DOM-ready was
    // already missed.
    if (webContents.getURL() && !webContents.isLoadingMainFrame()) {
      void this._inject();
    }
    webContents.once('destroyed', () => {
      this._settleReady();
      this._webContents = undefined;
      this._inject = undefined;
      this._cssKey = undefined;
      this._readyPromise = undefined;
    });
    return this._readyPromise;
  }

  dispose(): void {
    if (!this._webContents || !this._inject) {
      this._settleReady();
      return;
    }
    this._webContents.removeListener('dom-ready', this._inject);
    if (!this._webContents.isDestroyed() && this._cssKey) {
      void this._webContents.removeInsertedCSS(this._cssKey);
    }
    this._webContents = undefined;
    this._inject = undefined;
    this._cssKey = undefined;
    this._readyPromise = undefined;
    this._settleReady();
  }

  private _settleReady(error?: unknown): void {
    if (error) {
      this._rejectReady?.(error);
    } else {
      this._resolveReady?.();
    }
    this._resolveReady = undefined;
    this._rejectReady = undefined;
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
  private _readyPromise?: Promise<void>;
  private _resolveReady?: () => void;
  private _rejectReady?: (error: unknown) => void;
}
