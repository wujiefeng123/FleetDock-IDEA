# FleetDock

FleetDock 是面向协同设备控制的 Python SDK。当前版本为 `0.4.0`：内置 Mock 无人机/机器狗用于零依赖学习，PX4 集成为 `beta`，Unitree Go2/G1 仅由 Mock 适配器支撑并标记为 `simulated`，DJI 产品目前是 `planned` 的产品画像，不能视为真机已接入。完整边界见 [支持矩阵](docs/support-matrix.md)。

首次学习建议先使用不依赖厂商 SDK、仿真器或真机的 Mock 示例：

```powershell
python -m pip install -e .
python examples/quickstart/00_mock_drone.py
python examples/quickstart/00_mock_robot_dog.py
```

`examples/quickstart/` 先用两个 Mock 示例讲解跨设备标准流程，再以真实 `px4.generic` 为主线连接 PX4 SITL 或真机；PX4 部分默认使用 `udpin://0.0.0.0:14555`，按连接、起飞返航、移动、事件、Plan、Fleet、诊断错误、异步任务和航线生成组织。复杂跟随、避障和 Project AirSim 场景继续保留在 `examples/demo/`。完整学习顺序、运行前提和高级场景索引见 [示例导航](examples/README.md)。即时设备方法成功只表示命令被适配器或飞控接受，不表示物理动作已经完成，详见 [PX4 命令语义](docs/px4-command-semantics.md)。公共 API 的稳定范围和弃用规则见 [API 稳定性](docs/api-stability.md)。

新项目可从 `fleetdock.api` 使用精简稳定入口；历史 `from fleetdock import ...` 继续兼容。事件、路线、安全和避障等高级类型分别从对应领域模块导入，以降低顶层 API 的长期兼容成本。

需要记录设备标准命令生命周期时，可通过 `device.add_command_observer(...)` 订阅 `started`、`completed` 和 `failed` 阶段；观察器用于 IDE、诊断和审计集成，其异常不会改变设备命令结果，设备关闭时订阅会自动清理。

当前发布门禁包括：全仓源码 mypy 检查、Ruff 未定义/未使用符号检查、85% 行覆盖率、Python 3.11/3.12 单元测试、sdist/wheel 构建和 wheel 安装导入验证。Mock Quick Start 可在无外部设备的普通 CI 中运行；真实 PX4 SITL 烟雾测试通过 `FLEETDOCK_RUN_PX4_SITL=1` 显式启用。

`vision`、`full-prod` 和 `full-dev` 将 NumPy 限定为 `>=1.26,<2.4`，避免 NumPy 2.4 类型存根使用
Python 3.12 专属语法而破坏项目仍承诺支持的 Python 3.11 mypy 门禁。

## Fleet 动态跟随

`Fleet.follow(...)` 创建一个 leader 和多个 followers 的动态关系，并返回 `FollowSession`。
同一设备同一时间只能跟随一个 leader；默认冲突失败，只有显式使用
`FollowConflict.REPLACE` 才会把设备从旧会话迁移到新会话。当前版本负责关系、策略和冲突管理，
不会在创建会话时自动启动真实遥测与运动控制闭环；调用 `session.start(...)` 后才会启动 FleetDock
后台跟随执行器，按 `policy.mode` 或 `layout` 生成的相对槽位持续读取遥测并通过标准
`device.set_velocity(...)` 发出控制命令。`fleet.follow(...)` 仍是唯一多机协同入口；队形保持通过
`FormationLayout` 参数表达，不另设 `fleet.formation(...)`，避免开发者在“跟随”和“队形”两个入口间选择。
跟随执行器已接入 `SafetyPolicy` 安全前置策略：启动前会校验 follower 的移动与停止能力；运行中会检查
本地位置遥测新鲜度、leader/follower 坐标系一致性、follower 间最小间距、最大 leader 距离、限高、电量阈值
和 failsafe 状态，并按策略触发悬停、停止、返航或急停。生产部署仍应结合厂商 SDK 和上层业务配置更完整的
禁飞区、急停链路和现场安全策略。
推荐通过 `FollowPolicy.camera(...)`、`FollowPolicy.chase(...)` 等工厂方法创建策略；
示例和业务代码统一使用这个入口，避免开发者直接依赖内部策略数据类。

```python
from fleetdock import (
    FormationLayout,
    FollowConflict,
    FollowExecutionOptions,
    FollowPolicy,
    SafetyAction,
    SafetyPolicy,
)

safety = SafetyPolicy(
    max_relative_altitude_m=80.0,
    max_distance_from_leader_m=100.0,
    min_separation_m=6.0,
    telemetry_timeout_s=2.0,
    battery_return_fraction=0.2,
    action_on_violation=SafetyAction.HOVER,
)

session = fleet.follow(
    leader=drone_c,
    followers=[drone_a, drone_b],
    layout=FormationLayout.v_shape(spacing_m=8.0, count=2, altitude_step_m=1.0),
    safety_policy=safety,
)

session.add(drone_d, policy=FollowPolicy.relative(x_m=-6.0))
session.remove(drone_a)

camera = fleet.follow(
    leader=drone_b,
    followers=[],
    safety_policy=safety,
)
camera.add(view_drone, policy=FollowPolicy.camera(distance_m=15.0, side_m=5.0))
camera.add(drone_c, policy=FollowPolicy.chase(distance_m=12.0))
camera.start(
    FollowExecutionOptions(
        interval_s=0.2,
        min_separation_m=6.0,
        max_follow_distance_m=100.0,
        default_max_speed_m_s=7.0,
    )
)

other = fleet.follow(
    leader=drone_d,
    followers=[drone_a],
    conflict=FollowConflict.REPLACE,
)

print(camera.status().followers["drone-a"].distance_to_leader_m)

camera.stop()
session.stop()
fleet.unfollow(other.id)
```

三机 PX4 SITL 环境就绪后，可以运行 `python examples/demo/fleet_follow_formation.py` 查看 `SafetyPolicy`、
`FormationLayout` 和 `fleet.follow(..., layout=...)` 的真实队形跟随流程；也可以运行
`python examples/demo/fleet_follow_story.py` 查看视角跟随、追踪跟随、轨迹跟随和相对队形的组合演示。
不连接真实设备时，可以运行 `python examples/demo/fleet_follow.py` 查看关系编排、冲突失败和显式替换的完整输出。

`followers` 和策略参数的语义：

- `followers=None`：默认让 Fleet 中除 leader 外的全部设备跟随，适合同一批 follower 使用同一种 `policy`。
- `followers=[]`：创建空会话，后续用 `session.add(...)` 动态加入 follower。
- `follower_policies={drone: policy}`：创建时为不同 follower 指定不同策略，适合多种跟随模式混合编队。
- `layout=FormationLayout...`：按 follower 顺序分配队形槽位，并转换为 `FollowPolicy.relative(...)`；`layout` 不能和 `policy` 或 `follower_policies` 同时传入。
- `safety_policy=SafetyPolicy(...)`：为本次会话声明限高、间距、电量、遥测超时、最大距离和保护动作；也可在 `session.start(safety_policy=...)` 覆盖。

产品级跟随执行器提供：

- `FollowMode.RELATIVE`：固定相对队形。
- `FollowMode.CHASE`：朝向 leader 并保持目标距离。
- `FollowMode.TRAIL`：追 leader 历史轨迹点。
- `FollowMode.CAMERA`：保持侧后方视角并朝向 leader。
- `FollowMode.BAND`：只在距离带外移动，距离合适时悬停。

`FollowController.compute(...)` 是不执行设备 I/O 的纯计算入口，适合独立验证策略；
`FollowExecutor` 只负责生命周期、遥测读取、安全仲裁、命令发送和事件输出。`DeviceState` 记录
`local_position_frame`、`local_position_updated_at_s` 等状态元数据，调用方统一使用
`device.state.snapshot()` 获取线程安全的不可变快照。`device.state` 只负责汇总标准事件，业务代码
不得直接修改；持续控制和安全判断应在每轮重新取得快照，避免长期持有过期状态。

运行中可以调用 `session.pause()`、`session.resume()`、`session.set_policy(...)`、
`session.transition_to(...)`、`session.assign(...)`、`session.update_leader(...)`、
`session.stop_execution()` 和 `session.status()`。执行器会发布
`custom.follow.session.started`、`custom.follow.session.failed`、
`custom.follow.policy.changed`、`custom.follow.leader.changed`、
`custom.follow.follower.telemetry_lost`、`custom.follow.follower.command_failed` 和
`custom.follow.follower.target_updated` 等自定义事件，也会发布标准安全事件
`safety.violation.raised` 和 `safety.action.triggered`，便于 UI、日志系统或上层应用统一观察安全状态。

FleetDock 是一个 Python SDK 骨架，目标是用统一设备对象控制无人机、机器狗、人形机器人以及其他协同设备。开发人员优先面对 FleetDock 的标准设备对象和标准命令，不需要在业务代码里直接处理 MAVLink、MAVSDK、厂商 SDK 或仿真器协议细节。

## 当前能力

- 标准设备对象：`Drone`、`RobotDog`
- 标准命令：`connect`、`unlock`、`takeoff`、`land`、`move`、`stop`、`emergency_stop`、`stand`、`sit`、`get_status`
- 产品、设备类型、厂商、固件版本和能力枚举：`ProductType`、`DeviceCategory`、`VendorName`、`FirmwareVersion`、`CapabilityName`
- 产品注册结构：`ProductRegistry`、`ProductProfile`、`DeviceFactory`、`AdapterFactory`、`DeviceAdapter`
- 多机跟随与队形：`Fleet.follow(...)`、`FollowPolicy`、`FormationLayout`、`FollowSession`
- 安全前置策略：`SafetyPolicy`、`SafetySupervisor`、`SafetyAction`
- 避障与局部绕行：`Obstacle`、`ObstacleShape`、`ObstacleSnapshot`、`ObstacleProvider`、`ObstacleCapacityPolicy`、`AvoidancePolicy`、`AvoidanceDimensions`、`AvoidancePlanner`、`AvoidancePlannerBackend`、`AvoidancePlanRequest`、`AvoidancePlanResult`、`PlannerStatus`、`AvoidanceStrategy`、`device.avoidance`、`fleet.avoidance`
- 两种初始化模式：
  - FleetDock 创建模式：开发人员选择产品枚举，并传入连接参数。
  - 外部托管模式：开发人员把已经创建好的原生 SDK 对象交给 FleetDock 托管。
- Mock 适配器：`MockDroneAdapter`、`MockRobotDogAdapter`
- PX4 MAVSDK 适配器：`Px4MavlinkAdapter`
- 标准请求/响应模型：`StandardCommand`、`StandardResponse`
- 公共异常模型：`FleetDockError`、`FleetDockValidationError`、`ProductNotRegisteredError`、`AdapterNotFoundError`
- 标准设备事件能力：`EventType`、`device.events`、`fleet.events`
- 兼容进程内消息总线：`EventHub`、`MessageBus`
- 后台生命周期：`ManagedWorker`、`WorkerStatus`，统一提供启动、停止、等待、状态和错误诊断。

## 避障与自动绕行

FleetDock 提供跨设备的 SDK 侧避障能力，不把公共 API 绑定到 PX4、DJI 或 Unitree 的厂商对象。
业务或适配器通过 `ObstacleProvider.snapshot()` 提供标准 `ObstacleSnapshot`，每个 `Obstacle` 明确声明
坐标系、位置米制坐标、可选速度、几何外形、置信度、观测时间和 TTL。球形障碍物使用 `radius_m`，
屋顶、墙体和禁入箱体可用 `ObstacleShape.BOX` 配合 `size_m` 表示，柱子、树干等可用
`ObstacleShape.CYLINDER`。设备通过
`device.avoidance.add_provider(provider)` 接入通用感知来源。传感器发现和数据流统一由
`device.sensors.get()/subscribe()` 显式管理，回调把强类型样本交给长期存活的 Provider；
`AvoidanceManager` 不创建或订阅传感器。运行期解除通用来源可使用 `remove_provider()`。设备通过
`device.avoidance.configure(AvoidancePolicy(strategy=...))` 配置策略。新代码优先只选择一个
`AvoidanceStrategy` 枚举，例如 `HOLD_POSITION`、`RETURN_TO_LAUNCH`、`EMERGENCY_STOP` 或
`LOCAL_REPLAN`。
`AvoidancePolicy.dimensions` 默认使用 `AvoidanceDimensions.AUTO`：无人机按 3D 风险评估，机械狗、
人形机器人和其他地面设备按 2D 水平避障评估；需要跨楼层、楼梯、无人机贴地飞行或地面机器人处理高度差时，
仍可显式传入 `TWO_D` 或 `THREE_D`。
未在 `route(..., avoidance=...)` 显式传入策略时，SDK route 执行器会读取设备级默认策略；
显式传入的 `avoidance` 仅覆盖本次 route，传入 `AvoidancePolicy.disabled()` 可对单次路线关闭 SDK 避障。
运行期动态启停使用 `device.avoidance.disable()` 和 `device.avoidance.enable()`；它们会保留已配置的
策略和 provider，不需要重新构造策略对象。

