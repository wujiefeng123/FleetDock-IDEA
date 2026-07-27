// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

/* eslint-disable @typescript-eslint/naming-convention -- [UAV-IDE MODIFY] Preserve the published FleetDock IPC data-contract names during upstream migration. */

// [UAV-IDE MODIFY] Shared CodeAppCenter integration types for publishing desktop workspaces.

export interface CodeAppCenterSettingsData {
  gatewayBaseUrl: string;
  accessToken: string;
  refreshToken: string;
  tokenType: string;
  expiresIn: number;
  tokenExpireAt: number;
  organizationId: string;
  lastPublishMode: 'create-app' | 'new-version';
  lastAppId: string;
}

export interface CodeAppCenterLoginRequest {
  gatewayBaseUrl: string;
  loginType: string;
  loginValue: string;
  passWord: string;
}

export interface CodeAppCenterToken {
  accessToken?: string;
  tokenType?: string;
  refreshToken?: string;
  expiresIn?: number;
  scope?: string;
}

export interface CodeAppCenterUserProfile {
  userId?: string;
  organizationId?: string;
  organizationIds?: string[];
}

export interface CodeAppModel {
  id?: string;
  appCode?: string;
  appName?: string;
  name?: string;
  description?: string;
  creatorName?: string;
  createTime?: number;
  updateTime?: number;
  versionNo?: string;
  currentVersion?: string;
  latestVersionNo?: string;
  latestVersionCreateTime?: number;
  latestVersion?: {
    versionNo?: string;
    createTime?: number;
  };
}

export interface CodeAppPublishRequest {
  mode: 'create-app' | 'new-version';
  gatewayBaseUrl: string;
  appId?: string;
  appCode: string;
  appName: string;
  description: string;
  version: string;
  remark: string;
  requirementsText: string;
  workingDirectory: string;
}

export interface CodeAppPublishResult {
  ok: boolean;
  mode: 'create-app' | 'new-version';
  message: string;
  stagingDir?: string;
  zipPath?: string;
  response?: any;
}

export interface CodeAppPackageResult {
  zipPath: string;
  stagingDir: string;
  fileName: string;
}

export interface CodeAppDependencyScanResult {
  requirementsText: string;
  packages: string[];
}

export interface ResultContent<T> {
  code?: number;
  state?: number;
  message?: string;
  msg?: string;
  content?: T;
}
