"""PX4 多机计划示例；展示 Fleet 就绪、并行航线、事件和统一返航。"""

from __future__ import annotations

import sys
import time

from fleetdock import (
    DeviceFactory,
    DeviceMetadata,
    DeviceEvent,
    EventType,
    Fleet,
    Plan,
    Route,
    RouteTransform2D,
    TaskStepHookEvent,
    TaskStepHookPhase,
    UnlockStrategy,
)

from drone_plan import (
    ALL_ROUTES_NAME,
    DemoRoute,
    HeadingMode,
    MIN_ROUTE_ALTITUDE_M,
    MotionExecutionMode,
    TAKEOFF_ALTITUDE_M,
    TASK_CLIMB_SPEED_M_S,
    TASK_LOOKAHEAD_TIME_S,
    TASK_MIN_LOOKAHEAD_M,
    TASK_ROUTE_MOTION,
    TASK_SPEED_M_S,
    build_common_routes,
    capture_photo_on_waypoint,
    print_route_parameters,
    print_task_result,
)


DEFAULT_DRONES = (
    ("drone-001", "Drone1", "udpin://0.0.0.0:14555"),
    ("drone-002", "Drone2", "udpin://0.0.0.0:14556"),
    ("drone-003", "Drone3", "udpin://0.0.0.0:14557"),
)
DEFAULT_FLEET_ROUTE_NAMES = (
    DemoRoute.POINT_TO_POINT.value,
    DemoRoute.POLYLINE.value,
)
DRONE_ROUTE_OFFSETS_M = {
    "drone-001": (0.0, 0.0),
    "drone-002": (0.0, 0.0),
    "drone-003": (0.0, 0.0),
}
DRONE_ROUTE_ALTITUDES_M = {
    "drone-001": MIN_ROUTE_ALTITUDE_M,
    "drone-002": MIN_ROUTE_ALTITUDE_M + 2.0,
    "drone-003": MIN_ROUTE_ALTITUDE_M + 2.0,
}
FLEET_ROUTE_SCALE = 0.55
READY_PROGRESS_INTERVAL_S = 5.0
TAKEOFF_ONLY_ARG = "--takeoff-only"
TAKEOFF_CONFIRM_TIMEOUT_S = 90.0
TAKEOFF_CONFIRM_INTERVAL_S = 1.0
TAKEOFF_CONFIRM_TOLERANCE_M = 2.0
ROUTE_PLAN_LOG_INTERVAL_S = 10.0
FLEET_TELEMETRY_LOG_INTERVAL_S = 5.0
_last_route_plan_log_at = -ROUTE_PLAN_LOG_INTERVAL_S
_last_fleet_telemetry_log_at: dict[str, float] = {}


def print_task_step(event: TaskStepHookEvent) -> None:
    """实时打印 Plan 中每个 Fleet 或设备步骤的开始、完成和异常状态。"""
    target = "Fleet" if event.scope == "fleet" else event.device_id or "device"
    if event.phase == TaskStepHookPhase.BEFORE_STEP:
        print(f"[{target}] 步骤 {event.step_index} 开始：{event.step_name}，参数={dict(event.params)}", flush=True)
    elif event.phase == TaskStepHookPhase.AFTER_STEP:
        status = "成功" if event.result is not None and event.result.ok else "失败"
        message = event.result.message if event.result is not None else event.message
        print(f"[{target}] 步骤 {event.step_index} 完成：{event.step_name}，结果={status}，消息={message}", flush=True)
    elif event.phase == TaskStepHookPhase.ON_STEP_ERROR:
        print(f"[{target}] 步骤 {event.step_index} 异常：{event.step_name}，原因={event.message}", flush=True)


def build_fleet() -> Fleet:
    """创建与 examples/run_dev_multi.bat 默认端口对应的三机 Fleet。"""
    drones = []
    for device_id, name, endpoint in DEFAULT_DRONES:
        drones.append(
            DeviceFactory.create()
            .px4_generic()
            .params(endpoint=endpoint, timeout_s=30.0)
            .metadata(DeviceMetadata(device_id=device_id, name=name, tags=("multi-demo",)))
            .build()
        )
    return Fleet(drones, name="PX4 三机任务编队")


