// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Probe physical PX4 links through the bundled FleetDock runtime without sending control commands.

import * as fs from 'fs';
import * as path from 'path';
import * as net from 'net';
import { execFile } from 'child_process';
import {
  buildPhysicalConnectionURI,
  validatePhysicalDeviceConnection
} from './connection-uri';
import { PhysicalDeviceConnection } from './types';
import { cleanupFleetDockMavsdkServersByGrpcPort } from '../fleetdock/mavsdk-cleanup';
import { getAppDir, getBundledPythonPath } from '../utils';
import { PRODUCT_NAME } from '../branding';

export interface IPhysicalConnectionProbeResult {
  reportVersion: 1;
  testedAt: string;
  ok: boolean;
  endpoint: string;
  connected: boolean;
  telemetryReceived: boolean;
  released: boolean;
  systemId?: number;
  componentId?: number;
  firmwareVersion?: string;
  firmwareGitHash?: string;
  hardwareUid?: string;
  vendorName?: string;
  productName?: string;
  telemetry?: {
    armed?: boolean;
    inAir?: boolean;
    flightMode?: string;
    batteryPercent?: number;
    latitudeDeg?: number;
    longitudeDeg?: number;
    relativeAltitudeM?: number;
    gpsPositionOk?: boolean;
    armable?: boolean;
  };
  code?: string;
  message: string;
}

const PROBE_MARKER = 'SKYLAB_PHYSICAL_PROBE=';
const PROBE_SCRIPT = String.raw`
import json
import sys
import time

from fleetdock import DeviceFactory

endpoint = sys.argv[1]
server_port = int(sys.argv[2])
device = None
result = {
    "reportVersion": 1,
    "testedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "ok": False,
    "endpoint": endpoint,
    "connected": False,
    "telemetryReceived": False,
    "released": False,
}

try:
    device = (
        DeviceFactory.create()
        .px4_generic()
        .params(
            endpoint=endpoint,
            timeout_s=10,
            connect_attempts=1,
            connect_retry_delay_s=0,
            mavsdk_server_port=server_port,
        )
        .build()
    )
    response = device.connect()
    if not response.ok:
        raise RuntimeError(response.message or response.code.value)

    result["connected"] = True
    # [UAV-IDE MODIFY] FleetDock exposes the managed MAVSDK client through native_client.
    client = device.native_client
    identity = client.get_system_identity(timeout_s=2.0)
    if identity:
        result["systemId"] = identity.get("system_id")
        result["componentId"] = identity.get("component_id")
        result["firmwareVersion"] = identity.get("firmware_version")
        result["firmwareGitHash"] = identity.get("firmware_git_hash")
        result["hardwareUid"] = identity.get("hardware_uid")
        result["vendorName"] = identity.get("vendor_name")
        result["productName"] = identity.get("product_name")

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        snapshot = device.state_snapshot()
        if any(
            getattr(snapshot, field, None) is not None
            for field in (
                "armed",
                "in_air",
                "flight_mode",
                "battery_remaining_fraction",
                "roll_deg",
                "latitude_deg",
            )
        ):
            result["telemetryReceived"] = True
            result["telemetry"] = {
                "armed": snapshot.armed,
                "inAir": snapshot.in_air,
                "flightMode": snapshot.flight_mode,
                "batteryPercent": (
                    round(snapshot.battery_remaining_fraction * 100.0, 1)
                    if snapshot.battery_remaining_fraction is not None
                    else None
                ),
                "latitudeDeg": snapshot.latitude_deg,
                "longitudeDeg": snapshot.longitude_deg,
                "relativeAltitudeM": snapshot.relative_altitude_m,
                "gpsPositionOk": snapshot.health_global_position_ok,
                "armable": snapshot.health_armable,
            }
            break
        time.sleep(0.1)

    if not result["telemetryReceived"]:
        raise TimeoutError("PX4 heartbeat received but basic telemetry timed out.")

    result["ok"] = True
    result["message"] = "PX4 heartbeat and bidirectional MAVLink connection verified."
except Exception as exc:
    message = str(exc) or exc.__class__.__name__
    lowered = message.lower()
    if endpoint.lower().startswith("serial://") and any(
        token in lowered for token in ("busy", "access is denied", "permission", "bind error", "resource temporarily unavailable")
    ):
        code = "serial_in_use"
    elif endpoint.lower().startswith("serial://") and any(
        token in lowered for token in ("not found", "no such file", "does not exist", "cannot find")
    ):
        code = "serial_not_found"
    elif "baud" in lowered:
        code = "invalid_baud_rate"
    elif "telemetry" in lowered and any(token in lowered for token in ("timeout", "timed out")):
        code = "telemetry_timeout"
    elif any(token in lowered for token in ("timeout", "timed out")):
        code = "heartbeat_timeout"
    else:
        code = "connection_failed"
    result.update({"code": code, "message": message})
finally:
    if device is not None:
        try:
            device.close()
            result["released"] = True
        except Exception:
            pass

print("${PROBE_MARKER}" + json.dumps(result, ensure_ascii=False), flush=True)
`;

