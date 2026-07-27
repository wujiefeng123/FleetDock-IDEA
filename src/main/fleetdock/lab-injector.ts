// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import type { WebContents } from 'electron';
import * as fs from 'fs';
import * as path from 'path';

interface IAttachedLabRenderer {
  webContents: WebContents;
  inject: () => Promise<void>;
  cssKeys: string[];
}

/**
 * Loads FleetDock renderer assets without modifying the upstream LabView.
 */
export class FleetDockLabInjector {
  attach(webContents: WebContents): void {
    if (this._attached.some(item => item.webContents === webContents)) {
      return;
    }

    const attached: IAttachedLabRenderer = {
      webContents,
      cssKeys: [],
      inject: async () => {
        if (webContents.isDestroyed()) {
          return;
        }
        const cssKeys = await Promise.all(
          [
            'skylab-jupyterlab-theme.css',
            'skylab-command-center.css',
            'skylab-real-device-debugger.css'
          ].map(fileName => webContents.insertCSS(this._readAsset(fileName)))
        );
        attached.cssKeys.push(...cssKeys);

        await webContents.executeJavaScript(
          this._readAsset('skylab-command-center.js'),
          true
        );
        await webContents.executeJavaScript(
          this._readAsset('skylab-real-device-debugger.js'),
          true
        );
      }
    };

    this._attached.push(attached);
    webContents.on('dom-ready', attached.inject);
    // [UAV-IDE MODIFY] Cover fast/cached loads where the extension hook is
    // attached after the renderer's DOM-ready event.
    if (!webContents.isLoadingMainFrame()) {
      void attached.inject();
    }
    webContents.once('destroyed', () => this._detach(attached));
  }

  async dispose(): Promise<void> {
    const attachedRenderers = [...this._attached];
    this._attached = [];
    await Promise.all(
      attachedRenderers.map(async attached => {
        attached.webContents.removeListener('dom-ready', attached.inject);
        if (attached.webContents.isDestroyed()) {
          return;
        }
        await Promise.all(
          attached.cssKeys.map(key =>
            attached.webContents.removeInsertedCSS(key)
          )
        );
      })
    );
  }

  private _detach(attached: IAttachedLabRenderer): void {
    attached.webContents.removeListener('dom-ready', attached.inject);
    const index = this._attached.indexOf(attached);
    if (index !== -1) {
      this._attached.splice(index, 1);
    }
  }

  private _readAsset(fileName: string): string {
    // Compiled location: build/out/main/fleetdock. App assets: build/app-assets.
    const assetPath = path.resolve(
      __dirname,
      '..',
      '..',
      '..',
      'app-assets',
      fileName
    );
    return fs.readFileSync(assetPath, 'utf8');
  }

  private _attached: IAttachedLabRenderer[] = [];
}
