# PX4 命令与完成语义

`Drone` 的即时方法返回“命令是否被适配器/飞控接受”，不表示物理动作已经完成：

- `takeoff()` 成功：起飞命令已发送，不表示已达到目标高度。
- `move()` 成功：位置或速度目标已发送，不表示已到达目标点。
- `return_to_launch()` 成功：RTL 已触发，不表示已经返航或降落。
- `land()` 成功：降落命令已发送，不表示已经着陆。
- `connect()` 成功：控制链路已经建立，不表示定位、Home 或解锁条件就绪。

需要确认物理状态时使用 `Plan(...).takeoff()/route()/return_to_launch()` 等任务入口，并检查
`TaskResult`。PX4 `Plan.takeoff()` 自动完成预检、标准解锁和链路选择：本地位置新鲜且健康时使用有界
本地位置闭环，否则使用飞控原生 Takeoff；调用方不选择 MAVSDK/Offboard 策略。原生起飞只有在尚未
离地、没有高度或 in_air 进展且本地位置闭环已就绪时，才允许自动回退一次。

起飞完成后会根据健康遥测选择控制交接：全球位置和 Home 同时可用时退出 Offboard 并交接到飞控
Hold；只有光流等本地定位可用时保留最后的 Offboard 位置目标，使后续本地航线可以继续复用该控制
模式。仅本地定位下的 Land 会跳过需要切换 Hold 的 Offboard Stop，直接请求 PX4 原生 Land；全球
定位可用时仍先正常退出 Offboard。RTL/Land 可抢占起飞控制，Action Land 被拒绝时适配器只补发一次
NAV_LAND，不进行无限重试；
`Plan.land()` 会在 `in_air=False`、标准落地状态为 `on_ground`/`landed` 或解除解锁任一条件成立时
确认物理结束，避免某一种状态更新滞后造成落地后继续等待；`Plan.return_to_launch()` 仍通过落地或
解除解锁遥测确认物理结束。诊断就绪条件使用
`device.readiness_for(...)` 和 `device.state.snapshot()`。

PX4 控制遥测采用核心 SDK 内部需求租约。连接后只常驻电池、解锁、空中、着陆、模式和健康等安全
状态；起飞、移动、航线、手动控制、强类型事件订阅与 WS 状态会话按生命周期申请位置、速度、姿态和
Home，同一路 MAVSDK 流由多个使用者复用。任务完成、取消、失败、WS 断开或设备关闭时自动释放，
业务代码和 Demo 不调用 MAVSDK 订阅接口。`link_profile=low_bandwidth` 会进一步限制数传遥测频率；
频率设置失败时停止对应非基础流并发布遥测错误，避免静默使用可能压满低速链路的飞控默认频率。

本地 `Route` 的公共入口不会要求调用方选择 Mission 或 Offboard：存在健康本地定位时可以执行 SDK
本地平滑航线；存在健康全球定位与 Home 时可以使用 PX4 原生 Mission，并在不可用时按
`RouteMotion` 策略回退。显式 GPS 航点、强制 `FLIGHT_CONTROLLER` Mission 和 RTL 始终要求全球定位，
不能因为本地光流可起飞就推断它们可在无 GPS/Home 的室内使用。

跨环境 Demo 应使用 `ensure_land()` 收尾。它在正常路径执行 Land，也会在此前步骤失败、超时或被守卫
阻断后启动独立降落计划，并把结果保存到 `TaskResult.cleanup_result`；Fleet 版本会对全部无人机执行相同
清理。只有任务明确要求返回 Home 且全球定位有效时才使用 `ensure_return_to_launch()`。

即时命令的 `StandardCommand.timeout_s` 约束 MAVSDK 调用等待，超时统一映射为
`command_timeout`；Plan 的步骤超时还会继续约束高度、航点、落地等物理状态确认。因此命令被接受与动作
完成仍是两个独立边界。
