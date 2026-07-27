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
| `02_takeoff_rtl.py`    | 就绪检查、起飞确认和返航                   |
| `03_move.py`           | 起飞后的强类型本地目标                     |
| `04_events.py`         | PX4 标准遥测事件订阅                       |
| `05_plan.py`           | 圆形航线任务编排和结果检查                 |
| `06_fleet.py`          | 两架 PX4 的批量连接和诊断                  |
| `07_diagnostics.py`    | PX4 成熟度、能力、就绪报告和错误响应       |
| `08_async_task.py`     | PX4 后台任务会话、状态和等待结果           |
| `09_routes.py`         | 为 PX4 生成标准航线，不发送飞行命令        |
| `10_px4_sensors.py`    | 发现并订阅 PX4 标准 IMU、GNSS 和气压计数据 |
| `00_custom_sensor.py`  | 用 Mock 后端定义和订阅应用自定义传感器     |

## 高级验收场景

`demo/` 面向已经完成 Quick Start 的开发者，覆盖 PX4 起飞、遥测、事件、航线、Fleet、动态跟随、编队、安全与避障。它们依赖 PX4 SITL、Project AirSim 或对应连接环境，不应作为第一次接入时复制的最小模板。

- 单机基础：`drone_unlock_takeoff.py`、`drone_event.py`、`drone_plan.py`
- 航线与移动：`drone_smooth_spiral.py`、`drone_gps_relative_move.py`
- 多设备：`fleet_plan.py`
- 跟随与编队：`fleet_follow*.py`
- 安全与避障：`drone_avoidance.py`、`fleet_shared_avoidance.py`
- 传感器到障碍物：`drone_lidar_obstacle_pipeline.py` 先展示不飞行的 LiDAR 数据管线；`drone_sensors_subscribe_lidar_avoidance.py`、`drone_sensors_subscribe_depth_camera_avoidance.py`、`drone_sensors_subscribe_stereo_avoidance.py`、`drone_sensors_subscribe_radar_avoidance.py`、`drone_sensors_subscribe_distance_avoidance.py` 和 `drone_sensors_subscribe_lidar_depth_fusion_avoidance.py` 分别保留各类传感器的避障剧本，全部通过 `avoidance.*` 创建转换器并显式使用 `device.sensors.subscribe()`
- 图形调试控制器：`drone_controller_debug.py` 先通过 `fleetdock.simulation.ProjectAirSimRtspBridge` 把仿真 RGB 相机发布为 RTSP，再把 URI 注册到标准 PX4 设备；`camera_subscribe` 通过 `device.sensors.subscribe(SensorKind.RGB_CAMERA, ...)` 把标准帧交给 Controller，真机已有 RTSP 或厂商相机后端时不需要 AirSim Bridge。画面显示实际接收 FPS，持续按键输入以 5 Hz 更新，避免按界面帧率重复唤醒控制线程。连接、解锁、起飞、方向控制、悬停、降落和返航都由窗口通过公共 SDK 完成，Controller 底层不接触 AirSim、GStreamer 或原生 topic
- 传感器可视化调试：`drone_camera_debug.py` 使用同一个仿真 Bridge 和 `sensors.subscribe()`，把标准 `ImageFrame` 同时交给业务回调和后台 Dashboard；`drone_px4_sensor_dashboard.py` 在同一 HUD 显示 IMU、磁力计、GNSS 和气压计。Windows 运行图像示例前安装 `python -m pip install -e ".[px4,vision,projectairsim-rtsp]"`；Linux 还需按主 README 安装 GStreamer 原生运行库
- 高级 LiDAR 验收：`drone_lidar_search_avoidance_advanced.py` 显式展示安全出口选择、点云失效保护、随机螺旋搜索和底层订阅生命周期

`prepare_projectairsim_drone.py` 和启动脚本属于本地仿真环境准备工具，不是公共 SDK 用法示例。单机和多机共用的 `basic`、`video`、`depth`、`stereo`、`lidar`、`radar` 机器人 Profile 支持按需组合；`video` 专用于低延迟控制器画面，多机还可为每架无人机分配不同组合。配置方式见 [Project AirSim 仿真配置](projectairsim/README.md)。

Project AirSim 0.2.0 的 Python client 默认会在当前工作目录创建 `projectairsim_client.log`。FleetDock 的 Project AirSim demo 会预先把该依赖的日志配置为控制台输出，因此运行示例时不会生成这个文件。摄像头 demo 不要求业务代码创建原生 client；Bridge 内部负责原生订阅和清理。
