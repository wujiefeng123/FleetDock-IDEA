// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import * as fs from 'fs';
import * as path from 'path';

// [UAV-IDE MODIFY] Keep FleetDock teaching notebooks isolated from the generic application scaffold.
type NotebookCell = {
  cell_type: 'markdown' | 'code';
  id: string;
  metadata: { [key: string]: unknown };
  source: string[];
  execution_count?: null;
  outputs?: unknown[];
};

type NotebookDefinition = {
  fileName: string;
  cells: NotebookCell[];
};

function sourceLines(source: string): string[] {
  const normalized = source.replace(/^\n/, '').replace(/\n$/, '');
  const lines = normalized.split('\n');
  return lines.map((line, index) =>
    index < lines.length - 1 ? `${line}\n` : line
  );
}

function markdown(id: string, source: string): NotebookCell {
  return {
    cell_type: 'markdown',
    id,
    metadata: {},
    source: sourceLines(source)
  };
}

function code(id: string, source: string): NotebookCell {
  return {
    cell_type: 'code',
    id,
    metadata: {},
    source: sourceLines(source),
    execution_count: null,
    outputs: []
  };
}

const RUNTIME_SETUP = `
from pathlib import Path
from pprint import pprint
import sys

# Notebook 位于 demo/ 子目录，因此向上查找应用根目录中的 fleetdock_runtime.py。
APP_ROOT = next(
    (candidate for candidate in (Path.cwd(), *Path.cwd().parents)
     if (candidate / "fleetdock_runtime.py").exists()),
    None,
)
if APP_ROOT is None:
    raise RuntimeError("未找到 fleetdock_runtime.py，请在 FleetDock IDEA 应用目录中运行本 Notebook。")
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from fleetdock_runtime import FleetDock

FleetDock.init()
runtime_info = FleetDock.info()
device_aliases = list(runtime_info["devices"])
print("应用目录：", APP_ROOT)
print("FleetDock 运行时：")
pprint(runtime_info)
if not device_aliases:
    raise RuntimeError("当前应用尚未绑定无人机。请先在 FleetDock IDEA 中绑定设备并启动仿真。")
`;