```python
from fleetdock import AvoidanceDimensions, AvoidancePlanner, AvoidancePolicy, AvoidanceStrategy

device.avoidance.configure(
    AvoidancePolicy(
        strategy=AvoidanceStrategy.HOLD_POSITION,
        dimensions=AvoidanceDimensions.THREE_D,
        planner=AvoidancePlanner.TRAJECTORY_ROLLOUT,
        min_clearance_m=4.0,
    )
)
```

混合编队推荐把共享感知源注册到 `fleet.avoidance`，由 Fleet 维护共享世界模型，再通过轻量只读 provider
提供给各成员设备的 `device.avoidance` 做最终动作决策。这样真实雷达、地图或视觉 provider 只注册和释放一次，
不会因为同时服务多台无人机、机械狗或人形机器人而被重复 `snapshot()` 或重复 `close()`；队内其它设备也会作为
动态障碍物进入共享模型，当前设备在评估时会自动排除自己。`fleet.avoidance` 默认用 50 ms 的共享快照缓存让同一控制周期内的多设备复用感知结果，
可通过 `snapshot_cache_ttl_s` 调整或设为 0 关闭。

```python
fleet.avoidance.add_provider(lidar_or_map_provider).configure(
    default_policy=AvoidancePolicy(
        strategy=AvoidanceStrategy.LOCAL_REPLAN,
        fallback_strategy=AvoidanceStrategy.HOLD_POSITION,
    ),
    policies={
        "robot_dog": AvoidancePolicy(strategy=AvoidanceStrategy.STOP),
    },
    # 仅当所有设备确实共享同一本地坐标原点时启用；否则应配置 transformer。
    assume_shared_local_origin=True,
)
```

`fleet.avoidance.configure_world(...)` 可设置共享世界坐标系和可选 `FleetObstacleTransformer`。SDK 内置只可靠处理
同一原点下的 `LOCAL_NED`/`LOCAL_ENU` 轴转换；如果不同设备有不同本地原点、需要从 GPS/地图坐标归一，或 provider
输出 `BODY`/`GEODETIC` 坐标，必须由业务或适配器注入 transformer，无法安全转换的障碍物应返回 `None` 丢弃。
在未配置 transformer 且未显式声明 `assume_shared_local_origin=True` 时，SDK 不会把成员本地坐标直接合并，
避免把不同飞控、机器人各自的本地原点误认为同一个世界原点。
完整的仿真飞行共享 Provider 示例见 `examples/demo/fleet_shared_avoidance.py`：该 demo 会连接 PX4 仿真、
通过 Fleet 同时解锁并起飞两架无人机。Provider 只向 `fleet.avoidance` 注册一次，两架飞机执行航线时
会分别识别同一个共享障碍物，自动插入局部绕行航点，绕过障碍后继续飞抵终点并统一返航；仿真应使用
`examples/run_dev_multi.bat` 启动。

所有避障聚合入口都有容量保护。`ObstacleCapacityPolicy` 默认最多保留 512 个标准障碍物、单个 provider 每次最多贡献
256 个障碍物，并在聚合阶段做 TTL 过滤、`obstacle_id` 去重和保留优先级裁剪（动态目标、置信度更高、观测时间更新的
目标优先）。这只能保护 FleetDock 标准障碍物列表；激光雷达点云、图像帧、厂商 SDK 缓存等大对象仍应在 provider 内部
先做聚类、限流和释放，不能直接把原始点云当作上万个 `Obstacle` 长期塞入 SDK。

公共 `Route`、`Waypoint` 和 `route(..., altitude=...)` 的高度仍使用“向上为正”的米制高度；障碍物必须按
自身 `frame` 声明坐标语义。例如 `CoordinateFrame.LOCAL_NED` 使用北-东-地坐标系，z 轴向下为正，
同一 18 m 飞行高度对应障碍物 `position_m.z == -18.0`。SDK 在评估风险前会把当前位置、前视采样点和
障碍物统一到同一局部坐标系，避免 route 高度与 PX4/LOCAL_NED 下轴混用。

`AvoidancePolicy.strategy` 支持常用策略，开发者无需再组合多个布尔开关：

- `AvoidanceStrategy.HOLD_POSITION`：保持当前位置；无人机通常悬停，机器狗等地面设备会降级为停止。
- `AvoidanceStrategy.RETURN_TO_LAUNCH`：风险命中时请求返航；设备不支持返航时降级为停止。
- `AvoidanceStrategy.STOP` 和 `AvoidanceStrategy.EMERGENCY_STOP`：分别用于普通停止和最高风险急停。
- `AvoidanceStrategy.LOCAL_REPLAN`：FleetDock SDK route 执行器会在风险命中时调用 planner backend。
  默认后端是
  `AvoidancePlanner.TRAJECTORY_ROLLOUT`。该 planner 是 SDK 内置的纯 Python 局部轨迹采样实现，
  参考 DWA/DWB 的“候选轨迹生成、碰撞验证、代价评分”思路，不依赖 ROS、OMPL 或 C++ 扩展。它会尝试
  左、右、上、下及组合绕行候选，对每条候选轨迹重新检查所有当前障碍物，选出安全余量和路径代价更合适
  的临时点，发布 `navigation.route.replanned`，绕过障碍后回到原路线继续执行；绕行不可用或超过次数限制时，
  默认按 `fallback_strategy=AvoidanceStrategy.HOLD_POSITION` 保持位置，也可以显式设置为返航等策略。
- `AvoidanceStrategy.BLOCK`：只阻断当前 route，不主动发送设备动作，适合上层系统自行接管。

阻断类策略命中碰撞风险后，SDK 发布
`safety.collision_risk.raised` 和 `safety.avoidance.action.triggered`，并按策略触发安全动作。
局部绕行运行在 SDK 路线执行层；如果调用方强制使用飞控或厂商原生 Mission，SDK 不承诺动态改线，
会要求回到 SDK route 执行以保持可观察、可测试和可降级。临时绕行点不会占用原始 `Route.waypoints`
序号，后续原始航点的到达事件和航点动作会继续使用原来的 `waypoint_index`。
FleetDock 默认不在 `pyproject.toml` 中引入第三方避障库，避免基础 SDK 在 Windows 和 Linux 上被 ROS2、
C++ 编译链或 GPL 许可证绑定。后续接入 Nav2、OMPL、EGO/Fast-Planner 等专业栈时，会作为可选 external
planner backend 或独立 extra 暴露，公共入口仍保持 `AvoidancePolicy(strategy=LOCAL_REPLAN, planner=...)`。
自定义后端实现 `AvoidancePlannerBackend.plan(request)` 即可接入；FleetDock 负责聚合 provider、归一坐标系、
执行 fallback、发布事件和插入临时 route 点，后端只负责返回 `AvoidancePlanResult`。

```python
from fleetdock import (
    AvoidancePlanResult,
    AvoidancePlanner,
    AvoidancePolicy,
    AvoidanceStrategy,
    PlannerStatus,
    Vector3,
)

class MyExternalPlanner:
    def plan(self, request):
        # request.obstacles、request.start_m、request.target_m 均已由 SDK 归一到同一局部坐标系。
        return AvoidancePlanResult(
            PlannerStatus.REPLAN,
            detour_points_m=(Vector3(request.start_m.x, request.start_m.y + 3.0, request.start_m.z),),
            message="external planner selected detour",
        )

device.avoidance.register_planner_backend(AvoidancePlanner.EXTERNAL_PROCESS, MyExternalPlanner())
device.avoidance.configure(
    AvoidancePolicy(
        strategy=AvoidanceStrategy.LOCAL_REPLAN,
        planner=AvoidancePlanner.EXTERNAL_PROCESS,
    )
)
```

产品能力按层级声明：`OBSTACLE_SENSING` 表示可接入障碍物来源，`COLLISION_PREDICTION` 表示可做风险预测，
`OBSTACLE_AVOIDANCE` 表示可执行悬停、停止、返航或急停等避障动作，`LOCAL_REPLANNING` 表示可在 SDK route
中插入临时绕行点。PX4、DJI profile 和 Unitree profile 都通过同一套能力枚举表达支持边界；当前 PX4
可通过 SDK route 跑通无人机演示，DJI 后续适配器可把同一策略映射到虚拟摇杆、航点任务或厂商原生能力，
Unitree/机械狗可优先使用二维策略和 stop/emergency_stop 降级。

单机 PX4 SITL 环境就绪后，可以运行：

```powershell
python examples/demo/drone_avoidance.py
```

该 demo 会在同一个脚本中演示两次：第一次风险触发悬停并由失败处理器返航，第二次启用自动绕行，
由 SDK 插入临时绕行点并继续完成路线。控制台会打印避障风险、动作触发和路线重规划事件。

## 项目结构

```text
fleetdock/
  adapters/                # 厂商、协议或系统适配器
    base.py                # DeviceAdapter 基类
    mock.py                # 本地测试用模拟适配器
    px4_mavlink/           # PX4 MAVSDK 适配器包
      client.py            # MAVSDK 同步客户端门面
      adapter.py           # PX4 标准设备适配器
  devices.py               # 标准设备对象
  event_models.py          # 标准事件枚举、强类型数据对象和载荷转换
  events.py                # 单设备标准事件流
  message_bus.py           # 兼容的进程内消息总线
  follow_models.py         # Fleet 动态跟随策略模型
  obstacles.py             # 标准障碍物、障碍物快照和 provider 协议
  avoidance.py             # 单设备/Fleet 避障策略、共享世界模型、容量保护、风险判断和局部绕行管理器
  avoidance_backends/      # 内置轨迹采样 planner 与后续 ROS2/外部 planner 后端
  fleet_events.py          # Fleet 聚合设备事件订阅
  fleet.py                 # Fleet 容器和跟随会话
  device_factory.py        # DeviceFactory 和产品专属链式构造器
  factory.py               # AdapterFactory
  routes.py                # 航点、航线和内置航线生成器的公共入口
  task_models.py           # 任务状态、结果、选项和上下文模型的公共入口
  tasking/route_execution.py # 航线与平滑运动执行实现
  device_tasks.py          # 单设备任务编排类型的公共入口
  fleet_tasks.py           # Fleet 任务编排类型的公共入口
  plans.py                 # Plan 和 ParallelPlan 高级计划入口
  tasks.py                 # 任务编排兼容入口，继续导出历史公共符号
  models.py                # 枚举、连接、产品、能力、响应和错误模型
  registry.py              # ProductRegistry 和内置产品配置
packages/
  fleetdock-server/        # 独立 Python WS 服务端、客户端和图形控制器
  fleetdock-js/            # 浏览器与 Node.js 的 TypeScript WS 客户端
protocol/                  # WS 协议、Schema、AsyncAPI 和消息示例
examples/
  demo/                    # 面向 SDK 使用者的短示例
tests/                     # 基于标准库 unittest 的测试
```

## 快速开始

FleetDock 创建模式下，推荐使用 `DeviceFactory` 的产品专属构造器。开发人员先选择产品，再通过该产品的 `params(...)` 方法填写明确的连接参数；业务设备编号、展示名、标签等信息放在 `DeviceMetadata` 中，不再作为 SDK 初始化的必填项。

```python
from fleetdock import DeviceFactory, DeviceMetadata, Drone, RelativeMove2D, RobotDog

drone: Drone = (
    DeviceFactory.create()
    .px4_generic()
    .params(endpoint="udpin://0.0.0.0:14555", timeout_s=10.0)
    .metadata(DeviceMetadata(device_id="drone-001", name="演示无人机"))
    .build()
)

print(drone.connect())
print(drone.unlock())
print(drone.takeoff(altitude=10))

dog: RobotDog = (
    DeviceFactory.create()
    .unitree_go2()
    .params(network_interface="eth0")
    .metadata(DeviceMetadata(device_id="dog-001", name="演示机器狗"))
    .build()
)

print(dog.connect())
print(dog.stand())
print(dog.move(RelativeMove2D(forward_m=1.0)))
```

外部托管模式适合已有项目已经创建了 MAVSDK、DJI 或其他厂商 SDK 对象的场景。FleetDock 负责把原生对象包装成统一设备对象。默认情况下原生对象仍归调用方所有，`device.close()` 不会关闭它；只有显式调用 `.managed_object(client, close_on_close=True)`，才表示把关闭责任交给 FleetDock。

```python
from fleetdock import DeviceFactory, DeviceMetadata, Drone

mavlink_client = create_existing_mavlink_client()

drone: Drone = (
    DeviceFactory.create()
    .px4_generic()
    .params(endpoint="udpin://0.0.0.0:14555")
    .managed_object(mavlink_client)
    .metadata(DeviceMetadata(device_id="px4-001", name="PX4 托管无人机"))
    .build()
)
```

更多短示例见 `examples/demo/`。

GPS 螺旋示例当前起飞至相对 Home 20 米，并使用 314.2 米 AMSL 航点（仿真 Home 约 294.2 米），为大学城场景的树冠留出额外安全高度。
`examples/demo/drone_gps_relative_move.py` 展示固定 GPS 中心点的螺旋航线：脚本不接收命令行参数，暂时把重庆大学城附近的 WGS84 中心经纬度和 AMSL 绝对海拔声明为常量，通过 `Route.spiral(..., gps_origin=center)` 计算全部 GPS 航点并在执行前打印。随后使用 `Plan(drone)` 等待定位和 Home，按 `STANDARD_THEN_FORCE` 策略解锁、起飞，并以 `MotionExecutionMode.SDK` 执行螺旋 GPS 航线，最后返航。航线显式使用 `HeadingMode.TANGENT`，使无人机机头沿当前航段切向转动；仿真相机固定在机身前方时，画面会随螺旋航点方向转动，而不是保持固定朝向横向平移。SDK 执行器会把每个 WGS84 航点转换为相对当前全球位置的本地北/东偏移，并把 AMSL 绝对海拔减去 Home 绝对海拔后用于高度控制，避免混淆绝对与相对高度。后续可直接把中心点常量替换为业务传入值。

