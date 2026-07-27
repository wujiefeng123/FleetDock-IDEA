# Project AirSim 仿真配置

这里保存 FleetDock 单机和多机启动脚本共用的场景模板与机器人 Profile。文件是 FleetDock 的配置叠加层；`prepare_projectairsim_drone.py` 会把它们与 Project AirSim 自带的 PX4 WSL2 配置合并，再把完整生成文件写入 Project AirSim 的 `example_user_scripts/sim_config/`。

## 机器人 Profile

- `basic`：保留上游 PX4 机型的第三人称 `Chase` 观察相机、机载向下 `DownCamera` 和飞控传感器；标准 RGB 主相机不会在存在机载视角时选择 `Chase`。
- `video`：增加一路 640×480、30 FPS 的前视 RGB 相机，保留仿真窗口使用的第三人称 `Chase` 视角，
  仅关闭 `DownCamera`，用于低延迟控制器视频。
- `depth`：在 `basic` 基础上增加 `FrontDepthCamera`，同时发布 640×480 RGB 和透视深度图。相机沿机体
  前向安装在 `x=0.45 m`，位于前桨盘最前缘之外，默认画面不会包含机身和螺旋桨。
- `stereo`：在 `basic` 基础上增加 `StereoLeft` 和 `StereoRight`，双目基线为 0.12 米；两台相机同样
  位于 `x=0.45 m` 的前向安装面，确保左右图像不被前桨遮挡。
- `lidar`：在 `basic` 基础上增加 `lidar1`，配置为 16 线、100 米量程、每秒 100000 点、10 Hz 水平扫描。
- `radar`：在 `basic` 基础上增加前向 `radar1`，发布检测和目标轨迹，用于动态障碍预测。

在 `examples/env.bat` 中选择 Profile：

```bat
set "PROJECTAIRSIM_ROBOT_PROFILE=stereo"
```

旧变量仍然兼容。推荐通过 `PROJECTAIRSIM_SENSOR_PROFILES` 直接声明无人机需要组合的传感器 Profile：

```bat
set "PROJECTAIRSIM_SENSOR_PROFILES=stereo,lidar"
```

Profile 按声明顺序合并，`stereo` 会创建左右两台相机，因此组合项表示可复用的传感器配置模块，而不是单个物理传感器。双目与 LiDAR 同时启用时直接声明 `stereo,lidar`；这只代表两类传感器同时发布数据，不代表环境层已完成感知融合。未知 Profile、重复 Profile、重复传感器 ID 和循环引用会在加载场景前报错。`PROJECTAIRSIM_SENSOR_PROFILES` 配置后优先于旧的 `PROJECTAIRSIM_ROBOT_PROFILE`。

以上配置同时适用于 `run_dev.bat` 和 `run_dev_multi.bat`。多机未配置逐机组合时，非 `basic` 默认组合仍只应用于 Drone1；其余无人机回退到 `basic`。旧式选择方式可以覆盖为：

只通过 RTSP 使用相机画面时，可以在本机 `env.bat` 设置
`AIRSIM_ARGS=-RenderOffScreen -NoVSync`。启动脚本会把参数原样传给 Unreal 可执行文件，减少主视口
渲染和垂直同步开销；需要直接观察仿真窗口时应移除 `-RenderOffScreen`。
需要可见窗口时可设置 `AIRSIM_ARGS=-ResX=1280 -ResY=720 -Windowed -ForceRes`，明确使用 1280×720
窗口分辨率。

```bat
set "MULTI_PROJECTAIRSIM_SENSOR_DRONES=all"
```

或只指定部分无人机：

```bat
set "MULTI_PROJECTAIRSIM_SENSOR_DRONES=Drone1,Drone3"
```

推荐通过 `MULTI_PROJECTAIRSIM_SENSOR_PROFILES` 为每架无人机声明不同组合，Profile 之间使用 `+`，无人机之间使用 `;`：

