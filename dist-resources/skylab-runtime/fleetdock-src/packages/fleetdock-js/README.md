# @fleetdock/client

浏览器和 JavaScript 应用使用的 FleetDock WebSocket 客户端。客户端统一管理协议握手、控制租约、递增
序号、心跳、断线释放和遥测订阅；页面不需要直接拼接控制 JSON。

```bash
npm install
npm run build
```

`npm run build` 会一次性生成 SDK 的 `dist/index.js`、类型声明以及 Web 控制器的
`dist/web/` 静态文件。Web 控制器直接引用已经编译的 SDK，不再维护第二份 SDK 构建。

```ts
import { FleetDockClient } from '@fleetdock/client';

const client = new FleetDockClient('ws://127.0.0.1:8765');
await client.connect('drone-001');
await client.acquireControl('drone-001', 'simulation');

await client.press('forward');
await client.press('yaw_right');
await client.release('yaw_right');
await client.release('forward');

const sensors = await client.listSensors('drone-001');
const unsubscribeMessages = client.subscribeTelemetry(console.log);
// const subscriptionId = await client.subscribeSensor("drone-001", "imu.primary", 20);

unsubscribeMessages();
await client.close();
```

浏览器的键盘监听只在真实按下/抬起边沿调用 `press()` 与 `release()`，不要转发系统自动重复的
`keydown`。心跳、序号、多键动作集合和断线后的服务端保护由客户端与服务共同管理。

## Web 控制器 Demo

先通过仓库的 `examples/run_dev.bat` 启动 Project AirSim 与 PX4，再启动只托管仿真设备的 Python
WS 服务和浏览器控制台：

```powershell
python packages/fleetdock-server/examples/projectairsim_bridge_server.py
cd packages/fleetdock-js
npm install
npm run build
npm run web
```

`npm run web` 只启动静态预览，不会再次编译；需要修改页面并使用热更新时才运行
`npm run web:dev`。浏览器打开 `http://127.0.0.1:4173`。Web 控制器默认连接
`ws://127.0.0.1:23045` 和 `drone-001`，支持
`W/A/S/D`、`R/F`、`Q/E` 多键组合、鼠标与触屏按住控制、窗口失焦自动释放、状态轮询和传感器发现。
控制面板可显示并调整水平速度和升降速度：水平速度默认 `3.0 m/s`，每次增减 `0.5 m/s`，
范围为 `0.5–20.0 m/s`；升降速度默认 `0.6 m/s`，每次增减 `0.1 m/s`，范围为
`0.1–2.0 m/s`。可点击 `−/+`，也可使用 `-/+` 和 `[/]` 快捷键。页面通过
`manual.configure` 把设置提交给服务端，并以服务端响应中的实际值刷新显示；已经按住的移动动作会使用新速度。
视频区域通过 WHEP/WebRTC 播放独立流媒体服务提供的画面，不会把 RGB
视频装进 FleetDock WS，也不影响控制与遥测。可复制环境变量示例：

```powershell
Copy-Item examples/web/.env.example examples/web/.env
```

然后修改：

```text
VITE_FLEETDOCK_WHEP_URL=https://live.jpy.wang/rtc/v1/whep/?app=live&stream=drone-001
```

也可以在页面输入 WHEP 地址，或通过查询参数临时覆盖：

```text
http://127.0.0.1:4173/?whep=https%3A%2F%2Flive.example%2Frtc%2Fv1%2Fwhep%2F%3Fapp%3Dlive%26stream%3Ddrone-001
```

播放器采用非 Trickle ICE，以 `application/sdp` POST 创建会话，接受
HTTP 200/201，并在服务返回 `Location` 时于断开阶段发送 DELETE。生产页面
必须使用 HTTPS；SRS 返回的 ICE Candidate 与 WebRTC UDP 端口也必须能被浏览器访问。
视频连接、断开和重连均独立于 WS 控制连接。SDP 握手成功后，播放器还会等待首个
可解码首帧，只有 `<video>` 的尺寸有效且 `readyState >= 2` 后才显示
`VIDEO ONLINE`；支持时优先使用 `requestVideoFrameCallback` 检测。默认等待
10 秒，无流、只有空轨道或首帧超时都会关闭 PeerConnection、删除 WHEP 会话并显示错误。

`packages/fleetdock-server/examples/drone_controller_debug.py` 会启动并占用 Python 图形控制器的
独占控制租约，因此不要与 Web Demo
同时连接同一设备。`projectairsim_bridge_server.py` 连接
`udpin://0.0.0.0:14555` 的 PX4 仿真设备；也可以使用
`fleetdock-server --px4-endpoint ... --port 23045` 启动同等服务。
