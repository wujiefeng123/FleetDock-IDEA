// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import { app, WebContents } from 'electron';
import log from 'electron-log';
import { readFleetDockCss } from './styles';

interface IStyledDialog {
  webContents: WebContents;
  cssKey?: string;
  generation: number;
  pendingGeneration?: number;
  inject: () => void;
  navigate: (
    event: Electron.Event,
    url: string,
    isInPlace: boolean,
    isMainFrame: boolean
  ) => void;
  destroyed: () => void;
}

// [UAV-IDE MODIFY] Apply appearance only to local desktop documents, preserving all dialog IPC and controls.
export class FleetDockDialogInjector {
  attach(webContents: WebContents): void {
    if (webContents.isDestroyed() || this._attached.has(webContents)) {
      return;
    }
    const attached: IStyledDialog = {
      webContents,
      generation: 0,
      inject: () => void this._inject(attached),
      navigate: (_event, _url, isInPlace, isMainFrame) => {
        if (isMainFrame && !isInPlace) {
          attached.generation++;
          attached.cssKey = undefined;
        }
      },
      destroyed: () => this._detach(attached)
    };
    this._attached.set(webContents, attached);
    webContents.on('dom-ready', attached.inject);
    webContents.on('did-start-navigation', attached.navigate);
    webContents.once('destroyed', attached.destroyed);
    if (webContents.getURL() && !webContents.isLoadingMainFrame()) {
      attached.inject();
    }
  }

  dispose(): void {
    for (const attached of this._attached.values()) {
      this._detach(attached);
      if (!attached.webContents.isDestroyed() && attached.cssKey) {
        void attached.webContents
          .removeInsertedCSS(attached.cssKey)
          .catch((): undefined => undefined);
      }
    }
  }

  private async _inject(attached: IStyledDialog): Promise<void> {
    const { webContents, generation } = attached;
    if (
      webContents.isDestroyed() ||
      attached.cssKey ||
      attached.pendingGeneration === generation ||
      !webContents.getURL().startsWith('data:text/html')
    ) {
      return;
    }
    attached.pendingGeneration = generation;
    try {
      const key = await webContents.insertCSS(
        readFleetDockCss('fleetdock-dialog-extension.css')
      );
      if (webContents.isDestroyed()) {
        return;
      }
      if (
        this._attached.get(webContents) !== attached ||
        attached.generation !== generation
      ) {
        await webContents
          .removeInsertedCSS(key)
          .catch((): undefined => undefined);
        return;
      }
      attached.cssKey = key;
    } catch {
      if (!webContents.isDestroyed()) {
        log.warn(
          'FleetDock dialog appearance could not be loaded. Rebuild desktop assets and reopen the dialog.'
        );
      }
    } finally {
      if (attached.pendingGeneration === generation) {
        attached.pendingGeneration = undefined;
      }
    }
  }

  private _detach(attached: IStyledDialog): void {
    attached.webContents.removeListener('dom-ready', attached.inject);
    attached.webContents.removeListener(
      'did-start-navigation',
      attached.navigate
    );
    attached.webContents.removeListener('destroyed', attached.destroyed);
    this._attached.delete(attached.webContents);
  }

  private _attached = new Map<WebContents, IStyledDialog>();
}

let installed = false;

export function installFleetDockDialogStyles(): void {
  if (installed) {
    return;
  }
  installed = true;
  const injector = new FleetDockDialogInjector();
  const attach = (_event: Electron.Event, webContents: WebContents) =>
    injector.attach(webContents);
  app.on('web-contents-created', attach);
  app.once('will-quit', () => {
    app.removeListener('web-contents-created', attach);
    injector.dispose();
  });
}