## 统一移动目标

设备和计划统一使用 `move(target)`。开发者只需要表达“去一个点”或“相对当前位置移动”，
不需要直接选择 MAVLink 消息：

```python
from fleetdock import GpsPoint, Plan, RelativeMove2D, RelativeMove3D, RelativePoint2D, RelativePoint3D

drone.move(GpsPoint(31.2304, 121.4737, 120.0))
drone.move(RelativePoint3D(x_m=10.0, y_m=0.0, z_m=20.0))
drone.move(RelativeMove3D(forward_m=5.0, right_m=1.0, up_m=2.0))

dog.move(RelativePoint2D(x_m=3.0, y_m=1.0))
Plan(dog).move(RelativeMove2D(forward_m=2.0, yaw_deg=30.0)).run()
```

标准运动命令统一为 `CommandName.MOVE`，具体坐标语义由 `GpsPoint`、`RelativePoint3D`、
`RelativeMove3D` 等强类型目标表达，并由 `ProductProfile.supported_move_targets` 声明产品实际支持的
目标类型。`drone.goto_global(...)` 和 `drone.goto_local(...)` 只是便捷写法，内部仍发送同一个
`MOVE` 命令；SDK 不再为全球坐标和本地坐标维护重复的 `GOTO_GLOBAL/GOTO_LOCAL` 命令或能力枚举。

`GpsPoint.altitude_m` 是绝对海拔（AMSL，米），与 MAVSDK `goto_location` 一致。
`RelativePoint3D.z_m` 和 `RelativeMove3D.up_m` 向上为正。PX4 适配器把 GPS 目标交给
全球导航，把局部目标转换为 Local NED Offboard 位置目标；相对移动所需当前位置和姿态
由设备遥测提供。产品通过 `ProductProfile.supported_move_targets` 声明支持的目标类型，
例如机器狗默认只支持二维局部点和二维相对位移，收到 GPS 或三维目标时返回标准失败响应。

持续速度控制是航线跟踪、编队和跟随控制器使用的高级能力，使用
`device.set_velocity(...)`，不与普通目标式 `move(target)` 混用。`x/y/z` 只表达方向，SDK 会先归一化再乘以
`speed`；`speed=0` 表示严格停止，非零速度不能搭配零方向。任务局部坐标默认 `x` 向前/北、`y` 向右/东、
`z` 向上，各厂商适配器负责转换到 MAVSDK、DJI SDK、Unitree SDK 等原生坐标系。

`Waypoint.from_target(...)` 可直接包装标准位置目标，`route.targets` 可取回一组能逐个交给
`device.move(...)` 或 `Plan(device).move(...)` 的目标。所有内置航线算法仍在局部米制平面内
计算；`circle`、`ellipse`、`arc`、`grid` 和 `spiral` 可传 `gps_origin=GpsPoint(...)`
直接输出 GPS 航点，任意本地航线也可以调用 `route.to_global(origin, altitude_m=...)` 转换。
`survey_area` 和 `corridor` 支持直接传入 GPS 边界或中心线。局部转换采用 WGS84 局部近似，
适合园区和巡检区域，不用于跨区域大尺度测绘。PX4 Mission 执行 GPS 航线时会根据 Home
绝对海拔把航点绝对海拔转换成飞控要求的相对高度。

设备命令失败统一通过 `StandardResponse` 表达。产品注册、构建参数和适配器契约等无法生成标准命令的失败，
统一抛出 `FleetDockError` 子类，其 `code` 与 `StandardResponse.code` 使用同一套 `ErrorCode`。Fleet 批量操作会把
单台设备抛出的异常转换为该设备的失败响应并继续汇总，避免首个异常导致其他设备结果丢失；此类响应的
`command_id` 为空字符串，并在 `data.exception_type` 中提供非敏感异常类型。

## 调试日志

FleetDock 使用 Python 标准库 `logging` 记录设备创建、适配器选择、命令执行和事件发布等关键路径，SDK 内部运行日志统一为 `DEBUG`。SDK 默认不配置输出，使用者可以仅开启 FleetDock 日志：

```python
import logging

handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

logger = logging.getLogger("fleetdock")
logger.setLevel(logging.DEBUG)
logger.addHandler(handler)
logger.propagate = False
```

不配置日志时默认不显示；需要关闭已开启的 FleetDock 日志时，可调用 `logging.getLogger("fleetdock").disabled = True`。日志不得包含密码、令牌、密钥等敏感连接信息。

## 单设备事件订阅

每个设备对象提供独立的 `events` 事件流，订阅者默认接收该设备的全部事件，再根据标准事件类型进行过滤；该能力不依赖多设备消息总线。

```python
from fleetdock import EventType, GlobalPositionUpdatedEvent

def on_position(event: GlobalPositionUpdatedEvent):
    altitude = event.position.relative_altitude_m
    if altitude is not None:
        print(f"当前相对高度：{altitude:.2f} 米")

unsubscribe = drone.events.on(EventType.GLOBAL_POSITION_UPDATED, on_position)
```

所有标准事件都从唯一公开枚举 `EventType` 查找，Device 与 Fleet 使用相同事件类型：

```python
from fleetdock import EventType

unsubscribe_route_started = drone.events.on(
    EventType.ROUTE_STARTED,
    print_route_started,
)
```

旧分组名称 `CoreEventType`、`NavigationEventType`、`PowerEventType` 等暂时作为 `EventType` 的兼容别名保留；新代码统一使用 `EventType`。厂商、自定义或扩展事件仍通过 `on_name(...)` 使用字符串事件名。

调用方也可以继续使用 `drone.events.subscribe(handler)` 接收完整单设备事件流，或使用 `drone.events.on_name("vendor.<厂商>.<事件>", handler)` 订阅厂商、自定义等扩展事件名。`EventType` 的枚举值仍按能力领域使用 `core.*`、`navigation.*`、`power.*` 等命名空间，使无人机、机器狗及后续设备共享位置、速度、电池等事件标准。坐标数据必须声明 `CoordinateFrame`，公共字段使用 SI 单位并在名称中标明单位。产品通过 `supported_events` 和 `extension_namespaces` 声明事件能力，调用方可以使用 `device.supports_event(...)` 检查。

标准事件的 `event.type` 始终是对应枚举实例，事件本身也是 `GlobalPositionUpdatedEvent`、`BatteryUpdatedEvent` 等强类型事件类。标准字段使用 `GlobalPosition`、`Velocity`、`BatteryState` 等不可变数据对象，开发人员通过属性访问获得 IDE 补全，不需要记忆字典键。调用方还可以通过 `device.supported_events` 获取当前产品声明支持的标准事件枚举集合。

厂商适配器应优先把原生事件转换为标准事件；额外字段放入 `extensions`，来源事件放入 `event.original`，其中包含事件名、完整可序列化数据和仅限进程内使用的可选 `native_data`。无法匹配标准的厂商事件使用 `VendorEvent` 和 `vendor.<厂商>.<事件>` 形式透传，可直接访问 `event.event_name`、`event.raw_data` 和 `event.native_data`。与当前设备相关的自定义业务事件使用 `device.events.publish_custom("custom.<应用>.<事件>", payload)` 发布，使发布、订阅和排空操作统一归属单设备事件流。使用结束后应在 `finally` 中调用 `unsubscribe()` 和 `device.close()`，确保命令或返航发生异常时仍能释放订阅及后台遥测资源。

PX4 MAVSDK 适配器会把 `position`、`position_velocity_ned`、`home`、`velocity_ned`、`attitude_euler`、`battery`、`health`、`armed`、`in_air`、`landed_state` 和 `flight_mode` 转换为标准事件，但不会在连接后无条件启动全部流。电池、解锁、空中、着陆、模式和健康使用低带宽基础订阅；位置、速度、姿态和 Home 由起飞、移动、航线、手动控制、强类型事件订阅及 WS 状态会话自动申请，多个使用者复用同一路 MAVSDK 订阅，结束后自动释放。`events.on(EventType.GLOBAL_POSITION_UPDATED, ...)` 和现有 Plan/Demo 调用不需要增加厂商订阅代码；普通 `events.subscribe(handler)` 只观察已经激活的事件，不会隐式开启所有高频流。

事件分发和 PX4 回调队列均为有界队列。位置、速度、姿态等同类高频遥测过载时合并旧事件并保留最新值，命令、状态变化和安全事件不会被普通遥测优先挤出。若飞控不支持请求的遥测频率，SDK 会报告 `rate_configuration_failed` 诊断并继续使用飞控默认频率，避免位置流中断。`device.diagnostics()` 提供事件积压、容量、最高水位、丢弃数、当前遥测需求和回调丢弃数，便于区分数传拥塞、SDK 背压与遥测过期。电池剩余量继续在适配器边界统一归一化为 FleetDock 约定的 `0~1` 比例。

## 标准传感器订阅

每个 `Drone`、`RobotDog` 及后续设备都提供相同的 `device.sensors` 入口。控制协议、厂商 SDK 和仿真器的传感器装配由 FleetDock 集成层管理：PX4、Project AirSim 以及后续 DJI、Unitree、ROS 2 实现都转换成相同的标准描述和样本，应用代码不需要注册厂商后端或处理原生 topic。尚未实现的真机能力不会被标记为可用。

标准类型覆盖无人机与通用设备的 IMU、RGB/深度/双目相机、LiDAR、Radar、GNSS、磁力计、气压计、测距、光流和空速，也覆盖足式机器人或机械臂的关节状态、六维力矩、足端力与接触状态。标准样本明确 SI 单位、坐标系、传感器 ID 和时间字段；来源缺失的值使用 `None`，不得伪造为零。

常用代码只需要公开枚举，不需要知道 Project AirSim topic、ROS 2 topic 或厂商通道名：

```python
from fleetdock.api import PointCloudFrame, SensorKind

for sensor in device.sensors.list():
    print(sensor.sensor_id, sensor.kind, sensor.frame)

lidar = device.sensors.get(SensorKind.LIDAR)  # 选择该类型唯一或 primary 传感器

def on_lidar(sample: PointCloudFrame):
    print(sample.point_count)

unsubscribe = device.sensors.subscribe(SensorKind.LIDAR, on_lidar)
```

PX4 在连接后可以直接使用同一入口读取飞控传感器；IMU 使用航空器 `BODY_FRD` 机体系，即 x 前、y 右、z 下：

```python
from fleetdock.sensors import ImuSample, SensorKind

def on_imu(sample: ImuSample):
    print(sample.angular_velocity_rad_s, sample.linear_acceleration_m_s2)

unsubscribe = drone.sensors.subscribe(SensorKind.IMU, on_imu)
```

MAVLink 相机协议用于相机能力、控制和视频流信息，不把持续视频像素伪装为低带宽遥测。普通 RGB
视频统一通过 RTSP 等独立媒体链路进入设备集成层，再转换为标准 `ImageFrame`；业务代码仍然只通过
标准枚举选择和订阅：

```python
camera = drone.sensors.get(SensorKind.RGB_CAMERA)
unsubscribe = drone.sensors.subscribe(camera.sensor_id, on_frame)
```

`get(sensor_id)` 和 `subscribe(sensor_id, ...)` 保留给多相机、前后雷达等精确选择；`get(SensorKind.RGB_CAMERA)` 和枚举订阅优先选择后端标记的 `is_primary`。同类型存在多路且没有唯一主传感器时，SDK 会明确报错而不是猜测。确实需要同时启动全部同类传感器时，使用 `subscribe_all(SensorKind.RGB_CAMERA, handler)`；这样可以避免一行普通订阅意外启动多路高带宽相机。`latest(...)` 返回已经接收的最近样本且不会隐式启动流，`read_once(..., timeout_s=...)` 适合诊断时等待下一条样本，连续处理仍应使用订阅。

厂商专有和应用自定义传感器不需要修改 `SensorKind`，使用 `vendor.<厂商>.<名称>` 或
`custom.<应用>.<名称>` 命名空间：

```python
from fleetdock.sensors import ExtensionSensorKind

gas_kind = ExtensionSensorKind("custom.inspection.gas_detector")
unsubscribe = device.sensors.subscribe(gas_kind, on_gas)
```

自定义后端继续实现现有 `SensorBackend.list_sensors()/subscribe()/close()`，标准类型应优先返回对应
强类型样本，尚无公共模型的扩展观测可使用 `GenericSensorSample`。

设备会通过同一个 `device.events` 发布 `sensor.discovered`、`sensor.lost`、`sensor.stream.started`、
`sensor.stream.stopped` 和 `sensor.stream.error` 标准事件；事件类型为 `EventType`，载荷通过
`SensorStreamEvent.sensor` 强类型属性读取，不需要解析诊断字典。

订阅的资源策略通过枚举表达，数值只用于确实具有数量意义的队列容量和频率：

