# FleetDock WebSocket 协议

协议版本 `1` 使用 UTF-8 JSON 文本。控制与遥测共享同一监听端口：

- `/ws/control`：控制权、持续动作、心跳和离散命令。
- `/ws/telemetry`：设备状态、传感器发现与订阅。

RGB 视频和媒体地址不进入本协议；RTSP/WebRTC 由独立媒体链路配置。软件包版本与协议版本彼此独立，
客户端必须先发送 `session.hello` 完成版本协商。

持续控制采用“动作边沿 + 服务端状态”的语义：交互层只在真实按下或抬起时发送
`manual.input`，系统键盘自动重复不应产生重复消息。取得控制租约后可发送
`manual.configure`，使用 `horizontal_speed_m_s`、`vertical_speed_m_s` 和
`yaw_rate_deg_s` 调整水平速度、升降速度与偏航角速度。`control.acquired`、
`manual.configure` 以及后续手动控制状态都会返回服务端当前采用的三项实际值，客户端应以响应值
更新显示。
