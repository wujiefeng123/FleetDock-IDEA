// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import { EventManager } from '../eventmanager';
import { EventTypeMain } from '../eventtypes';
import { CodeAppCenterAuthError, CodeAppCenterClient } from './client';
import { CodeAppCenterSettings } from './settings';
import {
  createCodeAppPackage,
  readRequirementsText,
  scanPythonDependencies
} from './package';
import { CodeAppPublishRequest } from './types';

export interface ICodeAppCenterControllerOptions {
  eventManager: EventManager;
  settings: CodeAppCenterSettings;
  getDialogWebContents: () => Electron.WebContents | undefined;
  getWorkingDirectory: () => string;
}

// [UAV-IDE MODIFY] Keep CodeAppCenter authentication, scanning, and publishing IPC outside SessionWindow.
export class CodeAppCenterController {
  constructor(private _options: ICodeAppCenterControllerOptions) {}

  register() {
    const evm = this._options.eventManager;

    evm.registerSyncEventHandler(EventTypeMain.CodeAppCenterGetState, event => {
      if (!this._isDialogSender(event.sender)) {
        return null;
      }
      return this._options.settings.read();
    });

    evm.registerSyncEventHandler(
      EventTypeMain.CodeAppCenterLogin,
      async (event, request) => {
        if (!this._isDialogSender(event.sender)) {
          return null;
        }
        const client = new CodeAppCenterClient(request.gatewayBaseUrl);
        const token = await client.login(request);
        client.setAccessToken(token.accessToken || '');
        const profile = token.accessToken ? await client.currentUser() : null;
        const organizationId =
          profile?.organizationId || profile?.organizationIds?.[0] || '';
        return this._options.settings.write({
          gatewayBaseUrl: request.gatewayBaseUrl,
          accessToken: token.accessToken || '',
          refreshToken: token.refreshToken || '',
          tokenType: token.tokenType || 'Bearer',
          expiresIn: Number(token.expiresIn || 0),
          tokenExpireAt: token.expiresIn
            ? Date.now() + Number(token.expiresIn) * 1000
            : 0,
          organizationId
        });
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.CodeAppCenterListApps,
      async event => {
        if (!this._isDialogSender(event.sender)) {
          return [];
        }
        try {
          return await (await this._getClient()).listApps();
        } catch (error) {
          if (error instanceof CodeAppCenterAuthError) {
            return (await this._getClient(true)).listApps();
          }
          throw error;
        }
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.CodeAppCenterReadRequirements,
      event => {
        if (!this._isDialogSender(event.sender)) {
          return '';
        }
        return readRequirementsText(this._options.getWorkingDirectory());
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.CodeAppCenterScanDependencies,
      async event => {
        if (!this._isDialogSender(event.sender)) {
          return { requirementsText: '', packages: [] };
        }
        return scanPythonDependencies(this._options.getWorkingDirectory());
      }
    );

    evm.registerSyncEventHandler(
      EventTypeMain.CodeAppCenterPublish,
      async (event, request: CodeAppPublishRequest) => {
        if (!this._isDialogSender(event.sender)) {
          return null;
        }
        this._options.settings.write({
          gatewayBaseUrl: request.gatewayBaseUrl,
          lastPublishMode: request.mode,
          lastAppId: request.appId || ''
        });
        const pkg = await createCodeAppPackage(request);
        const publishWithClient = async (client: CodeAppCenterClient) => {
          const file = await client.uploadSourcePackage(
            pkg.zipPath,
            pkg.fileName
          );
          return request.mode === 'create-app'
            ? client.createApp({
                appCode: request.appCode,
                appName: request.appName,
                description: request.description,
                sourceFileName: file.fileName || pkg.fileName,
                sourceStoragePath: file.storagePath,
                sourceRemark: request.remark
              })
            : client.createVersion({
                appId: request.appId || '',
                sourceFileName: file.fileName || pkg.fileName,
                sourceStoragePath: file.storagePath,
                remark: request.remark
              });
        };
        let response: any;
        try {
          response = await publishWithClient(await this._getClient());
        } catch (error) {
          if (!(error instanceof CodeAppCenterAuthError)) {
            throw error;
          }
          response = await publishWithClient(await this._getClient(true));
        }
        return {
          ok: true,
          mode: request.mode,
          message: '发布完成',
          stagingDir: pkg.stagingDir,
          zipPath: pkg.zipPath,
          response
        };
      }
    );
  }

  private _isDialogSender(sender: Electron.WebContents): boolean {
    return sender === this._options.getDialogWebContents();
  }

  // [UAV-IDE MODIFY] Keep CodeAppCenter tokens fresh before gateway requests and persist refreshed credentials.
  private async _getClient(forceRefresh = false): Promise<CodeAppCenterClient> {
    let settings = this._options.settings.read();
    const shouldRefresh =
      !!settings.refreshToken &&
      (forceRefresh ||
        (!!settings.tokenExpireAt &&
          Date.now() + 60_000 >= settings.tokenExpireAt));

    if (shouldRefresh) {
      try {
        const refreshClient = new CodeAppCenterClient(settings.gatewayBaseUrl);
        const token = await refreshClient.refreshToken(settings.refreshToken);
        refreshClient.setAccessToken(token.accessToken || '');
        const profile = token.accessToken
          ? await refreshClient.currentUser()
          : null;
        const organizationId =
          profile?.organizationId ||
          profile?.organizationIds?.[0] ||
          settings.organizationId;
        settings = this._options.settings.write({
          accessToken: token.accessToken || settings.accessToken,
          refreshToken: token.refreshToken || settings.refreshToken,
          tokenType: token.tokenType || settings.tokenType || 'Bearer',
          expiresIn: Number(token.expiresIn || settings.expiresIn || 0),
          tokenExpireAt: token.expiresIn
            ? Date.now() + Number(token.expiresIn) * 1000
            : settings.tokenExpireAt,
          organizationId
        });
      } catch (error) {
        // [UAV-IDE MODIFY] Clear stale credentials so the publish dialog can switch back to the login panel instead of sending anonymous requests.
        this._options.settings.write({
          accessToken: '',
          refreshToken: '',
          expiresIn: 0,
          tokenExpireAt: 0,
          organizationId: ''
        });
        throw new CodeAppCenterAuthError('登录已失效，请重新登录应用中心');
      }
    }

    if (!settings.accessToken) {
      throw new CodeAppCenterAuthError('登录已失效，请重新登录应用中心');
    }

    return new CodeAppCenterClient(
      settings.gatewayBaseUrl,
      settings.accessToken,
      settings.organizationId
    );
  }
}