| `SensorDeliveryMode` | 默认场景                             | 队列与代价                                               |
| -------------------- | ------------------------------------ | -------------------------------------------------------- |
| `LATEST_ONLY`        | RGB、深度、双目、LiDAR 等实时感知    | 固定只保留最新 1 帧，旧帧未处理就丢弃，延迟和内存最稳定  |
| `BUFFERED`           | IMU、关节、足端力等有序数据          | 默认有界队列，短时抖动不立即丢样，但处理不及时会增加延迟 |
| `RELIABLE`           | 明确需要完整样本且允许反压的录制链路 | 队列满时阻塞生产者，不应放在飞控关键回调线程             |

因此“相机和点云默认 `LATEST_ONLY + capacity=1`”是 SDK 根据 `SensorKind` 解析出的默认策略，不是要求开发者记忆魔法数字。需要覆盖时传入 `SensorStreamOptions(delivery=..., capacity=..., overflow=..., max_rate_hz=...)`；交付模式和溢出行为使用 `SensorDeliveryMode`、`SensorOverflowPolicy` 枚举，`capacity` 仍是样本数量，`max_rate_hz` 仍是赫兹数值。`device.sensors.status(...)` 可查看接收、交付、丢弃、回调异常和订阅者数量，并通过 `age_s()`、`is_stale(max_age_s)` 判断数据新鲜度。

### WS 服务与图形调试控制器

FleetDock 同时支持两种部署方式：单机程序直接调用核心 `fleetdock` SDK；或安装独立
`fleetdock-server` 包，把一个或多个标准设备发布为 WS 服务。Python 图形界面和未来 Web UI 都是
WS 客户端，只依赖同一套控制、状态和传感器语义。服务包依赖核心 SDK，核心 SDK 不反向依赖服务包；
TypeScript 客户端位于 `packages/fleetdock-js`，协议定义位于 `protocol/`。

浏览器控制器示例位于 `packages/fleetdock-js/examples/web`。它直接使用 TypeScript SDK，支持键盘、
鼠标和触屏的多动作组合、失焦释放、设备状态及传感器发现；页面还会显示并调整水平与升降速度，
分别支持 `-/+` 和 `[/]` 快捷键，并通过 `manual.configure` 以服务端返回值为准同步状态。
视频区域通过独立 WHEP/WebRTC 会话播放，
不会把 RGB 像素或媒体会话塞入 FleetDock WS。`packages/fleetdock-js` 的 `npm run build` 会一次性
生成 SDK 的 JavaScript/类型声明和 `dist/web` 静态页面；之后使用 `npm run web` 只服务已有产物，
不会再次编译。只有页面开发热更新场景才使用 `npm run web:dev`。

```powershell
python -m pip install -e .
python -m pip install -e "packages/fleetdock-server[drone]"
fleetdock-server --mock-drone --host 127.0.0.1 --port 8765
fleetdock-server-drone --endpoint "udpin://0.0.0.0:14555" --ws-port 23045
```

室内低速数传使用 `--link-profile low_bandwidth`；仿真可使用 `simulation`，未指定时为 `standard`。
链路策略只影响 SDK 内部按需遥测频率，不改变 Demo、WS 路径或公共控制命令。

服务端在同一端口提供 `/ws/control` 与 `/ws/telemetry`。控制连接负责控制租约、动作按下/抬起、心跳
和离散命令；遥测连接负责设备状态、传感器枚举与订阅。每台设备同一时刻只允许一个手动控制租约，
客户端断开或输入超时会清空动作并关闭手动控制会话。默认只监听本机；当前版本尚未内置 TLS、认证与
授权，不能直接暴露到公网。

需要直接验收设备控制链时，显式启动服务端，再让 Python 图形控制器作为普通 WS 客户端连接：

```python
from fleetdock_server import ControlMode, FleetDockServer, PythonController

with drone, FleetDockServer() as server:
    device_id = drone.metadata.device_id or drone.instance_id
    server.register(drone, device_id=device_id)
    endpoint = server.start("127.0.0.1", 8765)
    PythonController(
        endpoint,
        device_id,
        mode=ControlMode.SIMULATION,
        rtsp_uri="rtsp://127.0.0.1:8554/Drone1_camera_front",
    ).run()
```

核心 `fleetdock` 不再提供 `device.debug.controller()`：`device.debug` 只负责标准传感器的本地调试，
控制器属于独立 `fleetdock-server` 包。`packages/fleetdock-server/examples/drone_controller_debug.py`
显式展示服务端和
客户端的生命周期。控制器把连接、普通
解锁、强制解锁、上锁、默认五米起飞、悬停、降落、返航、停止、紧急停机以及机体系前后左右、升降和
偏航控制集中在一个窗口中。按钮会按服务端返回的设备能力和状态启用；紧急停机需要在三秒内再次确认。
“台架强解锁”同样要求在三秒内二次点击确认，并且只调用公共 `drone.unlock(force=True)`；它只用于已采取
安全措施的台架测试或仿真诊断，不能替代飞行预检，PX4 可能在命令成功后立即自动上锁。普通解锁失败时不会
自动升级成强制解锁。控制器收到飞控主动上锁的遥测后会在操作日志中解释原因，避免把飞控安全行为误判为
控制器发送了“锁定”命令；需要飞行时应解决预检问题，使用普通解锁并在飞控允许时间内起飞。
关闭窗口会释放控制租约和手动控制，不会自动返航或降落。普通 RGB 视频刻意不经过 WS：真机或仿真
继续提供 RTSP，Python 控制器直接解码 RTSP；Web 端通过独立流媒体服务把 RTSP 转为 WebRTC。
高频或强类型传感器数据仍可走 `/ws/telemetry`，服务端采用有界、仅保留最新样本的交付策略。

`fleetdock-server` 提供 `GStreamerWhipRelay`，用于在当前 Python 进程中通过 GStreamer 把外部 H.264
RTSP 拉流地址无转码发布到 SRS WHIP：管线只执行 RTP 解包、H.264 参数整理、RTP 重新封装和 WebRTC
传输，不经过 RTMP，也不会再次解码或编码视频。发布器具有状态快照、错误脱敏和幂等关闭，并负责释放
GStreamer 管线及监听线程。兼容入口 `FfmpegWhipRelay`（原名 `WhipRelay`）继续保留给需要统一转码
输入格式的场景。`packages/fleetdock-server/src/fleetdock-cli/drone_server.py` 只透传已经存在的 RTSP
拉流地址，不创建 RTSP Server、不订阅 Project AirSim 相机，也不负责启动真机或仿真媒体源。

```powershell
$env:FLEETDOCK_SRS_WHIP_URL = "https://media.example/rtc/v1/whip/?app=live&stream=drone-001"
$env:FLEETDOCK_SRS_WHEP_URL = "https://media.example/rtc/v1/whep/?app=live&stream=drone-001"
$env:FLEETDOCK_MEDIA_BACKEND = "gstreamer" # 默认；兼容模式可设为 ffmpeg
python packages/fleetdock-server/src/fleetdock-cli/drone_server.py `
  --endpoint "udpin://0.0.0.0:14555" `
  --camera-rtsp-uri "rtsp://127.0.0.1:8554/Drone1_camera_front" `
  --camera-source physical `
  --media-publish-url $env:FLEETDOCK_SRS_WHIP_URL `
  --whep-url $env:FLEETDOCK_SRS_WHEP_URL `
  --media-backend $env:FLEETDOCK_MEDIA_BACKEND
```

Web Demo 使用非 Trickle ICE 创建 WHEP 会话，只有收到首个可解码视频帧后才显示 `VIDEO ONLINE`；
无流、空轨道或首帧超时会清理 PeerConnection 和 WHEP 会话，但不会影响 WS 控制租约。对于另外部署
且真实支持 `ANNOUNCE/RECORD` 的媒体服务器，仍可单独使用 `RtspRelay` 执行 RTSP/TCP 无转码转推。

方向按钮和运动热键使用游戏式“按住运动、松开即停”：鼠标按住方向按钮，或按住 `W/A/S/D`、方向键、
`R/F`（也支持 `PageUp/PageDown`）及 `Q/E` 时持续控制前后左右、升降和偏航，任一输入松开后立即发送
对应轴的零输入，全部运动键松开后才发送完整零速度。多个运动键可以同时合成，例如 `W+Q` 会在前进的
同时向左偏航，`W+D+E` 会同时前进、右移和向右偏航；水平对角移动会自动归一化，避免合成速度超过
当前速度设置。离散命令只在按下瞬间触发一次：`C` 连接、`U` 解锁、`L` 锁定、空格起飞、`Ctrl` 降落、
`H` 悬停、`T` 返航、`Z` 停止；`+/-` 调整水平速度，`[/]` 调整升降速度，`Esc/X` 关闭窗口。Windows
会读取真实按下/抬起状态，并且控制器窗口失去焦点时自动停止运动，避免按键粘连。

控制器启动时把当前水平速度设为 `3 m/s`，并可在 `0.5 m/s` 至 `20 m/s` 范围内调整；升降速度默认
为 `0.6 m/s`，可在 `0.1 m/s` 至 `2.0 m/s` 范围内调整。起飞按钮不再维护独立高度设置，直接调用
`drone.takeoff()` 并使用公共 SDK 默认的相对 Home 高度 `5 m`；起飞后的高度由使用者通过升降控制。
相机画面左下角会显示源帧分辨率和平滑后的实际接收 FPS。速度值仍会经过 FleetDock 手动控制会话裁剪，并受设备或飞控自身速度限制
约束。仓库提供的 PX4 SITL 启动脚本会把 `MPC_XY_VEL_MAX` 同步设为 PX4 允许的最高值 `20 m/s`，
只影响本地仿真环境，不由控制器在运行期修改真机参数。

方向控制最终由服务端的公共 `device.manual_control()` 会话承担，而不是由 GUI 或浏览器维持 PX4
Offboard。交互层只发送 `manual.press`、`manual.release` 和心跳，不发送完整四轴状态，也不依赖操作系统
约 300 ms 的键盘自动重复。服务端维护当前动作集合并计算四轴状态。会话输入使用设备
机体系：`forward_m_s`、`right_m_s`、`up_m_s` 单位为米/秒，`yaw_rate_deg_s` 单位为度/秒；持续输入
由核心手动会话按配置频率发送；GUI 只在动作边沿变化时发送按下或抬起，长按期间由 WS 心跳维持租约，
不会逐条转发或积压键盘自动重复事件。显示循环独立限制为最高 30 FPS；Windows 下由低级键盘 Hook 线程在消息
进入 OpenCV HighGUI 前吞掉所有普通按键的按下、松开和自动重复消息，避免消息队列阻塞整个窗口；只有
已绑定控制键会更新最新状态，未绑定键不会产生事件，`Alt+Tab`、`Alt+F4` 和 Windows 键仍交给系统；
超过 `input_timeout_s` 未收到有效心跳时自动悬停或停止。释放租约或断开连接会发送零速度并关闭会话，
但不会返航或降落。同一设备同时只允许一个 WS 控制租约。PX4 通过 MAVSDK
`VelocityBodyYawspeed` 转换，Mock 无人机走相同标准命令；当前机器狗产品尚未声明 `MANUAL_CONTROL`。

真实 PX4 视频只有在明确配置 `camera_stream_uri` 后才注册为标准相机，避免没有相机组件的飞控被
误报为支持摄像头。PX4 服务只消费真机、仿真器或独立媒体服务器已经提供的 RTSP 拉流地址，不会因为
本机 Project AirSim 端口可用就隐式合并仿真相机，也不会自行创建 RTSP Server。该地址不会写入 SDK
日志，`camera_stream_source` 可以继续声明物理、仿真或未知来源。

Windows 高 DPI 桌面下，调试控制器只为其 GUI 线程启用物理像素坐标，避免系统缩放导致窗口超出屏幕或鼠标按钮命中错位；关闭窗口后会恢复原线程 DPI 上下文。
控制器、摄像头窗口和传感器仪表盘的标题、状态、HUD、提示与操作日志默认使用中文显示，产品键、PX4、传感器 ID、坐标系、单位及飞行模式等专业标识保持原始格式；`vision` 可选依赖包含 Pillow，并使用同一套系统中文字体完成 Unicode 绘制。

普通电脑摄像头不需要虚构所属设备，可以直接调用独立调试入口：

```python
from fleetdock.debug import camera

def on_frame(frame: ImageFrame):
    print(frame.sequence, frame.width, frame.height)

camera(0, on_frame)
```

设备级 `device.debug.camera()` 自动选择主 RGB、深度或双目相机，也可以通过
`sensor="camera.front"` 精确指定。画面使用
只保留最新帧的内部显示队列，开发者回调仍收到未经显示转换的标准样本；按 Q、Esc 或关闭窗口后，SDK
只取消本次调试订阅，不会关闭设备。

IMU 等标准传感器可以通过显式订阅送入通用 HUD：

```python
def on_imu(sample: ImuSample):
    print(sample.angular_velocity_rad_s)
    dashboard(sample)

dashboard = device.debug.dashboard(title="FleetDock Sensor HUD")
unsubscribe = device.sensors.subscribe(SensorKind.IMU, on_imu)
try:
    # 在这里执行任务或等待业务流程结束。
    ...
finally:
    unsubscribe()
    dashboard.close()
```

