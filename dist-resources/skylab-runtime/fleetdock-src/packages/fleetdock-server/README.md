# FleetDock Server

`fleetdock-server` 把 FleetDock 核心 Python SDK 的标准设备、控制、状态和传感器能力发布为
统一 WebSocket 服务。服务端独占所注册设备；Python 图形控制器、Web 页面和其他语言客户端不得再
直接操作同一设备对象。

控制连接使用 `/ws/control`，状态和传感器连接使用 `/ws/telemetry`。RGB 视频和媒体地址不经过
FleetDock WebSocket；RTSP/WebRTC 由独立媒体链路配置。

取得控制租约后，客户端可通过 `manual.configure` 设置水平速度（`horizontal_speed_m_s`）、
升降速度（`vertical_speed_m_s`）和偏航角速度（`yaw_rate_deg_s`）。`control.acquired`、
`manual.configure` 和手动控制状态均返回服务端当前采用的实际值，交互层应以该响应刷新显示。

```powershell
python -m pip install -e ".[drone]"
fleetdock-server --mock-drone --host 127.0.0.1 --port 8765
fleetdock-server-drone --endpoint "udpin://0.0.0.0:14555" --ws-port 23045
fleetdock-controller --endpoint ws://127.0.0.1:8765 --device device-001 --mode simulation
```

示例按单一职责命名和组织：

- `examples/drone_controller_debug.py`：只连接已有 WS 服务并启动 Python 图形控制器。
- `examples/drone_client_debug.py`：使用 `FleetDockClient` 读取设备能力、状态和传感器列表。
- `examples/start_projectairsim_rtsp_bridge.bat`：只把 Project AirSim 相机转换为本地 RTSP 拉流地址。
- `examples/projectairsim_bridge_server.py`：显式创建 Project AirSim RTSP Bridge，并启动仿真 WS 服务。
- `examples/start_drone_server.bat`：主推的 Windows 通用服务入口，适用于真机和已提供 RTSP 的仿真环境。

BAT 文件以 `python src/fleetdock-cli/drone_server.py <命令行参数>` 的直观形式传入 PX4、WS、相机和
媒体配置。Python 封装把外部已有的 RTSP 拉流地址原样传给正式无人机服务，不创建 RTSP Server、
Project AirSim Bridge 或相机编码管线。服务默认只监听回环地址；当前版本未内置 TLS、认证和授权，
部署到局域网或公网前必须由网关补齐这些边界。

推荐的仿真组合是分别启动视频源适配器和通用无人机服务，让服务端与真机使用完全相同的外部 RTSP
输入语义：

```powershell
# 终端一：只把 Project AirSim 相机转换为 RTSP
examples\start_projectairsim_rtsp_bridge.bat

# 终端二：从该 RTSP 地址启动 PX4/WS 服务
examples\start_drone_server.bat
```

两个进程默认共用 `rtsp://127.0.0.1:8554/Drone1_camera_front`；也可以在两边通过
`FLEETDOCK_CAMERA_STREAM_URI` 设置同一个地址。`projectairsim_bridge_server.py` 仅作为 Bridge 与 WS
同进程托管的仿真集成示例保留，不是主推的真机部署方式。

正式 PX4 无人机服务使用独立命令。`--endpoint` 是透传给 PX4/MAVSDK 适配器的连接字符串，因此 UDP、
TCP 或串口不需要拆成不同参数：

```powershell
fleetdock-server-drone --endpoint "serial:///dev/ttyUSB0:57600" --link-profile low_bandwidth --ws-port 23045

fleetdock-server-drone `
  --endpoint "udpin://0.0.0.0:14555" `
  --camera-rtsp-uri "rtsp://127.0.0.1:8554/Drone1_camera_front" `
  --ws-host "127.0.0.1" `
  --ws-port 23045 `
  --media-publish-url "https://media.example/rtc/v1/whip/?app=live&stream=drone-001"
```

`--link-profile` 可选 `simulation`、`standard` 和 `low_bandwidth`。低带宽模式面向室内数传模块：
服务端状态会话、控制租约和传感器订阅只向核心 SDK 声明当前需求，核心 SDK 复用同一路 MAVSDK 流，
并对位置、姿态、速度和电池限频。WS 客户端不需要增加订阅步骤，现有控制器和 Demo 保持兼容。

未配置媒体地址时只启动 WS；媒体发布失败时默认保留控制与遥测服务，增加 `--require-media` 后则启动
失败。当前媒体发布地址是 WHIP HTTP(S) 地址，摄像头输入才是 RTSP/RTSPS 地址。

Python 图形控制器属于本包，并从稳定顶层入口导出：

```python
from fleetdock_server import ControlMode, FleetDockServer, PythonController
```

核心 `fleetdock` 的 `device.debug` 仅保留传感器本地调试，不会隐式启动 WS 服务，也不依赖本包。

真机摄像头、仿真器或独立媒体服务器必须先提供可访问的 H.264 RTSP 拉流地址。服务可通过进程内
GStreamer 将该地址无转码发布到 SRS WHIP 入口；Web Demo 再通过对应的 WHEP 地址播放。可用环境变量覆盖：

```powershell
$env:FLEETDOCK_SRS_WHIP_URL = "https://live.example/rtc/v1/whip/?app=live&stream=drone-001"
$env:FLEETDOCK_SRS_WHEP_URL = "https://live.example/rtc/v1/whep/?app=live&stream=drone-001"
$env:FLEETDOCK_MEDIA_BACKEND = "gstreamer"
$env:FLEETDOCK_WHIP_RTSP_LATENCY_MS = "0"
$env:FLEETDOCK_WHIP_DROP_ON_LATENCY = "true"
python src/fleetdock-cli/drone_server.py `
  --endpoint "udpin://0.0.0.0:14555" `
  --camera-rtsp-uri "rtsp://127.0.0.1:8554/Drone1_camera_front" `
  --camera-source physical `
  --media-publish-url $env:FLEETDOCK_SRS_WHIP_URL `
  --whep-url $env:FLEETDOCK_SRS_WHEP_URL `
  --media-backend $env:FLEETDOCK_MEDIA_BACKEND
```

`GStreamerWhipRelay` 使用 RTSP/TCP 拉取 H.264，只执行 RTP 解包、`h264parse`、RTP 重新封装和
`whipsink` 发布，不包含视频解码器或编码器，因此不会产生第二次有损编码，也不会启动外部进程。
视频分辨率、帧率、码率、GOP 和编码质量由外部摄像头或媒体源负责；FleetDock 不修改这些参数。
`drop-on-latency` 会优先丢弃超过接收缓冲窗口的旧数据，适合无人机实时控制画面。
`FLEETDOCK_WHIP_RTSP_LATENCY_MS=0` 适合本机稳定链路；局域网或公网抖动时可从 50 ～ 150 毫秒起调，
避免因为零缓冲产生花屏或频繁恢复。
运行环境必须提供 GStreamer、GstRtsp、`whipsink`、`rtph264depay`、`h264parse` 和 `rtph264pay`。
如需兼容非 H.264 或参数不适合 WebRTC 的输入，可显式设置
`FLEETDOCK_MEDIA_BACKEND=ffmpeg` 使用 `FfmpegWhipRelay`；旧名 `WhipRelay` 保持兼容。
`RtspRelay` 仍保留给真正支持 RTSP `ANNOUNCE/RECORD` 的其他媒体入口使用。媒体链路与 FleetDock
WebSocket 控制、遥测链路彼此独立。
