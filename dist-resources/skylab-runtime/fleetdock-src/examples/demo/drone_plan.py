"""PX4 单机计划与标准航线示例；集中展示 Plan、Route、Hook 和失败处理。"""

from __future__ import annotations

from enum import Enum
import sys

from fleetdock import (
    DeviceEvent,
    DeviceFactory,
    DeviceMetadata,
    Drone,
    EventType,
    HeadingMode,
    MotionExecutionMode,
    Plan,
    Route,
    RouteMotion,
    RouteCompletedEvent,
    RouteStartedEvent,
    TaskStatus,
    Waypoint,
    WaypointActionRequestedEvent,
    WaypointAction,
    WaypointSettledEvent,
)


MIN_ROUTE_ALTITUDE_M = 25.0
TAKEOFF_ALTITUDE_M = MIN_ROUTE_ALTITUDE_M
TASK_SPEED_M_S = 3.5
TASK_CLIMB_SPEED_M_S = 1.0
TASK_ACCELERATION_M_S2 = 0.6
TASK_DECELERATION_M_S2 = 0.8
TASK_LOOKAHEAD_TIME_S = 1.2
TASK_MIN_LOOKAHEAD_M = 2.0
TASK_SETTLE_TIME_S = 0.6
TASK_SETTLE_VELOCITY_M_S = 0.25
TASK_ROUTE_MOTION = RouteMotion(
    mode=MotionExecutionMode.AUTO,
    acceleration_m_s2=TASK_ACCELERATION_M_S2,
    deceleration_m_s2=TASK_DECELERATION_M_S2,
    lookahead_time_s=TASK_LOOKAHEAD_TIME_S,
    min_lookahead_m=TASK_MIN_LOOKAHEAD_M,
    settle_time_s=TASK_SETTLE_TIME_S,
    settle_velocity_m_s=TASK_SETTLE_VELOCITY_M_S,
)
PHOTO_ACCEPTANCE_RADIUS_M = 1.5
ALL_ROUTES_NAME = "all"
DEFAULT_ROUTE_NAME = ALL_ROUTES_NAME
VERBOSE_DEVICE_EVENTS = False

STATUS_TEXT = {
    TaskStatus.SUCCESS: "成功",
    TaskStatus.FAILED: "失败",
    TaskStatus.GUARD_TRIGGERED: "守卫条件触发",
    TaskStatus.TIMEOUT: "超时",
}


class DemoRoute(str, Enum):
    """演示脚本支持的航线类型，命令行参数应使用对应枚举值。"""

    POINT_TO_POINT = "point_to_point"
    POLYLINE = "polyline"
    POLYGON_PHOTO = "polygon_photo"
    CIRCLE_ORBIT = "circle_orbit"
    ELLIPSE_ORBIT = "ellipse_orbit"
    ARC_SCAN = "arc_scan"
    GRID_COVERAGE = "grid_coverage"
    SURVEY_AREA = "survey_area"
    SPIRAL_SEARCH = "spiral_search"
    CORRIDOR = "corridor"


def print_event(event: DeviceEvent) -> None:
    """打印计划执行期间收到的标准设备事件。"""
    event_type = getattr(event.type, "value", str(event.type))
    payload = event.to_dict()["data"]
    print(f"[设备事件] 序号={event.sequence} 类别={event.category.value} 类型={event_type} 数据={payload}")


def print_route_started(event: RouteStartedEvent) -> None:
    """打印航线名称、类型和航点数量。"""
    progress = event.progress
    route_name = progress.route_name or progress.pattern
    print(
        f"[航线开始] {route_name} 航点数={progress.waypoint_count} 类型={progress.pattern}",
        flush=True,
    )


def print_route_completed(event: RouteCompletedEvent) -> None:
    """打印航线完成事件和最终完成原因。"""
    progress = event.progress
    route_name = progress.route_name or progress.pattern
    print(f"[航线完成] {route_name}", flush=True)


def capture_photo_on_waypoint(event: WaypointSettledEvent | WaypointActionRequestedEvent) -> None:
    """在航点稳定或动作请求事件中模拟拍照，不直接调用厂商相机 SDK。"""
    try:
        progress = event.progress
        if progress.action != WaypointAction.PHOTO.value:
            return
        print(
            "[拍照触发] "
            f"航线={progress.route_name} 航点={progress.waypoint_index}/{progress.waypoint_count - 1} "
            f"位置=({progress.x_m}, {progress.y_m}, {progress.z_m}) 元数据={dict(progress.metadata)}",
            flush=True,
        )
    except Exception as exc:
        print(f"[拍照触发异常] {type(exc).__name__}: {exc}", flush=True)


def print_task_result(result) -> None:
    """以适合新手阅读的格式打印任务总结果和每一步响应。"""
    status = STATUS_TEXT.get(result.status, str(result.status))
    print(f"任务状态：{status}")
    print(f"任务消息：{result.message}")
    for index, step in enumerate(result.steps, start=1):
        step_status = "成功" if step.ok else "失败"
        print(f"步骤 {index}：{step.name}，结果={step_status}，消息={step.message}")