`dashboard()` 只创建后台显示窗口，不发现或订阅传感器。返回的 `SensorDashboard` 可以通过
`dashboard(sample)` 或 `dashboard.update(sample1, sample2)` 接收标准样本；不需要业务处理时，也可以把
`dashboard` 直接作为 `device.sensors.subscribe()` 的回调。HUD 根据 `ImuSample`、`GnssSample`、`BarometerSample`、
`JointStateSample` 等标准类型显示值、SI 单位、坐标系、频率和延迟，对不存在的字段显示 `N/A`，不会
自行推算来源没有提供的姿态或测量值。

HUD 使用 640×320 内部画布和初始窗口；摄像头模式使用完整画布并保持原始宽高比，姿态模式采用左侧人工地平仪、
右侧数据列表的紧凑布局。收到带 `orientation_rpy_deg` 的
`ImuSample` 时，左侧会显示人工地平仪、飞机轮廓以及 Roll、Pitch、Heading；姿态缺失时明确显示
`ATTITUDE N/A`，不会通过角速度积分伪造绝对姿态。PX4 后端在订阅 IMU 时同时接收 MAVSDK
`attitude_euler`，并主动请求 20 Hz 的 MAVSDK 融合 IMU 和姿态输出，把最近一秒内的真实姿态与后续
`imu` 测量组合为标准样本；姿态流超时后恢复为 `None`，取消 IMU 订阅时两条原生流会一起释放。飞控拒绝
频率设置时 SDK 会记录 DEBUG 日志并继续消费其默认输出频率。

需要先执行业务回调、再显示同一标准样本时，可以使用仪表盘回调代理：

```python
unsubscribe = device.sensors.subscribe(
    SensorKind.RGB_CAMERA,
    dashboard.handler(on_frame),
)
try:
    ...
finally:
    unsubscribe()
    dashboard.close()
```

`handler()` 即使遇到业务回调异常，也会把同一不可变样本送入 HUD，异常仍由 `SensorHub` 统计和隔离。
订阅、业务处理和显示职责保持独立，取消函数与 `dashboard.close()` 都应放在 `finally` 中。完整示例见
`examples/demo/drone_camera_debug.py` 和 `examples/demo/drone_px4_sensor_dashboard.py`。后者显式订阅
`SensorKind.IMU`、`SensorKind.MAGNETOMETER`、`SensorKind.GNSS` 和 `SensorKind.BAROMETER`，在无人机执行十二米起飞、
短暂悬停和返航期间显示标准 HUD，不依赖 PX4 内部传感器 ID；解锁前会通过 `wait_until_ready(...)` 等待全球位置、
Home 点和可解锁状态，避免 HUD 演示额外依赖航线控制稳定性。

PX4 无人机的机载视频像素通常来自独立视频链路，而不是通过 MAVLink 遥测传输。FleetDock 使用进程级
GStreamer RTSP Server 完成低延迟 H.264 编码和发布；`ProjectAirSimRtspBridge` 内部订阅 Project AirSim
的主 RGB 相机，并通过容量为一帧的最新值队列避免编码变慢时持续积累延迟。同一进程的多路 Bridge
共享 RTSP Server，每路使用独立路径，关闭其中一路不会中断其他流。业务代码不创建 AirSim client，
也不处理原生 topic、GStreamer 管线或 Server 生命周期。Project AirSim 回调只替换队列中的最新原始帧，
像素复制和 stride 归一化在发布线程执行；GStreamer PTS 使用真实帧到达时钟，低于编码帧率上限时不会持续累积播放延迟。

```python
from fleetdock import DeviceFactory, SensorKind
from fleetdock.simulation import ProjectAirSimRtspBridge

with ProjectAirSimRtspBridge("Drone1") as bridge:
    drone = (
        DeviceFactory.create()
        .px4_generic()
        .params(
            endpoint="udpin://0.0.0.0:14555",
            camera_stream_uri=bridge.stream_uri,
            camera_stream_source=bridge.sensor_source,
        )
        .build()
    )
    unsubscribe = drone.sensors.subscribe(SensorKind.RGB_CAMERA, on_frame)
```

`examples/demo/drone_camera_debug.py` 通过这条链路建立唯一标准订阅，由后台 Dashboard 显示同一份
`ImageFrame`，同时通过 `Plan` 完成解锁、八米起飞、悬停和返航。Controller 复用完全相同的
设备传感器入口，底层不需要 AirSim 专用实现。Project AirSim 0.2.0 某些 RGB 消息会把 `step` 报告为
单通道步长，SDK 会根据图像尺寸和实际缓冲区长度恢复每行字节数。后端还会把机器人级 `/actual_pose`
作为 `vendor.projectairsim.actual_pose` 扩展传感器暴露，并在取消订阅或重新初始化后丢弃旧一代回调，避免旧场景状态污染新会话。

RTSP/H.264 是普通 RGB 视频的统一外部传输边界，不取代 FleetDock 的强类型传感器标准。深度图、双目、
点云和需要严格采集时间同步的数据继续使用对应 `ImageFrame`、`StereoFrame`、`PointCloudFrame` 后端，
避免编码损失和时间戳丢失。Project AirSim 的非 RGB 传感器仍由显式集成后端按需接入，例如 LiDAR：

```python
drone = (
    DeviceFactory.create()
    .px4_generic()
    .params(endpoint="udpin://0.0.0.0:14555")
    .build()
)
lidar = drone.sensors.get(SensorKind.LIDAR)
unsubscribe = drone.sensors.subscribe(lidar.sensor_id, on_lidar)
```

LiDAR 避障同样从标准传感器入口开始：先用 `get(SensorKind.LIDAR)` 取得描述，再创建长期存活的
`avoidance.LidarObstacleProvider`，随后分别调用 `drone.avoidance.add_provider(provider)` 和
`sensors.subscribe(lidar.sensor_id, provider.observe)`。Provider 只负责点云转换，不会自行发现或订阅
传感器。坐标变换、点云过滤、短时多帧融合、垂直体素聚类和障碍物快照仍由其后台线程完成，不会把
点云重计算压在传感器回调上。`low_altitude`、`open_area`、`indoor`、`high_density` 四组预设覆盖常见
场景，也可传入 `avoidance.LidarObstacleConfig` 精确调整。`fleetdock.avoidance` 是避障能力的正式公共命名空间；
常用类型可直接从该领域包取得，精确分包路径则为 `fleetdock.avoidance.lidar`、`fleetdock.avoidance.vision`
和 `fleetdock.avoidance.ranging`。旧平铺模块暂时保留兼容，新代码和示例统一使用 `avoidance.*`。

```python
from fleetdock import avoidance

lidar = drone.sensors.get(SensorKind.LIDAR)
provider = avoidance.LidarObstacleProvider(
    lidar,
    pose_provider=drone.state.snapshot,
    config=avoidance.LidarObstacleConfig.for_preset(avoidance.LidarAvoidancePreset.LOW_ALTITUDE),
)
drone.avoidance.add_provider(provider)
unsubscribe = drone.sensors.subscribe(lidar.sensor_id, provider.observe)
```

`PointCloudCorridorAnalyzer` 和 `PointCloudObstacleTracker` 继续作为高级低层入口保留，适合开发自定义方向策略或感知管线。Tracker 会结合设备姿态与 `SensorDescriptor.mount` 安装外参，把机体系点云转换到 `LOCAL_NED`/`LOCAL_ENU`，并输出标准 `ObstacleShape.BOX` 障碍物；未知外参不会由 SDK 猜测。Project AirSim 适配器已声明其 LiDAR 机体系和安装位置。以上基础实现仍不等同于语义识别、SLAM 或动态目标预测；接入真机前必须标定外参、验证姿态新鲜度并配置安全降级策略。

`avoidance.DepthCameraObstacleProvider`、`avoidance.StereoObstacleProvider`、
`avoidance.RadarObstacleProvider` 和 `avoidance.DistanceObstacleProvider` 使用相同的显式接线方式，
分别接收米制深度图、双目帧、Radar 检测/轨迹与
单向测距样本，并输出同一种 `ObstacleSnapshot`。视觉 Provider 需要 `CameraIntrinsics`、
`StereoCalibration` 与 `SensorMount`；Radar Track 会保留标准速度。Distance 只适合作为近场悬停或停止
保护，不应单独宣称具有完整空间绕行能力。

`examples/demo/drone_lidar_obstacle_pipeline.py` 用十秒数据窗口展示完整的显式数据流，不执行飞行任务。
LiDAR、深度相机、双目、Radar、Distance 和 LiDAR+深度融合仍分别提供独立避障 demo，便于按设备能力
逐项验证；它们都显式调用 `device.sensors.subscribe()`，回调再把强类型样本交给对应 Provider。需要研究
安全出口选择、点云失效保护和随机螺旋搜索时，再阅读 `drone_lidar_search_avoidance_advanced.py`。

## 产品枚举与能力

FleetDock 优先通过产品枚举识别设备和底层控制体系，避免开发人员手写产品字符串。每个 `ProductType` 都提供中文显示名和说明，方便国人开发者在 IDE、日志、配置界面或文档中直接展示。

```python
print(ProductType.PX4_GENERIC.display_name)   # 通用 PX4 无人机
print(ProductType.PX4_GENERIC.description)    # 面向 PX4 飞控的通用无人机产品...
```

当前内置产品包括：

- `ProductType.MOCK_DRONE`：内置模拟无人机，无厂商依赖，适合初学者和持续集成测试。
- `ProductType.MOCK_ROBOT_DOG`：内置模拟机器狗，使用同一套设备、任务和响应模型展示跨设备语义。
- `ProductType.PX4_GENERIC`：通用 PX4 无人机，固件版本先按 `FirmwareVersion.PX4_V0_1` 维护。
- `ProductType.DJI_MATRICE_350_RTK`：大疆 Matrice 350 RTK，代表行业重载无人机，优先覆盖 MSDK、PSDK、Cloud API 三类接入。
- `ProductType.DJI_MAVIC_3_ENTERPRISE`：大疆 Mavic 3 行业系列，代表轻量行业无人机，优先覆盖 MSDK 和 Cloud API 接入。
- `ProductType.UNITREE_GO2`：宇树 Go2，代表机器狗产品，优先覆盖 SDK2 网络接口接入。
- `ProductType.UNITREE_G1`：宇树 G1，代表人形机器人产品，优先覆盖 SDK2 网络接口接入。
- `ProductType.OTHER`：其他/自研设备，正式控制前需要注册对应 adapter。

`ProductProfile` 维护中文产品名、中文说明、设备类型、厂商、控制系统、支持命令、能力集合、固件版本映射、默认固件、初始化模式和 `required_params`。所有内置产品都在 `registry.py` 中使用完整的 `default_registry.register(ProductProfile(...), adapters={...})` 结构集中注册，产品配置与各固件的具体适配器实现可以在同一位置查看和维护。注册接口使用受约束泛型：固件键必须属于字符串类型体系，适配器类必须继承 `DeviceAdapter`。后续接入真实 SDK 时，优先通过 profile 声明该产品需要的初始化入参，例如 PX4 的 `endpoint`、DJI 的 `sdk_mode`、宇树的 `network_interface`。面向用户的主入口由 `DeviceFactory` 提供产品专属 `params(...)` 签名，让开发者可以直接通过代码补全看到该产品要填哪些参数；底层 `Drone(...)`、`RobotDog(...)` 仍保留 `params` 字典入口用于兼容和扩展。调用侧可以通过 `device.has_capability(...)` 判断当前产品是否支持某项能力。

固件版本不是必填项。每个产品在 `ProductProfile.supported_firmware` 中维护该产品支持的固件/SDK 版本映射，并通过 `default_firmware` 声明默认值。用户不传 `firmware_version` 时，`Drone`、`RobotDog` 等标准设备对象会自动使用该产品的默认固件；显式传入时必须是该产品支持的版本，否则构造阶段直接报错。`ProductRegistry` 使用 `(product, firmware_version)` 作为适配器查询键，因此同一产品的不同固件可以注册不同适配器实现，并通过继承产品基础适配器只重写差异部分；`AdapterFactory` 只负责查询并实例化。当前维护 `FirmwareVersion.MOCK_V1`、`FirmwareVersion.PX4_V0_1`、`FirmwareVersion.DJI_MSDK_V5`、`FirmwareVersion.DJI_PSDK_V3`、`FirmwareVersion.DJI_CLOUD_API_V1`、`FirmwareVersion.UNITREE_SDK2`。第三方或自研固件可以使用 `FirmwareVersion.CUSTOM` 或自定义字符串记录，但正式控制前必须在产品注册处配置对应适配器。

## PX4 MAVSDK 设计

PX4 通用产品注册为 `ProductType.PX4_GENERIC`。开发者仍然使用标准 `Drone` 对象和 FleetDock 命令：

```python
from fleetdock import DeviceFactory, Drone, Plan

drone: Drone = (
    DeviceFactory.create()
    .px4_generic()
    .params(endpoint="udpin://0.0.0.0:14555")
    .build()
)

drone.connect()
result = Plan(drone).takeoff(altitude=10).return_to_launch().run()
print(result)
```

