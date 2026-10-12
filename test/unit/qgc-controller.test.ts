import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ipcMain } from '../setup/electron-stub';
import { EventManager } from '../../src/main/eventmanager';
import { EventTypeMain } from '../../src/main/fleetdock/eventtypes';
import { QgcController } from '../../src/main/groundstation/qgc-controller';

describe('QGroundControl IPC authorization', () => {
  beforeEach(() => vi.clearAllMocks());

  it.each([
    EventTypeMain.GroundStationLaunch,
    EventTypeMain.GroundStationClose
  ])('rejects unauthorized %s requests', async channel => {
    const labSender = {} as Electron.WebContents;
    const eventManager = new EventManager();
    const controller = new QgcController(
      eventManager,
      {} as Electron.BrowserWindow,
      () => labSender
    );
    controller.register();
    const handler = vi
      .mocked(ipcMain.handle)
      .mock.calls.find(
        ([registeredChannel]) => registeredChannel === channel
      )?.[1];
    expect(handler).toBeDefined();
    await expect(handler!({ sender: {} })).rejects.toThrow(
      '地面站请求来自未经授权的窗口。'
    );
  });
});