def _value_text(value) -> str:
    return getattr(value, "value", str(value))


def print_route_parameters(
    route: Route,
    *,
    label: str = "航线参数",
    altitude_m: float | None = None,
    speed_m_s: float | None = None,
    motion_mode=None,
    heading=None,
    lookahead_time_s: float | None = None,
    min_lookahead_m: float | None = None,
) -> None:
    """打印 demo 本次实际使用的航线和平滑执行参数。"""
    geometry = route.geometry.kind if route.geometry is not None else "-"
    geometry_params = dict(route.geometry.params) if route.geometry is not None else {}
    acceptance_values = [
        waypoint.acceptance_radius_m
        for waypoint in route.waypoints
        if waypoint.acceptance_radius_m is not None
    ]
    acceptance_text = (
        f"{min(acceptance_values):.1f}-{max(acceptance_values):.1f} m"
        if acceptance_values and min(acceptance_values) != max(acceptance_values)
        else (f"{acceptance_values[0]:.1f} m" if acceptance_values else "默认容差")
    )
    print(
        f"[{label}] name={route.name or '-'} pattern={_value_text(route.pattern)} "
        f"continuity={_value_text(route.continuity)} geometry={geometry} waypoints={len(route.waypoints)} "
        f"acceptance={acceptance_text}",
        flush=True,
    )
    if geometry_params:
        params_text = ", ".join(f"{key}={value}" for key, value in geometry_params.items())
        print(f"[{label}] geometry_params: {params_text}", flush=True)
    print(
        f"[{label}] altitude={altitude_m if altitude_m is not None else '默认'} m "
        f"speed={speed_m_s if speed_m_s is not None else '默认'} m/s "
        f"motion={_value_text(motion_mode) if motion_mode is not None else '默认'} "
        f"heading={_value_text(heading) if heading is not None else '默认'} "
        f"lookahead_time={lookahead_time_s if lookahead_time_s is not None else '默认'} s "
        f"min_lookahead={min_lookahead_m if min_lookahead_m is not None else '默认'} m",
        flush=True,
    )


def is_low_battery(ctx) -> bool:
    """返回当前任务设备的剩余电量是否低于 25%。"""
    return (
        ctx.state.battery_remaining_fraction is not None
        and ctx.state.battery_remaining_fraction < 0.25
    )


def return_to_launch(ctx) -> None:
    """作为任务守卫动作请求当前无人机返航。"""
    ctx.device.return_to_launch()


def handle_task_failed(ctx, result) -> None:
    """任务失败时停止当前运动并请求返航。"""
    print(f"任务失败处理：步骤={result.failed_step}，原因={result.message}")
    ctx.device.stop()
    ctx.device.return_to_launch()


def build_common_routes() -> dict[DemoRoute, Route]:
    """生成示例使用的标准航线集合并按演示枚举返回。"""
    def photo_waypoint(x_m: float, y_m: float, shot: str) -> Waypoint:
        """创建带拍照动作和镜头标签的本地米制航点。"""
        return Waypoint(
            x_m=x_m,
            y_m=y_m,
            action=WaypointAction.PHOTO,
            acceptance_radius_m=PHOTO_ACCEPTANCE_RADIUS_M,
            metadata={"shot": shot},
        )

    return {
        DemoRoute.POINT_TO_POINT: Route.polyline(
            (
                Waypoint(x_m=0.0, y_m=0.0),
                photo_waypoint(45.0, 0.0, "point_to_point"),
            ),
            name="point_to_point",
        ),
        DemoRoute.POLYLINE: Route.polyline(
            (
                Waypoint(x_m=0.0, y_m=0.0),
                Waypoint(x_m=30.0, y_m=0.0),
                photo_waypoint(45.0, 20.0, "polyline_corner"),
                Waypoint(x_m=18.0, y_m=42.0),
            ),
            name="polyline",
        ),
        DemoRoute.POLYGON_PHOTO: Route.polygon(
            (
                Waypoint(x_m=0.0, y_m=0.0),
                photo_waypoint(42.0, 0.0, "east_edge"),
                photo_waypoint(42.0, 36.0, "north_edge"),
                photo_waypoint(0.0, 36.0, "west_edge"),
            ),
            name="polygon_photo",
        ),
        DemoRoute.CIRCLE_ORBIT: Route.circle(radius_m=28.0, segments=24, name="circle_orbit"),
        DemoRoute.ELLIPSE_ORBIT: Route.ellipse(radius_x_m=42.0, radius_y_m=20.0, segments=28, name="ellipse_orbit"),
        DemoRoute.ARC_SCAN: Route.arc(radius_m=36.0, start_deg=20.0, end_deg=160.0, segments=16, name="arc_scan"),
        DemoRoute.GRID_COVERAGE: Route.grid(
            width_m=60.0,
            height_m=36.0,
            spacing_m=12.0,
            action=WaypointAction.PHOTO,
            acceptance_radius_m=PHOTO_ACCEPTANCE_RADIUS_M,
            name="grid_coverage",
        ),
        DemoRoute.SURVEY_AREA: Route.survey_area(
            (
                Waypoint(x_m=0.0, y_m=0.0),
                Waypoint(x_m=60.0, y_m=0.0),
                Waypoint(x_m=56.0, y_m=38.0),
                Waypoint(x_m=8.0, y_m=44.0),
            ),
            spacing_m=12.0,
            angle_deg=15.0,
            action=WaypointAction.PHOTO,
            acceptance_radius_m=PHOTO_ACCEPTANCE_RADIUS_M,
            name="survey_area",
        ),
        DemoRoute.SPIRAL_SEARCH: Route.spiral(max_radius_m=35.0, spacing_m=7.0, turns=2.5, name="spiral_search"),
        DemoRoute.CORRIDOR: Route.corridor(
            (
                Waypoint(x_m=0.0, y_m=0.0),
                Waypoint(x_m=35.0, y_m=8.0),
                Waypoint(x_m=70.0, y_m=24.0),
            ),
            width_m=12.0,
            name="corridor",
        ),
    }


