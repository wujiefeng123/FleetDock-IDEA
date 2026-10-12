import {
  FleetDockClient,
  type ControlMode,
  type FleetDockMessage,
  type ManualAction,
} from "../../../dist/index.js";
import {
  adjustManualSpeed,
  formatManualSpeed,
  MANUAL_SPEED_LIMITS,
  type ManualSpeedAxis,
  type SpeedDirection,
} from "./manual-speed";
import { WhepPlayer, type WhepPlayerState } from "./whep-player";
import "./style.css";

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) throw new Error("FleetDock Web Demo root element is missing.");

app.innerHTML = `
  <main class="shell">
    <header class="topbar">
      <div class="brand">
        <span class="brand-mark">FD</span>
        <div>
          <p class="eyebrow">FLEETDOCK / REMOTE STATION</p>
          <h1>飞行控制台</h1>
        </div>
      </div>
      <div class="connection-state" id="connection-state" data-state="offline">
        <span class="state-dot"></span>
        <span id="connection-label">未连接</span>
      </div>
    </header>

    <section class="connection-panel" aria-label="服务连接">
      <label>
        <span>WS 服务</span>
        <input id="endpoint" value="ws://127.0.0.1:23045" spellcheck="false" />
      </label>
      <label>
        <span>设备 ID</span>
        <input id="device-id" value="drone-001" spellcheck="false" />
      </label>
      <label>
        <span>控制环境</span>
        <select id="control-mode">
          <option value="simulation">仿真</option>
          <option value="bench">台架</option>
          <option value="live">真机</option>
        </select>
      </label>
      <button class="primary-button" id="connect-button">连接服务</button>
    </section>

    <section class="workspace">
      <div class="visual-column">
        <article class="video-card">
          <div class="card-heading">
            <div>
              <p class="eyebrow">VISUAL LINK</p>
              <h2>实时画面</h2>
            </div>
            <span class="signal-tag" id="video-state" data-state="offline">VIDEO OFFLINE</span>
          </div>
          <div class="video-placeholder">
            <video id="camera-video" autoplay playsinline muted></video>
            <div class="reticle"></div>
            <div class="video-copy" id="video-copy">
              <span class="video-icon">◉</span>
              <strong>等待 WHEP 视频连接</strong>
              <span>控制与遥测链路可独立运行</span>
            </div>
            <span class="corner corner-a"></span>
            <span class="corner corner-b"></span>
            <span class="corner corner-c"></span>
            <span class="corner corner-d"></span>
          </div>
          <div class="video-controls">
            <label>
              <span>WHEP 播放地址</span>
              <input id="whep-url" spellcheck="false" placeholder="https://live.example/rtc/v1/whep/?app=live&stream=drone-001" />
            </label>
            <div class="video-buttons">
              <button class="primary-button" id="connect-video">连接视频</button>
              <button id="reconnect-video" disabled>重连</button>
              <button id="disconnect-video" disabled>断开</button>
            </div>
          </div>
          <p class="video-error" id="video-error" role="alert"></p>
        </article>

        <article class="command-card">
          <div class="card-heading">
            <div>
              <p class="eyebrow">FLIGHT COMMANDS</p>
              <h2>设备命令</h2>
            </div>
          </div>
          <div class="command-grid">
            <button data-command="connect">连接设备</button>
            <button data-command="unlock">解锁</button>
            <button data-command="takeoff" data-params='{"altitude":5}'>起飞 5 m</button>
            <button data-command="hover">悬停</button>
            <button data-command="land">降落</button>
            <button data-command="return_to_launch">返航</button>
            <button data-command="stop" class="warning">停止</button>
          </div>
        </article>
      </div>

      <div class="control-column">
        <article class="telemetry-card">
          <div class="card-heading">
            <div>
              <p class="eyebrow">TELEMETRY</p>
              <h2 id="device-name">等待设备</h2>
            </div>
            <button class="text-button" id="refresh-sensors" disabled>传感器</button>
          </div>
          <div class="telemetry-grid">
            <div><span>连接</span><strong id="state-connected">—</strong></div>
            <div><span>解锁</span><strong id="state-armed">—</strong></div>
            <div><span>飞行</span><strong id="state-air">—</strong></div>
            <div><span>模式</span><strong id="state-mode">—</strong></div>
            <div><span>相对高度</span><strong id="state-altitude">—</strong></div>
            <div><span>电量</span><strong id="state-battery">—</strong></div>
          </div>
          <div class="sensor-strip" id="sensor-strip">尚未读取传感器</div>
        </article>

        <article class="manual-card">
          <div class="card-heading">
            <div>
              <p class="eyebrow">MANUAL CONTROL</p>
              <h2>机体系运动</h2>
            </div>
            <span class="key-hint">支持多键</span>
          </div>

          <div class="speed-controls" aria-label="手动控制速度">
            <div class="speed-control">
              <span class="group-label">水平移动速度</span>
              <div class="speed-adjust">
                <button id="horizontal-speed-down" data-speed-axis="horizontal" data-speed-direction="-1" disabled>−</button>
                <strong id="horizontal-speed-value">3.0 m/s</strong>
                <button id="horizontal-speed-up" data-speed-axis="horizontal" data-speed-direction="1" disabled>＋</button>
              </div>
              <small>快捷键 − / ＋</small>
            </div>
            <div class="speed-control">
              <span class="group-label">升高下降速度</span>
              <div class="speed-adjust">
                <button id="vertical-speed-down" data-speed-axis="vertical" data-speed-direction="-1" disabled>−</button>
                <strong id="vertical-speed-value">0.6 m/s</strong>
                <button id="vertical-speed-up" data-speed-axis="vertical" data-speed-direction="1" disabled>＋</button>
              </div>
              <small>快捷键 [ / ]</small>
            </div>
          </div>

          <div class="manual-layout">
            <div class="pad-group">
              <span class="group-label">水平移动</span>
              <div class="direction-pad">
                <button class="motion-button north" data-action="forward">
                  <b>↑</b><span>前进</span><kbd>W</kbd>
                </button>
                <button class="motion-button west" data-action="left">
                  <b>←</b><span>左移</span><kbd>A</kbd>
                </button>
                <button class="motion-button center release-button" id="release-all">
                  <b>■</b><span>释放</span>
                </button>
                <button class="motion-button east" data-action="right">
                  <b>→</b><span>右移</span><kbd>D</kbd>
                </button>
                <button class="motion-button south" data-action="backward">
                  <b>↓</b><span>后退</span><kbd>S</kbd>
                </button>
              </div>
            </div>

            <div class="axis-groups">
              <div>
                <span class="group-label">高度</span>
                <div class="axis-row">
                  <button class="motion-button" data-action="ascend"><b>＋</b><span>上升</span><kbd>R</kbd></button>
                  <button class="motion-button" data-action="descend"><b>−</b><span>下降</span><kbd>F</kbd></button>
                </div>
              </div>
              <div>
                <span class="group-label">偏航</span>
                <div class="axis-row">
                  <button class="motion-button" data-action="yaw_left"><b>↶</b><span>左转</span><kbd>Q</kbd></button>
                  <button class="motion-button" data-action="yaw_right"><b>↷</b><span>右转</span><kbd>E</kbd></button>
                </div>
              </div>
            </div>
          </div>

          <div class="active-actions">
            <span>当前动作</span>
            <strong id="active-actions">无</strong>
          </div>
        </article>

        <article class="event-card">
          <div class="card-heading compact">
            <div>
              <p class="eyebrow">EVENT LOG</p>
              <h2>操作记录</h2>
            </div>
            <button class="text-button" id="clear-log">清空</button>
          </div>
          <ol id="event-log" aria-live="polite"></ol>
        </article>
      </div>
    </section>
  </main>
`;

