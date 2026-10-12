// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Generate workspace-local FleetDock runtime bootstrap files without coupling Python SDK objects to Electron.

/* eslint-disable camelcase -- [UAV-IDE MODIFY] Generate the FleetDock Python runtime with its published snake_case API. */

import * as fs from 'fs';
import * as crypto from 'crypto';
import * as path from 'path';
import {
  IApplicationConfig,
  IApplicationEnvVar,
  normalizeApplicationEnvType,
  toEnvironmentMap
} from '../config/application';
import { DroneDevice, FleetDockDeviceConfig } from '../devices/types';
import {
  buildPhysicalConnectionURI,
  normalizePhysicalDeviceConnection
} from '../devices/connection-uri';
import { getAppDir } from '../utils';

export const FLEETDOCK_RUNTIME_FILE = 'fleetdock_runtime.py';
export const FLEETDOCK_RUNTIME_STUB_FILE = 'fleetdock_runtime.pyi';

export interface IFleetDockRuntimeSyncResult {
  runtimePath: string;
  stubPath: string;
  startupPath: string;
  mavsdkEndpoints: string[];
  bindingErrors: string[];
  env: { [key: string]: string };
}

type RuntimeDeviceConfig = {
  alias: string;
  device_id: string;
  name: string;
  product: string;
  builder: string;
  firmware?: string;
  params: { [key: string]: string | number | boolean | null | undefined };
  metadata: { [key: string]: string | number | boolean | null | undefined };
};

type RuntimeDeviceBuildResult = {
  devices: RuntimeDeviceConfig[];
  errors: string[];
};

export function ensureFleetDockRuntimeArtifacts(options: {
  workingDirectory: string;
  config: IApplicationConfig;
  devices: DroneDevice[];
}): IFleetDockRuntimeSyncResult {
  const runtimePath = path.join(
    options.workingDirectory,
    FLEETDOCK_RUNTIME_FILE
  );
  const stubPath = path.join(
    options.workingDirectory,
    FLEETDOCK_RUNTIME_STUB_FILE
  );
  const startupPath = path.join(
    options.workingDirectory,
    '.jupyter',
    'ipython',
    'profile_default',
    'startup',
    '00-skylab-fleetdock.py'
  );
  const runtimeBuild = buildRuntimeDevices(
    options.config,
    options.devices || []
  );

  writeTextIfChanged(
    runtimePath,
    renderRuntime(runtimeBuild.devices, runtimeBuild.errors)
  );
  writeTextIfChanged(stubPath, renderRuntimeStub(runtimeBuild.devices));
  writeTextIfChanged(
    startupPath,
    renderStartup(options.workingDirectory, runtimeBuild.devices)
  );

  return {
    runtimePath,
    stubPath,
    startupPath,
    mavsdkEndpoints: getMavsdkEndpoints(runtimeBuild.devices),
    bindingErrors: runtimeBuild.errors,
    env: buildFleetDockRuntimeEnv(options.workingDirectory, runtimePath)
  };
}

export function buildFleetDockServerEnv(options: {
  workingDirectory: string;
  config: IApplicationConfig;
  runtimeEnv: { [key: string]: string };
}): { [key: string]: string } {
  // [UAV-IDE MODIFY] Application env vars remain user-authored values such as waypoints/routes; device bindings are not mirrored as DRONE_n env vars.
  const env = {
    ...toEnvironmentMap(
      (options.config.envVars || []).filter(
        envVar => normalizeApplicationEnvType(envVar.type) !== 'Drone'
      )
    ),
    ...options.runtimeEnv
  };
  env.PYTHONPATH = prependPath(options.workingDirectory, env.PYTHONPATH);
  env.PYTHONPATH = prependPath(
    options.runtimeEnv.FLEETDOCK_IDE_RUNTIME_ROOT,
    env.PYTHONPATH
  );
  env.PYTHONPATH = prependPath(
    options.runtimeEnv.FLEETDOCK_SDK_ROOT,
    env.PYTHONPATH
  );
  return env;
}

function buildRuntimeDevices(
  config: IApplicationConfig,
  devices: DroneDevice[]
): RuntimeDeviceBuildResult {
  const deviceMap = new Map(devices.map(device => [device.id, device]));
  const deviceNameMap = new Map(devices.map(device => [device.name, device]));
  const usedAliases = new Set<string>();
  const usedEndpoints = new Map<string, string>();
  const runtimeDevices: RuntimeDeviceConfig[] = [];
  const errors: string[] = [];

  // [UAV-IDE MODIFY] Use Drone-type application variables as the single source for FleetDock.devices.* generation.
  (config.envVars || [])
    .filter(envVar => normalizeApplicationEnvType(envVar.type) === 'Drone')
    .forEach(envVar => {
      const alias = String(envVar.name || '').trim();
      if (!isPythonIdentifier(alias)) {
        errors.push(`无人机别名“${alias || '(空)'}”不是有效的 Python 标识符。`);
        return;
      }
      if (usedAliases.has(alias)) {
        errors.push(`无人机别名重复：${alias}。`);
        return;
      }
      const device = resolveEnvVarDevice(envVar, deviceMap, deviceNameMap);
      if (!device) {
        errors.push(
          `设备绑定已失效：${alias} -> ${String(
            envVar.value || envVar.defaultValue || '(空)'
          )}。请在应用设置中重新选择设备。`
        );
        return;
      }
      usedAliases.add(alias);
      const runtimeDevice = toRuntimeDeviceConfig(device, alias);
      const endpoint = String(runtimeDevice.params.endpoint || '').trim();
      if (
        (device.source === 'real' || device.runtimeKind === 'physical') &&
        (/^(?:udp|tcp)in:\/\//.test(endpoint) ||
          endpoint.startsWith('serial://'))
      ) {
        const endpointKey = endpoint.toLowerCase();
        const owner = usedEndpoints.get(endpointKey);
        if (owner) {
          errors.push(
            `物理连接地址冲突：${owner} 和 ${alias} 同时占用 ${endpoint}。`
          );
          return;
        }
        usedEndpoints.set(endpointKey, alias);
      }
      runtimeDevices.push(runtimeDevice);
    });
  return { devices: runtimeDevices, errors };
}

function resolveEnvVarDevice(
  envVar: IApplicationEnvVar,
  deviceMap: Map<string, DroneDevice>,
  deviceNameMap: Map<string, DroneDevice>
): DroneDevice | null {
  const rawValue = envVar.value || envVar.defaultValue || '';
  const parsed = parseDroneEnvValue(rawValue);
  if (!parsed) {
    const value = String(rawValue || '');
    return deviceMap.get(value) || deviceNameMap.get(value) || null;
  }

  const deviceId = String((parsed as Partial<DroneDevice>).id || '');
  const deviceName = String((parsed as Partial<DroneDevice>).name || '');
  // [UAV-IDE MODIFY] Legacy JSON snapshots remain usable; id bindings are resolved from the current global registry above.
  return {
    ...(parsed as Partial<DroneDevice>),
    id: deviceId || deviceName
  } as DroneDevice;
}

function parseDroneEnvValue(value: string): Partial<DroneDevice> | null {
  let current: unknown = value;
  for (let index = 0; index < 2; index += 1) {
    try {
      const parsed = JSON.parse(String(current || ''));
      if (typeof parsed === 'string') {
        current = parsed;
        continue;
      }
      return parsed &&
        typeof parsed === 'object' &&
        ((parsed as any).id || (parsed as any).name)
        ? (parsed as Partial<DroneDevice>)
        : null;
    } catch (error) {
      return null;
    }
  }
  return null;
}

function toRuntimeDeviceConfig(
  device: DroneDevice,
  alias: string
): RuntimeDeviceConfig {
  const fleetdock = device.fleetdock || fallbackFleetDockConfig(device);
  return {
    alias,
    device_id: device.id,
    name: device.name || alias,
    product: fleetdock.product,
    builder: fleetdock.builder,
    firmware: fleetdock.firmware,
    params: normalizeRuntimeParams(
      device,
      fleetdock,
      cleanParams(fleetdock.params || {})
    ),
    metadata: {
      source: device.source,
      deviceCategory: device.deviceCategory,
      runtimeKind: device.runtimeKind,
      systemId: device.systemId || device.uav?.systemId || null,
      airsimVehicleName:
        device.simulation?.airsimVehicleName || device.airsimVehicleName || ''
    }
  };
}

