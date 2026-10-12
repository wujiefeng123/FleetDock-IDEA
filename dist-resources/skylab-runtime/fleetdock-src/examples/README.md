# FleetDock 示例导航

运行示例前先在仓库根目录安装 SDK；零依赖 Mock 示例不需要安装厂商 SDK：

```powershell
python -m pip install -e .
```

运行 `01` 至 `10` 的 PX4 示例前，再安装 `python -m pip install -e ".[px4]"`。

## 新手路径

`quickstart/` 先用 Mock 展示跨设备标准流程，再使用真实的 `px4.generic` 适配器连接 PX4 SITL 或真机。PX4 示例运行前需要启动 PX4，并让 MAVLink 输出到本机 UDP `14555`；可用 `FLEETDOCK_PX4_ENDPOINT` 覆盖单机端点。多机示例默认使用 `14555`、`14556`，可通过逗号分隔的 `FLEETDOCK_PX4_FLEET_ENDPOINTS` 覆盖。建议按编号运行：

| 示例                   | 学习内容                                   |
| ---------------------- | ------------------------------------------ |
| `00_mock_drone.py`     | 无厂商依赖的无人机连接、Plan 和返航确认    |
| `00_mock_robot_dog.py` | 无厂商依赖的机器狗连接、移动和资源释放     |
| `01_connect.py`        | 创建设备、连接和释放资源                   |
| `02_takeoff_rtl.py`    | 室外/SITL 全球定位就绪、起飞确认和 RTL     |
| `03_move.py`           | 室内外通用起飞、本地目标和安全降落         |
| `04_events.py`         | PX4 标准遥测事件订阅                       |
| `05_plan.py`           | 室内外通用圆形航线、必执行降落和结果检查   |
| `06_fleet.py`          | 两架 PX4 的批量连接和诊断                  |
| `07_diagnostics.py`    | PX4 成熟度、能力、就绪报告和错误响应       |
| `08_async_task.py`     | PX4 后台起飞/降落任务会话、状态和等待结果  |
| `09_routes.py`         | 为 PX4 生成标准航线，不发送飞行命令        |
| `10_px4_sensors.py`    | 发现并订阅 PX4 标准 IMU、GNSS 和气压计数据 |
| `00_custom_sensor.py`  | 用 Mock 后端定义和订阅应用自定义传感器     |

## 高级验收场景

`demo/` 面向已经完成 Quick Start 的开发者，覆盖 PX4 起飞、遥测、事件、航线、Fleet、动态跟随、编队、安全与避障。它们依赖 PX4 SITL、Project AirSim 或对应连接环境，不应作为第一次接入时复制的最小模板。

`drone_plan.py` 和 `fleet_plan.py` 是当前推荐的主 Plan Demo：开发者只声明就绪、起飞、本地航线和
`ensure_land()`，PX4 适配器根据本地/全球定位自动选择控制链。GPS 航点、Mission 与 RTL 是明确的
室外能力，不能在无全球定位/Home 的室内环境中使用。多机批量 Plan 会逐机判断就绪；动态跟随还要求
所有成员有全球位置，或在 `DeviceMetadata.local_frame_id` 中明确声明同一个共享定位原点。

- 单机基础：`drone_unlock_takeoff.py`、`drone_event.py`、`drone_plan.py`
- 航线与移动：`drone_smooth_spiral.py`、`drone_gps_relative_move.py`
- 多设备：`fleet_plan.py`
- 跟随与编队：`fleet_follow*.py`
- 安全与避障：`drone_avoidance.py`、`fleet_shared_avoidance.py`
- 传感器到障碍物：`drone_lidar_obstacle_pipeline.py` 先展示不飞行的 LiDAR 数据管线；`drone_sensors_subscribe_lidar_avoidance.py`、`drone_sensors_subscribe_depth_camera_avoidance.py`、`drone_sensors_subscribe_stereo_avoidance.py`、`drone_sensors_subscribe_radar_avoidance.py`、`drone_sensors_subscribe_distance_avoidance.py` 和 `drone_sensors_subscribe_lidar_depth_fusion_avoidance.py` 分别保留各类传感器的避障剧本，全部通过 `avoidance.*` 创建转换器并显式使用 `device.sensors.subscribe()`
- 图形调试控制器：`packages/fleetdock-server/examples/drone_controller_debug.py` 只连接已经启动的 FleetDock WS 服务并打开 `PythonController`；GUI 只发送动作按下/抬起和心跳，普通 RGB 画面继续直接读取外部 RTSP，不塞入 WS。
- Python WS 客户端：`packages/fleetdock-server/examples/drone_client_debug.py` 使用 `FleetDockClient` 执行只读诊断，展示设备能力、状态和传感器列表，不启动服务端或图形界面。
- Project AirSim RTSP Bridge：`packages/fleetdock-server/examples/start_projectairsim_rtsp_bridge.bat` 只订阅仿真 RGB 相机并输出本地 H.264 RTSP 地址，不连接 PX4、不启动 WS、也不发布 WHIP；仿真时推荐先启动该脚本，再在另一个终端运行 `start_drone_server.bat`。
- Project AirSim Bridge 服务：`packages/fleetdock-server/examples/projectairsim_bridge_server.py` 是明确的仿真专用入口，负责创建 `ProjectAirSimRtspBridge`、托管 PX4 WS 服务，并可把生成的 H.264 RTSP 发布到 WHIP；真机入口不复用该 Bridge。
- Windows 通用无人机服务入口：`packages/fleetdock-server/examples/start_drone_server.bat` 读取可覆盖的 `FLEETDOCK_*` 环境变量，并以 `python src/fleetdock-cli/drone_server.py <命令行参数>` 形式逐项传入配置；它只消费真机、仿真器或独立媒体服务器已经提供的 RTSP 地址，是主推入口。Linux 和生产环境可直接运行该 Python 封装或使用安装后的 `fleetdock-server-drone` 命令。
- 传感器可视化调试：`drone_camera_debug.py` 使用同一个仿真 Bridge 和 `sensors.subscribe()`，把标准 `ImageFrame` 同时交给业务回调和后台 Dashboard；`drone_px4_sensor_dashboard.py` 在同一 HUD 显示 IMU、磁力计、GNSS 和气压计。Windows 运行图像示例前安装 `python -m pip install -e ".[px4,vision,projectairsim-rtsp]"`；Linux 还需按主 README 安装 GStreamer 原生运行库
- 高级 LiDAR 验收：`drone_lidar_search_avoidance_advanced.py` 显式展示安全出口选择、点云失效保护、随机螺旋搜索和底层订阅生命周期

`prepare_projectairsim_drone.py` 和启动脚本属于本地仿真环境准备工具，不是公共 SDK 用法示例。单机和多机共用的 `basic`、`video`、`depth`、`stereo`、`lidar`、`radar` 机器人 Profile 支持按需组合；`video` 专用于低延迟控制器画面，多机还可为每架无人机分配不同组合。配置方式见 [Project AirSim 仿真配置](projectairsim/README.md)。

Project AirSim 0.2.0 的 Python client 默认会在当前工作目录创建 `projectairsim_client.log`。FleetDock 的 Project AirSim demo 会预先把该依赖的日志配置为控制台输出，因此运行示例时不会生成这个文件。摄像头 demo 不要求业务代码创建原生 client；Bridge 内部负责原生订阅和清理。