`Plan.takeoff()` 是推荐的任务入口：开发者只声明目标高度，SDK 自动完成起飞预检、标准解锁、底层
链路选择和物理高度确认，不传 `takeoff_strategy`、MAVSDK 或 Offboard 参数。PX4 本地位置遥测新鲜且
健康时，会采用从受控真机 Demo 产品化而来的本地位置闭环：先预发送地面保持目标、建立控制模式，再把
标准 arm 与有界高度目标斜坡并发执行；条件不足时使用飞控原生 Takeoff。原生命令在离地前没有物理进展
且本地位置此时变为可用时，只允许自动切换一次；已经检测到爬升后绝不切换控制器。直接
`drone.takeoff()` 继续保持即时命令语义，并要求调用方自行管理解锁和完成确认。

起飞完成后 SDK 会根据 PX4 健康遥测选择交接方式：全球位置和 Home 同时可用时退出 Offboard 并
交接给飞控 Hold；只有光流等本地定位可用时保留最后的 Offboard 位置目标，避免无 GPS 条件下
`offboard.stop()` 因无法切换 Hold 而被飞控拒绝，后续本地航线直接复用该控制模式。仅本地定位时
降落也会跳过 Offboard Stop 并直接请求原生 Land；全球定位可用时仍先正常退出 Offboard。后续航线
仍由 `AUTO` 在 PX4 Mission 和 SDK 路线执行器间选择；返航继续使用原生 RTL，Action Land 失败时
有限补发一次 NAV_LAND。起飞、原生 Mission、Land、RTL、Hold 和紧急动作共享适配器控制权仲裁，
安全动作优先抢占，任何不确定空中状态都不会直接 disarm。`TakeoffOptions`、`TakeoffStatus` 和
`TakeoffExecutionState` 提供跨厂商安全参数与诊断状态；业务代码通常无需读取自动选出的
`TakeoffMethod`。

`Plan(drone).wait_until_ready().takeoff(...)` 是推荐的统一起飞入口，不需要先显式调用 `unlock()`。
默认就绪检查允许健康的本地位置链路与健康的全球位置/Home 链路择一通过，因此同一段 Demo 可用于
仿真、室内光流真机和室外 GPS 真机；室外任务可传 `require_global_position=True` 强制等待全球定位。
`hover()` 同样按当前遥测自适应：室外使用 PX4 原生 Hold，室内把最新 Local NED 位置写入并保持
Offboard；两条定位链路都不安全时返回 `HOLD_STRATEGY_UNAVAILABLE`，不会盲目切换模式。
本地航线也按同一能力边界选择执行器：健康本地定位可走 SDK Offboard 平滑，健康全球定位与 Home
可走 PX4 Mission；GPS 航点、强制 Mission 和 RTL 仍是全球定位能力，不能从“室内可起飞”推断为
无 GPS/Home 时可用。跨环境任务推荐以 `ensure_land()` 收尾，它在正常路径及前序失败后都会执行独立
降落确认；明确要求返回 Home 时才使用 `ensure_return_to_launch()`。

PX4 起飞预检仍要求电池、姿态和健康等遥测在五秒内更新；进入本地位置闭环爬升后，位置与姿态
继续使用五秒控制门限，低频电池监督遥测使用独立的十五秒门限。MAVSDK 连接会主动请求 1 Hz
电池遥测，并把本地位置、姿态和全球位置请求频率分别限制为 5 Hz、5 Hz 和 1 Hz。原生遥测到达后
先更新控制状态缓存，再通过独立的有序后台队列完成标准事件转换，避免事件发布阻塞 gRPC 遥测读取
和命令 ACK；既降低串口与 MAVSDK 回调队列压力，也保留电池长期中断和低电量时的安全中止。

`examples/demo/drone_one_meter_forward_backward_land.py` 面向带光流定位的室内 PX4，在同一真机安全
流程中展示一米起飞，并把按机头方向分两段各前进一米、原路返回起点和返程后悬停五秒编排为一条
逐段到点的本地折线航线。航线强制使用 FleetDock SDK 本地位置执行，不依赖 GPS，也不直接调用
MAVSDK 或编写 Offboard 控制循环。示例会在航线开始前打印估算目标和到点判定阈值，执行期间每秒
输出一次本地位置、速度、遥测新鲜度与队列积压摘要，结束时再输出各目标距离、失败步骤及安全收尾
是否发送 Land；这些只读诊断不改变航线、容差、超时或降落行为。SDK 分段航线会使用当前航点的
实际接受半径规划减速，并按当前位置到目标的二维距离持续纠偏；即使已经沿航线方向越过目标，只要
仍在接受半径外就不会把规划速度锁死为零。

`examples/demo/drone_unlock_takeoff.py` 继续展示连接、订阅 PX4 主动遥测、即时解锁/起飞、每 5 秒回显一次经纬度、绝对高度、相对 Home 点高度和本地坐标并返航；如果 RTL 发送失败则尝试直接降落。公共 `drone.takeoff(altitude=None)` 表示保留飞控或厂商 SDK 已配置的起飞高度，原有默认 5 米行为保持不变。`MavsdkClient.connect()` 会在收到心跳后于 `timeout_s` 内重试版本握手，验证 MAVLink 双向可用，避免只有单向心跳时延迟到 `arm()` 才超时，也避免 PX4 命令通道刚启动时被一次性握手误判失败；PX4 适配器还会在一次 MAVSDK client 连接失败后关闭当前 client、短暂等待并重建 client 再连接，降低连续运行示例脚本时的偶发连接失败。`examples/run_dev.bat` 会从 WSL 默认路由动态获取 Windows 主机地址，避免 WSL 重启后固定 IP 失效造成 MAVLink 只能单向接收。
`examples/demo/drone_event.py` 默认只按 `GlobalPositionUpdatedEvent` 每 5 秒打印位置摘要和相对高度，完整事件 data 通过脚本内 `VERBOSE_ALL_EVENTS` 开关启用；起飞后持续爬升到 14 米以上、以当前位置为原点执行 20 到 30 米随机半径速度航线，并在结束后返航。
基础 SDK 继续提供 `drone.unlock(force=False)`、`drone.takeoff(...)`、`drone.move(...)` 等立即执行方法；`unlock()` 默认执行标准解锁，开发者可在读取失败原因后显式调用 `drone.unlock(force=True)` 请求适配器支持的强制解锁。混编 `Fleet` 上推荐只直接执行通用能力，例如 `fleet.connect()`；解锁、起飞、返航等无人机专属动作应先筛选为 `fleet.drones()` 后再调用，例如 `fleet.drones().unlock(force=True)`，否则机器狗等不支持 `unlock` 的成员会在批量结果中返回 `unsupported_command`。高级计划编排入口为 `Plan(device)`，通用能力由 `DevicePlan` 承载，无人机返回 `DronePlan`，机器狗返回 `RobotDogPlan`；`Plan(fleet)` 返回 `FleetPlan`，纯无人机 Fleet 返回 `DroneFleetPlan`，纯机器狗 Fleet 返回 `RobotDogFleetPlan`。设备连接由 `device.connect()` 或 `fleet.connect()` 单独完成；`Plan(...)` 只声明连接完成后的动作计划。链式声明计划后由 `run()` 按队列顺序执行，并在每一步成功后进入下一步。`Waypoint` 用于描述航点位置、速度、朝向、到达半径、稳定时间和到点动作，`Route` 提供点到点/折线、多边形、圆形、椭圆、弧形、矩形网格、多边形面积覆盖、螺旋和走廊等常见航线生成器，`Route.survey_area(...)` 可基于本地多边形边界、扫描间距和扫描角度生成割草机式覆盖航线；圆形、椭圆、弧形、网格、面积覆盖、螺旋和走廊生成器都可通过 `action=WaypointAction.PHOTO` 等枚举为生成的每个航点设置到点动作，且会在需要时自动包含本地原点锚点，让设备先从当前位置进入图形轨迹。`Plan(drone).route(route)` 会执行单条航线，`Plan(drone).routes(routes)` 会按传入顺序执行多条航线，并发布 `navigation.route.started/completed`、`navigation.waypoint.started/reached/settled/action_requested` 等标准事件；拍照等业务动作应优先订阅 `WaypointSettledEvent`，在位置、高度和速度稳定后触发。航线可传 `max_duration_s` 设置最长正常执行秒数；达到该时长后 SDK 会停止航线并返回成功，`RouteCompletedEvent.progress.completion_reason` 和步骤响应的 `route_completion_reason` 均为 `duration_reached`。这与 `run(step_timeout_s=...)` 的异常超时不同；航线若提前飞完，会立即以 `route_finished` 成功结束。PX4 原生 Mission 和 SDK 平滑执行遵守相同语义。`examples/demo/fleet_follow_events.py` 直接使用该参数执行 B 机限时螺旋航线，不再在 demo 中实现航点控制循环。`examples/demo/drone_plan.py` 内置这些常见航线算法，默认在同一次计划中按顺序执行全部内置航线，也可以通过命令行参数选择 `point_to_point`、`polyline`、`circle_orbit`、`ellipse_orbit`、`arc_scan`、`grid_coverage`、`survey_area`、`spiral_search` 或 `corridor` 执行单条航线。`route(..., motion=RouteMotion(...))` 可为当前航线设置 `MotionExecutionMode` 执行策略和 SDK 平滑参数，默认 `AUTO` 会优先使用 PX4 Mission 等飞控原生航线能力，不可用时回退到 FleetDock SDK 侧 Offboard 平滑；`FLIGHT_CONTROLLER` 会强制使用飞控原生航线，不支持则失败；`SDK` 会强制使用水平加速度、减速度、前视时间、最小前视距离、到点稳定时间和稳定速度阈值等参数执行手写平滑。`motion` 只影响当前 `route(...)`，`routes(..., motion=...)` 只影响本次批量加入的航线，不会改变同一计划中的后续航线。PX4 Offboard 速度控制在未显式传入 yaw 时会保持当前遥测 yaw，避免无意中把机头拉回 0 度。`HeadingMode` 提供 `NONE`、`TANGENT`、`CENTER`、`FIXED` 等枚举值，`TANGENT` 会在航线中同步设置 yaw，让机头跟随航线切向；`wait_until_ready(...)` 默认等待适配器可用的本地或全球定位起飞链路，也可显式追加全球定位或可解锁要求，减少 PX4 预检未完成时的 `COMMAND_DENIED`；`when(...)` 可注册运行期守卫条件，并通过 `then` 回调执行自定义处理，例如电量低时自动返航。`examples/demo/drone_base.py` 展示相同飞行意图，但不使用计划编排，而是直接使用 `drone.connect()`、`drone.unlock()`、`drone.takeoff()`、`drone.move()`、`drone.stop()` 和 `drone.return_to_launch()` 等基础指令完成高度确认、矩形航线、圆形航线和异常返航。

> 0.4.0 Demo 调整：`examples/demo/drone_plan.py` 已收敛为可直接复制的本地圆形航线与安全降落短示例，
> 不再内置或通过命令行选择全部航线；更多标准航线生成器统一由 `examples/quickstart/09_routes.py` 展示。

限时航线可在同一个 `route(...)` 调用中用 `timeout_s` 设置异常超时；未传时才沿用 `run(step_timeout_s=...)`。因此长时间巡航推荐同时声明 `max_duration_s=300` 和略大的 `timeout_s=330`。

`Plan.parallel(..., failure_policy="wait_all")` 默认等待全部子计划结束后汇总结果；显式使用 `failure_policy="fail_fast"` 时，首个失败子计划会触发其它子计划的协作取消信号，并调用各子计划的通用安全停止动作。由 fail-fast 引起的后续取消不会覆盖最初失败原因，顶层结果仍保持 `TaskStatus.FAILED`，便于上层按失败而不是普通取消处理。

`Fleet` 用于把多台标准设备组成设备编队，成员可以混合无人机、机器狗和后续设备。`fleet.drones()`、`fleet.robot_dogs()`、`fleet.by_product(...)`、`fleet.by_capability(...)`、`fleet.by_id(...)`、`fleet.by_name(...)`、`fleet.by_category(...)`、`fleet.with_tag(...)`、`fleet.with_metadata(...)` 和 `fleet.where(...)` 都会返回新的 Fleet 视图，但不会复制设备对象。`Plan(fleet)` 会对 Fleet 成员批量执行通用动作；步骤之间串行，同一步内默认并发作用于所有成员。筛选后的 Fleet 可以继续使用同一个 `Plan(...)` 入口，例如先执行 `fleet.connect()`，再执行 `Plan(fleet.drones()).wait_until_ready().takeoff(20).ensure_land().run()`；混合 Fleet 推荐使用同一个根计划切换作用域，例如 `Plan(fleet).for_drones().unlock().root().for_robot_dogs().stand().root().run()`，其中 `root()` 只返回根 `FleetPlan` 继续声明后续步骤，不是执行边界。多条独立计划需要并行时使用 `Plan.parallel(Plan(fleet.drones()).route(air_route), Plan(fleet.robot_dogs()).route(ground_route)).run()`，返回 `ParallelTaskResult` 汇总各子计划结果；单个根 `FleetPlan` 内的步骤按声明顺序串行，同一步内对目标作用域成员并发。`fleet.events` 与 `device.events` 使用同一个 `EventType` 和同一种 `DeviceEvent` 回调参数；Fleet 只增加来源过滤能力。例如 `fleet.events.on(EventType.BATTERY_UPDATED, handler, device=drone)` 直接按设备对象订阅，无需维护设备 ID；`device` 也可以传设备序列或筛选后的 Fleet 视图。`device_type`、`product` 和 `predicate` 可继续用于能力范围和业务条件过滤。事件中的 `event.source.device_id`、`instance_id` 等来源字段仅用于日志、追踪和序列化，不作为订阅关系的必需配置。Fleet 事件流会为每台成员设备只建立一条底层订阅，避免多个上层订阅重复绑定设备事件。Fleet 跟随只有在所有成员具备全球位置，或通过 `DeviceMetadata.local_frame_id` 明确声明共享 VIO/动捕/地图原点时才会开始；仅仅同为 `LOCAL_NED` 不代表多台 PX4 的 EKF 原点相同。`examples/demo/fleet_plan.py` 对齐 `examples/run_dev_multi.bat` 默认三机端口 `14555`、`14556`、`14557`，先批量连接和起飞，再用 `Plan.parallel(...)` 执行逐机偏移航线，最后以不依赖 Home 的批量降落收尾。`examples/demo/fleet_follow_events.py` 的事件摘要同样会限频打印每台无人机的经纬度、绝对高度、相对高度和本地坐标。

