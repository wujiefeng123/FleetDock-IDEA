export type ManualAction =
  | 'forward'
  | 'backward'
  | 'left'
  | 'right'
  | 'ascend'
  | 'descend'
  | 'yaw_left'
  | 'yaw_right';

export type ControlMode = 'simulation' | 'bench' | 'live';

export interface FleetDockMessage {
  version: 1;
  type: string;
  request_id?: string;
  device_id?: string;
  [key: string]: unknown;
}

type TelemetryHandler = (message: FleetDockMessage) => void;

class RequestSocket {
  private socket?: WebSocket;
  private readonly pending = new Map<
    string,
    {
      resolve: (value: FleetDockMessage) => void;
      reject: (reason: unknown) => void;
    }
  >();
  private readonly telemetryHandlers = new Set<TelemetryHandler>();

  constructor(private readonly url: string) {}

  async connect(): Promise<void> {
    if (this.socket) throw new Error('WebSocket is already connected.');
    const socket = new WebSocket(this.url);
    this.socket = socket;
    socket.addEventListener('message', event => this.receive(event.data));
    socket.addEventListener('close', () => {
      for (const pending of this.pending.values()) {
        pending.reject(new Error('FleetDock WebSocket closed.'));
      }
      this.pending.clear();
      this.socket = undefined;
    });
    await new Promise<void>((resolve, reject) => {
      socket.addEventListener('open', () => resolve(), { once: true });
      socket.addEventListener(
        'error',
        () => reject(new Error('FleetDock WebSocket connection failed.')),
        {
          once: true
        }
      );
    });
  }

  async request(
    type: string,
    payload: Record<string, unknown> = {}
  ): Promise<FleetDockMessage> {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      throw new Error('FleetDock WebSocket is not connected.');
    }
    const requestId = crypto.randomUUID();
    const message: FleetDockMessage = {
      version: 1,
      type,
      request_id: requestId,
      ...payload
    };
    const result = new Promise<FleetDockMessage>((resolve, reject) => {
      this.pending.set(requestId, { resolve, reject });
    });
    this.socket.send(JSON.stringify(message));
    return result;
  }

  subscribe(handler: TelemetryHandler): () => void {
    this.telemetryHandlers.add(handler);
    return () => this.telemetryHandlers.delete(handler);
  }

  close(): void {
    this.socket?.close();
    this.socket = undefined;
  }

  private receive(data: unknown): void {
    if (typeof data !== 'string') return;
    const message = JSON.parse(data) as FleetDockMessage;
    const requestId = message.request_id;
    if (requestId && this.pending.has(requestId)) {
      const pending = this.pending.get(requestId)!;
      this.pending.delete(requestId);
      if (message.type === 'protocol.error') {
        pending.reject(
          new Error(`${String(message.code)}: ${String(message.message)}`)
        );
      } else {
        pending.resolve(message);
      }
      return;
    }
    for (const handler of this.telemetryHandlers) handler(message);
  }
}

export class FleetDockClient {
  private readonly control: RequestSocket;
  private readonly telemetry: RequestSocket;
  private controlId?: string;
  private deviceId?: string;
  private sequence = 0;
  private heartbeat?: ReturnType<typeof setInterval>;

  constructor(endpoint: string) {
    const root = endpoint.replace(/\/+$/, '');
    this.control = new RequestSocket(`${root}/ws/control`);
    this.telemetry = new RequestSocket(`${root}/ws/telemetry`);
  }

  async connect(deviceId?: string): Promise<FleetDockMessage> {
    await Promise.all([this.control.connect(), this.telemetry.connect()]);
    const [welcome] = await Promise.all([
      this.control.request('session.hello', { device_id: deviceId }),
      this.telemetry.request('session.hello', { device_id: deviceId })
    ]);
    return welcome;
  }

  async acquireControl(
    deviceId: string,
    mode: ControlMode = 'live'
  ): Promise<FleetDockMessage> {
    const response = await this.control.request('control.acquire', {
      device_id: deviceId,
      mode
    });
    this.controlId = String(response.control_id);
    this.deviceId = deviceId;
    const heartbeatMs = Number(response.heartbeat_interval_ms);
    this.heartbeat = setInterval(() => {
      void this.sendControl('manual.heartbeat').catch(() => {
        if (this.heartbeat) clearInterval(this.heartbeat);
        this.heartbeat = undefined;
      });
    }, heartbeatMs);
    return response;
  }

  press(action: ManualAction): Promise<FleetDockMessage> {
    return this.sendControl('manual.press', { action });
  }

  release(action: ManualAction): Promise<FleetDockMessage> {
    return this.sendControl('manual.release', { action });
  }

  releaseAll(): Promise<FleetDockMessage> {
    return this.sendControl('manual.release_all');
  }

  configureManual(options: {
    horizontal_speed_m_s?: number;
    vertical_speed_m_s?: number;
    yaw_rate_deg_s?: number;
  }): Promise<FleetDockMessage> {
    return this.sendControl('manual.configure', options);
  }

  submitCommand(
    command: string,
    params: Record<string, unknown> = {}
  ): Promise<FleetDockMessage> {
    return this.sendControl('command.submit', { command, params });
  }

  getDeviceState(deviceId: string): Promise<FleetDockMessage> {
    return this.telemetry.request('device.state.get', { device_id: deviceId });
  }

  listSensors(deviceId: string): Promise<FleetDockMessage> {
    return this.telemetry.request('sensor.list', { device_id: deviceId });
  }

  async subscribeSensor(
    deviceId: string,
    sensorId: string,
    maxRateHz = 20
  ): Promise<string> {
    const response = await this.telemetry.request('sensor.subscribe', {
      device_id: deviceId,
      sensor_id: sensorId,
      max_rate_hz: maxRateHz
    });
    return String(response.subscription_id);
  }

  async unsubscribeSensor(
    deviceId: string,
    subscriptionId: string
  ): Promise<void> {
    await this.telemetry.request('sensor.unsubscribe', {
      device_id: deviceId,
      subscription_id: subscriptionId
    });
  }

  subscribeTelemetry(handler: TelemetryHandler): () => void {
    return this.telemetry.subscribe(handler);
  }

  async close(): Promise<void> {
    if (this.heartbeat) clearInterval(this.heartbeat);
    this.heartbeat = undefined;
    try {
      if (this.controlId && this.deviceId) {
        try {
          await this.releaseAll();
        } catch {
          // 服务端可能已经因心跳超时释放租约，继续关闭本地连接。
        }
        try {
          await this.control.request('control.release', {
            control_id: this.controlId,
            device_id: this.deviceId
          });
        } catch {
          // 断线或已释放租约不应阻止客户端清理WebSocket。
        } finally {
          this.controlId = undefined;
          this.deviceId = undefined;
        }
      }
    } finally {
      this.telemetry.close();
      this.control.close();
    }
  }

  private sendControl(
    type: string,
    payload: Record<string, unknown> = {}
  ): Promise<FleetDockMessage> {
    if (!this.controlId || !this.deviceId)
      throw new Error('No FleetDock control lease is active.');
    this.sequence += 1;
    return this.control.request(type, {
      control_id: this.controlId,
      device_id: this.deviceId,
      sequence: this.sequence,
      ...payload
    });
  }
}