function normalizeRuntimeParams(
  device: DroneDevice,
  fleetdock: FleetDockDeviceConfig,
  params: FleetDockDeviceConfig['params']
): FleetDockDeviceConfig['params'] {
  if (fleetdock.builder !== 'px4_generic') {
    return params;
  }

  const dynamicPortParams: FleetDockDeviceConfig['params'] = { ...params };
  // [UAV-IDE MODIFY] Generated runtimes must not retain host-specific fixed gRPC ports.
  delete dynamicPortParams.mavsdk_server_port;
  if (device.source !== 'simulation') {
    return dynamicPortParams;
  }

  // [UAV-IDE MODIFY] Route the SDK-owned Project AirSim RGB bridge through the standard PX4 media boundary.
  const vehicleName = String(
    device.simulation?.airsimVehicleName || device.airsimVehicleName || ''
  ).trim();
  const normalized: FleetDockDeviceConfig['params'] = {
    ...dynamicPortParams,
    ...(vehicleName &&
    hasSimulationRgbCamera(device) &&
    !dynamicPortParams.camera_stream_uri
      ? {
          camera_stream_uri: `rtsp://127.0.0.1:8554/${vehicleName}_camera_front`,
          camera_stream_source: 'simulated'
        }
      : {})
  };
  return normalized;
}

function hasSimulationRgbCamera(device: DroneDevice): boolean {
  if (!Array.isArray(device.sensors)) {
    return true;
  }
  return device.sensors.some(
    sensor => sensor.enabled !== false && sensor.type === 'camera'
  );
}

function getMavsdkEndpoints(devices: RuntimeDeviceConfig[]): string[] {
  return Array.from(
    new Set(
      devices
        .map(device => String(device.params.endpoint || '').trim())
        .filter(endpoint =>
          /^(?:udp|tcp)(?:in|out):\/\/|^serial:\/\//.test(endpoint)
        )
    )
  );
}

function fallbackFleetDockConfig(device: DroneDevice): FleetDockDeviceConfig {
  if (device.source === 'real' || device.runtimeKind === 'physical') {
    return {
      product: 'px4.generic',
      builder: 'px4_generic',
      params: {
        endpoint: buildPhysicalConnectionURI(
          normalizePhysicalDeviceConnection(device)
        ),
        timeout_s: 10
      }
    };
  }
  const port = Number(
    device.uav?.mavlinkPort || device.telemetry?.bindPort || 14600
  );
  return {
    product: 'px4.generic',
    builder: 'px4_generic',
    params: {
      endpoint: `udpin://0.0.0.0:${port}`,
      timeout_s: 30
    }
  };
}

function cleanParams(
  params: FleetDockDeviceConfig['params']
): FleetDockDeviceConfig['params'] {
  const cleaned: FleetDockDeviceConfig['params'] = {};
  for (const [key, value] of Object.entries(params || {})) {
    if (key === 'force_arm') {
      continue;
    }
    if (value !== undefined && value !== '') {
      cleaned[key] = value;
    }
  }
  return cleaned;
}

function isPythonIdentifier(value: string): boolean {
  const keywords = new Set([
    'False',
    'None',
    'True',
    'and',
    'as',
    'assert',
    'async',
    'await',
    'break',
    'class',
    'continue',
    'def',
    'del',
    'elif',
    'else',
    'except',
    'finally',
    'for',
    'from',
    'global',
    'if',
    'import',
    'in',
    'is',
    'lambda',
    'nonlocal',
    'not',
    'or',
    'pass',
    'raise',
    'return',
    'try',
    'while',
    'with',
    'yield'
  ]);
  return /^[A-Za-z_][A-Za-z0-9_]*$/.test(value) && !keywords.has(value);
}

