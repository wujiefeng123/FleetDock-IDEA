// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import { execFile } from 'child_process';
import * as fs from 'fs';
import * as net from 'net';
import { getBundledPythonPath } from '../../utils';
import {
  UavSimulationCheckItem,
  UavSimulationEnvironmentReport,
  UavSimulationScene
} from '../types';
import {
  getDefaultPx4WslPath,
  windowsPathToWslPath
} from '../config/simulation-install-paths';
import { PRODUCT_NAME } from '../../branding';

type ExecResult = {
  stdout: string;
  stderr: string;
};

// [UAV-IDE MODIFY] Use the currently provisioned Ubuntu distro and its actual PX4 checkout until the dedicated image is available.
const DEFAULT_WSL_DISTRO = 'Ubuntu-24.04';
const DEFAULT_PX4_WSL_DIRECTORY = getDefaultPx4WslPath();
const STATIC_CHECK_CACHE_TTL_MS = 60000;

// [UAV-IDE MODIFY] Report UAV simulation environment readiness without installing or repairing anything automatically.
export class SimulationEnvironmentService {
  async check(
    options: {
      scene?: UavSimulationScene;
      ports?: number[];
      pythonExecutable?: string;
      wslDistro?: string;
      px4WslDirectory?: string;
    } = {}
  ): Promise<UavSimulationEnvironmentReport> {
    const items: UavSimulationCheckItem[] = [];
    const wslDistro = options.wslDistro || DEFAULT_WSL_DISTRO;
    const px4WslDirectory = windowsPathToWslPath(
      options.px4WslDirectory || DEFAULT_PX4_WSL_DIRECTORY
    );
    let pythonExecutable = options.pythonExecutable || '';
    let pythonExecutableError: unknown = null;
    if (!pythonExecutable) {
      try {
        pythonExecutable = this._resolvePythonExecutable();
      } catch (error) {
        pythonExecutableError = error;
      }
    }
    const staticCacheKey = this._staticCheckCacheKey({
      scene: options.scene,
      pythonExecutable,
      wslDistro,
      px4WslDirectory
    });
    const ports = Array.from(new Set(options.ports || []));
    const cachedStaticItems = this._readStaticCheckCache(staticCacheKey);
    if (cachedStaticItems) {
      items.push(...cachedStaticItems);
      // [UAV-IDE MODIFY] Dynamic port probes are independent and should not add serial startup latency.
      items.push(
        ...(await Promise.all(ports.map(port => this._checkPort(port))))
      );
      return {
        ok: items.every(item => item.ok),
        items,
        checkedAt: new Date().toISOString()
      };
    }

    items.push({
      id: 'windows',
      label: 'Windows 平台',
      ok: process.platform === 'win32',
      message:
        process.platform === 'win32'
          ? '当前主机是 Windows。'
          : '首版仿真仅支持 Windows + WSL2。',
      suggestion: '请在 Windows 主机上启动 Project AirSim packaged scene。'
    });

    // [UAV-IDE MODIFY] Run independent WSL, PX4, Python, and scene checks concurrently.
    const [wslList, px4Item, pythonItem] = await Promise.all([
      this._checkWsl(wslDistro),
      this._checkPx4(wslDistro, px4WslDirectory),
      this._checkProjectAirSimPythonResult(
        pythonExecutable,
        pythonExecutableError
      )
    ]);
    items.push(...wslList, px4Item, pythonItem);
    if (options.scene) {
      items.push(this._checkScene(options.scene));
    }
    this._writeStaticCheckCache(staticCacheKey, items);
    items.push(
      ...(await Promise.all(ports.map(port => this._checkPort(port))))
    );

    return {
      ok: items.every(item => item.ok),
      items,
      checkedAt: new Date().toISOString()
    };
  }

