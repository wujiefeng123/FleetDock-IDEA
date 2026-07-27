// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import { ChildProcess, execFile, spawn } from 'child_process';
import { randomBytes, randomUUID } from 'crypto';
import * as dgram from 'dgram';
import * as fs from 'fs';
import * as http from 'http';
import * as net from 'net';
import * as path from 'path';
import { getAppDir } from '../../utils';
import {
  UavSimulationCameraStream,
  UavSimulationMediaServiceStatus,
  UavSimulationWebRtcSessionResult
} from '../types';

const MEDIAMTX_VERSION = '1.19.2';
const MAX_SDP_BYTES = 256 * 1024;

export interface IFleetDockRtspStream extends UavSimulationCameraStream {
  rtspUri: string;
}

type ManagedStream = IFleetDockRtspStream & { mediaPath: string };
type ManagedSession = { url: URL; etag: string };

// [UAV-IDE MODIFY] Own one loopback MediaMTX gateway per simulation run; video never crosses Electron IPC.
export class MediaMtxService {
  constructor(private _log: (message: string) => Promise<void>) {}

  status(): UavSimulationMediaServiceStatus {
    return {
      ...this._status,
      streams: this.catalog()
    };
  }

  catalog(): UavSimulationCameraStream[] {
    return Array.from(this._streams.values()).map(stream => ({
      id: stream.id,
      vehicleName: stream.vehicleName,
      sensorId: stream.sensorId,
      label: stream.label
    }));
  }

  async start(
    runDirectory: string,
    streams: IFleetDockRtspStream[]
  ): Promise<void> {
    await this.stop();
    if (!streams.length) {
      this._status = {
        available: false,
        state: 'idle',
        version: MEDIAMTX_VERSION,
        streams: [],
        message: 'No FleetDock RTSP camera streams were discovered.'
      };
      return;
    }
    this._status = {
      available: false,
      state: 'starting',
      version: MEDIAMTX_VERSION,
      streams: [],
      message: 'Starting MediaMTX WebRTC gateway.'
    };
    try {
      const executable = this._resolveExecutable();
      const [webrtcPort, iceTcpPort, rtspPort] = await this._freeTcpPorts(3);
      const iceUdpPort = await this._freeUdpPort();
      this._webrtcPort = webrtcPort;
      this._username = 'fleetdock';
      this._password = randomBytes(32).toString('hex');
      const pathNonce = randomBytes(8).toString('hex');
      this._streams = new Map(
        streams.map((stream, index) => [
          stream.id,
          { ...stream, mediaPath: `fd_${pathNonce}_${index + 1}` }
        ])
      );
      const configPath = path.join(runDirectory, 'mediamtx.yml');
      await fs.promises.writeFile(
        configPath,
        this._configuration(rtspPort, webrtcPort, iceUdpPort, iceTcpPort),
        'utf8'
      );
      this._process = spawn(executable, [configPath], {
        cwd: runDirectory,
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe']
      });
      const child = this._process;
      child.once('exit', code => {
        if (this._process !== child) return;
        this._process = null;
        this._status = {
          available: false,
          state: 'failed',
          version: MEDIAMTX_VERSION,
          streams: this.catalog(),
          message: 'MediaMTX stopped unexpectedly.',
          lastError: `MediaMTX exited with code ${
            code == null ? 'unknown' : code
          }.`
        };
      });
      this._pipeLogs(this._process);
      await this._waitForPort(webrtcPort, 10000);
      if (!this._process || this._process.exitCode !== null) {
        throw new Error('MediaMTX exited during startup.');
      }
      this._status = {
        available: true,
        state: 'running',
        version: MEDIAMTX_VERSION,
        streams: this.catalog(),
        message: `${this._streams.size} WebRTC camera stream(s) are ready.`
      };
      await this._log(
        `MediaMTX v${MEDIAMTX_VERSION} is ready with ${this._streams.size} camera path(s).`
      );
    } catch (error) {
      const message = this._message(error);
      await this.stop();
      this._status = {
        available: false,
        state: 'failed',
        version: MEDIAMTX_VERSION,
        streams: [],
        message: 'MediaMTX WebRTC gateway failed to start.',
        lastError: message
      };
      const failure = new Error(message) as Error & { cause?: unknown };
      failure.cause = error;
      throw failure;
    }
  }

