// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] CodeAppCenter HTTP client used by the desktop publish flow.

import { net } from 'electron';
import * as fs from 'fs';
import {
  CodeAppCenterLoginRequest,
  CodeAppCenterToken,
  CodeAppCenterUserProfile,
  CodeAppModel,
  ResultContent
} from './types';

function normalizeBaseUrl(value: string): string {
  const trimmed = String(value || '')
    .trim()
    .replace(/\/+$/, '');
  if (!trimmed) {
    throw new Error('请先填写应用中心网关地址');
  }
  return trimmed;
}

function resultMessage(data: any): string {
  return data?.message || data?.msg || '应用中心请求失败';
}

function isAuthFailure(status: number, data: any): boolean {
  const message = resultMessage(data);
  const exceptionType = data?.exception?.type || '';
  const exceptionClass = data?.exception?.cls || '';
  return (
    status === 401 ||
    Number(data?.code) === 401 ||
    Number(data?.code) === 100401 ||
    /Authentication|Unauthorized|unauthorized|invalid token|expired token|token|未登录|登录.*过期|过期/i.test(
      `${message} ${exceptionType} ${exceptionClass}`
    )
  );
}

export class CodeAppCenterAuthError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'CodeAppCenterAuthError';
  }
}

export class CodeAppCenterClient {
  constructor(
    private _gatewayBaseUrl: string,
    private _accessToken = '',
    private _organizationId = ''
  ) {
    this._gatewayBaseUrl = normalizeBaseUrl(_gatewayBaseUrl);
  }

  setAccessToken(token: string): void {
    this._accessToken = token || '';
  }

  setOrganizationId(organizationId: string): void {
    this._organizationId = organizationId || '';
  }

  async login(request: CodeAppCenterLoginRequest): Promise<CodeAppCenterToken> {
    return this._json<CodeAppCenterToken>('/userserver/user/auth/login', {
      method: 'POST',
      body: {
        loginType: request.loginType || 'Phone',
        loginValue: request.loginValue,
        passWord: request.passWord,
        deviceType: 'Pc',
        deviceUUid: 'jupyterlab-desktop',
        deviceUserAgent: 'JupyterLab Desktop',
        deviceIp: ''
      },
      auth: false
    });
  }

  async refreshToken(refreshToken: string): Promise<CodeAppCenterToken> {
    return this._json<CodeAppCenterToken>(
      '/userserver/user/auth/refresh-token',
      {
        method: 'POST',
        body: { refreshToken },
        auth: false
      }
    );
  }

  async currentUser(): Promise<CodeAppCenterUserProfile> {
    return this._json<CodeAppCenterUserProfile>(
      '/userserver/user/auth/current',
      {
        method: 'GET',
        auth: true
      }
    );
  }

  async listApps(): Promise<CodeAppModel[]> {
    const apps = await this._json<CodeAppModel[]>('/codeappserver/app/list', {
      method: 'GET',
      auth: true
    });
    return Promise.all(
      (apps || []).map(async app => {
        if (!app.id) {
          return app;
        }
        try {
          const versions = await this._json<any[]>(
            `/codeappserver/app/version/list?appId=${encodeURIComponent(
              app.id || ''
            )}`,
            {
              method: 'GET',
              auth: true
            }
          );
          const latestVersion = (versions || [])
            .slice()
            .sort(
              (a, b) => Number(b?.createTime || 0) - Number(a?.createTime || 0)
            )[0];
          return {
            ...app,
            latestVersion,
            latestVersionNo: latestVersion?.versionNo || app.latestVersionNo,
            latestVersionCreateTime:
              Number(latestVersion?.createTime || 0) ||
              app.latestVersionCreateTime
          };
        } catch (_error) {
          return app;
        }
      })
    );
  }

  async uploadSourcePackage(filePath: string, fileName: string): Promise<any> {
    const token = await this._json<any>(
      '/codeappserver/app/file/upload/token',
      {
        method: 'POST',
        body: {
          bizType: 'source-package',
          fileName,
          contentType: 'application/zip'
        },
        auth: true
      }
    );

    if (!token?.uploadUrl || !Array.isArray(token.storagePath)) {
      throw new Error('应用中心没有返回有效上传令牌');
    }

    // [UAV-IDE MODIFY] RustFS/S3 presigned PUT rejects chunked uploads from
    // streamed uploads, so send a sized Buffer with Content-Length.
    const sourcePackage = fs.readFileSync(filePath);
    const response = await net.fetch(token.uploadUrl, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/zip',
        'Content-Length': String(sourcePackage.length)
      },
      body: sourcePackage
    });
    if (!response.ok) {
      throw new Error(`源码包上传失败: HTTP ${response.status}`);
    }

    return this._json<any>('/codeappserver/app/file/upload/finish', {
      method: 'POST',
      body: {
        fileName,
        contentType: 'application/zip',
        storagePath: token.storagePath
      },
      auth: true
    });
  }

  async createApp(data: {
    appCode: string;
    appName: string;
    description: string;
    sourceFileName: string;
    sourceStoragePath: string[];
    sourceRemark: string;
  }): Promise<any> {
    return this._json<any>('/codeappserver/app/create', {
      method: 'POST',
      body: data,
      auth: true
    });
  }

  async createVersion(data: {
    appId: string;
    sourceFileName: string;
    sourceStoragePath: string[];
    remark: string;
  }): Promise<any> {
    return this._json<any>('/codeappserver/app/version/create', {
      method: 'POST',
      body: data,
      auth: true
    });
  }

  private async _json<T>(
    pathname: string,
    options: { method: string; body?: any; auth?: boolean }
  ): Promise<T> {
    const headers: { [key: string]: string } = {
      Accept: 'application/json'
    };
    if (options.body !== undefined) {
      headers['Content-Type'] = 'application/json';
    }
    // [UAV-IDE MODIFY] Treat missing local tokens as an auth failure before
    // sending gateway requests without credentials.
    if (options.auth && !this._accessToken) {
      throw new CodeAppCenterAuthError('登录已失效，请重新登录应用中心');
    }
    if (options.auth && this._accessToken) {
      headers.Authorization = `Bearer ${this._accessToken}`;
      headers.accessToken = this._accessToken;
      if (this._organizationId) {
        headers.oid = this._organizationId;
      }
    }

    // [UAV-IDE MODIFY] Route through the platform gateway service-name
    // prefixes, e.g. /userserver and /codeappserver, instead of direct ports.
    const response = await net.fetch(`${this._gatewayBaseUrl}${pathname}`, {
      method: options.method,
      headers,
      body:
        options.body === undefined ? undefined : JSON.stringify(options.body)
    });
    const text = await response.text();
    let data: ResultContent<T> | any = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch (_error) {
      data = { message: text };
    }

    if (!response.ok) {
      if (isAuthFailure(response.status, data)) {
        throw new CodeAppCenterAuthError(
          resultMessage(data) || `HTTP ${response.status}`
        );
      }
      throw new Error(resultMessage(data) || `HTTP ${response.status}`);
    }

    if (data?.state && data.state !== 'Success') {
      if (isAuthFailure(response.status, data)) {
        throw new CodeAppCenterAuthError(resultMessage(data));
      }
      throw new Error(resultMessage(data));
    }

    if (data && Object.prototype.hasOwnProperty.call(data, 'content')) {
      const code = Number(data.code ?? data.state ?? 200);
      if (Number.isFinite(code) && code >= 400) {
        if (isAuthFailure(response.status, data)) {
          throw new CodeAppCenterAuthError(resultMessage(data));
        }
        throw new Error(resultMessage(data));
      }
      return data.content as T;
    }

    return data as T;
  }
}