  private async _checkWsl(distro: string): Promise<UavSimulationCheckItem[]> {
    try {
      const result = await this._execFileAsync('wsl.exe', ['-l', '-v']);
      const normalized = this._normalizeWslOutput(result.stdout);
      const line = normalized
        .split(/\r?\n/)
        .find(item => item.toLowerCase().includes(distro.toLowerCase()));
      return [
        {
          id: 'wsl2',
          label: 'WSL2 可用',
          ok: true,
          message: '已检测到 wsl.exe。',
          detail: normalized
        },
        {
          id: 'wsl-distro',
          label: `${distro} 发行版`,
          ok: !!line && /\b2\b/.test(line),
          message: line
            ? /\b2\b/.test(line)
              ? `${distro} 已安装且是 WSL2。`
              : `${distro} 已安装但不是 WSL2。`
            : `未找到 ${distro}。`,
          detail: line || normalized,
          suggestion: `请预先导入并初始化 ${distro}，并确认版本为 WSL2。`
        }
      ];
    } catch (error) {
      return [
        {
          id: 'wsl2',
          label: 'WSL2 可用',
          ok: false,
          message: '未能执行 wsl.exe。',
          detail: this._message(error),
          suggestion: '请安装/启用 WSL2 后重新检测。'
        },
        {
          id: 'wsl-distro',
          label: `${distro} 发行版`,
          ok: false,
          message: `无法检测 ${distro}。`,
          suggestion: `请先完成 ${distro} 预制环境导入。`
        }
      ];
    }
  }

  private async _checkPx4(
    distro: string,
    px4WslDirectory: string
  ): Promise<UavSimulationCheckItem> {
    try {
      // [UAV-IDE MODIFY] Check source and build paths in one WSL process while preserving precise diagnostics.
      await this._execFileAsync('wsl.exe', [
        '-d',
        distro,
        'bash',
        '-lc',
        [
          `if [ ! -d '${this._shellQuote(
            px4WslDirectory
          )}' ]; then echo SOURCE_MISSING >&2; exit 2; fi`,
          `if [ ! -d '${this._shellQuote(
            px4WslDirectory
          )}/build/px4_sitl_default' ]; then echo BUILD_MISSING >&2; exit 3; fi`
        ].join('; ')
      ]);
      return {
        id: 'px4',
        label: 'PX4 已安装并编译',
        ok: true,
        message: `${px4WslDirectory} 和 build/px4_sitl_default 均已存在。`
      };
    } catch (error) {
      const detail = this._message(error);
      return {
        id: 'px4',
        label: 'PX4 已安装并编译',
        ok: false,
        message: detail.includes('BUILD_MISSING')
          ? `已检测到 ${px4WslDirectory}，但未检测到 build/px4_sitl_default。`
          : `未检测到 ${px4WslDirectory}。`,
        detail,
        suggestion: `请确认 ${distro} 中存在 ${px4WslDirectory}，并已生成 ${px4WslDirectory}/build/px4_sitl_default。`
      };
    }
  }

  private async _checkProjectAirSimPython(
    pythonExecutable: string
  ): Promise<UavSimulationCheckItem> {
    try {
      await this._execFileAsync(pythonExecutable, [
        '-c',
        'import projectairsim, fleetdock; from fleetdock.adapters.projectairsim_sensors import _ProjectAirSimSensorBackend; from fleetdock import ProjectAirSimRtspBridge; print(fleetdock.__version__)'
      ]);
      return {
        id: 'projectairsim-python',
        label: 'Project AirSim Python 客户端',
        ok: true,
        message: `${pythonExecutable} 可以导入 projectairsim 和 FleetDock SDK 仿真传感器适配器。`
      };
    } catch (error) {
      return {
        id: 'projectairsim-python',
        label: 'Project AirSim Python 客户端',
        ok: false,
        message: `${pythonExecutable} 不能导入 projectairsim 或 FleetDock SDK 仿真传感器适配器。`,
        detail: this._message(error),
        suggestion:
          '请在 IDE 独立 Python 运行时中安装 Project AirSim Python client 和匹配版本的 FleetDock SDK。'
      };
    }
  }

  private async _checkProjectAirSimPythonResult(
    pythonExecutable: string,
    pythonExecutableError: unknown
  ): Promise<UavSimulationCheckItem> {
    try {
      if (pythonExecutableError) {
        throw pythonExecutableError;
      }
      return await this._checkProjectAirSimPython(pythonExecutable);
    } catch (error) {
      return {
        id: 'projectairsim-python',
        label: 'Project AirSim Python 客户端',
        ok: false,
        message: this._message(error),
        suggestion: `请重新安装 ${PRODUCT_NAME}，或重新生成包含 projectairsim 的内置 Python 环境。`
      };
    }
  }

