# 支持矩阵

| 产品/能力              | 成熟度      | 当前实现                                       | 适用范围                               |
| ---------------------- | ----------- | ---------------------------------------------- | -------------------------------------- |
| Mock Drone / Robot Dog | `simulated` | 内置 Mock 适配器                               | 零厂商依赖入门、业务流程和持续集成测试 |
| PX4 Generic            | `beta`      | MAVSDK/MAVLink 控制、事件及标准传感器流        | PX4 SITL 和受控真机验证                |
| Unitree Go2/G1         | `simulated` | Mock 适配器；`unitree-sdk2` 仅保留显式可选依赖 | SDK 学习和业务流程测试，不代表真机接入 |
| DJI 产品               | `planned`   | 仅 ProductProfile                              | 不能创建真实控制设备                   |
| 自定义产品             | `planned`   | 需自行注册适配器                               | 第三方扩展                             |

基础运行环境为 Python 3.11、3.12。PX4 可选依赖当前声明为 `mavsdk>=3.15.3`；正式部署应使用项目验证并锁定的 MAVSDK/PX4 组合。

PX4 当前覆盖连接、标准遥测、解锁、起降、返航、Mission、Offboard 速度/位置目标及路线执行；标准传感器后端复用同一 MAVSDK/MAVLink 连接，覆盖 IMU、磁力计、GNSS、气压、测距和空速流。具体飞控未持续输出某类消息时，应以 `device.sensors.status(...).is_stale(...)` 判断运行期可用性。相机像素不通过 MAVLink 遥测传输；PX4 后端可以从 MAVLink `VIDEO_STREAM_INFORMATION` 自动取得 RTSP 等视频 URI，再通过可选 OpenCV 后端解码，也可以显式接入 RTSP、视频文件或本机摄像头。多机跟随、共享避障和 SDK 平滑航线属于高级能力，应在目标机型和现场安全边界内单独验收。

本地可视化调试通过 `device.debug.camera()`、`device.debug.dashboard()` 或回调代理消费标准样本，支持
RGB/深度/双目画面以及无人机、足式设备和自定义传感器 HUD。该能力依赖 `vision` 可选依赖和图形桌面，
不会提高某个厂商真机后端的成熟度；某类数据能否显示仍以对应 `SensorBackend` 是否实际提供标准样本为准。

Unitree Go2/G1 与 DJI 当前没有真机传感器后端；公共 `SensorBackend`、标准足式设备模型和厂商/自定义命名空间已经预留扩展契约，但不能据此宣称完成真机接入。当前源码也没有导入 `unitree-sdk2`，因此该依赖不随 `integrations`、`full-prod` 或 `full-dev` 安装；待真实适配器和协议测试落地后再启用。