def select_routes() -> list[Route]:
    """按命令行参数选择航线；默认执行两条适合多机仿真观察的短航线。"""
    routes = build_common_routes()
    route_args = [arg for arg in sys.argv[1:] if arg != TAKEOFF_ONLY_ARG]
    route_name = route_args[0] if route_args else ",".join(DEFAULT_FLEET_ROUTE_NAMES)
    if route_name == ALL_ROUTES_NAME:
        selected_routes = list(routes.values())
        print(f"Fleet 本次执行全部航线：{', '.join(route.name or route.pattern.value for route in selected_routes)}")
        return selected_routes
    route_names = [name.strip() for name in route_name.split(",") if name.strip()]
    if len(route_names) > 1:
        selected_routes = []
        for name in route_names:
            try:
                selected_routes.append(routes[DemoRoute(name)])
            except ValueError as exc:
                names = ", ".join([ALL_ROUTES_NAME] + [route.value for route in DemoRoute])
                raise ValueError(f"未知航线：{name}，可选航线：{names}") from exc
        print(f"Fleet 本次执行航线：{', '.join(route_names)}")
        return selected_routes
    try:
        selected = DemoRoute(route_names[0])
    except ValueError as exc:
        names = ", ".join([ALL_ROUTES_NAME] + [route.value for route in DemoRoute])
        raise ValueError(f"未知航线：{route_name}，可选航线：{names}") from exc
    print(f"Fleet 本次执行航线：{route_names[0]}")
    return [routes[selected]]


def is_takeoff_only() -> bool:
    """判断命令行是否传入 ``--takeoff-only``；返回真时起飞确认后结束示例。"""
    return TAKEOFF_ONLY_ARG in sys.argv[1:]


def offset_route(route: Route, *, offset_x_m: float, offset_y_m: float, scale: float, name_suffix: str) -> Route:
    """调用标准路线变换，为单架无人机生成缩放和横向偏移航线。"""
    route_name = route.name or str(route.pattern)
    return route.transform(
        RouteTransform2D(
            scale_x=scale,
            scale_y=scale,
            translation_x_m=offset_x_m,
            translation_y_m=offset_y_m,
        ),
        name=f"{route_name}-{name_suffix}",
    )


def offset_routes_for_device(routes: list[Route], device_id: str) -> list[Route]:
    """按设备 ID 返回适合并行观察的偏移航线。"""
    offset_x_m, offset_y_m = DRONE_ROUTE_OFFSETS_M.get(device_id, (0.0, 0.0))
    return [
        offset_route(
            route,
            offset_x_m=offset_x_m,
            offset_y_m=offset_y_m,
            scale=FLEET_ROUTE_SCALE,
            name_suffix=device_id,
        )
        for route in routes
    ]


def route_altitude_for_device(device_id: str) -> float:
    """返回单机并行航线高度，单位为米；未知设备使用默认安全高度。"""
    return DRONE_ROUTE_ALTITUDES_M.get(device_id, MIN_ROUTE_ALTITUDE_M)


def configure_drone_plan(plan):
    """给无人机计划应用 fleet_plan 的统一速度和确认参数。"""
    return (
        plan.speed(TASK_SPEED_M_S)
        .climb_speed(TASK_CLIMB_SPEED_M_S)
        .altitude(MIN_ROUTE_ALTITUDE_M)
        .tolerance(0.5)
        .timeout(180.0)
        .heading(HeadingMode.TANGENT)
        .on_failed(handle_fleet_failed)
    )


def print_fleet_route_started(event: DeviceEvent) -> None:
    """按设备打印 Fleet 航线开始事件。"""
    progress = event.progress
    route_name = progress.route_name or progress.pattern
    print(
        f"[{event.source.device_id}] 航线开始 {route_name} 航点数={progress.waypoint_count} 类型={progress.pattern}",
        flush=True,
    )


def print_fleet_route_completed(event: DeviceEvent) -> None:
    """按设备打印 Fleet 航线完成事件。"""
    progress = event.progress
    route_name = progress.route_name or progress.pattern
    print(f"[{event.source.device_id}] 航线完成 {route_name}", flush=True)


def capture_fleet_photo_on_waypoint(event: DeviceEvent) -> None:
    """打印设备来源并复用单机示例的航点拍照回调。"""
    print(f"[{event.source.device_id}] ", end="", flush=True)
    capture_photo_on_waypoint(event)