`Plan(device, interceptors=[handler])` 可注册任务步骤 Hook。每个 Hook 会收到 `TaskStepHookEvent`，并在 `BEFORE_STEP`、`AFTER_STEP` 和 `ON_STEP_ERROR` 阶段实时触发；返回 `None` 表示只监听，返回 `TaskStepHookDecision.replace_params(...)`、`replace_result(...)`、`skip(...)` 或 `fail(...)` 可显式调整当前步骤。Fleet 计划会同时产生 `scope="fleet"` 的批量步骤事件和带 `device_id` 的成员步骤事件。`examples/demo/fleet_plan.py` 使用该入口实时打印每个执行步骤，任务结束后再输出最终结果汇总。

```python
from fleetdock import DroneFleet, Fleet

fleet: Fleet = Fleet([...])
drone_fleet: DroneFleet = fleet.drones()

fleet.connect()
drone_fleet.unlock()
```

高级任务默认要求通过标准状态遥测确认关键动作完成。PX4 `takeoff(altitude=10)` 由适配器闭环确认目标高度，不再由通用任务层在原生 Takeoff 后盲目发送速度补偿；旧客户端或其他适配器尚未实现物理起飞协议时，任务层保持兼容的标准命令与遥测确认路径。状态确认类步骤统一使用 `timeout(...)` 作为失败边界。PX4 执行返航、悬停或全局航点前会退出活动的本地控制；计划中的 `land()` 会在 `in_air=False`、标准落地状态为 `on_ground`/`landed` 或设备解除解锁任一条件成立时确认结束，避免单一 `in_air` 遥测滞后导致实体已经落地但任务仍持续等待。`return_to_launch()` 如果已确认设备在空中，会等待遥测显示落地或解除解锁后再结束。如果 armed、相对高度、in_air 等必要状态暂时还没到，任务会在 `timeout(...)` 内等待遥测出现；最终仍缺少状态或无法达标时明确失败。`on_failed(handler)` 可统一处理失败、异常、超时或守卫触发。需要底层即时命令时，仍可直接使用基础 SDK 方法。
即时 `StandardCommand.timeout_s` 会限制 PX4/MAVSDK 命令等待并在超时时返回 `COMMAND_TIMEOUT`；
它与 Plan 的物理状态确认超时相互独立。
公共 SDK 方法优先提供中文 docstring，开发者在 IDE 中查看 `Plan(drone).speed(...)`、`drone.takeoff(...)`、`device.events.on(...)` 等入口时，可以直接看到参数单位、执行语义和返回结果说明。

公共枚举及其成员同样提供中文说明，包括标准命令、能力、产品、固件、坐标系、事件类型、任务状态、运动策略、航点动作和航线模式，方便开发者在 IDE 和源码中直接理解语义、单位及适用场景。

内部流程是：`ProductRegistry` 根据产品枚举找到 `ProductProfile` 并解析默认或指定固件，`AdapterFactory` 根据 `(ProductType.PX4_GENERIC, FirmwareVersion.PX4_V0_1)` 创建 `Px4MavlinkAdapter`，适配器再通过 `MavsdkClient` 把标准命令转换为 MAVSDK 操作。

如果调用方已有 MAVLink client，可以走托管模式：

```python
drone: Drone = (
    DeviceFactory.create()
    .px4_generic()
    .params(endpoint="udpin://0.0.0.0:14555")
    .managed_object(mavlink_client)
    .build()
)
```

`ConnectionInfo` 仍保留为内部兼容模型，但面向用户的推荐入口是 `DeviceFactory` 的产品专属构造器。`mavsdk` 保持为 PX4 可选依赖。未安装该依赖时，Mock 设备、产品注册、适配器选择和注入式客户端测试仍可正常运行；只有连接真实 PX4 端点时才需要安装它。

## 本地开发

FleetDock 包要求 Python 3.11 或更高版本；本仓库本地调试优先使用 Python 3.12 的 conda 环境 `fleetdock`：

```powershell
conda create -n fleetdock python=3.12 pip
conda activate fleetdock
pip install -e .
python -m unittest discover -s tests
```

开发 WS 服务和客户端时额外安装并测试独立包：

```powershell
python -m pip install -e packages/fleetdock-server
python -m unittest discover -s packages/fleetdock-server/tests
cd packages/fleetdock-js
npm install
npm test
```

新电脑可直接按用途选择聚合安装入口；当前聚合安装不包含尚未实现真机适配器的 Unitree SDK：

```powershell
python -m pip install ".[full-prod]"     # 已实现集成的完整生产运行依赖
python -m pip install -e ".[full-dev]"   # 已实现集成 + 测试、检查和构建工具
```

如果只需要轻量集成依赖，可以使用兼容入口：

```powershell
python -m pip install -e ".[integrations-lite]"
```

如果只安装某一类厂商或协议依赖，可以按需选择：

```powershell
python -m pip install -e ".[px4]"
python -m pip install -e ".[dji-cloud]"
python -m pip install -e ".[airsim]"
python -m pip install -e ".[projectairsim-rtsp,vision]"
```

`full-prod` 是当前已实现运行集成的聚合入口，覆盖 PX4/MAVSDK、DJI Cloud、Project AirSim、GStreamer RTSP 和 OpenCV 图像能力；`full-dev` 在此基础上增加测试、覆盖率、Mypy、Ruff 和构建工具。碎片化 extra 仍用于按需安装，已实现的运行依赖调整必须同步到两个完整入口，并由测试检查集合一致性。`integrations` 和 `integrations-lite` 仅作为兼容、轻量入口保留。DJI MSDK 和 PSDK 主要是移动端或原生 SDK，后续接入时应通过 adapter 或托管对象桥接，不放入 Python 依赖。Unitree Go2/G1 当前只使用 Mock 适配器，代码没有导入 `unitree-sdk2`，因此 `unitree` extra 暂时仅作为显式试验入口，不包含在 `integrations`、`full-prod` 或 `full-dev` 中；待真实 Unitree 适配器和协议测试落地后再纳入聚合安装。若需提前试装，使用 Python 3.12，并先准备 CycloneDDS C 库和对应环境变量，再执行 `python -m pip install -e ".[unitree]"`。Project AirSim Python client 当前通过 PyPI 的 `projectairsim==0.2.0` 安装。`projectairsim-rtsp` 在 Windows 和 macOS 通过官方 `gstreamer-meta` 安装 GStreamer 1.28 运行库、Python Binding 和必要插件；Linux 的 PyPI extra 安装 `PyGObject`，GStreamer 原生库仍需由 Conda 或系统包提供。

FleetDock 的 Conda 开发环境在 Windows、Linux x86-64 和 Linux ARM64 上推荐使用同一组 conda-forge
依赖，版本统一限制在 GStreamer 1.28 系列：

```powershell
conda install -n fleetdock -c conda-forge "gstreamer>=1.28,<1.29" "gst-python>=1.28,<1.29" "gst-rtsp-server>=1.28,<1.29" "gst-plugins-base>=1.28,<1.29" "gst-plugins-good>=1.28,<1.29" "gst-plugins-bad>=1.28,<1.29" "pygobject>=3.50,<4" "openh264>=2.6,<2.7"
```

不要在同一个 Conda 环境中混用系统 GStreamer、官方 Windows MSI 和 `gstreamer-meta`。安装后应确认
`appsrc`、`queue`、`videoconvert`、`openh264enc`、`openh264dec`、`rtph264pay` 和 `appsink` 均可用；
Bridge 启动时也会检查发布端必需元素，缺失时直接报告依赖错误。仿真 H.264 RTSP 在 SDK 内使用
`rtspsrc latency=0`、TCP 和仅保留最新一帧的 `appsink` 接收，避免 OpenCV 预编译 FFmpeg 后端约半秒的
RTSP 缓冲；OpenCV 继续用于调试窗口显示和非仿真视频输入。仿真桥默认以 `4000 kbit/s` 编码，快速
画面变化时编码器优先跳过过时帧而不是累积延迟。

Project AirSim 与 PX4 SITL 辅助脚本保留在 `examples/`：

```powershell
examples\run_dev.bat
examples\run_dev_multi.bat
```

`examples/run_dev.bat` 和 `examples/run_dev_multi.bat` 会在启动时把 Windows 控制台切换到 UTF-8 代码页，避免中文提示乱码。脚本会读取本机私有的 `examples/env.bat`；可以复制 `examples/env.example.bat` 为 `examples/env.bat` 后按本机路径修改，所有支持覆盖的环境变量及默认值示例均集中列在该模板中。其中 `AIRSIM_EXE`、`PROJECTAIRSIM_EXAMPLES_DIR`、`PROJECTAIRSIM_SRC_DIR`、`PX4_ROOT_WSL` 和 `PX4_BUILD_DIR_WSL` 可由本机配置覆盖；`prepare_projectairsim_drone.py` 在 ProjectAirSim 与 FleetDock 位于同一父目录时也能自动推导两个 Project AirSim Python 路径，避免 Conda 子进程缺少路径变量时提前退出。启动前不再依赖固定的 AirSim 进程名，而是直接从 `AIRSIM_EXE` 推导安装目录，并结束可执行文件实际路径位于该目录及其子目录中的旧进程。清理脚本拒绝空路径和位于磁盘根目录的 `AIRSIM_EXE`，避免扩大误杀范围。`CONDA_BAT`、`CONDA_NAME`、`WSL_DISTRO`、`AIRSIM_STARTUP_WAIT_S`、`PX4_SOCKET`、`PX4_READY_TIMEOUT_S`、`FLEETDOCK_CAMERA_STREAM_URI` 和 MAVLink 端口、速率参数可以在 `examples/env.bat` 中覆盖；未设置时脚本使用通用默认值。

Project AirSim 0.2.0 的 Python client 默认会在当前工作目录创建 `projectairsim_client.log`。FleetDock 的 Project AirSim 初始化脚本和传感器 demo 会提前把该依赖配置为仅向控制台输出日志，因此运行这些示例不会生成客户端日志文件。

LiDAR 螺旋避障 demo 默认在 18 米相对高度执行，保留 Neighborhood 场景中的实际低空绕障过程；起飞后会重新采集覆盖完整水平扫描周期的点云，并使用与 SDK route 最小前视距离一致的安全出口探测段。候选出口还会交给 route 使用的同一避障管理器复核；螺旋航线先沿验证过的出口直飞到入口半径，再开始向外旋转，避免半径为零处的高曲率首段立即偏离安全走廊。运行时使用 0.7 米机体半径和额外 1.5 米净空判断障碍，遇险会插入临时绕行点，并从障碍物后方重新接回尚未完成的螺旋，而不会折返到障碍前方；水平侧绕不可用时，无人机可短暂升降越障，随后回到 18 米任务高度继续原螺旋。规划器单帧暂时没有安全候选时会先悬停并等待新点云重试，不会立即返航；持续无路可走超过安全等待窗口时才执行 fallback。3 米航点间距使用 2 米到达半径，避免 PX4 在最终航点附近反复修正直至超时。

单机和多机启动现在共用 `examples/projectairsim/` 中的机器人 Profile 和场景模板。`basic` 保留现有轻量相机，`video` 提供独立 960×540、30 FPS、16:9 前向 RGB 控制视频，宽高比与控制器画面区一致，媒体链路仍只保留最新一帧；该 Profile 同时保留仿真窗口的第三人称 `Chase` 视角并关闭 `DownCamera`。`depth` 增加前向 RGB/米制透视深度相机，`stereo` 增加基线 0.12 米的前向双目相机，`lidar` 增加 16 线、100 米量程的 `lidar1`，`radar` 增加 150 米量程的前向 Radar。控制器仿真推荐使用 `basic,video`；可见窗口可通过 `AIRSIM_ARGS=-ResX=1280 -ResY=720 -Windowed -ForceRes` 固定为 1280×720，仅通过 RTSP 使用时才设置 `AIRSIM_ARGS=-RenderOffScreen -NoVSync`；需要深度数据时仍应显式选择 `depth`。推荐使用 `PROJECTAIRSIM_SENSOR_PROFILES=stereo,lidar` 这类显式组合按需启用传感器模块；环境层只负责发布原始数据，不提供会暗示已完成融合的聚合预设。旧的单值 `PROJECTAIRSIM_ROBOT_PROFILE` 继续兼容。Profile 只描述相对上游 `robot_quadrotor_px4_sitl_wsl2.jsonc` 新增的传感器和覆盖项，启动时再生成完整 Project AirSim 机器人配置，避免在 FleetDock 中复制整份上游机型。未知或重复 Profile、重复传感器 ID 和循环引用会在加载场景前报错。`run_dev.bat` 和 `run_dev_multi.bat` 中的 `PROJECTAIRSIM_SCENE_CONFIG_PATH` 默认为空，此时使用 `examples/projectairsim/scenes/scene_px4_neighborhood.jsonc`；将其设为自定义场景配置的绝对路径即可改用指定文件。也可以通过 `PROJECTAIRSIM_ROBOT_CONFIG_PATH` 使用自定义机器人配置；自定义机器人文件需要多层组合时应在 JSONC 中声明 `include-profiles`。详细字段和用法见 `examples/projectairsim/README.md`。