function renderRuntime(
  devices: RuntimeDeviceConfig[],
  errors: string[]
): string {
  const deviceAttributes = renderDeviceAttributes(devices);
  return `# [UAV-IDE MODIFY] Generated FleetDock runtime facade for this FleetDock IDEA application.
# This file is regenerated by FleetDock IDEA when Drone environment variables change.

from __future__ import annotations

import os
import json
import math
import socket
import threading
import atexit
import time
import uuid
from collections import deque
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from fleetdock import BaseDevice, CapabilityName, DeviceFactory, DeviceMetadata, Drone, Fleet, Plan, ReadinessRequirement, RobotDog, SensorSourceKind, SensorStreamOptions
from fleetdock_ide_runtime import IdeSimulationSensorProxy


_DEVICE_CONFIGS = json.loads(${JSON.stringify(JSON.stringify(devices))})
_BINDING_ERRORS = json.loads(${JSON.stringify(JSON.stringify(errors))})


class _FleetDockEnvs:
    def get(self, name: str, default: Any = None) -> Any:
        return os.getenv(name, default)

    def require(self, name: str) -> str:
        value = os.getenv(name)
        if value is None:
            raise KeyError(f"Missing required environment variable: {name}")
        return value

    def all(self) -> dict[str, str]:
        return dict(os.environ)


class _FleetDockHudBridge:
    _OBSERVATION_TIMEOUT_S = 2.0
    _NAVIGATION_STALE_AFTER_MS = 2000

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._event_unsubscribers: list[Any] = []
        self._sensor_unsubscribers: dict[str, Any] = {}
        self._sensor_thread: threading.Thread | None = None
        self._sensor_stop = threading.Event()
        self._sensor_descriptors: dict[str, dict[str, Any]] = {}
        self._sensor_next_refresh_at: dict[str, float] = {}
        self._sensor_states: dict[str, dict[str, Any]] = {}
        self._sensor_received_times: dict[str, deque[float]] = {}
        self._last_events: dict[str, dict[str, Any]] = {}
        self._event_received_times: dict[str, deque[float]] = {}
        self._event_updated_at_s: dict[str, dict[str, float]] = {}
        self._telemetry_releases: dict[str, Any] = {}
        self._last_snapshot_requested_at_s: float | None = None
        self._command_history: deque[dict[str, Any]] = deque(maxlen=100)
        self._command_started: dict[str, dict[str, Any]] = {}
        self._timeline: deque[dict[str, Any]] = deque(maxlen=240)
        self._timeline_event_signatures: dict[str, str] = {}
        self._command_lock = threading.RLock()
        self._sensor_samples: dict[str, dict[str, Any]] = {}
        self._sensor_errors: dict[str, str] = {}
        self._generation = 0

    def start(self, runtime: type["_FleetDockRuntime"]) -> None:
        if os.getenv("FLEETDOCK_HUD_ENABLED", "1") == "0":
            return
        with self._lock:
            if self._server is not None:
                return
            # [UAV-IDE MODIFY] Every close/init cycle owns a fresh callback generation.
            # Late deliveries from the previous device graph must not populate the new HUD.
            self._generation += 1
            self._subscribe_events(runtime)
            host = os.getenv("FLEETDOCK_HUD_HOST", "127.0.0.1")
            base_port = int(os.getenv("FLEETDOCK_HUD_PORT", "0") or "0")
            token = os.getenv("FLEETDOCK_HUD_TOKEN", "")
            if not base_port or not token:
                return

            bridge = self

            class Handler(BaseHTTPRequestHandler):
                def _send_json(self, payload: Any, status: int = 200) -> None:
                    body = json.dumps(
                        bridge._json_safe(payload),
                        ensure_ascii=False,
                        allow_nan=False,
                    ).encode("utf-8")
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def do_GET(self) -> None:
                    if self.path.split("?", 1)[0] not in ("/health", "/snapshot"):
                        self.send_error(404)
                        return
                    if self.headers.get("Authorization", "") != "Bearer " + token:
                        self.send_error(403)
                        return
                    self._send_json(bridge.snapshot(runtime))

                def do_POST(self) -> None:
                    if self.path.split("?", 1)[0] != "/diagnostic-command":
                        self.send_error(404)
                        return
                    if self.headers.get("Authorization", "") != "Bearer " + token:
                        self.send_error(403)
                        return
                    try:
                        length = min(int(self.headers.get("Content-Length", "0") or "0"), 65536)
                        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                        self._send_json(bridge.execute_command(runtime, payload))
                    except Exception as error:
                        self._send_json({
                            "ok": False,
                            "code": "DEBUG_COMMAND_FAILED",
                            "message": str(error) or error.__class__.__name__,
                        }, 500)

                def log_message(self, _format: str, *args: Any) -> None:
                    return

            class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
                allow_reuse_address = False

                def server_bind(self) -> None:
                    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    super().server_bind()

            for port in range(base_port, base_port + 32):
                try:
                    self._server = ExclusiveThreadingHTTPServer((host, port), Handler)
                    break
                except OSError:
                    self._server = None
            if self._server is None:
                return
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name="skylab-fleetdock-hud",
                daemon=True,
            )
            self._thread.start()
            self._start_sensor_monitor(runtime)

    def snapshot(self, runtime: type["_FleetDockRuntime"]) -> dict[str, Any]:
        # [UAV-IDE MODIFY] Treat visible HUD polling as a short-lived observation lease.
        # This keeps navigation telemetry live without opening another device connection.
        with self._lock:
            self._last_snapshot_requested_at_s = time.monotonic()
        self._reconcile_hud_telemetry(runtime)
        with self._lock:
            devices = []
            for config in _DEVICE_CONFIGS:
                alias = config["alias"]
                device = getattr(runtime.devices, alias, None)
                devices.append(self._device_snapshot(alias, config, device))
            return {
                "ok": True,
                "online": True,
                "devices": devices,
                "commandHistory": list(self._command_history),
                "timeline": list(self._timeline),
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            }

    def stop(self) -> None:
        self._sensor_stop.set()
        with self._lock:
            self._generation += 1
            unsubscribers = (
                list(self._event_unsubscribers)
                + list(self._sensor_unsubscribers.values())
                + list(self._telemetry_releases.values())
            )
            self._event_unsubscribers = []
            self._sensor_unsubscribers = {}
            self._telemetry_releases = {}
            self._last_snapshot_requested_at_s = None
            sensor_thread = self._sensor_thread
            self._sensor_thread = None
            self._sensor_samples = {}
            self._sensor_errors = {}
            self._last_events = {}
            self._event_received_times = {}
            self._event_updated_at_s = {}
            self._command_history.clear()
            self._command_started = {}
            self._timeline.clear()
            self._timeline_event_signatures = {}
            self._sensor_states = {}
            self._sensor_descriptors = {}
            self._sensor_next_refresh_at = {}
            self._sensor_received_times = {}
            server = self._server
            self._server = None
            self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        for unsubscribe in unsubscribers:
            try:
                unsubscribe()
            except Exception:
                pass
        if sensor_thread is not None and sensor_thread.is_alive():
            sensor_thread.join(timeout=2.0)

    def _subscribe_events(self, runtime: type["_FleetDockRuntime"]) -> None:
        if self._event_unsubscribers:
            return
        for config in _DEVICE_CONFIGS:
            # [UAV-IDE MODIFY] A physical link is reserved for the user's control task.
            # HUD event observation must not add callback pressure to its MAVSDK connection.
            alias = config["alias"]
            device = getattr(runtime.devices, alias, None)
            events = getattr(device, "events", None)
            subscribe = getattr(events, "subscribe", None)
            if self._automatic_subscriptions_enabled(config) and callable(subscribe):
                generation = self._generation

                def on_event(
                    event: Any,
                    device_alias: str = alias,
                    expected_generation: int = generation,
                ) -> None:
                    event_type = getattr(getattr(event, "type", None), "value", str(getattr(event, "type", "")))
                    received_at = getattr(event, "received_at", None)
                    with self._lock:
                        if expected_generation != self._generation:
                            return
                        received_monotonic = time.monotonic()
                        self._last_events[device_alias] = {
                            "type": event_type,
                            "receivedAt": received_at.isoformat() if hasattr(received_at, "isoformat") else "",
                        }
                        self._event_received_times.setdefault(
                            device_alias, deque(maxlen=128)
                        ).append(received_monotonic)
                        self._event_updated_at_s.setdefault(device_alias, {})[
                            event_type
                        ] = received_monotonic
                        # [UAV-IDE MODIFY] Keep the fault timeline diagnostic:
                        # record state changes and alerts, not high-rate attitude/position samples.
                        lowered_type = event_type.lower()
                        if any(
                            marker in lowered_type
                            for marker in ("changed", "error", "warning", "failsafe", "health")
                        ):
                            event_data = self._json_safe(getattr(event, "data", None))
                            signature = json.dumps(
                                event_data,
                                ensure_ascii=False,
                                sort_keys=True,
                                default=str,
                            )
                            signature_key = device_alias + ":" + event_type
                            if self._timeline_event_signatures.get(signature_key) != signature:
                                self._timeline_event_signatures[signature_key] = signature
                                message = event_type
                                if isinstance(event_data, Mapping):
                                    message = str(
                                        event_data.get("message")
                                        or event_data.get("reason")
                                        or event_data.get("code")
                                        or event_type
                                    )
                                severity = (
                                    "error"
                                    if "error" in lowered_type
                                    else "warning"
                                    if "warning" in lowered_type or "failsafe" in lowered_type
                                    else "info"
                                )
                                self._timeline.append({
                                    "id": str(uuid.uuid4()),
                                    "deviceAlias": device_alias,
                                    "source": "telemetry",
                                    "severity": severity,
                                    "type": event_type,
                                    "message": message,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                })

                try:
                    self._event_unsubscribers.append(subscribe(on_event))
                except Exception:
                    pass
            add_command_observer = getattr(device, "add_command_observer", None)
            if callable(add_command_observer):
                try:
                    self._event_unsubscribers.append(
                        add_command_observer(
                            lambda stage, command, response=None, error=None, device_alias=alias:
                                self._record_command(
                                    device_alias,
                                    stage,
                                    command,
                                    response,
                                    error,
                                )
                        )
                    )
                except Exception:
                    pass

    def _record_command(
        self,
        alias: str,
        stage: str,
        command: Any,
        response: Any = None,
        error: Any = None,
    ) -> None:
        command_id = str(getattr(command, "command_id", ""))
        command_name = getattr(getattr(command, "command", None), "value", "")
        now = datetime.now(timezone.utc)
        now_monotonic = time.monotonic()
        with self._lock:
            if stage == "started":
                self._command_started[command_id] = {
                    "deviceAlias": alias,
                    "command": command_name,
                    "params": self._json_safe(dict(getattr(command, "params", {}) or {})),
                    "startedAt": now.isoformat(),
                    "startedMonotonic": now_monotonic,
                }
                self._timeline.append({
                    "id": command_id + ":started",
                    "deviceAlias": alias,
                    "source": "code",
                    "severity": "info",
                    "type": "command.started",
                    "message": "执行指令：" + command_name,
                    "timestamp": now.isoformat(),
                    "commandId": command_id,
                })
                return

            started = self._command_started.pop(command_id, {})
            started_monotonic = started.pop("startedMonotonic", now_monotonic)
            ok = bool(getattr(response, "ok", False)) if response is not None else False
            code = getattr(getattr(response, "code", None), "value", "")
            message = (
                str(getattr(response, "message", "") or "")
                if response is not None
                else str(error or "")
            )
            item = {
                **started,
                "deviceAlias": alias,
                "commandId": command_id,
                "command": command_name,
                "completedAt": now.isoformat(),
                "durationMs": round(max(0.0, now_monotonic - float(started_monotonic)) * 1000),
                "ok": ok,
                "code": code or ("EXCEPTION" if error is not None else ""),
                "message": message,
                "data": self._json_safe(dict(getattr(response, "data", {}) or {}))
                if response is not None
                else {},
            }
            self._command_history.append(item)
            self._timeline.append({
                "id": command_id + ":completed",
                "deviceAlias": alias,
                "source": "code",
                "severity": "info" if ok else "error",
                "type": "command.completed" if ok else "command.failed",
                "message": (
                    command_name + " 执行成功"
                    if ok
                    else command_name + " 执行失败：" + (message or code or "未知错误")
                ),
                "timestamp": now.isoformat(),
                "commandId": command_id,
            })

    @staticmethod
    def _readiness_issues(device: Any, capability: CapabilityName, extra=()) -> list[dict[str, str]]:
        try:
            report = device.readiness_for(
                capability,
                extra_requirements=extra,
            )
            return [
                {
                    "requirement": getattr(issue.requirement, "value", str(issue.requirement)),
                    "message": str(issue.message),
                }
                for issue in report.issues
            ]
        except Exception as error:
            return [{"requirement": "readiness_check", "message": str(error)}]

    @classmethod
    def _response_payload(cls, response: Any) -> dict[str, Any]:
        return {
            "ok": bool(getattr(response, "ok", False)),
            "commandId": str(getattr(response, "command_id", "")),
            "code": getattr(getattr(response, "code", None), "value", ""),
            "message": str(getattr(response, "message", "") or ""),
            "data": cls._json_safe(dict(getattr(response, "data", {}) or {})),
        }

    def execute_command(
        self,
        runtime: type["_FleetDockRuntime"],
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        alias = str(payload.get("alias") or "")
        device_id = str(payload.get("deviceId") or "")
        command_name = str(payload.get("command") or "")
        params = payload.get("params")
        params = dict(params) if isinstance(params, Mapping) else {}
        if command_name not in {
            "connect",
            "reconnect",
            "unlock_test",
            "takeoff_test",
            "return_to_launch_test",
            "lock",
        }:
            return {
                "ok": False,
                "code": "COMMAND_NOT_ALLOWED",
                "message": "该调试指令不在允许列表中。",
            }
        config = next(
            (
                item
                for item in _DEVICE_CONFIGS
                if (alias and item.get("alias") == alias)
                or (device_id and item.get("device_id") == device_id)
            ),
            None,
        )
        if config is None:
            return {
                "ok": False,
                "code": "DEVICE_NOT_BOUND",
                "message": "该物理设备尚未绑定到当前应用。",
            }
        alias = str(config["alias"])
        device = getattr(runtime.devices, alias, None)
        if device is None:
            return {
                "ok": False,
                "code": "DEVICE_NOT_INITIALIZED",
                "message": "FleetDock 运行时尚未初始化该设备。",
            }

        started = time.monotonic()
        with self._command_lock:
            state = device.state.snapshot()
            issues: list[dict[str, str]] = []
            if command_name == "unlock_test":
                # [UAV-IDE MODIFY] Enforce real-device safety in the runtime, not only in renderer button state.
                if state.armed is not False:
                    issues.append({
                        "requirement": "disarmed",
                        "message": "只有飞控明确处于上锁状态时才能执行解锁测试。",
                    })
                if state.in_air is not False:
                    issues.append({
                        "requirement": "on_ground",
                        "message": "只有飞控明确处于地面状态时才能执行解锁测试。",
                    })
                issues.extend(
                    self._readiness_issues(
                        device,
                        CapabilityName.ARM,
                        extra=(ReadinessRequirement.ARMABLE,),
                    )
                )
            elif command_name == "takeoff_test":
                issues.extend(
                    self._readiness_issues(device, CapabilityName.TAKEOFF)
                )
                if state.armed is not True:
                    issues.append({
                        "requirement": "armed",
                        "message": "设备尚未解锁。",
                    })
                try:
                    altitude = float(params.get("altitude", 1.0))
                except (TypeError, ValueError):
                    altitude = float("nan")
                if not math.isfinite(altitude) or altitude < 0.5 or altitude > 10.0:
                    issues.append({
                        "requirement": "safe_test_altitude",
                        "message": "起飞测试高度必须在 0.5 到 10 米之间。",
                    })
            elif command_name == "return_to_launch_test":
                issues.extend(
                    self._readiness_issues(
                        device,
                        CapabilityName.RETURN_TO_LAUNCH,
                        extra=(
                            ReadinessRequirement.HOME_POSITION,
                            ReadinessRequirement.IN_AIR,
                        ),
                    )
                )
            elif command_name == "lock" and state.in_air is not False:
                issues.append({
                    "requirement": "on_ground",
                    "message": "只有飞控明确处于地面状态时才能从工作台上锁。",
                })
            if issues:
                result = {
                    "ok": False,
                    "code": "PRECONDITION_FAILED",
                    "message": "飞控尚未满足该测试的前置条件。",
                    "preconditions": issues,
                }
            elif command_name in {"connect", "reconnect"}:
                result = self._response_payload(device.connect())
            elif command_name == "unlock_test":
                unlock_result = device.unlock()
                result = self._response_payload(unlock_result)
                if unlock_result.ok:
                    time.sleep(2.0)
                    latest = device.state.snapshot()
                    if latest.in_air is not True:
                        auto_lock = self._response_payload(device.lock())
                        result["autoLock"] = auto_lock
                        if not auto_lock["ok"]:
                            result.update({
                                "ok": False,
                                "code": "AUTO_LOCK_FAILED",
                                "message": "解锁测试完成，但自动上锁失败，请立即人工上锁。",
                            })
            elif command_name == "takeoff_test":
                result = self._response_payload(
                    device.takeoff(altitude)
                )
            elif command_name == "return_to_launch_test":
                result = self._response_payload(device.return_to_launch())
            else:
                result = self._response_payload(device.lock())
        result.update({
            "deviceId": device_id or str(config.get("device_id") or ""),
            "alias": alias,
            "debugCommand": command_name,
            "durationMs": round((time.monotonic() - started) * 1000),
            "completedAt": datetime.now(timezone.utc).isoformat(),
        })
        with self._lock:
            self._timeline.append({
                "id": str(uuid.uuid4()),
                "deviceAlias": alias,
                "source": "workbench",
                "severity": "info" if result.get("ok") else "error",
                "type": "workbench." + command_name,
                "message": (
                    "调试操作成功：" + command_name
                    if result.get("ok")
                    else "调试操作失败：" + str(result.get("message") or result.get("code"))
                ),
                "timestamp": result["completedAt"],
            })
        return result

    def _start_sensor_monitor(self, runtime: type["_FleetDockRuntime"]) -> None:
        if self._sensor_thread is not None and self._sensor_thread.is_alive():
            return
        self._sensor_stop.clear()
        generation = self._generation
        self._sensor_thread = threading.Thread(
            target=self._sensor_monitor_loop,
            args=(runtime, generation),
            name="fleetdock-hud-sensors",
            daemon=True,
        )
        self._sensor_thread.start()

    def _sensor_monitor_loop(
        self,
        runtime: type["_FleetDockRuntime"],
        generation: int,
    ) -> None:
        while not self._sensor_stop.is_set() and generation == self._generation:
            self._reconcile_hud_telemetry(runtime)
            for config in _DEVICE_CONFIGS:
                if self._sensor_stop.is_set() or generation != self._generation:
                    break
                # [UAV-IDE MODIFY] Do not open HUD sensor streams on a real aircraft.
                # Command-scoped SDK telemetry remains the sole owner of the physical link.
                if not self._automatic_subscriptions_enabled(config):
                    continue
                alias = config["alias"]
                device = getattr(runtime.devices, alias, None)
                try:
                    self._reconcile_sensors(
                        alias,
                        device,
                        expected_generation=generation,
                    )
                except Exception as error:
                    with self._lock:
                        if generation == self._generation:
                            self._sensor_errors[alias + ":*"] = str(error)
            self._sensor_stop.wait(1.0)

    def _reconcile_hud_telemetry(self, runtime: type["_FleetDockRuntime"]) -> None:
        now = time.monotonic()
        with self._lock:
            requested_at_s = self._last_snapshot_requested_at_s
            observation_active = (
                requested_at_s is not None
                and now - requested_at_s <= self._OBSERVATION_TIMEOUT_S
            )

        desired_aliases: set[str] = set()
        for config in _DEVICE_CONFIGS:
            if not observation_active or not self._automatic_subscriptions_enabled(config):
                continue
            alias = config["alias"]
            device = getattr(runtime.devices, alias, None)
            if not self._device_connected(device):
                continue
            acquire = getattr(device, "_acquire_capability_telemetry", None)
            if not callable(acquire):
                continue
            desired_aliases.add(alias)
            with self._lock:
                if alias in self._telemetry_releases:
                    continue
                try:
                    self._telemetry_releases[alias] = acquire(CapabilityName.STATUS)
                except Exception as error:
                    self._sensor_errors[alias + ":hud-telemetry"] = str(error)

        releases: list[Any] = []
        with self._lock:
            for alias in tuple(self._telemetry_releases):
                if alias not in desired_aliases:
                    releases.append(self._telemetry_releases.pop(alias))
            if not observation_active:
                self._last_snapshot_requested_at_s = None
        for release in releases:
            try:
                release()
            except Exception:
                pass

    @staticmethod
    def _automatic_subscriptions_enabled(config: Mapping[str, Any]) -> bool:
        metadata = config.get("metadata") or {}
        return not (
            metadata.get("source") == "real"
            or metadata.get("runtimeKind") == "physical"
        )

    def _subscribe_sensors(self, alias: str, device: Any) -> None:
        # [UAV-IDE MODIFY] Retain this focused entry for diagnostics while production uses the reconnecting monitor.
        self._reconcile_sensors(
            alias,
            device,
            force=True,
            expected_generation=self._generation,
        )

    def _reconcile_sensors(
        self,
        alias: str,
        device: Any,
        *,
        force: bool = False,
        expected_generation: int | None = None,
    ) -> None:
        generation = self._generation if expected_generation is None else expected_generation
        if self._sensor_stop.is_set() or generation != self._generation:
            return
        sensors = getattr(device, "sensors", None)
        list_sensors = getattr(sensors, "list", None)
        refresh_sensors = getattr(sensors, "refresh", None)
        subscribe = getattr(sensors, "subscribe", None)
        if not callable(list_sensors) or not callable(subscribe):
            return
        device_connected = self._device_connected(device)
        try:
            now = time.monotonic()
            with self._lock:
                refresh_due = now >= self._sensor_next_refresh_at.get(alias, 0.0)
            # [UAV-IDE MODIFY] SensorHub caches discovery; refresh periodically so late Project AirSim topics appear.
            if callable(refresh_sensors) and (force or refresh_due):
                descriptors = refresh_sensors()
                with self._lock:
                    self._sensor_next_refresh_at[alias] = now + 5.0
            else:
                descriptors = list_sensors()
        except Exception as error:
            with self._lock:
                self._sensor_errors[alias + ":*"] = str(error)
            return
        descriptor_map = {
            str(getattr(descriptor, "sensor_id", "")): descriptor
            for descriptor in descriptors
            if str(getattr(descriptor, "sensor_id", ""))
        }
        with self._lock:
            self._sensor_descriptors[alias] = descriptor_map
            self._sensor_errors.pop(alias + ":*", None)
        desired: set[str] = set()
        for descriptor in descriptors:
            sensor_id = str(getattr(descriptor, "sensor_id", ""))
            kind = getattr(getattr(descriptor, "kind", None), "value", "")
            if not sensor_id or kind in ("rgb_camera", "depth_camera", "stereo_camera", "lidar"):
                continue
            key = alias + ":" + sensor_id
            desired.add(key)
            with self._lock:
                state_item = self._sensor_states.setdefault(key, {})
                state_item.update({"discovered": True, "subscribed": key in self._sensor_unsubscribers})
            # [UAV-IDE MODIFY] Only the kernel that owns an active device connection may subscribe.
            # Auto-initialized idle notebook kernels otherwise duplicate Project AirSim streams,
            # overload its callback queue, and can win HUD source selection with unrelated samples.
            if not device_connected:
                self._unsubscribe_sensor(key)
                with self._lock:
                    self._sensor_errors.pop(key, None)
                continue
            if key in self._sensor_unsubscribers:
                with self._lock:
                    cached = self._sensor_samples.get(key) or {}
                    subscribed_at = self._sensor_states.get(key, {}).get("subscribedAtMonotonicS")
                last_activity = cached.get("receivedAtMonotonicS") or subscribed_at
                if last_activity and time.monotonic() - float(last_activity) > 10.0:
                    # [UAV-IDE MODIFY] A replaced sensor-service socket does not retain native subscriptions; renew stale streams.
                    self._unsubscribe_sensor(key)
                else:
                    self._update_sensor_status(key, sensors, sensor_id)
                    continue

            with self._lock:
                generation = self._generation

            def on_sample(
                sample: Any,
                sample_key: str = key,
                expected_generation: int = generation,
            ) -> None:
                received_at = time.monotonic()
                with self._lock:
                    if expected_generation != self._generation:
                        return
                    self._sensor_samples[sample_key] = {
                        "data": self._sensor_value(sample),
                        "receivedAt": datetime.now(timezone.utc).isoformat(),
                        "receivedAtMonotonicS": received_at,
                    }
                    timestamps = self._sensor_received_times.setdefault(sample_key, deque(maxlen=32))
                    timestamps.append(received_at)
                    self._sensor_states.setdefault(sample_key, {}).update({
                        "discovered": True,
                        "subscribed": True,
                    })
                    self._sensor_errors.pop(sample_key, None)

            try:
                # [UAV-IDE MODIFY] Bound developer-panel sampling so IMU diagnostics do not overload the HUD bridge.
                options = SensorStreamOptions(max_rate_hz=10.0 if kind == "imu" else 5.0)
                unsubscribe = subscribe(sensor_id, on_sample, options=options)
            except TypeError:
                try:
                    unsubscribe = subscribe(sensor_id, on_sample)
                except Exception as error:
                    with self._lock:
                        self._sensor_errors[key] = str(error)
                        self._sensor_states.setdefault(key, {}).update({"subscribed": False})
                    continue
            except Exception as error:
                with self._lock:
                    self._sensor_errors[key] = str(error)
                    self._sensor_states.setdefault(key, {}).update({"subscribed": False})
                continue
            stale_subscription = False
            with self._lock:
                stale_subscription = (
                    self._sensor_stop.is_set()
                    or generation != self._generation
                )
                if not stale_subscription:
                    self._sensor_unsubscribers[key] = unsubscribe
                    self._sensor_states.setdefault(key, {}).update({
                        "subscribed": True,
                        "subscribedAtMonotonicS": time.monotonic(),
                    })
                    self._sensor_errors.pop(key, None)
            if stale_subscription:
                try:
                    unsubscribe()
                except Exception:
                    pass
                continue
            self._update_sensor_status(key, sensors, sensor_id)
        with self._lock:
            obsolete = [
                key
                for key in self._sensor_unsubscribers
                if key.startswith(alias + ":") and key not in desired
            ]
        for key in obsolete:
            self._unsubscribe_sensor(key)

    def _update_sensor_status(self, key: str, sensors: Any, sensor_id: str) -> None:
        status_method = getattr(sensors, "status", None)
        if not callable(status_method):
            return
        try:
            status = status_method(sensor_id)
        except Exception:
            return
        with self._lock:
            self._sensor_states.setdefault(key, {}).update({
                "subscriberCount": getattr(status, "subscriber_count", None),
                "receivedSamples": getattr(status, "received_samples", None),
                "deliveredSamples": getattr(status, "delivered_samples", None),
                "droppedSamples": getattr(status, "dropped_samples", None),
                "handlerErrorCount": getattr(status, "handler_error_count", None),
            })

    def _unsubscribe_sensor_alias(self, alias: str) -> None:
        with self._lock:
            keys = [key for key in self._sensor_unsubscribers if key.startswith(alias + ":")]
        for key in keys:
            self._unsubscribe_sensor(key)

    def _unsubscribe_sensor(self, key: str) -> None:
        with self._lock:
            unsubscribe = self._sensor_unsubscribers.pop(key, None)
            self._sensor_states.setdefault(key, {}).update({"subscribed": False})
        if unsubscribe is not None:
            try:
                unsubscribe()
            except Exception:
                pass

    @staticmethod
    def _hud_number(value: Any) -> float | None:
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _hud_vector(cls, value: Any) -> dict[str, float | None]:
        mapping = value if isinstance(value, Mapping) else {}
        return {
            "x": cls._hud_number(mapping.get("x")),
            "y": cls._hud_number(mapping.get("y")),
            "z": cls._hud_number(mapping.get("z")),
        }

    @staticmethod
    def _hud_first(primary: Any, fallback: Any) -> Any:
        return fallback if primary is None else primary

    @classmethod
    def _fresh_hud_group(
        cls,
        primary: Mapping[str, Any],
        primary_age_ms: Any,
        fallback: Mapping[str, Any],
        fallback_age_ms: Any,
    ) -> tuple[dict[str, Any], int | None]:
        keys = tuple(dict.fromkeys((*primary.keys(), *fallback.keys())))
        for values, age_ms in (
            (primary, primary_age_ms),
            (fallback, fallback_age_ms),
        ):
            if not isinstance(age_ms, (int, float)) or age_ms > cls._NAVIGATION_STALE_AFTER_MS:
                continue
            if any(values.get(key) is not None for key in keys):
                return ({key: values.get(key) for key in keys}, int(age_ms))
        return ({key: None for key in keys}, None)

    def _event_age_ms(self, alias: str, event_type: str) -> int | None:
        with self._lock:
            updated_at_s = (self._event_updated_at_s.get(alias) or {}).get(event_type)
        return self._age_ms(updated_at_s)

    @staticmethod
    def _device_connected(device: Any) -> bool:
        # [UAV-IDE MODIFY] The connection event is delivered asynchronously. Immediately
        # after a successful reconnect the adapter/lifecycle is authoritative even when
        # DeviceStateView has not consumed CONNECTION_CHANGED yet.
        state_view = getattr(device, "state", None)
        state_snapshot = getattr(state_view, "snapshot", None)
        state = state_snapshot() if callable(state_snapshot) else state_view
        adapter = getattr(device, "adapter", None)
        lifecycle = getattr(device, "lifecycle_state", None)
        lifecycle_value = getattr(lifecycle, "value", lifecycle)
        return (
            getattr(state, "connected", None) is True
            or getattr(adapter, "connected", None) is True
            or str(lifecycle_value or "").lower() == "connected"
        )

    def _sensor_hud_fallback(self, sensors: list[dict[str, Any]]) -> dict[str, Any]:
        # [UAV-IDE MODIFY] Backfill only simulator-authoritative observations when MAVSDK telemetry
        # is unavailable. Never infer pose from acceleration/magnetic field or invent a home origin
        # from the first GNSS sample; unavailable flight-state fields must remain None.
        by_type: dict[str, dict[str, Any]] = {}
        latest_age_ms: int | None = None
        latest_received_at = ""
        for item in sensors:
            sample = item.get("sample")
            if not isinstance(sample, Mapping) or item.get("stale") is True:
                continue
            kind = str(item.get("type") or sample.get("kind") or "")
            age_ms = item.get("sampleAgeMs")
            current = by_type.get(kind)
            current_age = current.get("sampleAgeMs") if current else None
            if current is None or (
                isinstance(age_ms, (int, float))
                and (not isinstance(current_age, (int, float)) or age_ms < current_age)
            ):
                by_type[kind] = item
            if isinstance(age_ms, (int, float)):
                latest_age_ms = int(age_ms) if latest_age_ms is None else min(latest_age_ms, int(age_ms))
            received_at = str(item.get("sampleReceivedAt") or "")
            if received_at > latest_received_at:
                latest_received_at = received_at

        gnss = dict((by_type.get("gnss") or {}).get("sample") or {})
        gnss_age_ms = (by_type.get("gnss") or {}).get("sampleAgeMs")
        pose = dict(
            (by_type.get("vendor.projectairsim.actual_pose") or {}).get("sample")
            or {}
        )
        pose_age_ms = (
            by_type.get("vendor.projectairsim.actual_pose") or {}
        ).get("sampleAgeMs")

        latitude = self._hud_number(gnss.get("latitude_deg"))
        longitude = self._hud_number(gnss.get("longitude_deg"))
        absolute_altitude = self._hud_number(gnss.get("absolute_altitude_m"))
        pose_values = pose.get("values")
        pose_values = pose_values if isinstance(pose_values, Mapping) else pose
        nested_pose = pose_values.get("pose")
        if isinstance(nested_pose, Mapping):
            pose_values = nested_pose
        translation = pose_values.get("translation") or pose_values.get("position")
        local = self._hud_vector(translation)

        rotation = pose_values.get("rotation") or pose_values.get("orientation")
        rotation = rotation if isinstance(rotation, Mapping) else {}
        quaternion = {
            axis: self._hud_number(rotation.get(axis))
            for axis in ("w", "x", "y", "z")
        }
        attitude = {"roll": None, "pitch": None, "yaw": None}
        if all(value is not None for value in quaternion.values()):
            w = quaternion["w"]
            x = quaternion["x"]
            y = quaternion["y"]
            z = quaternion["z"]
            attitude = {
                "roll": math.degrees(
                    math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
                ),
                "pitch": math.degrees(
                    math.asin(max(-1.0, min(1.0, 2.0 * (w * y - z * x))))
                ),
                "yaw": math.degrees(
                    math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
                ) % 360.0,
            }
        has_local_pose = all(value is not None for value in local.values())

        return {
            "telemetryAgeMs": latest_age_ms,
            "updatedAt": latest_received_at,
            "ages": {
                "global": gnss_age_ms,
                "local": pose_age_ms,
                "velocity": None,
                "attitude": pose_age_ms,
            },
            "global": {
                "lat": latitude,
                "lon": longitude,
                "absoluteAltitudeM": absolute_altitude,
                "relativeAltitudeM": None,
            },
            "home": {
                "lat": None,
                "lon": None,
                "absoluteAltitudeM": None,
            },
            "local": local,
            "velocity": {"x": None, "y": None, "z": None},
            "attitude": attitude,
            "health": {
                "localPositionOk": True if has_local_pose else None,
                "globalPositionOk": latitude is not None and longitude is not None,
                "homePositionOk": None,
            },
        }

    def _device_snapshot(self, alias: str, config: dict[str, Any], device: Any) -> dict[str, Any]:
        state_view = getattr(device, "state", None)
        state_snapshot = getattr(state_view, "snapshot", None)
        # [UAV-IDE MODIFY] Read the current SDK DeviceStateView snapshot while retaining compatibility with legacy direct state objects.
        state = state_snapshot() if callable(state_snapshot) else state_view
        connected = self._device_connected(device)
        last_event = self._last_events.get(alias, {})
        sensor_snapshot = self._sensor_snapshot(alias, device)
        sensor_fallback = self._sensor_hud_fallback(sensor_snapshot)
        global_fallback = sensor_fallback["global"]
        home_fallback = sensor_fallback["home"]
        local_fallback = sensor_fallback["local"]
        velocity_fallback = sensor_fallback["velocity"]
        attitude_fallback = sensor_fallback["attitude"]
        health_fallback = sensor_fallback["health"]
        fallback_ages = sensor_fallback["ages"]
        global_values, global_age_ms = self._fresh_hud_group(
            {
                "lat": self._value(state, "latitude_deg"),
                "lon": self._value(state, "longitude_deg"),
                "absoluteAltitudeM": self._value(state, "absolute_altitude_m"),
                "relativeAltitudeM": self._value(state, "relative_altitude_m"),
            },
            self._age_ms(self._value(state, "global_position_updated_at_s")),
            global_fallback,
            fallback_ages["global"],
        )
        local_values, local_age_ms = self._fresh_hud_group(
            {
                "x": self._value(state, "local_x_m"),
                "y": self._value(state, "local_y_m"),
                "z": self._value(state, "local_z_m"),
            },
            self._age_ms(self._value(state, "local_position_updated_at_s")),
            local_fallback,
            fallback_ages["local"],
        )
        velocity_values, velocity_age_ms = self._fresh_hud_group(
            {
                "x": self._value(state, "velocity_x_m_s"),
                "y": self._value(state, "velocity_y_m_s"),
                "z": self._value(state, "velocity_z_m_s"),
            },
            self._event_age_ms(alias, "motion.velocity.updated"),
            velocity_fallback,
            fallback_ages["velocity"],
        )
        attitude_values, attitude_age_ms = self._fresh_hud_group(
            {
                "roll": self._value(state, "roll_deg"),
                "pitch": self._value(state, "pitch_deg"),
                "yaw": self._value(state, "yaw_deg"),
            },
            self._event_age_ms(alias, "motion.attitude.updated"),
            attitude_fallback,
            fallback_ages["attitude"],
        )
        home_values = {
            "lat": self._hud_first(self._value(state, "home_latitude_deg"), home_fallback["lat"]),
            "lon": self._hud_first(self._value(state, "home_longitude_deg"), home_fallback["lon"]),
            "absoluteAltitudeM": self._hud_first(
                self._value(state, "home_absolute_altitude_m"),
                home_fallback["absoluteAltitudeM"],
            ),
        }
        if (
            global_values["relativeAltitudeM"] is None
            and global_values["absoluteAltitudeM"] is not None
            and home_values["absoluteAltitudeM"] is not None
        ):
            global_values["relativeAltitudeM"] = (
                global_values["absoluteAltitudeM"] - home_values["absoluteAltitudeM"]
            )
        navigation_ages = [
            age_ms
            for age_ms in (
                global_age_ms,
                local_age_ms,
                velocity_age_ms,
                attitude_age_ms,
            )
            if age_ms is not None
        ]
        # [UAV-IDE MODIFY] AGE represents the oldest navigation group displayed,
        # rather than an unrelated fresh battery or state event.
        telemetry_age_ms = max(navigation_ages) if navigation_ages else None
        if not connected:
            # [UAV-IDE MODIFY] Sensor discovery may survive a disconnect, but
            # cached samples and state defaults are not live vehicle telemetry.
            telemetry_age_ms = None
            sensor_snapshot = [
                {
                    **item,
                    "sample": None,
                    "sampleAgeMs": None,
                    "actualRateHz": None,
                }
                for item in sensor_snapshot
            ]
        with self._lock:
            event_times = tuple(self._event_received_times.get(alias) or ())
            command_history = [
                item for item in self._command_history
                if item.get("deviceAlias") == alias
            ][-20:]
        message_rate_hz = None
        if len(event_times) >= 2 and event_times[-1] > event_times[0]:
            message_rate_hz = round(
                (len(event_times) - 1) / (event_times[-1] - event_times[0]),
                2,
            )
        return {
            "alias": alias,
            "deviceId": config.get("device_id", ""),
            "name": config.get("name", alias),
            "product": config.get("product", ""),
            "builder": config.get("builder", ""),
            "metadata": config.get("metadata") or {},
            # [UAV-IDE MODIFY] State which process owns the live physical link;
            # the IDE HUD observes this bridge and never opens a second link.
            "connected": connected,
            "connectionOwner": "sdk-runtime" if connected else "none",
            "ownerPid": os.getpid() if connected else None,
            "armed": self._value(state, "armed") if connected else None,
            "inAir": self._value(state, "in_air") if connected else None,
            "landedState": self._value(state, "landed_state") if connected else None,
            "flightMode": self._value(state, "flight_mode") if connected else None,
            "failsafe": self._value(state, "failsafe") if connected else None,
            "telemetryAgeMs": telemetry_age_ms,
            "link": {
                "messageRateHz": message_rate_hz if connected else None,
                "telemetryAgeMs": telemetry_age_ms,
                "packetLossPercent": None,
                "latencyMs": (
                    command_history[-1].get("durationMs")
                    if connected and command_history else None
                ),
                "qualitySource": "sdk-events",
            },
            "batteryRemaining": (
                self._value(state, "battery_remaining_fraction")
                if connected else None
            ),
            "global": global_values if connected else {key: None for key in global_values},
            "home": home_values if connected else {key: None for key in home_values},
            "local": local_values if connected else {key: None for key in local_values},
            "velocity": velocity_values if connected else {key: None for key in velocity_values},
            "attitude": attitude_values if connected else {key: None for key in attitude_values},
            "health": {
                "localPositionOk": self._hud_first(
                    self._value(state, "health_local_position_ok"),
                    health_fallback["localPositionOk"],
                ) if connected else None,
                "globalPositionOk": self._hud_first(
                    self._value(state, "health_global_position_ok"),
                    health_fallback["globalPositionOk"],
                ) if connected else None,
                "homePositionOk": self._hud_first(
                    self._value(state, "health_home_position_ok"),
                    health_fallback["homePositionOk"],
                ) if connected else None,
                "armable": self._value(state, "health_armable") if connected else None,
            },
            "sensorError": self._sensor_errors.get(alias + ":*", ""),
            "sensors": sensor_snapshot,
            "lastEvent": last_event,
            "recentCommands": command_history,
            "updatedAt": max(last_event.get("receivedAt") or "", sensor_fallback.get("updatedAt") or ""),
        }

    @staticmethod
    def _value(source: Any, name: str) -> Any:
        return getattr(source, name, None) if source is not None else None

    @staticmethod
    def _age_ms(updated_at_s: Any) -> Any:
        try:
            return max(0, round((time.monotonic() - float(updated_at_s)) * 1000))
        except (TypeError, ValueError):
            return None

    def _sensor_snapshot(self, alias: str, device: Any) -> list[dict[str, Any]]:
        with self._lock:
            descriptors = tuple(self._sensor_descriptors.get(alias, {}).values())
        if not descriptors:
            sensors = getattr(device, "sensors", None)
            list_sensors = getattr(sensors, "list", None)
            if not callable(list_sensors):
                return []
            try:
                descriptors = tuple(list_sensors())
            except Exception:
                return []
        result = []
        for descriptor in descriptors:
            kind = getattr(descriptor, "kind", None)
            source = getattr(descriptor, "source", None)
            sensor_id = str(getattr(descriptor, "sensor_id", ""))
            key = alias + ":" + sensor_id
            with self._lock:
                cached = dict(self._sensor_samples.get(key) or {})
                stream_state = dict(self._sensor_states.get(key) or {})
                timestamps = tuple(self._sensor_received_times.get(key) or ())
                stream_error = self._sensor_errors.get(key, "") or self._sensor_errors.get(alias + ":*", "")
            sample_age_ms = self._age_ms(cached.get("receivedAtMonotonicS"))
            actual_rate_hz = None
            if len(timestamps) >= 2 and timestamps[-1] > timestamps[0]:
                actual_rate_hz = round((len(timestamps) - 1) / (timestamps[-1] - timestamps[0]), 2)
            expected_rate_hz = getattr(descriptor, "nominal_rate_hz", None)
            stale_after_ms = max(
                3000,
                round(3000 / float(expected_rate_hz)) if expected_rate_hz else 10000,
            )
            result.append({
                "id": sensor_id,
                "type": getattr(kind, "value", str(kind or "")),
                "source": getattr(source, "value", str(source or "")),
                "primary": bool(getattr(descriptor, "is_primary", False)),
                "rateHz": expected_rate_hz,
                "actualRateHz": actual_rate_hz,
                "discovered": True,
                "subscribed": bool(stream_state.get("subscribed", False)),
                "subscriberCount": stream_state.get("subscriberCount"),
                "receivedSamples": stream_state.get("receivedSamples"),
                "deliveredSamples": stream_state.get("deliveredSamples"),
                "droppedSamples": stream_state.get("droppedSamples"),
                "handlerErrorCount": stream_state.get("handlerErrorCount"),
                "sample": cached.get("data"),
                "sampleAgeMs": sample_age_ms,
                "stale": sample_age_ms is not None and sample_age_ms > stale_after_ms,
                "sampleReceivedAt": cached.get("receivedAt", ""),
                "streamError": stream_error,
            })
        return result

    @classmethod
    def _sensor_value(cls, value: Any, depth: int = 0) -> Any:
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if value is None or isinstance(value, (bool, int, str)):
            return value
        enum_value = getattr(value, "value", None)
        if isinstance(enum_value, (bool, int, float, str)):
            return enum_value
        if isinstance(value, (bytes, bytearray, memoryview)):
            return {"byteLength": len(value)}
        if depth >= 4:
            return str(value)
        if is_dataclass(value):
            return {
                field.name: cls._sensor_value(getattr(value, field.name), depth + 1)
                for field in fields(value)
                if field.name != "native_data"
            }
        if isinstance(value, Mapping):
            return {
                str(key): cls._sensor_value(item, depth + 1)
                for key, item in list(value.items())[:32]
            }
        if isinstance(value, (list, tuple)):
            return [cls._sensor_value(item, depth + 1) for item in value[:32]]
        if hasattr(value, "isoformat"):
            try:
                return value.isoformat()
            except Exception:
                pass
        return str(value)

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        # [UAV-IDE MODIFY] Keep the HUD transport strict-JSON even if an SDK object
        # bypasses _sensor_value and contains NaN or infinity.
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if value is None or isinstance(value, (bool, int, str)):
            return value
        if isinstance(value, Mapping):
            return {
                str(key): cls._json_safe(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        return value


class _FleetDockDevices:
${deviceAttributes}

    def __dir__(self) -> list[str]:
        return sorted(set(super().__dir__()) | {config["alias"] for config in _DEVICE_CONFIGS})


class _FleetDockCore:
    def plan(self, target: Any, **kwargs: Any):
        _FleetDockRuntime.init()
        return Plan(target, **kwargs)

    def fleet(self, *devices: BaseDevice, name: str | None = None) -> Fleet:
        _FleetDockRuntime.init()
        selected = tuple(devices)
        if not selected:
            raise ValueError("FleetDock.core.fleet requires at least one device.")
        if any(device is None for device in selected):
            raise ValueError("FleetDock.core.fleet received an empty device.")
        if len({id(device) for device in selected}) != len(selected):
            raise ValueError("FleetDock.core.fleet received duplicate devices.")
        return Fleet(selected, name=name)


class _FleetDockRuntime:
    devices = _FleetDockDevices()
    envs = _FleetDockEnvs()
    core = _FleetDockCore()
    hud = _FleetDockHudBridge()
    _initialized = False
    _device_names: tuple[str, ...] = ()

    @classmethod
    def init(cls):
        if cls._initialized:
            return cls
        if _BINDING_ERRORS:
            raise RuntimeError("FleetDock device binding errors:\\n- " + "\\n- ".join(_BINDING_ERRORS))

        factory = DeviceFactory.create()
        names: list[str] = []
        for config in _DEVICE_CONFIGS:
            builder_name = config["builder"]
            builder_factory = getattr(factory, builder_name)
            builder = builder_factory()
            firmware = config.get("firmware")
            if firmware:
                builder = builder.firmware(firmware)
            params = {key: value for key, value in config.get("params", {}).items() if value is not None}
            if isinstance(params.get("camera_stream_source"), str):
                params["camera_stream_source"] = SensorSourceKind(params["camera_stream_source"])
            builder = builder.params(**params)
            metadata = DeviceMetadata(
                device_id=config.get("device_id"),
                name=config.get("name"),
                extra=config.get("metadata") or {},
            )
            device = builder.metadata(metadata).build()
            airsim_vehicle_name = str((config.get("metadata") or {}).get("airsimVehicleName") or "")
            if airsim_vehicle_name and (config.get("metadata") or {}).get("source") == "simulation":
                # [UAV-IDE MODIFY] The proxy transports SDK-standard samples; Project AirSim adaptation stays in the SDK service.
                device.sensors.register_backend(
                    IdeSimulationSensorProxy(
                        vehicle_name=airsim_vehicle_name,
                        manifest_path=os.getenv("FLEETDOCK_SIMULATION_MANIFEST", ""),
                        excluded_kinds=("rgb_camera",) if params.get("camera_stream_uri") else (),
                    )
                )
            setattr(cls.devices, config["alias"], device)
            names.append(config["alias"])

        cls._device_names = tuple(names)
        cls._initialized = True
        cls.hud.start(cls)
        return cls

    @classmethod
    def info(cls) -> dict[str, Any]:
        return {
            "initialized": cls._initialized,
            "devices": cls._device_names,
            "runtime_path": __file__,
            "sdk_root": os.getenv("FLEETDOCK_SDK_ROOT", ""),
            "binding_errors": tuple(_BINDING_ERRORS),
        }

    @classmethod
    def close(cls) -> None:
        cls.hud.stop()
        for name in cls._device_names:
            device = getattr(cls.devices, name, None)
            close = getattr(device, "close", None)
            if callable(close):
                close()
        cls.devices = _FleetDockDevices()
        cls._device_names = ()
        cls._initialized = False


FleetDock = _FleetDockRuntime
atexit.register(FleetDock.close)


if os.getenv("FLEETDOCK_AUTO_INIT", "1") != "0":
    FleetDock.init()
`;
}

