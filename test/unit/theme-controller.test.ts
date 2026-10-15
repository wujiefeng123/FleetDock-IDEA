import { describe, expect, it, vi } from 'vitest';
import type { IpcMainEvent, WebContents } from 'electron';
import type { EventManager } from '../../src/main/eventmanager';
import { FleetDockThemeController } from '../../src/main/fleetdock/theme-controller';
import { EventTypeMain } from '../../src/main/fleetdock/eventtypes';

function setup() {
  const events = {
    registerEventHandler: vi.fn(),
    unregisterEventHandler: vi.fn()
  };
  const lab = {} as WebContents;
  const apply = vi.fn();
  const controller = new FleetDockThemeController(
    (events as unknown) as EventManager,
    () => lab,
    apply
  );
  controller.register();
  const sync = events.registerEventHandler.mock.calls[0][1];
  return { events, lab, apply, controller, sync };
}

describe('FleetDock titlebar theme synchronization', () => {
  it('accepts both theme directions from the owning Lab renderer', () => {
    const { sync, lab, apply } = setup();
    sync({ sender: lab } as IpcMainEvent, true);
    sync({ sender: lab } as IpcMainEvent, false);
    expect(apply.mock.calls).toEqual([[true], [false]]);
  });

  it('rejects other windows and malformed theme payloads', () => {
    const { sync, lab, apply } = setup();
    sync({ sender: {} } as IpcMainEvent, true);
    for (const value of ['dark', 'false', 1, null, {}]) {
      sync({ sender: lab } as IpcMainEvent, value);
    }
    expect(apply).not.toHaveBeenCalled();
  });

  it('unregisters the exact session listener on disposal', () => {
    const { controller, events, sync } = setup();
    controller.dispose();
    expect(events.unregisterEventHandler).toHaveBeenCalledWith(
      EventTypeMain.SyncTitleBarTheme,
      sync
    );
  });
});