Web 仿真 Demo 默认以 8000 kbit/s、30 FPS 编码场景当前选中的 RGB 控制视频，保持原始分辨率，使用 OpenH264 中等复杂度并由 GStreamer 无转码发布到 WHIP。Demo 顶部显式列出目标码率、帧率、GOP、编码复杂度、实时丢帧、RTSP 抖动缓冲和超时丢包参数，对应的 `FLEETDOCK_CAMERA_*`、`FLEETDOCK_WHIP_*` 环境变量可按 CPU 与网络条件覆盖。码率是编码目标值，SRS 上观察到的实际上行会随画面复杂度变化，不应把静止场景低于目标码率直接判断为分辨率下降。

Windows 通用无人机服务入口 `packages/fleetdock-server/examples/start_drone_server.bat` 会把这些环境变量展开为清晰的命令行参数，再调用 `python packages/fleetdock-server/src/fleetdock-cli/drone_server.py <命令行参数>`。真机、仿真器或独立媒体服务器必须预先提供 `FLEETDOCK_CAMERA_STREAM_URI` 指向的 RTSP 流；该 Python 封装只复用正式无人机服务入口启动 PX4、WS 和可选媒体转发，不创建 RTSP Server 或 Project AirSim Bridge。仿真环境可先在一个终端运行 `packages\fleetdock-server\examples\start_projectairsim_rtsp_bridge.bat`，再在另一个终端运行 `packages\fleetdock-server\examples\start_drone_server.bat`；两者默认使用同一个本地 RTSP 地址。

启动脚本会从 WSL 默认路由自动检测 Windows 主机 IP，最多重试 10 秒；仍未取得默认路由时，如果 `examples/env.bat` 已配置 `PX4_WINDOWS_HOST_IP`，则使用该值兜底，否则明确报错退出。成功检测后，脚本会将 `PX4_WINDOWS_HOST_IP` 和 `PX4_SIM_HOST_ADDR` 统一为检测值；不同的手工配置只会触发提示。单机和多机启动脚本默认把仿真 GPS Home 设在重庆轨道交通大学城站附近：WGS84 纬度 `29.61002`、经度 `106.30514`，近似绝对海拔 `293.0` 米（AMSL）。可在 `examples/env.bat` 中覆盖 `PROJECTAIRSIM_HOME_LATITUDE_DEG`、`PROJECTAIRSIM_HOME_LONGITUDE_DEG` 和 `PROJECTAIRSIM_HOME_ALTITUDE_M`；启动脚本会生成带 `home-geo-point` 的 FleetDock 场景文件，保证 `GpsPoint`、Home 点和本地坐标原点可复现。Project AirSim 场景中无人机默认朝向为 0°，启动脚本通过 `PX4_INITIAL_HEADING_DEG=0` 对齐 PX4 估计器航向并等待预检通过，避免地理位置改变后航向创新检查持续阻止解锁；可在 `examples/env.bat` 中覆盖该角度，设为空值则跳过对齐。脚本启动 AirSim 后会等待 `AIRSIM_STARTUP_WAIT_S`，再在独立且保持打开的控制台中启动 PX4。随后沿用原有并行启动方式：独立窗口只等待 PX4 命令 socket 后运行 `examples/prepare_projectairsim_drone.py`，主窗口同时由 `examples/start_px4_mavlink.bat` 等待 commander 完整就绪、对齐航向并启动指向 Windows MAVSDK 监听端口的 MAVLink 输出。如果 PX4 异常退出，控制台会保留错误信息。`examples/demo/fleet_follow_formation.py` 会在连接 PX4 前只读检查 Project AirSim topic，并等待 `SceneFleetDock3Drones` 的三个机器人位姿出现，避免三机场景尚未加载时继续执行。`examples/mavlink_random_route_rtl.py` 只负责 MAVSDK 飞行流程，并会通过有界本地 NED 位置目标上升，超过 10 米后以当前位置为中心，随机选择圆形、三角形、正方形、矩形或梯形航线，闭合图形并回到中心后返航；如果 RTL 不可用则直接降落。

Project AirSim 的 `depth` 和 `stereo` Profile 默认把前向相机安装在机体系 `x=0.45 m`，位于 PX4
四旋翼前桨盘最前缘之外，因此控制器和摄像头调试窗口不会再把前桨拍入画面；自定义安装位置、朝向和
FOV 的方法见 `examples/projectairsim/README.md`。

`examples/run_dev_multi.bat` 用于按 `MULTI_DRONE_COUNT` 启动多架 PX4 SITL 无人机的开发环境，默认启动 3 架，并按 `MULTI_DRONE_NAME_PREFIX` 自动生成 `Drone1`、`Drone2`、`Drone3` 等名称。脚本会从所选机器人 Profile 自动生成对应数量的机器人配置，各实例依次使用模拟器 TCP 端口 `4560`、`4561`、`4562`。未配置逐机组合时，`MULTI_PROJECTAIRSIM_SENSOR_DRONES` 默认是 `Drone1`，避免所有无人机同时发布高带宽图像和点云；可以设为 `all`，或设为 `Drone1,Drone3` 这类逗号分隔名称。推荐使用 `MULTI_PROJECTAIRSIM_SENSOR_PROFILES=Drone1=stereo+lidar;Drone2=depth` 为不同无人机直接分配组合，未列出的成员使用 `basic`，且该变量优先于旧式感知无人机列表。脚本默认让 `Drone1` 保持单机示例的出生点，并把 `Drone2`、`Drone3` 放在 Drone1 前方 12 米、左右各 4 米的位置，形成便于 Drone1 视角观察的前导双机队形；可通过 `MULTI_PROJECTAIRSIM_SPAWN_SPACING_M` 覆盖更多无人机的兜底横向间距。启动前会按进程名清理旧 PX4 实例和残留的 `mavsdk_server.exe`，并按可执行文件所在路径清理旧 AirSim 进程；PX4 清理会先尝试正常终止，若仍未退出则强制结束。默认就绪检测窗口为 180 秒。多机流程沿用原有顺序：等待各实例命令 socket、设置 `MAV_SYS_ID`、异步加载 Project AirSim 场景，再逐实例等待 commander 并启动 MAVLink 输出；预加载阶段显式使用 socket-only 检查，避免改变原有并行启动关系。各实例按端口基数暴露 MAVSDK 监听端口 `14555`、`14556`、`14557`。PX4 多实例命令统一使用 CLI 的 `--instance N` 参数路由到目标进程，启动 MAVLink 输出时只停止当前实例的目标本地端口，不使用 `stop-all`，避免启动后续无人机时清掉前面实例的链路。可在 `examples/env.bat` 中覆盖 `MULTI_DRONE_COUNT`、`MULTI_DRONE_NAME_PREFIX`、`MULTI_PROJECTAIRSIM_SCENE_NAME`、`PROJECTAIRSIM_SENSOR_PROFILES`、`MULTI_PROJECTAIRSIM_SENSOR_PROFILES`、`MULTI_PROJECTAIRSIM_SENSOR_DRONES`、`MULTI_PX4_SIM_MODEL`、`MULTI_PX4_SOCKET_PREFIX`、`MULTI_MAVLINK_LOCAL_PORT_BASE`、`MULTI_MAVLINK_REMOTE_PORT_BASE` 和 `MULTI_PX4_DEFAULT_GCS_PORT_BASE`。`examples/demo/fleet_plan.py` 会分段打印 Fleet 连接、ready 和起飞状态，并把每台无人机的业务 ID、高度和本地坐标分别打印在独立行，避免窄终端裁切掉其他成员；航线参数日志最多每 10 秒打印一次，避免并行计划重复生成时刷屏。在当前 Project AirSim + PX4 SITL 多机场景中，脚本会先用 FleetDock 标准遥测等待每台无人机的本地或全球定位链路就绪，再通过 `Plan(fleet.drones()).takeoff(...)` 让各成员自动选择底层起飞链路，并继续用 SDK 速度控制补高到目标高度，确认容差为 2 米以适配 PX4 多机 SITL 的实际悬停高度；航线阶段会把公共航线缩放到 55%，三架无人机执行相同的本地航线以保持出生时的前导队形，并使用 `25/27/27` 米高度层增加安全余量，便于从 Drone1 附近视角同时观察三架无人机。传入 `--takeoff-only` 时，脚本会在全部无人机达到目标高度后结束，不继续执行航线。如果某台无人机缺少可用定位链路或起飞高度遥测，控制台会持续显示对应设备的缺项。

为避免重复启动时残留 socket 被误判为新实例已就绪，多机脚本会在旧进程结束后仅清理本次实例对应的 socket；设置 `MAV_SYS_ID` 前还会等待参数服务真正可用，并在短暂启动抖动时进行有限重试。场景加载和 MAVLink 启动顺序保持不变。

每条 MAVSDK 专用链路保持 MAVLink forwarding 关闭，避免请求被转发到同一 PX4 的其他链路后造成响应路由不稳定。

当前 `examples/run_dev.bat` 和 `examples/run_dev_multi.bat` 都会在主窗口同步执行 Project AirSim 场景准备，并检查准备脚本退出码；只有场景和无人机位姿准备成功后才继续等待 PX4 commander。准备失败时会直接保留异常并终止启动，避免后台窗口关闭后表现为持续等待 PX4。

多机环境就绪后，可在 FleetDock conda 环境中运行三机 Fleet 计划示例：

```powershell
python examples\demo\fleet_plan.py
python examples\demo\fleet_plan.py circle_orbit
python examples\demo\fleet_plan.py all
python examples\demo\fleet_plan.py --takeoff-only
```

所有 `examples/demo/*.py` 飞行示例和独立教学 Demo 的运行期控制流程都必须只通过 FleetDock SDK 或 MAVSDK 访问设备，不直接调用 `px4-commander`、`px4-param`、`px4-mavlink` 或 PX4 二进制命令，确保同一套示例可以面向真机连接复用；PX4 命令行只允许出现在 `examples/run_dev*.bat`、`examples/start_px4_mavlink.bat` 等本地 SITL 开发环境启动脚本中。标准事件、任务状态、跟随状态和停止策略等 SDK 已公开枚举的参数与判断统一使用对应枚举成员；字符串仅保留给设备 ID、连接端点、命令行参数和示例自定义事件等开放值。

## SDK 连续平滑航线

`Route` 现在包含 `continuity` 和 `geometry` 信息。`Route.spiral(...)`、`Route.circle(...)`
和 `Route.arc(...)` 等曲线路线会保留生成时的曲线参数，并默认声明为
`RouteContinuity.REQUIRED`，因此 `MotionExecutionMode.AUTO` 会跳过 PX4 Mission 式到点航线，
直接使用 FleetDock SDK 的连续航迹执行器。执行器会基于整条路径计算前视点，普通中间航点只作为
路径进度点飞越，不再执行“到点停止、转向、再飞下一段”；只有带 `WaypointAction`、`hold_time_s`
或最终航点才会进入稳定和停留语义。

连续航迹执行器仍通过标准 `device.set_velocity(...)` 输出控制量，因此 `Plan(drone).route(...)`、
`Plan(fleet.drones()).route(...)` 和二维 `Plan(robot_dog).route(...)` 共享同一套路径采样、
前视和速度规划逻辑。无人机可使用本地 x/y/z 或 GPS 航线，机器狗路线必须保持二维本地 x/y，
声明阶段会拒绝包含高度的路线。普通折线默认保持兼容：`AUTO` 仍可优先使用适配器原生航线，
需要强制 SDK 平滑时可在当前航线调用中传入 `motion=RouteMotion(mode=MotionExecutionMode.SDK)`。

最小螺旋演示见 `examples/demo/drone_smooth_spiral.py`。脚本只展示标准 SDK 调用：
连接 PX4、生成 `Route.spiral(...)`、起飞、使用 `Plan(...).route(..., motion=RouteMotion(...))` 执行平滑螺旋，
控制闭环和航点平滑逻辑都在 SDK 内部完成。

航线与跟飞 demo 会在执行前打印本次实际使用的路线名称、`pattern`、`continuity`、几何参数、
航点数、接受半径、飞行高度、速度、执行模式、朝向和前视参数；Fleet demo 还会逐设备显示
路线缩放、偏移和高度，跟飞 demo 会显示跟随周期、最大速度、跟随距离及最小安全间距。