```bat
set "MULTI_PROJECTAIRSIM_SENSOR_PROFILES=Drone1=stereo+lidar;Drone2=depth"
```

该示例让 Drone1 使用双目与 LiDAR、Drone2 使用前向 RGB/深度相机，未列出的 Drone3 等成员使用 `basic`。逐机组合配置优先于 `MULTI_PROJECTAIRSIM_SENSOR_DRONES`，并会拒绝不存在的无人机名称。

## 自定义配置

`PROJECTAIRSIM_ROBOT_CONFIG_PATH` 可以指向完整的 Project AirSim 机器人 JSONC，也可以指向包含
`base-robot-config`、`additional-sensors` 和可选 `sensor-overrides` 的 FleetDock 叠加配置；覆盖项通过
传感器 `id` 修改基础配置，上游模板不存在该 ID 时忽略，以兼容不同 Project AirSim 版本。自定义机器人文件与旧式单 Profile 入口兼容；需要
组合多个自定义层时，应在自定义 JSONC 中使用 `include-profiles`，不能同时设置多项
`PROJECTAIRSIM_SENSOR_PROFILES`。`PROJECTAIRSIM_SCENE_CONFIG_PATH` 同理，可以指向完整场景，或通过
`base-scene-config` 继承 Project AirSim 自带场景。

传感器 Profile 只负责让仿真器创建并发布数据。图像和点云仍需通过 Project AirSim Python Client 订阅；启动脚本不会自动把原始大数据写入 FleetDock 事件总线。

## RGB 相机 RTSP 桥接

普通 RGB 视频使用 RTSP 作为仿真与真机共享的外部传输边界。FleetDock 提供
`fleetdock.simulation.ProjectAirSimRtspBridge`：它在 SDK 仿真集成层内部订阅 Project AirSim RGB topic，通过容量为一帧的
实时队列交给进程级 GStreamer RTSP Server，以低延迟 H.264 发布。业务代码仍只配置
`camera_stream_uri` 并调用 `device.sensors.subscribe(SensorKind.RGB_CAMERA, ...)`，不接触原生 client、
topic、编码器或 RTSP Server。

Windows 运行前安装 Python 集成依赖，`projectairsim-rtsp` 会通过官方 Wheel 安装 GStreamer 1.28：

```powershell
python -m pip install -e ".[px4,vision,projectairsim-rtsp]"
```

Linux Conda 环境还需按仓库主 README 安装 `gstreamer`、`gst-python`、`gst-rtsp-server`、Base/Good/Bad
插件、`pygobject` 和 `openh264`。默认发布地址为 `rtsp://127.0.0.1:8554/Drone1_camera_front`；可通过
`FLEETDOCK_CAMERA_STREAM_URI` 改写，但托管地址必须指向本机。同一进程、同一端口的多路 Bridge 共享
GStreamer Server 并使用各自的 RTSP 路径；SDK 接收端使用 TCP、零延迟抖动缓冲和单帧 `appsink`，只
交付最新解码帧。退出上下文时只注销本路流，最后一路关闭后才停止 Server。

`drone_camera_debug.py` 和 `drone_controller_debug.py` 展示完整桥接生命周期。RTSP 只用于普通 RGB
视频；深度、双目、点云和需要严格采集时间同步的数据继续使用标准强类型传感器后端，不压缩为普通
H.264 视频。

需要自定义视角时，可以复制对应机器人 Profile，并修改相机 `origin.xyz`、`origin.rpy-deg` 和
`capture-settings[].fov-degrees`，再通过 `PROJECTAIRSIM_ROBOT_CONFIG_PATH` 使用自定义配置。`xyz` 使用
机体系米制位置，当前 PX4 四旋翼的前桨中心约为 `x=0.253 m`、桨半径约为 `0.114 m`；前视相机若放在
`x<0.367 m` 的中心线附近，就可能把桨盘拍入画面。
