// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Discover Windows COM devices without opening ports or adding a native serial dependency.

import { execFile } from 'child_process';

export interface IDiscoveredSerialPort {
  port: string;
  name: string;
  manufacturer?: string;
  pnpDeviceId?: string;
}

export interface ISerialPortDiscoveryResult {
  ports: IDiscoveredSerialPort[];
  error?: string;
}

const DISCOVERY_SCRIPT = String.raw`
$items = @()

Get-CimInstance Win32_SerialPort -ErrorAction SilentlyContinue | ForEach-Object {
  $items += [pscustomobject]@{
    port = [string]$_.DeviceID
    name = [string]$_.Name
    manufacturer = [string]$_.Manufacturer
    pnpDeviceId = [string]$_.PNPDeviceID
  }
}

Get-CimInstance Win32_PnPEntity -Filter "Name LIKE '%(COM%'" -ErrorAction SilentlyContinue | ForEach-Object {
  $match = [regex]::Match([string]$_.Name, '\((COM[1-9][0-9]*)\)', 'IgnoreCase')
  if ($match.Success) {
    $items += [pscustomobject]@{
      port = $match.Groups[1].Value.ToUpperInvariant()
      name = [string]$_.Name
      manufacturer = [string]$_.Manufacturer
      pnpDeviceId = [string]$_.PNPDeviceID
    }
  }
}

[System.IO.Ports.SerialPort]::GetPortNames() | ForEach-Object {
  $items += [pscustomobject]@{
    port = [string]$_
    name = [string]$_
    manufacturer = ''
    pnpDeviceId = ''
  }
}

$items | ConvertTo-Json -Compress
`;

export async function discoverWindowsSerialPorts(): Promise<
  ISerialPortDiscoveryResult
> {
  if (process.platform !== 'win32') {
    return { ports: [], error: '串口自动检测当前仅支持 Windows。' };
  }

  return new Promise(resolve => {
    execFile(
      'powershell.exe',
      [
        '-NoProfile',
        '-NonInteractive',
        '-ExecutionPolicy',
        'Bypass',
        '-Command',
        DISCOVERY_SCRIPT
      ],
      { timeout: 5000, windowsHide: true, maxBuffer: 1024 * 1024 },
      (error, stdout, stderr) => {
        if (error) {
          resolve({
            ports: [],
            error:
              String(stderr || error.message || '').trim() ||
              'Windows 串口检测失败。'
          });
          return;
        }
        resolve({
          ports: parseSerialPortDiscoveryOutput(String(stdout || ''))
        });
      }
    );
  });
}

export function parseSerialPortDiscoveryOutput(
  output: string
): IDiscoveredSerialPort[] {
  const text = String(output || '')
    .replace(/^\uFEFF/, '')
    .trim();
  if (!text) {
    return [];
  }

  try {
    const parsed = JSON.parse(text);
    const rows = Array.isArray(parsed) ? parsed : [parsed];
    const byPort = new Map<string, IDiscoveredSerialPort>();
    for (const row of rows) {
      const portMatch = String(row?.port || '').match(/^COM([1-9]\d*)$/i);
      if (!portMatch) {
        continue;
      }
      const port = `COM${Number(portMatch[1])}`;
      const current = byPort.get(port);
      const candidate: IDiscoveredSerialPort = {
        port,
        name: String(row?.name || port).trim() || port,
        manufacturer: String(row?.manufacturer || '').trim() || undefined,
        pnpDeviceId: String(row?.pnpDeviceId || '').trim() || undefined
      };
      // [UAV-IDE MODIFY] Prefer the richer PnP/CIM record over the .NET name-only fallback.
      if (!current || candidate.name !== port) {
        byPort.set(port, candidate);
      }
    }
    return Array.from(byPort.values()).sort(
      (left, right) => Number(left.port.slice(3)) - Number(right.port.slice(3))
    );
  } catch (error) {
    return [];
  }
}