  private _checkScene(scene: UavSimulationScene): UavSimulationCheckItem {
    const rootOk =
      !!scene.rootDirectory &&
      fs.existsSync(scene.rootDirectory) &&
      fs.statSync(scene.rootDirectory).isDirectory();
    const exeOk =
      !!scene.executablePath &&
      fs.existsSync(scene.executablePath) &&
      fs.statSync(scene.executablePath).isFile();
    return {
      id: 'scene',
      label: '仿真场景',
      ok: rootOk && exeOk,
      message:
        rootOk && exeOk
          ? `${scene.name} 场景目录和入口 EXE 有效。`
          : '场景目录或入口 EXE 不存在。',
      detail: scene.executablePath,
      suggestion: '如果场景目录被移动，请在场景管理中删除登记后重新添加目录。'
    };
  }

  private async _checkPort(port: number): Promise<UavSimulationCheckItem> {
    const ok = await this._isPortFree(port);
    return {
      id: `port-${port}`,
      label: `端口 ${port}`,
      ok,
      message: ok ? `端口 ${port} 可用。` : `端口 ${port} 已被占用。`,
      suggestion: ok ? undefined : '请停止占用端口的程序，或稍后重试。'
    };
  }

  private _isPortFree(port: number): Promise<boolean> {
    return new Promise(resolve => {
      const server = net.createServer();
      server.once('error', () => resolve(false));
      server.once('listening', () => {
        server.close(() => resolve(true));
      });
      server.listen(port, '127.0.0.1');
    });
  }

  private _execFileAsync(command: string, args: string[]): Promise<ExecResult> {
    return new Promise((resolve, reject) => {
      execFile(
        command,
        args,
        { windowsHide: true },
        (error, stdout, stderr) => {
          if (error) {
            reject(new Error(stderr || error.message));
            return;
          }
          resolve({
            stdout: String(stdout || ''),
            stderr: String(stderr || '')
          });
        }
      );
    });
  }

  private _normalizeWslOutput(output: string): string {
    return String(output || '')
      .replace(/\0/g, '')
      .replace(/\u0008/g, '')
      .trim();
  }

  private _shellQuote(value: string): string {
    return String(value || '').replace(/'/g, "'\\''");
  }

  private _resolvePythonExecutable(): string {
    const bundledPython = getBundledPythonPath();
    if (bundledPython && fs.existsSync(bundledPython)) {
      return bundledPython;
    }
    // [UAV-IDE MODIFY] FleetDock simulation must use the IDE-bundled Python runtime, not a system interpreter.
    throw new Error(`${PRODUCT_NAME} bundled Python runtime was not found.`);
  }

  private _message(error: unknown): string {
    return error instanceof Error ? error.message : String(error || '未知错误');
  }

  private _staticCheckCacheKey(options: {
    scene?: UavSimulationScene;
    pythonExecutable: string;
    wslDistro: string;
    px4WslDirectory: string;
  }): string {
    return JSON.stringify({
      platform: process.platform,
      wslDistro: options.wslDistro,
      px4WslDirectory: options.px4WslDirectory,
      pythonExecutable: options.pythonExecutable,
      pythonExecutableSignature: this._fileSignature(options.pythonExecutable),
      scene: options.scene
        ? {
            rootDirectory: options.scene.rootDirectory,
            rootDirectorySignature: this._fileSignature(
              options.scene.rootDirectory
            ),
            executablePath: options.scene.executablePath,
            executableSignature: this._fileSignature(
              options.scene.executablePath
            )
          }
        : null
    });
  }

  private _fileSignature(filePath: string): string {
    try {
      const stat = fs.statSync(filePath);
      return `${stat.size}:${Math.round(stat.mtimeMs)}`;
    } catch (_error) {
      return 'missing';
    }
  }

  private _readStaticCheckCache(key: string): UavSimulationCheckItem[] | null {
    if (
      !this._staticCheckCache ||
      this._staticCheckCache.key !== key ||
      Date.now() - this._staticCheckCache.checkedAt > STATIC_CHECK_CACHE_TTL_MS
    ) {
      return null;
    }
    return this._staticCheckCache.items.map(item => ({ ...item }));
  }

  private _writeStaticCheckCache(
    key: string,
    items: UavSimulationCheckItem[]
  ): void {
    this._staticCheckCache = {
      key,
      checkedAt: Date.now(),
      items: items.map(item => ({ ...item }))
    };
  }

  private _staticCheckCache: {
    key: string;
    checkedAt: number;
    items: UavSimulationCheckItem[];
  } | null = null;
}

export const UAV_SIMULATION_DEFAULT_WSL_DISTRO = DEFAULT_WSL_DISTRO;
export const UAV_SIMULATION_DEFAULT_PX4_WSL_DIRECTORY = DEFAULT_PX4_WSL_DIRECTORY;
