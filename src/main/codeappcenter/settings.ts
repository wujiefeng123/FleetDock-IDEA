// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Persist CodeAppCenter connection and publish preferences in Electron userData.

import { app } from 'electron';
import * as fs from 'fs';
import * as path from 'path';
import { CodeAppCenterSettingsData } from './types';

const DEFAULT_SETTINGS: CodeAppCenterSettingsData = {
  gatewayBaseUrl: '',
  accessToken: '',
  refreshToken: '',
  tokenType: 'Bearer',
  expiresIn: 0,
  tokenExpireAt: 0,
  organizationId: '',
  lastPublishMode: 'create-app',
  lastAppId: ''
};

export class CodeAppCenterSettings {
  private _path = path.join(app.getPath('userData'), 'codeapp-center.json');

  read(): CodeAppCenterSettingsData {
    try {
      if (!fs.existsSync(this._path)) {
        return { ...DEFAULT_SETTINGS };
      }
      return {
        ...DEFAULT_SETTINGS,
        ...JSON.parse(fs.readFileSync(this._path, 'utf8'))
      };
    } catch (error) {
      console.error('Failed to read CodeAppCenter settings:', error);
      return { ...DEFAULT_SETTINGS };
    }
  }

  write(next: Partial<CodeAppCenterSettingsData>): CodeAppCenterSettingsData {
    const merged = {
      ...this.read(),
      ...next
    };
    const dirPath = path.dirname(this._path);
    if (!fs.existsSync(dirPath)) {
      fs.mkdirSync(dirPath, { recursive: true });
    }
    fs.writeFileSync(this._path, JSON.stringify(merged, null, 2));
    return merged;
  }
}
