import { BrowserWindow, dialog } from 'electron';
import { EventManager } from '../eventmanager';
import { EventTypeMain } from '../fleetdock/eventtypes';
import { QgcManager } from './qgc-manager';

// [UAV-IDE MODIFY] Only the owning Lab renderer can discover or launch the bundled ground station.
export class QgcController {
  constructor(
    private _eventManager: EventManager,
    private _window: BrowserWindow,
    private _getLabWebContents: () => Electron.WebContents | undefined
  ) {}

  register(): void {
    const authorized = (event: { sender: Electron.WebContents }) => {
      if (event.sender !== this._getLabWebContents()) {
        throw new Error('地面站请求来自未经授权的窗口。');
      }
    };
    this._eventManager.registerSyncEventHandler(
      EventTypeMain.GroundStationStatus,
      event => {
        authorized(event);
        return this._manager.status();
      }
    );
    this._eventManager.registerSyncEventHandler(
      EventTypeMain.GroundStationLaunch,
      async event => {
        authorized(event);
        return this._manager.launch();
      }
    );
    this._eventManager.registerSyncEventHandler(
      EventTypeMain.GroundStationClose,
      async event => {
        authorized(event);
        return this._manager.close();
      }
    );
    this._eventManager.registerSyncEventHandler(
      EventTypeMain.GroundStationSelectExecutable,
      async event => {
        authorized(event);
        const result = await dialog.showOpenDialog(this._window, {
          title: '选择 QGroundControl.exe',
          properties: ['openFile'],
          filters: [{ name: 'QGroundControl', extensions: ['exe'] }]
        });
        return result.canceled || !result.filePaths[0]
          ? this._manager.status()
          : this._manager.setExecutablePath(result.filePaths[0]);
      }
    );
    this._eventManager.registerSyncEventHandler(
      EventTypeMain.GroundStationOpenInstallDirectory,
      async event => {
        authorized(event);
        await this._manager.openInstallDirectory();
        return true;
      }
    );
  }

  private _manager = new QgcManager();
}