function renderRuntimeStub(devices: RuntimeDeviceConfig[]): string {
  return `# [UAV-IDE MODIFY] Generated FleetDock runtime type hints for this FleetDock IDEA application.
# This file is regenerated by FleetDock IDEA when Drone environment variables change.

from __future__ import annotations

from typing import Any, overload

from fleetdock import BaseDevice, DevicePlan, Drone, DronePlan, Fleet, FleetPlan, RobotDog, RobotDogPlan


class _FleetDockEnvs:
    def get(self, name: str, default: Any = None) -> Any: ...
    def require(self, name: str) -> str: ...
    def all(self) -> dict[str, str]: ...


class _FleetDockDevices:
${renderDeviceAttributes(devices)}


class _FleetDockCore:
    @overload
    def plan(self, target: Drone, **kwargs: Any) -> DronePlan: ...
    @overload
    def plan(self, target: RobotDog, **kwargs: Any) -> RobotDogPlan: ...
    @overload
    def plan(self, target: Fleet, **kwargs: Any) -> FleetPlan: ...
    @overload
    def plan(self, target: BaseDevice, **kwargs: Any) -> DevicePlan: ...
    def fleet(self, *devices: BaseDevice, name: str | None = None) -> Fleet: ...


class _FleetDockRuntime:
    devices: _FleetDockDevices
    envs: _FleetDockEnvs
    core: _FleetDockCore

    @classmethod
    def init(cls) -> type[_FleetDockRuntime]: ...

    @classmethod
    def info(cls) -> dict[str, Any]: ...

    @classmethod
    def close(cls) -> None: ...


FleetDock: type[_FleetDockRuntime]
`;
}