export async function probePhysicalConnection(
  value: PhysicalDeviceConnection
): Promise<IPhysicalConnectionProbeResult> {
  const connection = validatePhysicalDeviceConnection(value);
  const endpoint = buildPhysicalConnectionURI(connection);
  const testedAt = new Date().toISOString();
  if (connection.kind === 'serial') {
    const serialError = preflightSerialPort(connection.serialPort);
    if (serialError) {
      return { ...serialError, endpoint };
    }
  }

  const pythonPath = getBundledPythonPath();
  if (!fs.existsSync(pythonPath)) {
    return {
      ok: false,
      reportVersion: 1,
      testedAt,
      endpoint,
      connected: false,
      telemetryReceived: false,
      released: true,
      code: 'runtime_missing',
      message: `${PRODUCT_NAME} 内置 Python 不存在：${pythonPath}`
    };
  }

  const sdkRoot = resolveFleetDockSdkRoot();
  const env = {
    ...process.env,
    PYTHONPATH: prependPath(sdkRoot, process.env.PYTHONPATH)
  };
  // [UAV-IDE MODIFY] Use a probe-exclusive gRPC port so cleanup never targets
  // a user kernel or another application's connection by endpoint.
  let mavsdkServerPort: number;
  try {
    mavsdkServerPort = await findAvailableLoopbackPort();
  } catch (error) {
    return {
      reportVersion: 1,
      testedAt,
      ok: false,
      endpoint,
      connected: false,
      telemetryReceived: false,
      released: true,
      code: 'probe_port_unavailable',
      message: `无法为连接测试分配本地 MAVSDK 端口：${String(
        (error as Error)?.message || error
      )}`
    };
  }

  let result: IPhysicalConnectionProbeResult;
  try {
    result = await new Promise<IPhysicalConnectionProbeResult>(resolve => {
      execFile(
        pythonPath,
        ['-c', PROBE_SCRIPT, endpoint, String(mavsdkServerPort)],
        {
          env,
          timeout: 25000,
          windowsHide: true,
          maxBuffer: 1024 * 1024
        },
        (error, stdout, stderr) => {
          const parsed = parseProbeOutput(String(stdout || ''), endpoint);
          if (parsed) {
            resolve(parsed);
            return;
          }
          const message = String(stderr || error?.message || '').trim();
          resolve({
            ok: false,
            reportVersion: 1,
            testedAt,
            endpoint,
            connected: false,
            telemetryReceived: false,
            released: false,
            code:
              error && (error as any).killed
                ? 'connection_timeout'
                : 'probe_failed',
            message: friendlyProbeMessage(
              error && (error as any).killed
                ? 'connection_timeout'
                : 'probe_failed',
              message || '连接测试没有返回可识别结果。',
              endpoint
            )
          });
        }
      );
    });
  } finally {
    cleanupFleetDockMavsdkServersByGrpcPort([mavsdkServerPort]);
  }
  const released =
    connection.kind !== 'serial' ||
    preflightSerialPort(connection.serialPort) === null;
  if (!released) {
    return {
      ...result,
      ok: false,
      released: false,
      code: 'release_failed',
      message:
        '连接测试已经结束，但串口仍未释放。请关闭可能残留的连接程序后重试。'
    };
  }
  return {
    ...result,
    released: true,
    message: result.ok
      ? '配置验证通过，已收到 PX4 遥测；测试连接已关闭，端口已释放。'
      : result.message
  };
}

function preflightSerialPort(
  serialPort: string
): Omit<IPhysicalConnectionProbeResult, 'endpoint'> | null {
  if (process.platform !== 'win32') {
    return {
      ok: false,
      reportVersion: 1,
      testedAt: new Date().toISOString(),
      connected: false,
      telemetryReceived: false,
      released: true,
      code: 'unsupported_platform',
      message: '当前串口连接测试仅支持 Windows。'
    };
  }
  const devicePath = `\\\\.\\${serialPort}`;
  try {
    const descriptor = fs.openSync(devicePath, 'r+');
    fs.closeSync(descriptor);
    return null;
  } catch (error) {
    const code = String((error as NodeJS.ErrnoException)?.code || '');
    const notFound = code === 'ENOENT';
    return {
      ok: false,
      reportVersion: 1,
      testedAt: new Date().toISOString(),
      connected: false,
      telemetryReceived: false,
      released: true,
      code: notFound ? 'serial_not_found' : 'serial_in_use',
      message: notFound
        ? `未找到串口 ${serialPort}。请确认设备和驱动已连接。`
        : `串口 ${serialPort} 无法打开，可能正被 QGroundControl 或其他程序占用。`
    };
  }
}