  async createSession(
    streamId: string,
    offerSdp: string
  ): Promise<UavSimulationWebRtcSessionResult> {
    const id = String(streamId || '').trim();
    const stream = this._streams.get(id);
    if (this._status.state !== 'running' || !this._webrtcPort || !stream) {
      return {
        ok: false,
        streamId: id,
        message: 'The requested WebRTC camera stream is not available.'
      };
    }
    if (
      !offerSdp.startsWith('v=0') ||
      Buffer.byteLength(offerSdp, 'utf8') > MAX_SDP_BYTES
    ) {
      return {
        ok: false,
        streamId: id,
        message: 'The WebRTC offer is invalid.'
      };
    }
    try {
      const response = await this._request(
        'POST',
        new URL(
          `http://127.0.0.1:${this._webrtcPort}/${stream.mediaPath}/whep`
        ),
        offerSdp,
        { 'Content-Type': 'application/sdp' }
      );
      if (response.statusCode !== 201 || !response.body.startsWith('v=0')) {
        throw new Error(
          `MediaMTX WHEP negotiation failed (${
            response.statusCode
          }): ${response.body.slice(0, 240)}`
        );
      }
      const location = response.headers.location;
      if (!location) {
        throw new Error('MediaMTX did not return a WHEP session location.');
      }
      const sessionUrl = new URL(
        location,
        `http://127.0.0.1:${this._webrtcPort}`
      );
      if (
        sessionUrl.hostname !== '127.0.0.1' ||
        Number(sessionUrl.port) !== this._webrtcPort
      ) {
        throw new Error('MediaMTX returned an invalid WHEP session location.');
      }
      const sessionId = randomUUID();
      this._sessions.set(sessionId, {
        url: sessionUrl,
        etag: String(response.headers.etag || '')
      });
      return { ok: true, streamId: id, sessionId, answerSdp: response.body };
    } catch (error) {
      return { ok: false, streamId: id, message: this._message(error) };
    }
  }

  async closeSession(sessionId: string): Promise<void> {
    const id = String(sessionId || '').trim();
    const session = this._sessions.get(id);
    if (!session) {
      return;
    }
    this._sessions.delete(id);
    try {
      await this._request(
        'DELETE',
        session.url,
        '',
        session.etag ? { 'If-Match': session.etag } : {}
      );
    } catch (_error) {
      // [UAV-IDE MODIFY] MediaMTX also clears the session when the peer connection closes.
    }
  }

  async stop(): Promise<void> {
    const sessions = Array.from(this._sessions.keys());
    await Promise.all(sessions.map(id => this.closeSession(id)));
    const child = this._process;
    this._process = null;
    if (child && child.exitCode === null) {
      child.kill();
      await this._waitForExit(child, 1500);
      if (child.exitCode === null && child.pid) {
        await new Promise<void>(resolve => {
          execFile(
            'taskkill.exe',
            ['/pid', String(child.pid), '/t', '/f'],
            { windowsHide: true },
            () => resolve()
          );
        });
      }
    }
    this._streams.clear();
    this._sessions.clear();
    this._webrtcPort = 0;
    this._username = '';
    this._password = '';
    if (this._status.state !== 'failed') {
      this._status = {
        available: false,
        state: 'idle',
        version: MEDIAMTX_VERSION,
        streams: []
      };
    }
  }

  private _configuration(
    rtspPort: number,
    webrtcPort: number,
    iceUdpPort: number,
    iceTcpPort: number
  ): string {
    const paths = Array.from(this._streams.values())
      .map(
        stream =>
          `  ${stream.mediaPath}:\n    source: ${JSON.stringify(
            stream.rtspUri
          )}\n    sourceOnDemand: true\n    sourceOnDemandStartTimeout: 15s\n    sourceOnDemandCloseAfter: 5s\n    rtspTransport: tcp`
      )
      .join('\n');
    return `# FleetDock IDEA generated MediaMTX v${MEDIAMTX_VERSION} configuration.\nlogLevel: info\nlogDestinations: [stdout]\nreadTimeout: 15s\nwriteTimeout: 15s\napi: false\nmetrics: false\npprof: false\nplayback: false\nauthMethod: internal\nauthInternalUsers:\n  - user: ${this._username}\n    pass: ${this._password}\n    ips: [\"127.0.0.1\", \"::1\"]\n    permissions:\n      - action: read\nrtsp: true\nrtspAddress: 127.0.0.1:${rtspPort}\nrtspTransports: [tcp]\nrtmp: false\nhls: false\nwebrtc: true\nwebrtcAddress: 127.0.0.1:${webrtcPort}\nwebrtcEncryption: false\nwebrtcAllowOrigins: []\nwebrtcLocalUDPAddress: 127.0.0.1:${iceUdpPort}\nwebrtcLocalTCPAddress: 127.0.0.1:${iceTcpPort}\nwebrtcIPsFromInterfaces: false\nwebrtcAdditionalHosts: [127.0.0.1]\nwebrtcICEServers2: []\nsrt: false\nmoq: false\npaths:\n${paths}\n`;
  }