const NOTEBOOKS: NotebookDefinition[] = [
  {
    fileName: '00_FleetDock_Demo_导航.ipynb',
    cells: [
      markdown(
        'intro',
        `
# FleetDock Demo 学习导航

这套 Notebook 从“认识运行时和设备”逐步进入命令、Plan、多机、事件、异步任务、失败恢复和航线设计。

建议按编号学习。所有可能发送控制命令的 Demo 都有 \`RUN_FLIGHT = False\` 或同类安全开关；先启动仿真、确认设备和场景安全，再显式开启。`
      ),
      markdown(
        'catalog',
        `
## 课程目录

| 编号 | 主题 | 设备要求 | 需要仿真 | 默认发送命令 |
|---|---|---:|---:|---:|
| 01 | 单机直接 takeoff 与高度确认 | 1 架 | 是 | 否 |
| 02 | 单机 Plan 与高度确认 | 1 架 | 是 | 否 |
| 03 | 多机 Fleet Plan | 至少 2 架 | 是 | 否 |
| 04 | 多机螺旋与走廊航线 | 至少 2 架 | 是 | 否 |
| 05 | 设备诊断、能力与生命周期 | 1 架 | 可选 | 否 |
| 06 | 单机强类型事件订阅 | 1 架 | 是 | 否 |
| 07 | 多机事件与状态面板 | 至少 2 架 | 是 | 否 |
| 08 | 异步任务、状态与取消 | 1 架 | 是 | 否 |
| 09 | 失败、超时与恢复 | 1 架 | 可选 | 否 |
| 10 | 标准航线生成与 SVG 可视化 | 0 架 | 否 | 否 |`
      ),
      code(
        'runtime-overview',
        `
from pathlib import Path
from pprint import pprint
import sys

APP_ROOT = next(
    (candidate for candidate in (Path.cwd(), *Path.cwd().parents)
     if (candidate / "fleetdock_runtime.py").exists()),
    None,
)
if APP_ROOT is None:
    raise RuntimeError("未找到 fleetdock_runtime.py。")
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from fleetdock import __version__ as fleetdock_version
from fleetdock_runtime import FleetDock

FleetDock.init()
print("FleetDock SDK 版本：", fleetdock_version)
print("应用运行时信息：")
pprint(FleetDock.info())
print("提示：绑定设备发生变化后，FleetDock IDEA 会重新生成 fleetdock_runtime.py。")
FleetDock.close()
`
      ),
      markdown(
        'mental-model',
        `
## 先记住这四层关系

1. **FleetDock IDEA 应用配置**保存设备绑定和仿真参数。
2. **fleetdock_runtime.py** 把绑定结果转换成 \`FleetDock.devices.<别名>\`。
3. **FleetDock 门面**提供 \`devices / core / envs / info / close\` 等应用级入口。
4. **fleetdock SDK**提供设备、事件、Fleet、Plan、Route、安全和任务结果模型。

应用代码优先从 \`fleetdock_runtime import FleetDock\` 获取已绑定设备，再从 \`fleetdock\` 导入通用 SDK 类型。`
      )
    ]
  },
  {
    fileName: '01_单机_takeoff_高度确认.ipynb',
    cells: [
      markdown(
        'intro',
        `
# 01｜单机基础：直接调用 takeoff() 并确认目标高度

这个 Demo 展示最直接的 FleetDock SDK 调用方式：取得应用已绑定的无人机，依次连接、解锁、发送起飞命令，再读取标准状态确认是否达到目标高度。

> 关键语义：\`drone.takeoff()\` 返回成功，只代表飞控接受了命令，不代表无人机已经到达目标高度。因此直接命令模式需要应用自己完成超时、容差和状态检查。

运行前请先启动 PX4 / Project AirSim 仿真。默认不会发送飞行命令；确认环境安全后，把 \`RUN_FLIGHT\` 改为 \`True\`。`
      ),
      code('runtime', RUNTIME_SETUP),
      code(
        'select-device',
        `
from fleetdock import CapabilityName

DRONE_ALIAS = device_aliases[0]
drone = getattr(FleetDock.devices, DRONE_ALIAS)
print(f"本次使用：{DRONE_ALIAS} / {drone.metadata.device_id}")
print("起飞能力检查：", drone.readiness_for(CapabilityName.TAKEOFF))
`
      ),
      code(
        'helpers',
        `
import time

TARGET_ALTITUDE_M = 10.0
ALTITUDE_TOLERANCE_M = 1.0
ALTITUDE_TIMEOUT_S = 60.0

def require_ok(label, response):
    """把 StandardResponse 转为清晰的教学输出，失败时立即中止。"""
    print(f"{label}: ok={response.ok}, code={response.code}, message={response.message}")
    if not response.ok:
        raise RuntimeError(f"{label}失败：{response.message}")

def wait_for_readiness(device, timeout_s=60.0):
    """直接命令模式下由应用轮询起飞条件；Plan 会在 wait_until_ready 中完成同类工作。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        report = device.readiness_for(CapabilityName.TAKEOFF)
        if report.ready:
            return report
        messages = [issue.message for issue in report.issues]
        print("等待设备就绪：", messages)
        time.sleep(1.0)
    return device.readiness_for(CapabilityName.TAKEOFF)

def wait_for_altitude(device, target_m, tolerance_m=1.0, timeout_s=60.0):
    """轮询 FleetDock 标准状态，直到相对 Home 高度进入容差范围。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        altitude = device.state.snapshot().relative_altitude_m
        if altitude is not None:
            print(f"当前高度 {altitude:.2f} m / 目标 {target_m:.2f} m")
            if abs(altitude - target_m) <= tolerance_m:
                return True, altitude
        time.sleep(0.5)
    return False, device.state.snapshot().relative_altitude_m
`
      ),
      code(
        'run-flight',
        `
RUN_FLIGHT = False  # 确认仿真已启动后改为 True

if not RUN_FLIGHT:
    print("安全开关未开启：本单元未发送飞行命令。")
else:
    try:
        require_ok("连接", drone.connect())
        readiness = wait_for_readiness(drone, timeout_s=60.0)
        print("起飞前诊断：", readiness)
        if not readiness.ready:
            raise RuntimeError(f"设备尚未就绪：{readiness.issues}")
        require_ok("解锁", drone.unlock())
        require_ok("发送起飞命令", drone.takeoff(TARGET_ALTITUDE_M))

        reached, actual_altitude = wait_for_altitude(
            drone,
            TARGET_ALTITUDE_M,
            ALTITUDE_TOLERANCE_M,
            ALTITUDE_TIMEOUT_S,
        )
        print(f"是否达到目标高度：{reached}；最终高度：{actual_altitude}")
        if not reached:
            raise TimeoutError("起飞命令已接受，但未在规定时间内达到目标高度。")
    finally:
        print("安全清理：", drone.return_to_launch())
        FleetDock.close()
`
      ),
      markdown(
        'summary',
        `
## 小结

- \`FleetDock.devices.<别名>\` 是 FleetDock IDEA 根据应用设备绑定生成的标准设备对象。
- \`connect / unlock / takeoff\` 是即时命令，返回 \`StandardResponse\`。
- 即时命令不会代替业务侧判断“动作是否真正完成”；高度确认需要读取 \`state.snapshot()\`。
- 清理逻辑应放进 \`finally\`，确保异常时仍触发返航并释放 SDK 资源。`
      )
    ]
  },
  {
    fileName: '02_单机_Plan_高度确认.ipynb',
    cells: [
      markdown(
        'intro',
        `
# 02｜单机基础：使用 Plan 起飞并确认目标高度

这个 Demo 使用 \`FleetDock.core.plan(drone)\` 创建单机计划。Plan 会按顺序执行就绪检查、解锁和起飞，并把“到达目标高度”作为起飞步骤的完成条件。

与直接调用 \`takeoff()\` 相比，Plan 更适合需要超时、步骤结果和完成确认的任务。默认不会飞行；确认仿真安全后把 \`RUN_FLIGHT\` 改为 \`True\`。`
      ),
      code('runtime', RUNTIME_SETUP),
      code(
        'prepare',
        `
TARGET_ALTITUDE_M = 10.0
STEP_TIMEOUT_S = 90.0

DRONE_ALIAS = device_aliases[0]
drone = getattr(FleetDock.devices, DRONE_ALIAS)
print(f"本次使用：{DRONE_ALIAS} / {drone.metadata.device_id}")
`
      ),
      code(
        'run-plan',
        `
RUN_FLIGHT = False  # 确认仿真已启动后改为 True

if not RUN_FLIGHT:
    print("安全开关未开启：Plan 已准备，但没有执行。")
else:
    try:
        connect_response = drone.connect()
        print("连接结果：", connect_response)
        if not connect_response.ok:
            raise RuntimeError(connect_response.message)

        plan = (
            FleetDock.core.plan(drone)
            .wait_until_ready(timeout_s=60.0)
            .unlock()
            .takeoff(altitude=TARGET_ALTITUDE_M)
        )
        result = plan.run(step_timeout_s=STEP_TIMEOUT_S)

        print(f"Plan 成功：{result.ok}")
        print(f"Plan 状态：{result.status}")
        print(f"Plan 消息：{result.message}")
        for step in result.steps:
            print(f"  - {step.name}: ok={step.ok}, message={step.message}")

        altitude = drone.state.snapshot().relative_altitude_m
        print(f"Plan 完成时的标准相对高度：{altitude} m")
        if not result.ok:
            raise RuntimeError(f"失败步骤={result.failed_step}: {result.message}")
    finally:
        print("安全清理：", drone.return_to_launch())
        FleetDock.close()
`
      ),
      markdown(
        'comparison',
        `
## takeoff() 与 Plan 的区别

| 方式 | 成功的含义 | 完成确认 | 适合场景 |
|---|---|---|---|
| \`drone.takeoff()\` | 起飞命令被接受 | 应用自行读取状态判断 | 简单指令、已有业务状态机 |
| \`FleetDock.core.plan(drone).takeoff().run()\` | 起飞步骤达到 Plan 的完成条件 | SDK 统一处理高度确认和超时 | 教学、任务编排、可靠流程 |

Plan 不会在链式声明时执行动作，真正执行发生在 \`run()\`。`
      )
    ]
  },
  {
    fileName: '03_多机_Plan_高度确认.ipynb',
    cells: [
      markdown(
        'intro',
        `
# 03｜多机基础：Fleet + Plan 起飞和高度确认

这个 Demo 从 \`FleetDock.devices\` 取得应用绑定的多架无人机，通过 \`FleetDock.core.fleet(...)\` 组成 Fleet，再让一个 Plan 对所有成员执行相同任务。

Plan 返回 \`FleetTaskResult\`，既有整体结果，也保留每架无人机的结果。默认不会飞行；请先启动至少 2 架仿真无人机。`
      ),
      code('runtime', RUNTIME_SETUP),
      code(
        'prepare-fleet',
        `
TARGET_ALTITUDE_M = 12.0
STEP_TIMEOUT_S = 120.0

if len(device_aliases) < 2:
    raise RuntimeError("多机 Demo 至少需要绑定 2 架无人机。")

# 教学 Demo 默认使用前 4 架；设备更多时可自行调整切片。
selected_aliases = device_aliases[:4]
drones = [getattr(FleetDock.devices, alias) for alias in selected_aliases]
fleet = FleetDock.core.fleet(*drones, name="基础起飞编队")
print("编队成员：", selected_aliases)
`
      ),
      code(
        'run-plan',
        `
RUN_FLIGHT = False  # 确认所有仿真设备已启动后改为 True

if not RUN_FLIGHT:
    print("安全开关未开启：多机 Plan 没有执行。")
else:
    try:
        connect_result = fleet.connect()
        print("批量连接：", connect_result.message)
        if not connect_result.ok:
            raise RuntimeError(connect_result.message)

        result = (
            FleetDock.core.plan(fleet)
            .wait_until_ready(timeout_s=90.0)
            .unlock()
            .takeoff(altitude=TARGET_ALTITUDE_M)
            .run(step_timeout_s=STEP_TIMEOUT_S)
        )

        print(f"编队任务成功：{result.ok}，状态：{result.status}")
        print("成功设备：", result.succeeded_devices)
        print("失败设备：", result.failed_devices)
        for device_id, device_result in result.device_results.items():
            print(f"  - {device_id}: ok={device_result.ok}, message={device_result.message}")

        print("\\nPlan 完成后的高度快照：")
        for alias, drone in zip(selected_aliases, drones):
            altitude = drone.state.snapshot().relative_altitude_m
            reached = altitude is not None and abs(altitude - TARGET_ALTITUDE_M) <= 1.0
            print(f"  - {alias}: altitude={altitude}, reached={reached}")
        if not result.ok:
            raise RuntimeError(result.message)
    finally:
        print("编队安全返航：", fleet.drones().return_to_launch())
        FleetDock.close()
`
      ),
      markdown(
        'summary',
        `
## 多机编排要点

- Fleet 是设备集合，\`FleetDock.core.plan(fleet)\` 会生成多机 Plan。
- 多机结果不要只看顶层 \`ok\`，还应检查 \`device_results\` 和 \`failed_devices\`。
- Fleet Plan 适用于“所有成员执行同一种动作”；需要每架机执行不同航线时，可分别创建单机 Plan，再使用并行计划能力。`
      )
    ]
  },
  {
    fileName: '04_多机_Plan_螺旋与走廊航线.ipynb',
    cells: [
      markdown(
        'intro',
        `
# 04｜多机进阶：Plan 执行螺旋航线与走廊航线

这个 Demo 展示 FleetDock 的标准航线对象：先用 \`Route.spiral()\` 生成螺旋搜索，再用 \`Route.corridor()\` 根据中心线生成走廊巡检路径，最后交给一个多机 Plan 顺序执行。

航线生成本身不会控制无人机，可以安全预览。真正飞行只发生在 Plan 的 \`run()\`。示例默认使用前 2 架无人机，并假设仿真中的初始位置已安全分离。`
      ),
      code('runtime', RUNTIME_SETUP),
      code(
        'build-routes',
        `
from fleetdock import Route

spiral_route = Route.spiral(
    max_radius_m=12.0,
    spacing_m=3.0,
    turns=2.0,
    name="多机螺旋搜索",
    acceptance_radius_m=1.5,
)

corridor_route = Route.corridor(
    [
        {"x_m": 0.0, "y_m": 0.0},
        {"x_m": 20.0, "y_m": 0.0},
        {"x_m": 35.0, "y_m": 12.0},
    ],
    width_m=8.0,
    name="多机走廊巡检",
    acceptance_radius_m=1.5,
)

for route in (spiral_route, corridor_route):
    print(
        f"{route.name}: pattern={route.pattern.value}, "
        f"航点数={len(route.waypoints)}, geometry={route.geometry}"
    )
`
      ),
      code(
        'prepare-fleet',
        `
if len(device_aliases) < 2:
    raise RuntimeError("进阶多机 Demo 至少需要绑定 2 架无人机。")

selected_aliases = device_aliases[:2]
drones = [getattr(FleetDock.devices, alias) for alias in selected_aliases]
fleet = FleetDock.core.fleet(*drones, name="搜索巡检编队")

ALTITUDE_M = 15.0
SPEED_M_S = 3.0
STEP_TIMEOUT_S = 180.0

print("编队成员：", selected_aliases)
print("注意：同一条本地航线会应用到每个成员各自的本地坐标系。")
`
      ),
      code(
        'run-routes',
        `
RUN_FLIGHT = False  # 确认仿真和安全间距后改为 True

if not RUN_FLIGHT:
    print("航线已生成并完成预览；安全开关未开启，没有发送飞行命令。")
else:
    try:
        connect_result = fleet.connect()
        print("批量连接：", connect_result.message)
        if not connect_result.ok:
            raise RuntimeError(connect_result.message)

        result = (
            FleetDock.core.plan(fleet)
            .wait_until_ready(timeout_s=90.0)
            .unlock()
            .takeoff(altitude=ALTITUDE_M)
            .routes(
                [spiral_route, corridor_route],
                altitude=ALTITUDE_M,
                speed=SPEED_M_S,
                timeout_s=STEP_TIMEOUT_S,
            )
            .return_to_launch()
            .run(step_timeout_s=STEP_TIMEOUT_S)
        )

        print(f"航线任务成功：{result.ok}，状态：{result.status}")
        print("成功设备：", result.succeeded_devices)
        print("失败设备：", result.failed_devices)
        for device_id, device_result in result.device_results.items():
            print(f"  - {device_id}: ok={device_result.ok}, message={device_result.message}")
        if not result.ok:
            raise RuntimeError(result.message)
    finally:
        # 即使 Plan 中已有返航，finally 仍保留兜底清理。
        print("兜底返航：", fleet.drones().return_to_launch())
        FleetDock.close()
`
      ),
      markdown(
        'summary',
        `
## 航线 API 的教学重点

- \`Route.spiral / Route.corridor\` 只负责生成标准航线，不会立即起飞。
- \`plan.routes([route1, route2])\` 按列表顺序执行多条航线。
- \`altitude\`、\`speed\`、\`timeout_s\` 属于执行参数；半径、间距、走廊宽度属于航线几何参数。
- 生产环境应进一步配置安全间距、地理围栏和避障策略；不要让多机在同一世界坐标中重叠飞行。`
      )
    ]
  },
  {
    fileName: '05_设备诊断_能力与生命周期.ipynb',
    cells: [
      markdown(
        'intro',
        `
# 05｜设备诊断、能力发现与生命周期

控制设备前，先回答三个问题：设备是什么、SDK 声明它能做什么、当前是否具备执行条件。

这个 Demo 只读取 FleetDock 标准模型；连接检查默认关闭，不会发送飞行命令。`
      ),
      code('runtime', RUNTIME_SETUP),
      code(
        'profile',
        `
from fleetdock import CapabilityName

DRONE_ALIAS = device_aliases[0]
drone = getattr(FleetDock.devices, DRONE_ALIAS)
profile = drone.profile

print("别名：", DRONE_ALIAS)
print("业务设备 ID：", drone.metadata.device_id)
print("SDK 实例 ID：", drone.instance_id)
print("产品：", drone.product_key)
print("固件：", drone.firmware_version)
print("设备类别：", profile.category.value)
print("厂商：", profile.vendor.value)
print("集成成熟度：", profile.maturity.value)
print("生命周期：", drone.lifecycle_state.value)
print("标准能力：", sorted(item.value for item in profile.capabilities))
print("标准命令：", sorted(item.value for item in profile.supported_commands))
print("标准事件数量：", len(profile.supported_events))
`
      ),
      code(
        'readiness',
        `
for capability in (
    CapabilityName.TELEMETRY,
    CapabilityName.TAKEOFF,
    CapabilityName.ROUTE,
):
    print(
        f"{capability.value}: supported={drone.has_capability(capability)}, "
        f"readiness={drone.readiness_for(capability)}"
    )

print("当前状态快照：", drone.state.snapshot())
print("当前诊断快照：", drone.diagnostics())
`
      ),
      code(
        'optional-connect',
        `
RUN_CONNECTION_CHECK = False  # 启动仿真后可改为 True；只连接和读取诊断，不起飞

if not RUN_CONNECTION_CHECK:
    print("未连接设备；上面的 readiness 会明确显示缺少哪些条件。")
else:
    try:
        response = drone.connect()
        print("连接响应：", response)
        print("连接后诊断：", drone.diagnostics())
        print("连接后起飞就绪：", drone.readiness_for(CapabilityName.TAKEOFF))
    finally:
        FleetDock.close()
`
      ),
      markdown(
        'summary',
        `
## 要点

- \`profile\` 描述产品能力，不代表当前设备已经在线。
- \`diagnostics()\` 是连接和遥测健康快照。
- \`readiness_for()\` 针对某项能力返回缺失条件，不会等待状态变化。
- 需要等待时使用 Plan 的 \`wait_until_ready()\`，或在直接命令模式中自行轮询。`
      )
    ]
  },
  {
    fileName: '06_单机_强类型事件订阅.ipynb',
    cells: [
      markdown(
        'intro',
        `
# 06｜单机强类型事件订阅

FleetDock 把 MAVSDK 等厂商遥测转换成统一事件。业务代码可以直接读取经纬度、高度、电池和状态变化，不需要解析厂商字典。

本 Demo 只连接并观察遥测，不解锁、不起飞。`
      ),
      code('runtime', RUNTIME_SETUP),
      code(
        'handlers',
        `
from fleetdock import (
    ArmedChangedEvent,
    BatteryUpdatedEvent,
    EventType,
    GlobalPositionUpdatedEvent,
)

DRONE_ALIAS = device_aliases[0]
drone = getattr(FleetDock.devices, DRONE_ALIAS)

def on_position(event: GlobalPositionUpdatedEvent):
    position = event.position
    print(
        f"[位置] device={event.source.device_id} "
        f"lat={position.latitude_deg:.7f} lon={position.longitude_deg:.7f} "
        f"relative_altitude={position.relative_altitude_m}"
    )

def on_battery(event: BatteryUpdatedEvent):
    battery = event.battery
    print(f"[电池] device={event.source.device_id} remaining={battery.remaining_fraction:.1%}")

def on_armed(event: ArmedChangedEvent):
    print(f"[解锁状态] {event.change.previous} -> {event.change.current}")
`
      ),
      code(
        'observe',
        `
import time

RUN_EVENT_OBSERVE = False  # 启动仿真后改为 True
OBSERVE_SECONDS = 10.0

if not RUN_EVENT_OBSERVE:
    print("事件处理器已定义；安全开关未开启，没有连接设备。")
else:
    unsubscribes = [
        drone.events.on(EventType.GLOBAL_POSITION_UPDATED, on_position),
        drone.events.on(EventType.BATTERY_UPDATED, on_battery),
        drone.events.on(EventType.ARMED_CHANGED, on_armed),
    ]
    try:
        response = drone.connect()
        print("连接：", response)
        if not response.ok:
            raise RuntimeError(response.message)
        time.sleep(OBSERVE_SECONDS)
        drone.events.drain(timeout_s=3.0)
    finally:
        for unsubscribe in unsubscribes:
            unsubscribe()
        FleetDock.close()
`
      ),
      markdown(
        'summary',
        `
## 事件订阅规则

- \`events.on(EventType.X, handler)\` 按标准类型订阅。
- handler 收到强类型事件；来源统一从 \`event.source\` 读取。
- 订阅返回取消函数，必须在 \`finally\` 中调用。
- \`events.drain()\` 用于等待已经入队的回调处理完成。`
      )
    ]
  },
  {
    fileName: '07_多机_事件与状态面板.ipynb',
    cells: [
      markdown(
        'intro',
        `
# 07｜多机事件汇总与状态面板

Fleet 的事件中心会聚合所有成员的标准事件，并保留来源设备。这个 Demo 同时展示事件过滤和多机状态快照表。

默认不连接设备；启动至少 2 架仿真无人机后再开启观察。`
      ),
      code('runtime', RUNTIME_SETUP),
      code(
        'fleet',
        `
from fleetdock import EventType

if len(device_aliases) < 2:
    raise RuntimeError("多机状态面板至少需要 2 架无人机。")

selected_aliases = device_aliases[:4]
drones = [getattr(FleetDock.devices, alias) for alias in selected_aliases]
fleet = FleetDock.core.fleet(*drones, name="状态观察编队")

def print_event(event):
    print(f"[{event.source.device_id}] {event.type.value}")

def snapshot_rows():
    rows = []
    for alias, drone in zip(selected_aliases, drones):
        state = drone.state.snapshot()
        rows.append({
            "alias": alias,
            "device_id": drone.metadata.device_id,
            "connected": state.connected,
            "armed": state.armed,
            "in_air": state.in_air,
            "altitude_m": state.relative_altitude_m,
            "battery": state.battery_remaining_fraction,
            "flight_mode": state.flight_mode,
            "failsafe": state.failsafe,
        })
    return rows
`
      ),
      code(
        'observe',
        `
import time
from IPython.display import display
import pandas as pd

RUN_FLEET_OBSERVE = False  # 启动多机仿真后改为 True
OBSERVE_SECONDS = 10.0

if not RUN_FLEET_OBSERVE:
    print("当前未连接；先展示标准状态表结构：")
    display(pd.DataFrame(snapshot_rows()))
else:
    unsubscribes = [
        fleet.events.subscribe(EventType.BATTERY_UPDATED, print_event),
        fleet.events.subscribe(EventType.GLOBAL_POSITION_UPDATED, print_event),
        fleet.events.subscribe(EventType.FAILSAFE_CHANGED, print_event),
    ]
    try:
        connect_result = fleet.connect()
        print("批量连接：", connect_result.message)
        if not connect_result.ok:
            raise RuntimeError(connect_result.message)
        time.sleep(OBSERVE_SECONDS)
        fleet.events.drain(timeout_s=3.0)
        display(pd.DataFrame(snapshot_rows()))
    finally:
        for unsubscribe in unsubscribes:
            unsubscribe()
        FleetDock.close()
`
      ),
      markdown(
        'summary',
        `
## 可继续扩展

- 通过 \`device=...\` 只订阅某个成员。
- 通过 \`predicate=...\` 只保留低电量或高于某高度的事件。
- 周期调用 \`snapshot_rows()\` 可构建实时仪表盘。
- Fleet 只聚合事件，不会丢失原始 \`event.source.device_id\`。`
      )
    ]
  },
  {
    fileName: '08_异步任务_状态与取消.ipynb',
    cells: [
      markdown(
        'intro',
        `
# 08｜异步任务、状态查询与取消

\`plan.run()\` 会阻塞当前单元直到任务完成；\`plan.start()\` 会立即返回 \`TaskSession\`，适合 UI、后台任务和人工取消。

请按单元顺序运行。默认不会启动任务。`
      ),
      code('runtime', RUNTIME_SETUP),
      code(
        'prepare',
        `
from fleetdock import Route, TaskCancelMode

DRONE_ALIAS = device_aliases[0]
drone = getattr(FleetDock.devices, DRONE_ALIAS)
route = Route.circle(radius_m=10.0, segments=24, name="异步圆形巡检")
session = None
print(f"设备={DRONE_ALIAS}，航线航点数={len(route.waypoints)}")
`
      ),
      code(
        'start',
        `
RUN_ASYNC_FLIGHT = False  # 启动仿真并确认安全后改为 True

if not RUN_ASYNC_FLIGHT:
    print("安全开关未开启：没有启动后台任务。")
else:
    response = drone.connect()
    print("连接：", response)
    if not response.ok:
        raise RuntimeError(response.message)
    session = (
        FleetDock.core.plan(drone)
        .wait_until_ready(timeout_s=60.0)
        .unlock()
        .takeoff(altitude=12.0)
        .route(route, altitude=12.0, speed=2.5, timeout_s=180.0)
        .return_to_launch()
        .start(step_timeout_s=180.0)
    )
    print("后台任务已启动，当前状态：", session.state.value)
`
      ),
      code(
        'inspect-cancel',
        `
REQUEST_CANCEL = False  # 任务运行时可改为 True，再单独运行本单元

if session is None:
    print("当前没有活动会话。")
else:
    print("state=", session.state.value, "done=", session.done, "result=", session.result)
    if REQUEST_CANCEL and not session.done:
        accepted = session.cancel(TaskCancelMode.SAFE_STOP)
        print("取消请求是否首次被接受：", accepted)
`
      ),
      code(
        'wait-cleanup',
        `
WAIT_FOR_RESULT = False  # 想等待最终结果时改为 True

if session is None:
    print("没有任务需要等待。")
elif not WAIT_FOR_RESULT:
    print("未等待任务；稍后可重新运行状态查询单元。")
else:
    try:
        result = session.wait(timeout_s=240.0)
        print("最终状态：", session.state.value)
        print("任务结果：", result)
    finally:
        if not session.done:
            session.cancel(TaskCancelMode.SAFE_STOP)
        print("兜底返航：", drone.return_to_launch())
        FleetDock.close()
`
      ),
      markdown(
        'summary',
        `
## 关键语义

- \`start()\` 创建后台线程并立即返回 TaskSession。
- \`session.done / state / result / exception\` 可随时查询。
- \`wait(timeout_s)\` 超时只停止等待，不会自动取消任务。
- \`cancel(SAFE_STOP)\` 是协作式取消和通用安全停止，不等同于自动返航；业务仍应显式处理返航或降落。`
      )
    ]
  },
  {
    fileName: '09_失败_超时与恢复.ipynb',
    cells: [
      markdown(
        'intro',
        `
# 09｜失败、超时与恢复

可靠应用不能只演示成功路径。本 Demo 展示三类边界：声明阶段参数错误、就绪等待失败、Plan 的结构化失败结果与 \`on_failed\` 恢复回调。

默认只执行不会控制设备的参数校验。`
      ),
      code('runtime', RUNTIME_SETUP),
      code(
        'validation-error',
        `
from fleetdock import Route

try:
    Route.corridor([{"x_m": 0.0, "y_m": 0.0}], width_m=5.0)
except ValueError as error:
    print("声明阶段参数错误已捕获：", error)

try:
    Route.spiral(max_radius_m=-1.0, spacing_m=2.0)
except ValueError as error:
    print("非法螺旋参数已捕获：", error)
`
      ),
      code(
        'result-printer',
        `
DRONE_ALIAS = device_aliases[0]
drone = getattr(FleetDock.devices, DRONE_ALIAS)

def print_task_result(result):
    print(f"ok={result.ok}, status={result.status}, failed_step={result.failed_step}")
    print("message=", result.message)
    for step in result.steps:
        print(f"  - {step.name}: ok={step.ok}, message={step.message}")

def recover_with_rtl(ctx, result):
    """失败回调应短小，只触发明确的安全补偿。"""
    print(f"恢复回调：失败步骤={result.failed_step}，准备返航")
    ctx.device.return_to_launch()
`
      ),
      code(
        'readiness-failure',
        `
RUN_READINESS_PROBE = False  # 可改为 True；只连接并等待就绪，不解锁、不起飞

if not RUN_READINESS_PROBE:
    print("恢复流程已定义；默认不连接设备。")
else:
    try:
        response = drone.connect()
        print("连接：", response)
        result = (
            FleetDock.core.plan(drone)
            .on_failed(recover_with_rtl)
            .wait_until_ready(timeout_s=5.0)
            .run(step_timeout_s=6.0)
        )
        print_task_result(result)
    finally:
        FleetDock.close()
`
      ),
      markdown(
        'summary',
        `
## 错误处理原则

- 配置错误通常直接抛出 \`ValueError\`，应在任务开始前修正。
- 运行期失败通过 \`TaskResult\` 返回，不要解析日志判断成功。
- 重点检查 \`status / failed_step / steps\`；多机任务还要检查 \`device_results\`。
- \`on_failed\` 用于业务补偿，但回调异常不应掩盖原始失败。
- 超时、取消、失败是不同状态，应采用不同恢复策略。`
      )
    ]
  },
  {
    fileName: '10_标准航线生成与可视化.ipynb',
    cells: [
      markdown(
        'intro',
        `
# 10｜标准航线生成与 SVG 可视化

FleetDock 的 Route 工厂可以在没有设备和仿真的情况下生成航线。本 Demo 创建常见本地米制航线，并用无额外依赖的内联 SVG 绘图；整个 Notebook 不会发送控制命令。`
      ),
      code(
        'routes',
        `
from fleetdock import Route

routes = [
    Route.polyline(
        [{"x_m": 0, "y_m": 0}, {"x_m": 12, "y_m": 5}, {"x_m": 20, "y_m": 0}],
        name="折线",
    ),
    Route.polygon(
        [{"x_m": 0, "y_m": 0}, {"x_m": 15, "y_m": 0}, {"x_m": 12, "y_m": 10}],
        name="多边形",
    ),
    Route.circle(radius_m=8, segments=24, name="圆形"),
    Route.ellipse(radius_x_m=12, radius_y_m=6, segments=32, name="椭圆"),
    Route.arc(radius_m=10, start_deg=20, end_deg=220, segments=20, name="圆弧"),
    Route.grid(width_m=20, height_m=12, spacing_m=3, name="网格覆盖"),
    Route.survey_area(
        [{"x_m": 0, "y_m": 0}, {"x_m": 20, "y_m": 0}, {"x_m": 16, "y_m": 14}, {"x_m": 2, "y_m": 10}],
        spacing_m=3,
        angle_deg=20,
        name="区域测绘",
    ),
    Route.spiral(max_radius_m=12, spacing_m=3, turns=2, name="螺旋搜索"),
    Route.corridor(
        [{"x_m": 0, "y_m": 0}, {"x_m": 16, "y_m": 0}, {"x_m": 28, "y_m": 10}],
        width_m=6,
        name="走廊巡检",
    ),
]

for route in routes:
    print(f"{route.name}: pattern={route.pattern.value}, waypoints={len(route.waypoints)}")
`
      ),
      code(
        'plot',
        `
from html import escape
from IPython.display import SVG, display

PANEL_WIDTH = 300
PANEL_HEIGHT = 230
COLUMNS = 3
ROWS = (len(routes) + COLUMNS - 1) // COLUMNS

svg = [
    f'<svg xmlns="http://www.w3.org/2000/svg" '
    f'viewBox="0 0 {PANEL_WIDTH * COLUMNS} {PANEL_HEIGHT * ROWS}">',
    '<rect width="100%" height="100%" fill="#f8fafc"/>',
]

for index, route in enumerate(routes):
    column = index % COLUMNS
    row = index // COLUMNS
    offset_x = column * PANEL_WIDTH
    offset_y = row * PANEL_HEIGHT
    points = [(float(item.x_m or 0.0), float(item.y_m or 0.0)) for item in route.waypoints]
    xs, ys = zip(*points)
    span_x = max(max(xs) - min(xs), 1.0)
    span_y = max(max(ys) - min(ys), 1.0)
    scale = min(240.0 / span_x, 150.0 / span_y)
    screen_points = [
        (30.0 + (x - min(xs)) * scale, 195.0 - (y - min(ys)) * scale)
        for x, y in points
    ]
    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in screen_points)
    start_x, start_y = screen_points[0]
    end_x, end_y = screen_points[-1]
    title = escape(f"{route.name} / {route.pattern.value}")

    svg.extend([
        f'<g transform="translate({offset_x},{offset_y})">',
        '<rect x="8" y="8" width="284" height="214" rx="8" fill="white" stroke="#cbd5e1"/>',
        f'<text x="20" y="30" font-size="14" font-family="sans-serif" fill="#0f172a">{title}</text>',
        f'<polyline points="{polyline}" fill="none" stroke="#0284c7" stroke-width="2"/>',
        f'<circle cx="{start_x:.1f}" cy="{start_y:.1f}" r="5" fill="#16a34a"/>',
        f'<circle cx="{end_x:.1f}" cy="{end_y:.1f}" r="4" fill="#dc2626"/>',
        f'<text x="20" y="215" font-size="11" font-family="sans-serif" fill="#475569">航点 {len(points)}；绿=起点，红=终点</text>',
        '</g>',
    ])

svg.append('</svg>')
display(SVG("".join(svg)))
`
      ),
      markdown(
        'summary',
        `
## 从生成到执行

Route 只描述几何和航点。执行时再把它交给：

\`FleetDock.core.plan(drone).route(route, altitude=..., speed=...).run()\`

这种分离让应用可以先校验、绘图、保存和审批航线，再决定是否控制设备。GPS 航线还可以结合 \`GpsPoint\` 与坐标转换 API。`
      )
    ]
  }
];

function notebookJson(cells: NotebookCell[]): string {
  return `${JSON.stringify(
    {
      cells,
      metadata: {
        kernelspec: {
          display_name: 'Python 3 (FleetDock IDEA)',
          language: 'python',
          name: 'python3'
        },
        language_info: {
          name: 'python',
          version: '3.12'
        }
      },
      nbformat: 4,
      nbformat_minor: 5
    },
    null,
    1
  )}\n`;
}

export function ensureFleetDockDemoNotebooks(workingDirectory: string): void {
  const demoDirectory = path.join(workingDirectory, 'demo');
  fs.mkdirSync(demoDirectory, { recursive: true });
  for (const notebook of NOTEBOOKS) {
    const notebookPath = path.join(demoDirectory, notebook.fileName);
    if (!fs.existsSync(notebookPath)) {
      fs.writeFileSync(notebookPath, notebookJson(notebook.cells), 'utf8');
    }
  }
}
