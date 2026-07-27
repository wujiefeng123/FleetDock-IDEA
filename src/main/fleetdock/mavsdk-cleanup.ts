// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Clean only FleetDock MAVSDK servers bound to the active application's device endpoints.

import { execFileSync } from 'child_process';

export interface IFleetDockMavsdkServerProcess {
  pid: number;
  endpoint: string;
  commandLine: string;
}

export function cleanupFleetDockMavsdkServers(
  endpoints: string[],
  knownServers?: IFleetDockMavsdkServerProcess[]
): void {
  // [UAV-IDE MODIFY] Reuse the confirmation scan during simulation stop instead of enumerating CIM twice.
  const servers = knownServers || listFleetDockMavsdkServers(endpoints);
  if (!servers.length) {
    return;
  }

  const processIds = servers.map(server => server.pid);
  const script = `
$processIds = ConvertFrom-Json @'
${JSON.stringify(processIds)}
'@
foreach ($processId in $processIds) {
  try { Stop-Process -Id $processId -Force -ErrorAction Stop } catch {}
}
`;

  try {
    execFileSync(
      'powershell.exe',
      ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', script],
      { windowsHide: true, stdio: 'ignore' }
    );
  } catch (error) {
    // Best-effort cleanup: the generated Python startup hook will try again for new kernels.
  }
}

// [UAV-IDE MODIFY] A temporary hardware probe owns a unique gRPC port, so
// cleanup must match that port instead of an endpoint another app may use.
export function cleanupFleetDockMavsdkServersByGrpcPort(ports: number[]): void {
  if (process.platform !== 'win32') {
    return;
  }
  const normalizedPorts = Array.from(
    new Set(ports.filter(port => Number.isInteger(port) && port > 0))
  );
  if (!normalizedPorts.length) {
    return;
  }
  const script = `
$ports = ConvertFrom-Json @'
${JSON.stringify(normalizedPorts)}
'@
$servers = Get-CimInstance Win32_Process -Filter "Name = 'mavsdk_server.exe'"
foreach ($server in $servers) {
  $cmd = [string]$server.CommandLine
  foreach ($port in $ports) {
    $pattern = '(?i)(?:^|[\\s"''])-p[\\s"'']+' + [regex]::Escape([string]$port) + '(?=$|[\\s"''])'
    if ([regex]::IsMatch($cmd, $pattern)) {
      try { Stop-Process -Id $server.ProcessId -Force -ErrorAction Stop } catch {}
      break
    }
  }
}
`;
  try {
    execFileSync(
      'powershell.exe',
      ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', script],
      { windowsHide: true, stdio: 'ignore' }
    );
  } catch (error) {
    // Best-effort cleanup for a probe-owned child process.
  }
}

export function listFleetDockMavsdkServers(
  endpoints: string[]
): IFleetDockMavsdkServerProcess[] {
  if (process.platform !== 'win32') {
    return [];
  }

  const normalizedEndpoints = normalizeEndpoints(endpoints);
  if (!normalizedEndpoints.length) {
    return [];
  }

  const script = `
$endpoints = ConvertFrom-Json @'
${JSON.stringify(normalizedEndpoints)}
'@
$matches = @()
$servers = Get-CimInstance Win32_Process -Filter "Name = 'mavsdk_server.exe'"
foreach ($server in $servers) {
  $cmd = [string]$server.CommandLine
  foreach ($endpoint in $endpoints) {
    $pattern = '(?i)(?:^|[\\s"''])' + [regex]::Escape([string]$endpoint) + '(?=$|[\\s"''])'
    if ([regex]::IsMatch($cmd, $pattern)) {
      $matches += [pscustomobject]@{
        pid = $server.ProcessId
        endpoint = [string]$endpoint
        commandLine = $cmd
      }
      break
    }
  }
}
$matches | ConvertTo-Json -Compress
`;

  try {
    const output = execFileSync(
      'powershell.exe',
      ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', script],
      {
        encoding: 'utf8',
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'ignore']
      }
    ).trim();
    if (!output) {
      return [];
    }
    const parsed = JSON.parse(output);
    return (Array.isArray(parsed) ? parsed : [parsed])
      .map(item => ({
        pid: Number(item?.pid),
        endpoint: String(item?.endpoint || ''),
        commandLine: String(item?.commandLine || '')
      }))
      .filter(item => Number.isInteger(item.pid) && item.endpoint);
  } catch (error) {
    return [];
  }
}

function normalizeEndpoints(endpoints: string[]): string[] {
  return Array.from(
    new Set(
      endpoints
        .map(endpoint => String(endpoint || '').trim())
        // [UAV-IDE MODIFY] Match only complete application-owned UDP/TCP/serial endpoints.
        .filter(endpoint =>
          /^(?:udp|tcp)(?:in|out):\/\/|^serial:\/\//.test(endpoint)
        )
    )
  );
}
