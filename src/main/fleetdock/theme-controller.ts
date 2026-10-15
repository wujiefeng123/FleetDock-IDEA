// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import type { IpcMainEvent, WebContents } from 'electron';
import type { EventManager } from '../eventmanager';
import { EventTypeMain } from './eventtypes';

// [UAV-IDE MODIFY] A session may update only its own chrome from its active Lab renderer.
export class FleetDockThemeController {
  constructor(
    private _events: EventManager,
    private _getLab: () => WebContents | undefined,
    private _applyTheme: (dark: boolean) => void
  ) {}

  register(): void {
    this._events.registerEventHandler(
      EventTypeMain.SyncTitleBarTheme,
      this._sync
    );
  }

  dispose(): void {
    this._events.unregisterEventHandler(
      EventTypeMain.SyncTitleBarTheme,
      this._sync
    );
  }

  private _sync = (event: IpcMainEvent, dark: unknown): void => {
    if (event.sender === this._getLab() && typeof dark === 'boolean') {
      this._applyTheme(dark);
    }
  };
}