def should_print_fleet_telemetry(device_id: str) -> bool:
    """按设备限制遥测打印频率，避免 PX4 高频遥测刷屏。"""
    now = time.monotonic()
    last_print_at = _last_fleet_telemetry_log_at.get(device_id, 0.0)
    if now - last_print_at < FLEET_TELEMETRY_LOG_INTERVAL_S:
        return False
    _last_fleet_telemetry_log_at[device_id] = now
    return True


def print_fleet_global_position(event: DeviceEvent) -> None:
    """打印 Fleet 成员全球位置，包括经纬度、绝对高度和相对 Home 高度。"""
    device_id = event.source.device_id
    if not should_print_fleet_telemetry(device_id):
        return
    position = event.position
    relative_altitude = position.relative_altitude_m
    relative_altitude_text = "未知" if relative_altitude is None else f"{relative_altitude:.2f} m"
    print(
        f"[{device_id}] 全球位置 "
        f"lat={position.latitude_deg:.7f}, lon={position.longitude_deg:.7f}, "
        f"absolute_alt={position.absolute_altitude_m:.2f} m, relative_alt={relative_altitude_text}",
        flush=True,
    )


def is_any_low_battery(ctx) -> bool:
    """返回 Fleet 中是否存在剩余电量低于 25% 的设备。"""
    for device in ctx.device:
        remaining = device.state.snapshot().battery_remaining_fraction
        if remaining is not None and remaining < 0.25:
            return True
    return False


def return_fleet_to_launch(ctx) -> None:
    """作为守卫动作请求 Fleet 中全部无人机返航。"""
    for device in ctx.device.drones():
        device.return_to_launch()


def handle_fleet_failed(ctx, result) -> None:
    """Fleet 任务失败时停止全部无人机并请求返航。"""
    print(f"Fleet 任务失败处理：步骤={result.failed_step}，原因={result.message}")
    for device in ctx.device.drones():
        device.stop()
        device.return_to_launch()


def print_readiness(report) -> None:
    """打印 SDK 强类型就绪报告。"""
    missing = ", ".join(item.value for item in report.missing_requirements) or "ready"
    print(f"[Fleet 准备状态] [{report.device_id}] capability={report.capability.value} missing={missing}", flush=True)


class FleetDemoSubscriptions:
    """封装示例脚本的 Fleet 事件订阅与释放逻辑。"""

    def __init__(self, fleet: Fleet) -> None:
        """保存 Fleet；进入上下文时再注册事件。"""
        self._fleet = fleet
        self._unsubscribe_route_started = lambda: None
        self._unsubscribe_route_completed = lambda: None
        self._unsubscribe_photo = lambda: None
        self._unsubscribe_global_position = lambda: None

    def __enter__(self) -> "FleetDemoSubscriptions":
        """注册示例所需事件订阅，并返回当前对象。"""
        self._unsubscribe_route_started = self._fleet.events.subscribe(
            EventType.ROUTE_STARTED,
            print_fleet_route_started,
        )
        self._unsubscribe_route_completed = self._fleet.events.subscribe(
            EventType.ROUTE_COMPLETED,
            print_fleet_route_completed,
        )
        self._unsubscribe_photo = self._fleet.events.subscribe(
            EventType.WAYPOINT_ACTION_REQUESTED,
            capture_fleet_photo_on_waypoint,
        )
        self._unsubscribe_global_position = self._fleet.events.subscribe(
            EventType.GLOBAL_POSITION_UPDATED,
            print_fleet_global_position,
        )
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        """释放示例事件订阅。"""
        self._unsubscribe_global_position()
        self._unsubscribe_photo()
        self._unsubscribe_route_completed()
        self._unsubscribe_route_started()


def ensure_connect_ok(connect_result) -> None:
    """打印 Fleet 连接结果；任一设备连接失败时直接终止示例。"""
    print(f"[Fleet 连接结果] {connect_result.message}", flush=True)
    for device_id, response in connect_result.device_results.items():
        print(f"[{device_id}] connect ok={response.ok} message={response.message}", flush=True)
    if not connect_result.ok:
        print(f"连接失败设备：{', '.join(connect_result.failed_devices)}")
        raise SystemExit(1)


