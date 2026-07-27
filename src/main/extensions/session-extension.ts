// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import type { BrowserWindow, WebContents } from 'electron';
import type { WorkspaceSettings } from '../config/settings';
import type { SessionConfig } from '../config/sessionconfig';
import type { EventManager } from '../eventmanager';
import type { IRegistry } from '../registry';
import type { JupyterServer } from '../server';

/**
 * Stable capabilities exposed to optional desktop session extensions.
 *
 * Keep this surface deliberately small so product extensions do not depend on
 * SessionWindow internals.
 */
export interface ISessionExtensionContext {
  window: BrowserWindow;
  eventManager: EventManager;
  registry: IRegistry;
  isDarkTheme: boolean;
  getSessionConfig: () => SessionConfig | undefined;
  getWorkspaceSettings: () => WorkspaceSettings;
  setWorkspaceSettings: (settings: WorkspaceSettings) => void;
  getWelcomeWebContents: () => WebContents | undefined;
  getLabWebContents: () => WebContents | undefined;
  getServerPythonPath: () => string | undefined;
  restartSessionInPythonEnvironment: (pythonPath: string) => void;
  notifySessionConfigChanged: () => void;
  openSession: (sessionConfig: SessionConfig) => Promise<void>;
}

export interface ISessionExtension {
  readonly id: string;
  activate?(): void;
  beforeCreateServer?(options: JupyterServer.IOptions): void | Promise<void>;
  onWelcomeViewCreated?(webContents: WebContents): void | Promise<void>;
  onLabViewCreated?(webContents: WebContents): void | Promise<void>;
  dispose?(): void | Promise<void>;
}

export type ISessionExtensionFactory = (
  context: ISessionExtensionContext
) => ISessionExtension;

/**
 * Owns optional per-session extensions without leaking their implementation
 * into the upstream window coordinator.
 */
export class SessionExtensionHost {
  constructor(
    factories: readonly ISessionExtensionFactory[],
    context: ISessionExtensionContext
  ) {
    this._extensions = factories.map(factory => factory(context));
  }

  activate(): void {
    this._extensions.forEach(extension => extension.activate?.());
  }

  async beforeCreateServer(options: JupyterServer.IOptions): Promise<void> {
    for (const extension of this._extensions) {
      await extension.beforeCreateServer?.(options);
    }
  }

  onWelcomeViewCreated(webContents: WebContents): void {
    this._notify(
      extension => extension.onWelcomeViewCreated?.(webContents),
      'welcome view'
    );
  }

  onLabViewCreated(webContents: WebContents): void {
    this._notify(
      extension => extension.onLabViewCreated?.(webContents),
      'lab view'
    );
  }

  async dispose(): Promise<void> {
    for (const extension of [...this._extensions].reverse()) {
      try {
        await extension.dispose?.();
      } catch (error) {
        console.error(
          `Session extension '${extension.id}' failed during disposal:`,
          error
        );
      }
    }
  }

  private _notify(
    callback: (extension: ISessionExtension) => void | Promise<void>,
    lifecycle: string
  ): void {
    this._extensions.forEach(extension => {
      Promise.resolve(callback(extension)).catch(error => {
        console.error(
          `Session extension '${extension.id}' failed during ${lifecycle}:`,
          error
        );
      });
    });
  }

  private _extensions: ISessionExtension[];
}