const endpointInput = getElement<HTMLInputElement>("endpoint");
const deviceInput = getElement<HTMLInputElement>("device-id");
const modeInput = getElement<HTMLSelectElement>("control-mode");
const connectButton = getElement<HTMLButtonElement>("connect-button");
const connectionState = getElement<HTMLDivElement>("connection-state");
const connectionLabel = getElement<HTMLSpanElement>("connection-label");
const activeActionsLabel = getElement<HTMLElement>("active-actions");
const eventLog = getElement<HTMLOListElement>("event-log");
const refreshSensorsButton = getElement<HTMLButtonElement>("refresh-sensors");
const whepInput = getElement<HTMLInputElement>("whep-url");
const videoElement = getElement<HTMLVideoElement>("camera-video");
const videoStateLabel = getElement<HTMLElement>("video-state");
const videoCopy = getElement<HTMLElement>("video-copy");
const videoError = getElement<HTMLElement>("video-error");
const connectVideoButton = getElement<HTMLButtonElement>("connect-video");
const reconnectVideoButton = getElement<HTMLButtonElement>("reconnect-video");
const disconnectVideoButton = getElement<HTMLButtonElement>("disconnect-video");
const horizontalSpeedValue = getElement<HTMLElement>("horizontal-speed-value");
const verticalSpeedValue = getElement<HTMLElement>("vertical-speed-value");