function renderDeviceAttributes(devices: RuntimeDeviceConfig[]): string {
  if (!devices.length) {
    return '    pass';
  }
  return devices
    .map(device => `    ${device.alias}: ${pythonDeviceType(device)}`)
    .join('\n');
}

function pythonDeviceType(device: RuntimeDeviceConfig): string {
  if (device.builder === 'unitree_go2' || device.builder === 'unitree_g1') {
    return 'RobotDog';
  }
  if (
    device.builder === 'px4_generic' ||
    device.builder === 'dji_matrice_350_rtk' ||
    device.builder === 'dji_mavic_3_enterprise'
  ) {
    return 'Drone';
  }
  return 'BaseDevice';
}

function renderStartup(
  workingDirectory: string,
  devices: RuntimeDeviceConfig[]
): string {
  const endpoints = getMavsdkEndpoints(devices);
  return `# [UAV-IDE MODIFY] Ensure every FleetDock IDEA notebook kernel initializes FleetDock before user code.
import os
import sys
import json
import subprocess

app_root = os.getenv("FLEETDOCK_APP_ROOT", ${JSON.stringify(workingDirectory)})
runtime_root = os.getenv("FLEETDOCK_IDE_RUNTIME_ROOT", "")
sdk_root = os.getenv("FLEETDOCK_SDK_ROOT", "")
for import_root in (app_root, runtime_root, sdk_root):
    if import_root and import_root not in sys.path:
        sys.path.insert(0, import_root)

_FLEETDOCK_MAVSDK_ENDPOINTS = json.loads(${JSON.stringify(
    JSON.stringify(endpoints)
  )})


def _cleanup_stale_mavsdk_servers() -> None:
    if os.name != "nt" or os.getenv("FLEETDOCK_CLEAN_STALE_MAVSDK", "1") == "0":
        return
    if not _FLEETDOCK_MAVSDK_ENDPOINTS:
        return
    script = r"""
$endpoints = ConvertFrom-Json @'
__FLEETDOCK_ENDPOINTS__
'@
$servers = Get-CimInstance Win32_Process -Filter "Name = 'mavsdk_server.exe'" | Where-Object {
  $cmd = [string]$_.CommandLine
  foreach ($endpoint in $endpoints) {
    $pattern = '(?i)(?:^|[\\s"''])' + [regex]::Escape([string]$endpoint) + '(?=$|[\\s"''])'
    if ([regex]::IsMatch($cmd, $pattern)) { return $true }
  }
  return $false
}
foreach ($server in $servers) {
  try { Stop-Process -Id $server.ProcessId -Force -ErrorAction Stop } catch {}
}
""".replace("__FLEETDOCK_ENDPOINTS__", json.dumps(_FLEETDOCK_MAVSDK_ENDPOINTS))
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            check=False,
            timeout=5,
            creationflags=creationflags,
        )
    except Exception:
        pass


_cleanup_stale_mavsdk_servers()

from fleetdock_runtime import FleetDock

FleetDock.init()
`;
}

