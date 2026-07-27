// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Build and validate canonical MAVSDK v3 connection URIs for physical PX4 devices.

import {
  DroneDevice,
  PhysicalDeviceConnection,
  TelemetryConfig
} from './types';

const DEFAULT_PHYSICAL_HOST = '0.0.0.0';
const DEFAULT_PHYSICAL_PORT = 14560;
const DEFAULT_SERIAL_PORT = 'COM3';
// [UAV-IDE MODIFY] Match the common PX4 telemetry-radio default used by the physical-device form.
const DEFAULT_SERIAL_BAUD_RATE = 57600;

export function buildPhysicalConnectionURI(
  connection: PhysicalDeviceConnection
): string {
  const normalized = validatePhysicalDeviceConnection(connection);
  if (normalized.kind === 'serial') {
    return `serial://${normalized.serialPort}:${normalized.baudRate}`;
  }
  const direction = normalized.mode === 'listen' ? 'in' : 'out';
  return `${normalized.kind}${direction}://${normalized.host}:${normalized.port}`;
}

export function validatePhysicalDeviceConnection(
  connection: PhysicalDeviceConnection
): PhysicalDeviceConnection {
  if (connection.kind === 'serial') {
    const serialPort = String(connection.serialPort || '')
      .trim()
      .toUpperCase();
    const baudRate = Number(connection.baudRate);
    if (!/^COM(?:[1-9]\d*)$/.test(serialPort)) {
      throw new Error('串口必须使用 COM1、COM2 等 Windows COM 端口格式。');
    }
    if (!Number.isInteger(baudRate) || baudRate <= 0) {
      throw new Error('串口波特率必须是正整数。');
    }
    return { kind: 'serial', serialPort, baudRate };
  }

  const host = String(connection.host || '').trim();
  const port = Number(connection.port);
  if (!host) {
    throw new Error(
      connection.mode === 'listen'
        ? '请填写本机监听 IP。'
        : '请填写远端设备 IP。'
    );
  }
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error('网络端口必须是 1 到 65535 之间的整数。');
  }
  return {
    kind: connection.kind === 'tcp' ? 'tcp' : 'udp',
    mode: connection.mode === 'dial' ? 'dial' : 'listen',
    host,
    port
  };
}

export function normalizePhysicalDeviceConnection(
  value: Partial<DroneDevice>
): PhysicalDeviceConnection {
  const canonical = value.real?.connection;
  if (canonical) {
    return validatePhysicalDeviceConnection(canonical);
  }

  const endpoint = parsePhysicalConnectionURI(
    String(value.fleetdock?.params?.endpoint || '')
  );
  const connectionKind = value.real?.connectionKind;
  if (connectionKind === 'serial' || endpoint?.kind === 'serial') {
    return validatePhysicalDeviceConnection({
      kind: 'serial',
      serialPort:
        value.real?.serialPort ||
        (endpoint?.kind === 'serial'
          ? endpoint.serialPort
          : DEFAULT_SERIAL_PORT),
      baudRate:
        value.real?.baudRate ||
        (endpoint?.kind === 'serial'
          ? endpoint.baudRate
          : DEFAULT_SERIAL_BAUD_RATE)
    });
  }

  const telemetry = (value.telemetry || {}) as Partial<TelemetryConfig>;
  const endpointNetwork = endpoint || undefined;
  const kind =
    connectionKind === 'tcp' || telemetry.protocol === 'tcp'
      ? 'tcp'
      : endpointNetwork?.kind || 'udp';
  const mode = telemetry.mode || endpointNetwork?.mode || 'listen';
  const host =
    (mode === 'listen' ? telemetry.bindHost : telemetry.remoteHost) ||
    value.real?.host ||
    endpointNetwork?.host ||
    (mode === 'listen' ? DEFAULT_PHYSICAL_HOST : '127.0.0.1');
  const port = Number(
    (mode === 'listen' ? telemetry.bindPort : telemetry.remotePort) ||
      value.real?.port ||
      endpointNetwork?.port ||
      DEFAULT_PHYSICAL_PORT
  );
  return validatePhysicalDeviceConnection({ kind, mode, host, port });
}

export function parsePhysicalConnectionURI(
  value: string
): PhysicalDeviceConnection | null {
  const text = String(value || '').trim();
  const serialMatch = text.match(/^serial:\/\/(COM[1-9]\d*):(\d+)$/i);
  if (serialMatch) {
    return {
      kind: 'serial',
      serialPort: serialMatch[1].toUpperCase(),
      baudRate: Number(serialMatch[2])
    };
  }
  const networkMatch = text.match(/^(udp|tcp)(in|out):\/\/([^\s/:]+):(\d+)$/i);
  if (!networkMatch) {
    return null;
  }
  return {
    kind: networkMatch[1].toLowerCase() === 'tcp' ? 'tcp' : 'udp',
    mode: networkMatch[2].toLowerCase() === 'out' ? 'dial' : 'listen',
    host: networkMatch[3],
    port: Number(networkMatch[4])
  };
}

export function buildConnectionURI(t: TelemetryConfig): string {
  return buildPhysicalConnectionURI({
    kind: t.protocol,
    mode: t.mode,
    host:
      t.mode === 'listen'
        ? t.bindHost || DEFAULT_PHYSICAL_HOST
        : t.remoteHost || '127.0.0.1',
    port: Number(
      t.mode === 'listen'
        ? t.bindPort || DEFAULT_PHYSICAL_PORT
        : t.remotePort || DEFAULT_PHYSICAL_PORT
    )
  });
}