const DEFAULT_WHEP_URL =
  "https://live.jpy.wang/rtc/v1/whep/?app=live&stream=drone-001";
const query = new URLSearchParams(window.location.search);
whepInput.value =
  query.get("whep") ??
  query.get("whepUrl") ??
  import.meta.env.VITE_FLEETDOCK_WHEP_URL ??
  DEFAULT_WHEP_URL;

const whepPlayer = new WhepPlayer({
  video: videoElement,
  onStateChange: updateVideoState,
});

const keyboardActions: Record<string, ManualAction> = {
  KeyW: "forward",
  KeyS: "backward",
  KeyA: "left",
  KeyD: "right",
  KeyR: "ascend",
  KeyF: "descend",
  KeyQ: "yaw_left",
  KeyE: "yaw_right",
};

const actionLabels: Record<ManualAction, string> = {
  forward: "前进",
  backward: "后退",
  left: "左移",
  right: "右移",
  ascend: "上升",
  descend: "下降",
  yaw_left: "左偏航",
  yaw_right: "右偏航",
};

let client: FleetDockClient | undefined;
let connected = false;
let stateTimer: ReturnType<typeof setInterval> | undefined;
let supportedCommands = new Set<string>();
let supportedActions = new Set<ManualAction>();
let horizontalSpeedMps = MANUAL_SPEED_LIMITS.horizontal.initial;
let verticalSpeedMps = MANUAL_SPEED_LIMITS.vertical.initial;
let speedUpdating = false;
const activeSources = new Map<string, ManualAction>();
const actionCounts = new Map<ManualAction, number>();

connectButton.addEventListener("click", () => {
  void (connected ? disconnect() : connect());
});

document.querySelectorAll<HTMLButtonElement>("[data-command]").forEach((button) => {
  button.disabled = true;
  button.addEventListener("click", () => void submitCommand(button));
});

document.querySelectorAll<HTMLButtonElement>("[data-action]").forEach((button) => {
  button.disabled = true;
  const action = button.dataset.action as ManualAction;
  button.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    button.setPointerCapture(event.pointerId);
    void activate(`pointer:${event.pointerId}`, action);
  });
  const releasePointer = (event: PointerEvent) => {
    void deactivate(`pointer:${event.pointerId}`);
  };
  button.addEventListener("pointerup", releasePointer);
  button.addEventListener("pointercancel", releasePointer);
  button.addEventListener("lostpointercapture", releasePointer);
});

getElement<HTMLButtonElement>("release-all").addEventListener("click", () => void releaseAll());
document.querySelectorAll<HTMLButtonElement>("[data-speed-axis]").forEach((button) => {
  button.addEventListener("click", () => {
    void changeManualSpeed(
      button.dataset.speedAxis as ManualSpeedAxis,
      Number(button.dataset.speedDirection) as SpeedDirection,
    );
  });
});
refreshSensorsButton.addEventListener("click", () => void refreshSensors());
getElement<HTMLButtonElement>("clear-log").addEventListener("click", () => {
  eventLog.replaceChildren();
});
connectVideoButton.addEventListener("click", () => void connectVideo());
reconnectVideoButton.addEventListener("click", () => void connectVideo());
disconnectVideoButton.addEventListener("click", () => void whepPlayer.close());
whepInput.addEventListener("input", () => {
  reconnectVideoButton.disabled = !whepInput.value.trim();
});

window.addEventListener("keydown", (event) => {
  if (!event.repeat && !isTypingTarget(event.target)) {
    const speedChange = speedChangeForKey(event.code);
    if (speedChange) {
      event.preventDefault();
      void changeManualSpeed(speedChange.axis, speedChange.direction);
      return;
    }
  }
  const action = keyboardActions[event.code];
  if (!action || event.repeat || isTypingTarget(event.target)) return;
  event.preventDefault();
  void activate(`key:${event.code}`, action);
});

window.addEventListener("keyup", (event) => {
  const action = keyboardActions[event.code];
  if (!action || isTypingTarget(event.target)) return;
  event.preventDefault();
  void deactivate(`key:${event.code}`);
});

window.addEventListener("blur", () => void releaseAll());
document.addEventListener("visibilitychange", () => {
  if (document.hidden) void releaseAll();
});
window.addEventListener("beforeunload", () => {
  if (client) void client.close();
  void whepPlayer.close();
});