function buildFleetDockRuntimeEnv(
  workingDirectory: string,
  runtimePath: string
): { [key: string]: string } {
  const runtimeRoot = resolveFleetDockRuntimeRoot();
  return {
    FLEETDOCK_APP_ROOT: workingDirectory,
    // [UAV-IDE MODIFY] Let the generated FleetDock runtime publish read-only HUD telemetry for the current workspace.
    FLEETDOCK_HUD_ENABLED: '1',
    FLEETDOCK_HUD_HOST: '127.0.0.1',
    FLEETDOCK_HUD_PORT: String(fleetDockHudPort(workingDirectory)),
    FLEETDOCK_HUD_TOKEN: fleetDockHudToken(workingDirectory),
    FLEETDOCK_RUNTIME_PATH: runtimePath,
    FLEETDOCK_IDE_RUNTIME_ROOT: runtimeRoot,
    FLEETDOCK_SIMULATION_MANIFEST: path.join(
      workingDirectory,
      '.jupyter',
      'simulation',
      'runtime.json'
    ),
    FLEETDOCK_SDK_ROOT: resolveFleetDockSdkRoot(),
    IPYTHONDIR: path.join(workingDirectory, '.jupyter', 'ipython'),
    PYTHONSTARTUP: path.join(
      workingDirectory,
      '.jupyter',
      'ipython',
      'profile_default',
      'startup',
      '00-skylab-fleetdock.py'
    )
  };
}