function parseProbeOutput(
  stdout: string,
  endpoint: string
): IPhysicalConnectionProbeResult | null {
  const line = stdout
    .split(/\r?\n/)
    .reverse()
    .find(item => item.startsWith(PROBE_MARKER));
  if (!line) {
    return null;
  }
  try {
    const parsed = JSON.parse(line.slice(PROBE_MARKER.length));
    return {
      reportVersion: 1,
      testedAt: String(parsed?.testedAt || new Date().toISOString()),
      ok: parsed?.ok === true,
      endpoint,
      connected: parsed?.connected === true,
      telemetryReceived: parsed?.telemetryReceived === true,
      released: parsed?.released === true,
      systemId: Number.isInteger(Number(parsed?.systemId))
        ? Number(parsed.systemId)
        : undefined,
      componentId: Number.isInteger(Number(parsed?.componentId))
        ? Number(parsed.componentId)
        : undefined,
      firmwareVersion: textOrUndefined(parsed?.firmwareVersion),
      firmwareGitHash: textOrUndefined(parsed?.firmwareGitHash),
      hardwareUid: textOrUndefined(parsed?.hardwareUid),
      vendorName: textOrUndefined(parsed?.vendorName),
      productName: textOrUndefined(parsed?.productName),
      telemetry:
        parsed?.telemetry && typeof parsed.telemetry === 'object'
          ? {
              armed: booleanOrUndefined(parsed.telemetry.armed),
              inAir: booleanOrUndefined(parsed.telemetry.inAir),
              flightMode: textOrUndefined(parsed.telemetry.flightMode),
              batteryPercent: numberOrUndefined(
                parsed.telemetry.batteryPercent
              ),
              latitudeDeg: numberOrUndefined(parsed.telemetry.latitudeDeg),
              longitudeDeg: numberOrUndefined(parsed.telemetry.longitudeDeg),
              relativeAltitudeM: numberOrUndefined(
                parsed.telemetry.relativeAltitudeM
              ),
              gpsPositionOk: booleanOrUndefined(parsed.telemetry.gpsPositionOk),
              armable: booleanOrUndefined(parsed.telemetry.armable)
            }
          : undefined,
      code: parsed?.code ? String(parsed.code) : undefined,
      message: friendlyProbeMessage(
        parsed?.code ? String(parsed.code) : undefined,
        String(parsed?.message || ''),
        endpoint
      )
    };
  } catch (error) {
    return null;
  }
}

function friendlyProbeMessage(
  code: string | undefined,
  detail: string,
  endpoint: string
): string {
  const suffix = detail ? `\n详情：${detail}` : '';
  switch (code) {
    case 'serial_in_use':
      return `串口被占用。请先断开 QGroundControl 或其他串口程序。${suffix}`;
    case 'serial_not_found':
      return `未找到串口。请检查 COM 口名称、USB 连接和驱动。${suffix}`;
    case 'invalid_baud_rate':
      return `串口波特率不可用。请核对飞控串口波特率。${suffix}`;
    case 'heartbeat_timeout':
      return `10 秒内未收到 PX4 心跳。请检查 ${endpoint}、数据线和飞控波特率。${suffix}`;
    case 'telemetry_timeout':
      return `已收到 PX4 心跳，但未收到基础遥测。请检查 MAVLink 遥测流配置。${suffix}`;
    case 'connection_timeout':
      return `连接测试超时并已停止，端口已释放。请检查 ${endpoint}。${suffix}`;
    case 'release_failed':
      return `连接测试已结束，但端口释放校验失败。请关闭残留连接程序后重试。${suffix}`;
    default:
      return detail || '连接测试失败。';
  }
}

function textOrUndefined(value: unknown): string | undefined {
  const text = String(value || '').trim();
  return text || undefined;
}

function numberOrUndefined(value: unknown): number | undefined {
  const number = Number(value);
  return Number.isFinite(number) ? number : undefined;
}

function booleanOrUndefined(value: unknown): boolean | undefined {
  return typeof value === 'boolean' ? value : undefined;
}

function resolveFleetDockSdkRoot(): string {
  const appDir = getAppDir();
  const candidates = [
    path.join(appDir, 'dist-resources', 'skylab-runtime', 'fleetdock-src'),
    path.join(appDir, 'skylab-runtime', 'fleetdock-src')
  ];
  return (
    candidates.find(candidate => fs.existsSync(candidate)) || candidates[0]
  );
}

function prependPath(value: string, existing?: string): string {
  return [
    value,
    ...String(existing || '')
      .split(path.delimiter)
      .map(item => item.trim())
      .filter(item => item && path.resolve(item) !== path.resolve(value))
  ].join(path.delimiter);
}

function findAvailableLoopbackPort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      const port =
        address && typeof address === 'object' ? Number(address.port) : 0;
      server.close(error => {
        if (error) {
          reject(error);
        } else if (!Number.isInteger(port) || port <= 0) {
          reject(new Error('Unable to allocate a MAVSDK probe port.'));
        } else {
          resolve(port);
        }
      });
    });
  });
}