async function connectVideo(): Promise<void> {
  videoError.textContent = "";
  try {
    await whepPlayer.connect(whepInput.value);
    log("WebRTC 视频已连接", "success");
  } catch (error) {
    log(`视频连接失败：${errorMessage(error)}`, "error");
  }
}

function updateVideoState(state: WhepPlayerState, error?: Error): void {
  const labels: Record<WhepPlayerState, string> = {
    offline: "VIDEO OFFLINE",
    connecting: "VIDEO CONNECTING",
    online: "VIDEO ONLINE",
    error: "VIDEO ERROR",
  };
  videoStateLabel.dataset.state = state;
  videoStateLabel.textContent = labels[state];
  videoCopy.hidden = state === "online";
  whepInput.disabled = state === "connecting";
  connectVideoButton.disabled = state === "connecting" || state === "online";
  reconnectVideoButton.disabled = state === "connecting" || !whepInput.value.trim();
  disconnectVideoButton.disabled = state === "offline";
  if (error) videoError.textContent = error.message;
  else if (state !== "error") videoError.textContent = "";
}

async function changeManualSpeed(
  axis: ManualSpeedAxis,
  direction: SpeedDirection,
): Promise<void> {
  if (!client || !connected || speedUpdating) return;
  const current = axis === "horizontal" ? horizontalSpeedMps : verticalSpeedMps;
  const next = adjustManualSpeed(axis, current, direction);
  if (next === current) return;
  speedUpdating = true;
  updateSpeedControls();
  try {
    const response = await client.configureManual(
      axis === "horizontal"
        ? { horizontal_speed_m_s: next }
        : { vertical_speed_m_s: next },
    );
    if (axis === "horizontal") horizontalSpeedMps = next;
    else verticalSpeedMps = next;
    applyManualSpeedState(response);
    log(
      `${axis === "horizontal" ? "水平移动" : "升高下降"}速度：${formatManualSpeed(next)}`,
      "success",
    );
  } catch (error) {
    log(`速度调整失败：${errorMessage(error)}`, "error");
  } finally {
    speedUpdating = false;
    updateSpeedControls();
  }
}

function applyManualSpeedState(message: FleetDockMessage): void {
  const nestedState =
    message.state && typeof message.state === "object"
      ? message.state as Record<string, unknown>
      : undefined;
  const state = nestedState ?? message;
  const horizontal = Number(state.horizontal_speed_m_s);
  const vertical = Number(state.vertical_speed_m_s);
  if (Number.isFinite(horizontal) && horizontal > 0) horizontalSpeedMps = horizontal;
  if (Number.isFinite(vertical) && vertical > 0) verticalSpeedMps = vertical;
  updateSpeedControls();
}

function updateSpeedControls(): void {
  horizontalSpeedValue.textContent = formatManualSpeed(horizontalSpeedMps);
  verticalSpeedValue.textContent = formatManualSpeed(verticalSpeedMps);
  document.querySelectorAll<HTMLButtonElement>("[data-speed-axis]").forEach((button) => {
    const axis = button.dataset.speedAxis as ManualSpeedAxis;
    const direction = Number(button.dataset.speedDirection) as SpeedDirection;
    const current = axis === "horizontal" ? horizontalSpeedMps : verticalSpeedMps;
    button.disabled =
      !connected ||
      speedUpdating ||
      adjustManualSpeed(axis, current, direction) === current;
  });
}

function speedChangeForKey(
  code: string,
): { axis: ManualSpeedAxis; direction: SpeedDirection } | undefined {
  if (code === "Minus" || code === "NumpadSubtract") {
    return { axis: "horizontal", direction: -1 };
  }
  if (code === "Equal" || code === "NumpadAdd") {
    return { axis: "horizontal", direction: 1 };
  }
  if (code === "BracketLeft") return { axis: "vertical", direction: -1 };
  if (code === "BracketRight") return { axis: "vertical", direction: 1 };
  return undefined;
}