  private _resolveExecutable(): string {
    const appDir = getAppDir();
    const candidates = [
      path.join(
        appDir,
        'dist-resources',
        'skylab-runtime',
        'mediamtx',
        'windows-x64',
        'mediamtx.exe'
      ),
      path.join(
        appDir,
        'skylab-runtime',
        'mediamtx',
        'windows-x64',
        'mediamtx.exe'
      ),
      path.join(
        process.cwd(),
        'dist-resources',
        'skylab-runtime',
        'mediamtx',
        'windows-x64',
        'mediamtx.exe'
      )
    ];
    const executable = candidates.find(candidate => fs.existsSync(candidate));
    if (!executable) {
      throw new Error(
        'Bundled MediaMTX executable was not found. Reinstall FleetDock IDEA runtime resources.'
      );
    }
    return executable;
  }

  private _freeTcpPorts(count: number): Promise<number[]> {
    return new Promise((resolve, reject) => {
      const servers = Array.from({ length: count }, () => net.createServer());
      const ports: number[] = [];
      const fail = (error: Error) => {
        servers.forEach(server => {
          if (server.listening) server.close();
        });
        reject(error);
      };
      servers.forEach(server => {
        server.once('error', fail);
        server.listen(0, '127.0.0.1', () => {
          const address = server.address();
          ports.push(typeof address === 'object' && address ? address.port : 0);
          if (ports.length === servers.length) {
            let remaining = servers.length;
            servers.forEach(item =>
              item.close(error => {
                if (error) return fail(error);
                remaining -= 1;
                if (!remaining) resolve(ports);
              })
            );
          }
        });
      });
    });
  }

  private _freeUdpPort(): Promise<number> {
    return new Promise((resolve, reject) => {
      const socket = dgram.createSocket('udp4');
      socket.once('error', reject);
      socket.bind(0, '127.0.0.1', () => {
        const address = socket.address();
        socket.close(() => resolve(address.port));
      });
    });
  }

  private async _waitForPort(port: number, timeoutMs: number): Promise<void> {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (
        await new Promise<boolean>(resolve => {
          const socket = net.connect({ host: '127.0.0.1', port });
          socket.once('connect', () => {
            socket.destroy();
            resolve(true);
          });
          socket.once('error', () => resolve(false));
        })
      ) {
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error('Timed out waiting for the MediaMTX WebRTC listener.');
  }

  private _request(
    method: string,
    url: URL,
    body: string,
    headers: Record<string, string>
  ): Promise<{
    statusCode: number;
    headers: http.IncomingHttpHeaders;
    body: string;
  }> {
    return new Promise((resolve, reject) => {
      const request = http.request(
        url,
        {
          method,
          headers: {
            Authorization: `Basic ${Buffer.from(
              `${this._username}:${this._password}`
            ).toString('base64')}`,
            'Content-Length': String(Buffer.byteLength(body, 'utf8')),
            ...headers
          }
        },
        response => {
          const chunks: Buffer[] = [];
          response.on('data', chunk => chunks.push(Buffer.from(chunk)));
          response.on('end', () =>
            resolve({
              statusCode: response.statusCode || 0,
              headers: response.headers,
              body: Buffer.concat(chunks).toString('utf8')
            })
          );
        }
      );
      request.once('error', reject);
      request.setTimeout(15000, () =>
        request.destroy(new Error('MediaMTX WHEP request timed out.'))
      );
      if (body) request.write(body);
      request.end();
    });
  }

  private _pipeLogs(child: ChildProcess): void {
    for (const stream of [child.stdout, child.stderr]) {
      stream?.on('data', data => {
        const message = String(data || '').trim();
        if (message) void this._log(`[mediamtx] ${message}`);
      });
    }
  }

  private _waitForExit(child: ChildProcess, timeoutMs: number): Promise<void> {
    if (child.exitCode !== null) return Promise.resolve();
    return new Promise(resolve => {
      const timeout = setTimeout(resolve, timeoutMs);
      child.once('exit', () => {
        clearTimeout(timeout);
        resolve();
      });
    });
  }

  private _message(error: unknown): string {
    return error instanceof Error
      ? error.message
      : String(error || 'Unknown MediaMTX error.');
  }

  private _process: ChildProcess | null = null;
  private _streams = new Map<string, ManagedStream>();
  private _sessions = new Map<string, ManagedSession>();
  private _webrtcPort = 0;
  private _username = '';
  private _password = '';
  private _status: UavSimulationMediaServiceStatus = {
    available: false,
    state: 'idle',
    version: MEDIAMTX_VERSION,
    streams: []
  };
}
