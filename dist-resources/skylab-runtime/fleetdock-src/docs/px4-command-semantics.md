# PX4 命令与完成语义

`Drone` 的即时方法返回“命令是否被适配器/飞控接受”，不表示物理动作已经完成：

- `takeoff()` 成功：起飞命令已发送，不表示已达到目标高度。
- `move()` 成功：位置或速度目标已发送，不表示已到达目标点。
- `return_to_launch()` 成功：RTL 已触发，不表示已经返航或降落。
- `land()` 成功：降落命令已发送，不表示已经着陆。
- `connect()` 成功：控制链路已经建立，不表示定位、Home 或解锁条件就绪。

需要确认物理状态时使用 `Plan(...).wait_until_ready()/takeoff()/route()` 等任务入口，并检查 `TaskResult`。诊断就绪条件使用 `device.readiness_for(...)` 和 `device.state.snapshot()`。