def select_routes() -> list[Route]:
    """按命令行选择一条或全部航线，并返回待执行路线列表。"""
    routes = build_common_routes()
    route_name = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ROUTE_NAME
    if route_name == ALL_ROUTES_NAME:
        selected_routes = list(routes.values())
        print(f"本次执行全部航线：{', '.join(route.name or route.pattern.value for route in selected_routes)}")
        return selected_routes
    try:
        selected = DemoRoute(route_name)
    except ValueError as exc:
        names = ", ".join([ALL_ROUTES_NAME] + [route.value for route in DemoRoute])
        raise ValueError(f"未知航线：{route_name}，可选航线：{names}") from exc
    print(f"本次执行航线：{route_name}")
    return [routes[selected]]


def main() -> None:
    """连接 PX4，执行就绪检查、航线计划和返航，并释放订阅与设备。"""
    drone: Drone = (
        DeviceFactory.create()
        .px4_generic()
        .params(endpoint="udpin://0.0.0.0:14555", timeout_s=30.0)
        .metadata(DeviceMetadata(device_id="drone-001", name="任务编排演示无人机"))
        .build()
    )
    selected_routes: list[Route] = select_routes()
    for route in selected_routes:
        print_route_parameters(
            route,
            altitude_m=MIN_ROUTE_ALTITUDE_M,
            speed_m_s=TASK_SPEED_M_S,
            motion_mode=MotionExecutionMode.AUTO,
            heading=HeadingMode.TANGENT,
            lookahead_time_s=TASK_LOOKAHEAD_TIME_S,
            min_lookahead_m=TASK_MIN_LOOKAHEAD_M,
        )
    unsubscribe_verbose = drone.events.subscribe(print_event) if VERBOSE_DEVICE_EVENTS else lambda: None
    unsubscribe_route_started = drone.events.on(EventType.ROUTE_STARTED, print_route_started)
    unsubscribe_route_completed = drone.events.on(EventType.ROUTE_COMPLETED, print_route_completed)
    unsubscribe_photo = drone.events.on(
        EventType.WAYPOINT_ACTION_REQUESTED,
        capture_photo_on_waypoint,
    )

    try:
        connect_response = drone.connect()
        if not connect_response.ok:
            print(f"设备连接失败：{connect_response.message}")
            return
        result = (
            Plan(drone)
            .speed(TASK_SPEED_M_S)
            .climb_speed(TASK_CLIMB_SPEED_M_S)
            .altitude(MIN_ROUTE_ALTITUDE_M)
            .tolerance(0.5)
            .timeout(180.0)
            .heading(HeadingMode.TANGENT)
            .on_failed(handle_task_failed)
            # 室内真机：等待光流、VIO、动捕等提供健康的本地位置。
            # 室外真机：等待健康的全球位置、相对高度和 Home。
            # 仿真：按实际收到的本地或全球定位遥测判断。
            # .wait_until_ready(timeout_s=60.0)
            .unlock()
            .takeoff(altitude=TAKEOFF_ALTITUDE_M)
            .when(
                is_low_battery,
                then=return_to_launch,
                reason="电量低，任务中断并返航",
            )
            .routes(selected_routes, motion=TASK_ROUTE_MOTION)
            .return_to_launch()
            .run()
        )
        print_task_result(result)
        if result.ok:
            print("任务执行完成")
        else:
            print(f"任务执行失败：{result.message}")
    finally:
        drone.events.drain(timeout_s=5.0)
        unsubscribe_photo()
        unsubscribe_route_completed()
        unsubscribe_route_started()
        unsubscribe_verbose()
        drone.close()


if __name__ == "__main__":
    main()
