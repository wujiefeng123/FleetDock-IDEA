import { app, shell } from 'electron';
import { spawn } from 'child_process';
import type { ChildProcess } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import { findQgcExecutable, getBundledQgcPath } from './qgc-paths';
import { getSkyLabLibRoot } from '../uavsimulation/config/simulation-install-paths';

export interface IQgcStatus {
  installed: boolean;
  running: boolean;
  executablePath: string;
  version: string;
  message: string;
}

// [UAV-IDE MODIFY] QGC remains an independent user application after the IDE session closes.
export class QgcManager {
  status(): IQgcStatus {
    const executablePath = findQgcExecutable(this._configuredPath());
    const running = !!this._child && this._child.exitCode === null;
    return {
      installed: !!executablePath,
      running,
      executablePath: executablePath || getBundledQgcPath(),
      version: this._isBundledPath(executablePath)
        ? this._bundledVersion()
        : '',
      message: executablePath
        ? running
          ? 'QGroundControl 已由 IDEA 启动。'
          : 'QGroundControl 已就绪。'
        : '未找到 QGroundControl，请检查随 IDEA 安装的组件或选择程序文件。'
    };
  }

  async launch(): Promise<IQgcStatus> {
    if (this._closing) {
      await this._closing;
    }
    const executablePath = findQgcExecutable(this._configuredPath());
    if (!executablePath) {
      throw new Error(
        '未找到 QGroundControl.exe。请在地面站页面选择程序文件。'
      );
    }
    if (this._child && this._child.exitCode === null) {
      return this.status();
    }
    if (this._launching) {
      return this._launching;
    }
    this._launching = new Promise<IQgcStatus>((resolve, reject) => {
      const child = spawn(executablePath, [], {
        cwd: path.dirname(executablePath),
        windowsHide: false,
        detached: true,
        stdio: 'ignore'
      });
      child.once('error', error => {
        this._child = undefined;
        reject(new Error(`QGroundControl 启动失败：${error.message}`));
      });
      child.once('spawn', () => {
        this._child = child;
        child.unref();
        resolve(this.status());
      });
      child.once('exit', () => {
        if (this._child === child) {
          this._child = undefined;
        }
      });
    }).finally(() => {
      this._launching = undefined;
    });
    return this._launching;
  }

  // [UAV-IDE MODIFY] Close only the QGC process launched and tracked by this IDE session.
  async close(): Promise<IQgcStatus> {
    if (this._launching) {
      await this._launching;
    }
    if (this._closing) {
      return this._closing;
    }
    const child = this._child;
    if (!child || child.exitCode !== null) {
      return this.status();
    }
    this._closing = new Promise<IQgcStatus>((resolve, reject) => {
      const timeout = setTimeout(() => {
        reject(new Error('QGroundControl 未在 10 秒内关闭，请检查程序窗口。'));
      }, 10000);
      child.once('exit', () => {
        clearTimeout(timeout);
        resolve(this.status());
      });
      try {
        if (!child.kill()) {
          clearTimeout(timeout);
          reject(new Error('无法关闭 QGroundControl，请检查程序窗口。'));
        }
      } catch (error) {
        clearTimeout(timeout);
        reject(new Error(`无法关闭 QGroundControl：${error}`));
      }
    }).finally(() => {
      this._closing = undefined;
    });
    return this._closing;
  }

  setExecutablePath(executablePath: string): IQgcStatus {
    const resolved = path.resolve(executablePath);
    if (
      path.basename(resolved).toLowerCase() !== 'qgroundcontrol.exe' ||
      !fs.existsSync(resolved) ||
      !fs.statSync(resolved).isFile()
    ) {
      throw new Error('请选择有效的 QGroundControl.exe 程序文件。');
    }
    const preferencePath = this._preferencePath();
    fs.mkdirSync(path.dirname(preferencePath), { recursive: true });
    fs.writeFileSync(
      preferencePath,
      JSON.stringify({ executablePath: resolved }),
      'utf8'
    );
    return this.status();
  }

  async openInstallDirectory(): Promise<void> {
    const executablePath = findQgcExecutable(this._configuredPath());
    if (!executablePath) {
      throw new Error('未找到 QGroundControl 安装目录。');
    }
    const error = await shell.openPath(path.dirname(executablePath));
    if (error) {
      throw new Error(`无法打开 QGroundControl 安装目录：${error}`);
    }
  }

  private _configuredPath(): string | undefined {
    try {
      const executablePath = JSON.parse(
        fs.readFileSync(this._preferencePath(), 'utf8')
      ).executablePath;
      return typeof executablePath === 'string' ? executablePath : undefined;
    } catch {
      return undefined;
    }
  }

  private _bundledVersion(): string {
    try {
      const manifestPath = path.join(
        getSkyLabLibRoot(),
        'QGroundControl',
        'fleetdock-qgc-manifest.json'
      );
      return String(
        JSON.parse(fs.readFileSync(manifestPath, 'utf8')).version || ''
      );
    } catch {
      return '';
    }
  }

  private _preferencePath(): string {
    return path.join(app.getPath('userData'), 'groundstation.json');
  }

  private _isBundledPath(executablePath?: string): boolean {
    if (!executablePath) {
      return false;
    }
    const normalize = (value: string) =>
      process.platform === 'win32'
        ? path.resolve(value).toLowerCase()
        : path.resolve(value);
    return normalize(executablePath) === normalize(getBundledQgcPath());
  }

  private _child: ChildProcess | undefined;
  private _launching: Promise<IQgcStatus> | undefined;
  private _closing: Promise<IQgcStatus> | undefined;
}