async function connect(): Promise<void> {
  setConnectionState("connecting", "连接中");
  connectButton.disabled = true;
  endpointInput.disabled = true;
  deviceInput.disabled = true;
  modeInput.disabled = true;
  const nextClient = new FleetDockClient(endpointInput.value.trim());
  try {
    const deviceId = deviceInput.value.trim();
    const welcome = await nextClient.connect(deviceId);
    const lease = await nextClient.acquireControl(deviceId, modeInput.value as ControlMode);
    client = nextClient;
    connected = true;
    applyManualSpeedState(lease);
    const device = (welcome.devices as Array<Record<string, unknown>>)[0];
    supportedCommands = new Set(device.supported_commands as string[]);
    supportedActions = new Set(device.supported_manual_actions as ManualAction[]);
    getElement<HTMLElement>("device-name").textContent =
      `${String(device.device_id)} · ${String(device.product)}`;
    applyCapabilities();
    setConnectionState("online", "WS 已连接");
    connectButton.textContent = "断开服务";
    connectButton.disabled = false;
    refreshSensorsButton.disabled = false;
    updateSpeedControls();
    log("已取得设备控制租约", "success");
    await refreshState();
    stateTimer = setInterval(() => void refreshState(), 500);
  } catch (error) {
    await nextClient.close();
    resetConnectionControls();
    setConnectionState("error", "连接失败");
    log(errorMessage(error), "error");
  }
}

async function disconnect(): Promise<void> {
  connectButton.disabled = true;
  clearInterval(stateTimer);
  stateTimer = undefined;
  await releaseAll();
  const closing = client;
  client = undefined;
  connected = false;
  if (closing) await closing.close();
  resetConnectionControls();
  setConnectionState("offline", "未连接");
  log("WS 服务已断开");
}

async function submitCommand(button: HTMLButtonElement): Promise<void> {
  if (!client) return;
  const command = button.dataset.command;
  if (!command) return;
  const params = button.dataset.params ? JSON.parse(button.dataset.params) as Record<string, unknown> : {};
  button.disabled = true;
  try {
    const result = await client.submitCommand(command, params);
    const response = result.response as Record<string, unknown>;
    log(
      `${button.textContent?.trim() ?? command}：${response.ok ? "成功" : String(response.message ?? response.code)}`,
      response.ok ? "success" : "error",
    );
  } catch (error) {
    log(`${button.textContent?.trim() ?? command}：${errorMessage(error)}`, "error");
  } finally {
    await refreshState();
  }
}

async function activate(source: string, action: ManualAction): Promise<void> {
  if (!client || activeSources.has(source) || !supportedActions.has(action)) return;
  activeSources.set(source, action);
  const count = actionCounts.get(action) ?? 0;
  actionCounts.set(action, count + 1);
  updateActiveActions();
  if (count > 0) return;
  try {
    await client.press(action);
    setActionPressed(action, true);
  } catch (error) {
    activeSources.delete(source);
    actionCounts.delete(action);
    updateActiveActions();
    log(`运动指令被拒绝：${errorMessage(error)}`, "error");
  }
}

async function deactivate(source: string): Promise<void> {
  const action = activeSources.get(source);
  if (!action) return;
  activeSources.delete(source);
  const nextCount = Math.max(0, (actionCounts.get(action) ?? 1) - 1);
  if (nextCount > 0) {
    actionCounts.set(action, nextCount);
    return;
  }
  actionCounts.delete(action);
  updateActiveActions();
  setActionPressed(action, false);
  if (!client) return;
  try {
    await client.release(action);
  } catch (error) {
    log(`释放动作失败：${errorMessage(error)}`, "error");
  }
}

async function releaseAll(): Promise<void> {
  activeSources.clear();
  actionCounts.clear();
  updateActiveActions();
  document.querySelectorAll<HTMLElement>("[data-action].is-active").forEach((item) => {
    item.classList.remove("is-active");
  });
  if (!client) return;
  try {
    await client.releaseAll();
  } catch (error) {
    if (connected) log(`全部释放失败：${errorMessage(error)}`, "error");
  }
}

async function refreshState(): Promise<void> {
  if (!client) return;
  try {
    const message = await client.getDeviceState(deviceInput.value.trim());
    const state = message.state as Record<string, unknown>;
    const diagnostics = message.diagnostics as Record<string, unknown>;
    setText("state-connected", truthLabel(diagnostics.connected));
    setText("state-armed", truthLabel(state.armed));
    setText("state-air", truthLabel(state.in_air));
    setText("state-mode", String(state.flight_mode ?? "—"));
    setText(
      "state-altitude",
      state.relative_altitude_m == null ? "—" : `${Number(state.relative_altitude_m).toFixed(1)} m`,
    );
    setText(
      "state-battery",
      state.battery_remaining_fraction == null
        ? "—"
        : `${Math.round(Number(state.battery_remaining_fraction) * 100)}%`,
    );
    applyCommandReadiness(state, diagnostics);
    applyMotionReadiness(state.in_air === true);
  } catch (error) {
    log(`状态读取失败：${errorMessage(error)}`, "error", true);
  }
}