function fleetDockHudPort(workingDirectory: string): number {
  const digest = crypto
    .createHash('sha256')
    .update(path.resolve(workingDirectory))
    .digest();
  return 17500 + (digest.readUInt16BE(0) % 1000);
}

function fleetDockHudToken(workingDirectory: string): string {
  return crypto
    .createHash('sha256')
    .update(`skylab-fleetdock-hud:${path.resolve(workingDirectory)}`)
    .digest('hex')
    .slice(0, 32);
}

function resolveFleetDockSdkRoot(): string {
  return path.join(resolveFleetDockRuntimeRoot(), 'fleetdock-src');
}

function resolveFleetDockRuntimeRoot(): string {
  const appDir = getAppDir();
  const candidates = [
    path.join(appDir, 'dist-resources', 'skylab-runtime'),
    path.join(appDir, 'skylab-runtime'),
    path.join(process.cwd(), 'dist-resources', 'skylab-runtime')
  ];
  return (
    candidates.find(candidate =>
      fs.existsSync(path.join(candidate, 'fleetdock_ide_runtime'))
    ) || candidates[0]
  );
}

function prependPath(value: string, existing?: string): string {
  const parts = String(existing || '')
    .split(path.delimiter)
    .map(item => item.trim())
    .filter(Boolean);
  return [
    value,
    ...parts.filter(item => path.resolve(item) !== path.resolve(value))
  ].join(path.delimiter);
}

function writeTextIfChanged(filePath: string, content: string): void {
  const dirPath = path.dirname(filePath);
  if (!fs.existsSync(dirPath)) {
    fs.mkdirSync(dirPath, { recursive: true });
  }
  if (
    fs.existsSync(filePath) &&
    fs.readFileSync(filePath, 'utf8') === content
  ) {
    return;
  }
  fs.writeFileSync(filePath, content, 'utf8');
}