def prepare_and_takeoff_fleet(fleet: Fleet, *, altitude_m: float = TAKEOFF_ALTITUDE_M) -> bool:
    """通过标准 Fleet 计划完成就绪检查、显式解锁策略和指定米制高度起飞确认。"""
    print(
        "[Fleet 准备] 等待全部无人机收到位置和 Home 遥测，随后解锁并起飞。",
        flush=True,
    )
    result = (
        configure_drone_plan(Plan(fleet, interceptors=[print_task_step]).for_drones())
        .wait_until_ready(
            timeout_s=60.0,
            require_armable=False,
            progress_callback=print_readiness,
        )
        .unlock(strategy=UnlockStrategy.STANDARD_THEN_FORCE)
        .takeoff(altitude=altitude_m)
        .run(step_timeout_s=TAKEOFF_CONFIRM_TIMEOUT_S)
    )
    print_task_result(result)
    return result.ok


def build_route_plans_for_fleet(fleet: Fleet, selected_routes: list[Route]):
    """按设备生成并行航线计划，并打印每台无人机的航线参数。"""
    global _last_route_plan_log_at

    now = time.monotonic()
    should_log_route_params = now - _last_route_plan_log_at >= ROUTE_PLAN_LOG_INTERVAL_S
    if should_log_route_params:
        _last_route_plan_log_at = now

    route_plans = []
    for device_id in fleet.drones().device_ids:
        routes = offset_routes_for_device(selected_routes, device_id)
        route_altitude_m = route_altitude_for_device(device_id)
        offset_x_m, offset_y_m = DRONE_ROUTE_OFFSETS_M.get(device_id, (0.0, 0.0))
        if should_log_route_params:
            print(
                f"[{device_id}] 并行航线 scale={FLEET_ROUTE_SCALE:.2f} "
                f"local_offset=({offset_x_m:.1f}, {offset_y_m:.1f}) m "
                f"alt={route_altitude_m:.1f} m"
            )
            for route in routes:
                print_route_parameters(
                    route,
                    label=f"{device_id} 航线",
                    altitude_m=route_altitude_m,
                    speed_m_s=TASK_SPEED_M_S,
                    motion_mode=MotionExecutionMode.AUTO,
                    heading=HeadingMode.TANGENT,
                    lookahead_time_s=TASK_LOOKAHEAD_TIME_S,
                    min_lookahead_m=TASK_MIN_LOOKAHEAD_M,
                )
        route_plan = Plan(fleet.by_id(device_id), interceptors=[print_task_step])
        route_plans.append(
            configure_drone_plan(route_plan).routes(
                routes,
                altitude=route_altitude_m,
                motion=TASK_ROUTE_MOTION,
            )
        )
    return route_plans


def main() -> None:
    """连接多机 Fleet，完成起飞、并行航线和统一返航流程。"""
    fleet = build_fleet()
    selected_routes = select_routes()

    try:
        with FleetDemoSubscriptions(fleet):
            connect_result = fleet.connect()
            ensure_connect_ok(connect_result)
            setup_ok = prepare_and_takeoff_fleet(fleet)
        print("[Fleet 准备/起飞结果]")
        if not setup_ok:
            print("任务状态：失败")
            print(f"任务消息：起飞高度未在 {TAKEOFF_CONFIRM_TIMEOUT_S:.0f} 秒内全部达到目标。")
            print("起飞阶段失败设备：全部或部分无人机")
            raise SystemExit(1)
        print("任务状态：成功")
        print("任务消息：全部无人机已起飞并达到目标高度。")
        if is_takeoff_only():
            print("[Fleet takeoff-only] 已完成三机起飞确认，跳过航线执行。")
            return

        route_plans = build_route_plans_for_fleet(fleet, selected_routes)
        route_result = Plan.parallel(*route_plans).run()
        print("[Fleet 并行航线结果]")
        print(route_result)

        rtl_plan = Plan(fleet, interceptors=[print_task_step]).for_drones()
        rtl_result = configure_drone_plan(rtl_plan).return_to_launch().run()
        print("[Fleet 返航结果]")
        print_task_result(rtl_result)
    finally:
        fleet.events.drain(timeout_s=5.0)
        fleet.close()


if __name__ == "__main__":
    main()