async function refreshSensors(): Promise<void> {
  if (!client) return;
  try {
    const response = await client.listSensors(deviceInput.value.trim());
    const sensors = response.sensors as Array<Record<string, unknown>>;
    getElement<HTMLElement>("sensor-strip").textContent = sensors.length
      ? sensors.map((sensor) => `${String(sensor.sensor_id)} · ${String(sensor.kind)}`).join("  /  ")
      : "设备未声明标准传感器";
    log(`读取到 ${sensors.length} 个标准传感器`, "success");
  } catch (error) {
    log(`传感器读取失败：${errorMessage(error)}`, "error");
  }
}

function applyCapabilities(): void {
  document.querySelectorAll<HTMLButtonElement>("[data-command]").forEach((button) => {
    button.dataset.supported = String(supportedCommands.has(button.dataset.command ?? ""));
    button.disabled = true;
  });
  document.querySelectorAll<HTMLButtonElement>("[data-action]").forEach((button) => {
    button.dataset.supported = String(supportedActions.has(button.dataset.action as ManualAction));
  });
}

function applyCommandReadiness(
  state: Record<string, unknown>,
  diagnostics: Record<string, unknown>,
): void {
  const deviceConnected = diagnostics.connected === true;
  const armed = state.armed === true;
  const inAir = state.in_air === true;
  document.querySelectorAll<HTMLButtonElement>("[data-command]").forEach((button) => {
    const command = button.dataset.command ?? "";
    if (!connected || button.dataset.supported !== "true") {
      button.disabled = true;
      return;
    }
    if (command === "connect") button.disabled = deviceConnected;
    else if (command === "unlock") button.disabled = !deviceConnected || armed;
    else if (command === "takeoff") {
      button.disabled = !armed || inAir || modeInput.value === "bench";
    } else if (["hover", "land", "return_to_launch", "stop"].includes(command)) {
      button.disabled = !inAir;
    } else {
      button.disabled = false;
    }
  });
}

function applyMotionReadiness(inAir: boolean): void {
  document.querySelectorAll<HTMLButtonElement>("[data-action]").forEach((button) => {
    const supported = button.dataset.supported === "true";
    button.disabled = !connected || !supported || !inAir;
  });
}

function resetConnectionControls(): void {
  endpointInput.disabled = false;
  deviceInput.disabled = false;
  modeInput.disabled = false;
  connectButton.disabled = false;
  connectButton.textContent = "连接服务";
  refreshSensorsButton.disabled = true;
  supportedCommands.clear();
  supportedActions.clear();
  horizontalSpeedMps = MANUAL_SPEED_LIMITS.horizontal.initial;
  verticalSpeedMps = MANUAL_SPEED_LIMITS.vertical.initial;
  speedUpdating = false;
  updateSpeedControls();
  document.querySelectorAll<HTMLButtonElement>("[data-command], [data-action]").forEach((button) => {
    button.disabled = true;
  });
}

function updateActiveActions(): void {
  const actions = [...actionCounts.keys()];
  activeActionsLabel.textContent = actions.length
    ? actions.map((action) => actionLabels[action]).join(" + ")
    : "无";
}

function setActionPressed(action: ManualAction, pressed: boolean): void {
  const button = document.querySelector<HTMLElement>(`[data-action="${action}"]`);
  button?.classList.toggle("is-active", pressed);
}

function setConnectionState(state: string, label: string): void {
  connectionState.dataset.state = state;
  connectionLabel.textContent = label;
}

function log(message: string, kind: "normal" | "success" | "error" = "normal", dedupe = false): void {
  if (dedupe && eventLog.lastElementChild?.textContent?.includes(message)) return;
  const item = document.createElement("li");
  item.dataset.kind = kind;
  const time = document.createElement("time");
  time.textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  const content = document.createElement("span");
  content.textContent = message;
  item.append(time, content);
  eventLog.prepend(item);
  while (eventLog.children.length > 30) eventLog.lastElementChild?.remove();
}

function truthLabel(value: unknown): string {
  if (value === true) return "是";
  if (value === false) return "否";
  return "—";
}

function isTypingTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLInputElement || target instanceof HTMLSelectElement;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function setText(id: string, value: string): void {
  getElement<HTMLElement>(id).textContent = value;
}

function getElement<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) throw new Error(`Missing element: ${id}`);
  return element as T;
}

updateVideoState("offline");
updateSpeedControls();
log("控制台已就绪；控制与视频可以分别连接");
