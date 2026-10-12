"""控制带光流定位的 PX4 真机分两段前进两米、返回起点并降落。"""

import math
import os
import time
from threading import Event, Thread
from typing import Any

from fleetdock import (
    DeviceFactory,
    MotionExecutionMode,
    Plan,
    Route,
    RouteContinuity,
    RouteMotion,
    Waypoint,
    WaypointReachedEvent,
    WaypointSettledEvent,
    WaypointStartedEvent,
)


TAKEOFF_ALTITUDE_M = 1.0
CLIMB_SPEED_M_S = 0.25
ALTITUDE_TOLERANCE_M = 0.15
HOLD_DURATION_S = 5.0
MOVE_DISTANCE_M = 1.0
ROUTE_SPEED_M_S = 0.25
WAYPOINT_TOLERANCE_M = 0.15
ROUTE_ALTITUDE_TOLERANCE_M = 0.5
FAR_POINT_HOLD_DURATION_S = 1.0
SETTLE_TIME_S = 0.5
SETTLE_VELOCITY_M_S = 0.15
ROUTE_DIAGNOSTIC_INTERVAL_S = 1.0


def _format_value(value: Any, *, digits: int = 3) -> str:
    """把可空数值格式化为适合真机排障的一行文本。"""
    if value is None:
        return "未知"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _data_age_s(updated_at_s: float | None, now_s: float) -> float | None:
    """计算单调时钟遥测时间戳距当前的秒数。"""
    if updated_at_s is None:
        return None
    return max(0.0, now_s - updated_at_s)


def _task_status_text(value: Any) -> str:
    """返回任务状态或结束原因的稳定文本值。"""
    return str(getattr(value, "value", value))


def _print_task_result(label: str, result: Any) -> None:
    """用紧凑、可读的格式输出任务及各步骤结果。"""
    print(
        f"[{label}] ok={result.ok} status={_task_status_text(result.status)} "
        f"failed_step={result.failed_step or '-'} "
        f"completion_reason={_task_status_text(result.completion_reason) if result.completion_reason else '-'} "
        f"message={result.message}",
        flush=True,
    )
    for index, step in enumerate(result.steps, start=1):
        duration_s = max(0.0, step.finished_at_s - step.started_at_s)
        response_code = "-"
        if step.response is not None:
            response_code = _task_status_text(step.response.code)
        print(
            f"  步骤{index}: name={step.name} ok={step.ok} duration={duration_s:.2f}s "
            f"response_code={response_code} message={step.message or '-'}",
            flush=True,
        )


def _print_waypoint_event(event: Any) -> None:
    """打印航点开始、进入容差和稳定完成三个关键阶段。"""
    phase = {
        WaypointStartedEvent: "started",
        WaypointReachedEvent: "reached",
        WaypointSettledEvent: "settled",
    }.get(type(event), type(event).__name__)
    progress = event.progress
    print(
        f"[航点事件] phase={phase} index={progress.waypoint_index}/"
        f"{progress.waypoint_count - 1} "
        f"route_xy=({_format_value(progress.x_m)},{_format_value(progress.y_m)})m "
        f"reported_distance={_format_value(progress.distance_m)}m",
        flush=True,
    )


def _route_targets(
    origin_x_m: float | None,
    origin_y_m: float | None,
    first_forward_x_m: float,
    first_forward_y_m: float,
    second_forward_x_m: float,
    second_forward_y_m: float,
) -> tuple[tuple[str, float, float], ...]:
    """按日志采样时的本地原点估算三个绝对航点坐标。"""
    if origin_x_m is None or origin_y_m is None:
        return ()
    return (
        ("前进1米", origin_x_m + first_forward_x_m, origin_y_m + first_forward_y_m),
        ("前进2米", origin_x_m + second_forward_x_m, origin_y_m + second_forward_y_m),
        ("返回起点", origin_x_m, origin_y_m),
    )


def _print_route_diagnostic(
    drone: Any,
    *,
    elapsed_s: float,
    targets: tuple[tuple[str, float, float], ...],
    final: bool = False,
) -> None:
    """输出一次只读航线诊断，不向飞控发送任何控制命令。"""
    now_s = time.monotonic()
    state = drone.state.snapshot()
    diagnostic = drone.diagnostics()

    velocity_values = (state.velocity_x_m_s, state.velocity_y_m_s, state.velocity_z_m_s)
    speed_m_s = None
    if all(value is not None for value in velocity_values):
        speed_m_s = math.sqrt(sum(float(value) ** 2 for value in velocity_values))

    distances: list[tuple[str, float]] = []
    if state.local_x_m is not None and state.local_y_m is not None:
        distances = [
            (name, math.hypot(state.local_x_m - target_x_m, state.local_y_m - target_y_m))
            for name, target_x_m, target_y_m in targets
        ]
    nearest_text = "未知"
    if distances:
        nearest_name, nearest_distance_m = min(distances, key=lambda item: item[1])
        nearest_text = (
            f"{nearest_name}/{nearest_distance_m:.3f}m/"
            f"容差内={nearest_distance_m <= WAYPOINT_TOLERANCE_M}"
        )
    distances_text = ",".join(f"{name}:{distance_m:.3f}m" for name, distance_m in distances)

    position_age_s = _data_age_s(state.local_position_updated_at_s, now_s)
    event_age_s = _data_age_s(state.last_event_updated_at_s, now_s)
    speed_ok = speed_m_s is not None and speed_m_s <= SETTLE_VELOCITY_M_S
    altitude_error_m = None
    if state.relative_altitude_m is not None:
        altitude_error_m = abs(state.relative_altitude_m - TAKEOFF_ALTITUDE_M)
    altitude_ok = altitude_error_m is not None and altitude_error_m <= ROUTE_ALTITUDE_TOLERANCE_M
    prefix = "航线最终诊断" if final else "航线诊断"
    print(
        f"[{prefix} +{elapsed_s:05.1f}s] "
        f"local_ned=({_format_value(state.local_x_m)},"
        f"{_format_value(state.local_y_m)},{_format_value(state.local_z_m)})m "
        f"velocity=({_format_value(state.velocity_x_m_s)},"
        f"{_format_value(state.velocity_y_m_s)},{_format_value(state.velocity_z_m_s)})m/s "
        f"speed={_format_value(speed_m_s)}m/s speed_ok={speed_ok} "
        f"alt_error={_format_value(altitude_error_m)}m alt_ok={altitude_ok} "
        f"nearest={nearest_text} distances=[{distances_text or '未知'}] "
        f"pos_age={_format_value(position_age_s)}s event_age={_format_value(event_age_s)}s "
        f"in_air={state.in_air} mode={state.flight_mode or '未知'} failsafe={state.failsafe} "
        f"callback_pending={diagnostic.telemetry_callback_pending} "
        f"callback_dropped={diagnostic.telemetry_dropped_notifications} "
        f"event_pending={diagnostic.event_pending_events} "
        f"event_dropped={diagnostic.event_dropped_events}",
        flush=True,
    )
    if final:
        distance_text = ", ".join(f"{name}={distance_m:.3f}m" for name, distance_m in distances)
        print(
            "  最终状态: "
            f"armed={state.armed} landed_state={state.landed_state or '未知'} "
            f"relative_altitude={_format_value(state.relative_altitude_m)}m "
            f"yaw={_format_value(state.yaw_deg, digits=1)}deg "
            f"各目标距离=[{distance_text or '未知'}]",
            flush=True,
        )
        print(
            "  队列详情: "
            f"requested_streams={diagnostic.telemetry_requested_streams or ()} "
            f"event_pending={diagnostic.event_pending_events}/"
            f"{diagnostic.event_queue_capacity} "
            f"event_high_watermark={diagnostic.event_queue_high_watermark} "
            f"event_dropped={diagnostic.event_dropped_events} "
            f"callback_pending={diagnostic.telemetry_callback_pending} "
            f"callback_dropped={diagnostic.telemetry_dropped_notifications}",
            flush=True,
        )


def _monitor_route(
    drone: Any,
    targets: tuple[tuple[str, float, float], ...],
    stop_event: Event,
) -> None:
    """在航线执行期间每秒采集一次公共状态和诊断快照。"""
    started_at_s = time.monotonic()
    while not stop_event.wait(ROUTE_DIAGNOSTIC_INTERVAL_S):
        try:
            _print_route_diagnostic(
                drone,
                elapsed_s=time.monotonic() - started_at_s,
                targets=targets,
            )
        except Exception as exc:  # noqa: BLE001 - 诊断线程不能影响真机控制主流程。
            print(f"[航线诊断] 采样失败：{type(exc).__name__}: {exc}", flush=True)
            return


def main() -> None:
    """确认真机安全条件后执行起飞、本地航线往返、悬停和降落。"""
    endpoint = os.getenv("FLEETDOCK_PX4_ENDPOINT", "serial://COM3:57600")
    if input("确认场地净空且遥控器可接管后输入 FLY：").strip() != "FLY":
        print("已取消真机飞行。")
        return

    drone = DeviceFactory.create().px4_generic().params(endpoint=endpoint, timeout_s=30.0).build()
    landing_completed = False
    try:
        connect_result = drone.connect()
        print(
            f"[连接] ok={connect_result.ok} code={_task_status_text(connect_result.code)} "
            f"message={connect_result.message}",
            flush=True,
        )
        if not connect_result.ok:
            return

        takeoff_result = (
            Plan(drone)
            .climb_speed(CLIMB_SPEED_M_S)
            .altitude_tolerance(ALTITUDE_TOLERANCE_M)
            .wait_until_ready(timeout_s=60.0)
            .takeoff(altitude=TAKEOFF_ALTITUDE_M)
            .run(step_timeout_s=60.0)
        )
        _print_task_result("起飞", takeoff_result)
        if not takeoff_result.ok:
            return

        yaw_deg = drone.state.snapshot().yaw_deg
        if yaw_deg is None:
            print("未取得机头朝向，无法安全生成向前航线。")
            return

        yaw_rad = math.radians(yaw_deg)
        first_forward_x_m = MOVE_DISTANCE_M * math.cos(yaw_rad)
        first_forward_y_m = MOVE_DISTANCE_M * math.sin(yaw_rad)
        second_forward_x_m = 2.0 * MOVE_DISTANCE_M * math.cos(yaw_rad)
        second_forward_y_m = 2.0 * MOVE_DISTANCE_M * math.sin(yaw_rad)
        route = Route(
            waypoints=(
                Waypoint(x_m=0.0, y_m=0.0),
                Waypoint(
                    x_m=first_forward_x_m,
                    y_m=first_forward_y_m,
                    acceptance_radius_m=WAYPOINT_TOLERANCE_M,
                ),
                Waypoint(
                    x_m=second_forward_x_m,
                    y_m=second_forward_y_m,
                    acceptance_radius_m=WAYPOINT_TOLERANCE_M,
                    hold_time_s=FAR_POINT_HOLD_DURATION_S,
                ),
                Waypoint(
                    x_m=0.0,
                    y_m=0.0,
                    acceptance_radius_m=WAYPOINT_TOLERANCE_M,
                    hold_time_s=HOLD_DURATION_S,
                ),
            ),
            name="indoor-forward-backward",
            continuity=RouteContinuity.STOP_ALLOWED,
        )

        origin_state = drone.state.snapshot()
        targets = _route_targets(
            origin_state.local_x_m,
            origin_state.local_y_m,
            first_forward_x_m,
            first_forward_y_m,
            second_forward_x_m,
            second_forward_y_m,
        )
        target_text = ", ".join(
            f"{name}=({target_x_m:.3f},{target_y_m:.3f})m"
            for name, target_x_m, target_y_m in targets
        )
        print(
            f"执行本地航线：向机头方向前进 {MOVE_DISTANCE_M:.0f} 米，"
            f"再前进 {MOVE_DISTANCE_M:.0f} 米，然后原路返回起点。",
            flush=True,
        )
        print(
            "[航线基准] "
            f"frame={origin_state.local_position_frame.value} "
            f"origin=({_format_value(origin_state.local_x_m)},"
            f"{_format_value(origin_state.local_y_m)})m yaw={yaw_deg:.1f}deg "
            f"targets=[{target_text or '本地位置未知'}]",
            flush=True,
        )
        print(
            "[航线判定] "
            f"位置容差<={WAYPOINT_TOLERANCE_M:.2f}m，"
            f"高度误差<={ROUTE_ALTITUDE_TOLERANCE_M:.2f}m，"
            f"速度<={SETTLE_VELOCITY_M_S:.2f}m/s 且持续 {SETTLE_TIME_S:.1f}s；"
            "日志中的目标坐标按航线启动前快照估算，仅用于排障。",
            flush=True,
        )

        route_started_at_s = time.monotonic()
        monitor_stop = Event()
        monitor_thread = Thread(
            target=_monitor_route,
            args=(drone, targets, monitor_stop),
            name="fleetdock-demo-route-diagnostics",
            daemon=True,
        )
        unsubscribe_waypoints = drone.events.subscribe(
            (WaypointStartedEvent, WaypointReachedEvent, WaypointSettledEvent),
            _print_waypoint_event,
        )
        monitor_thread.start()
        try:
            route_result = (
                Plan(drone)
                .altitude_tolerance(ROUTE_ALTITUDE_TOLERANCE_M)
                .route(
                    route,
                    altitude=TAKEOFF_ALTITUDE_M,
                    speed=ROUTE_SPEED_M_S,
                    motion=RouteMotion(
                        mode=MotionExecutionMode.SDK,
                        acceleration_m_s2=0.3,
                        deceleration_m_s2=0.4,
                        lookahead_time_s=0.8,
                        min_lookahead_m=0.2,
                        settle_time_s=SETTLE_TIME_S,
                        settle_velocity_m_s=SETTLE_VELOCITY_M_S,
                    ),
                    timeout_s=60.0,
                )
                .run(step_timeout_s=70.0)
            )
        finally:
            monitor_stop.set()
            monitor_thread.join(timeout=2.0)
            unsubscribe_waypoints()
            try:
                _print_route_diagnostic(
                    drone,
                    elapsed_s=time.monotonic() - route_started_at_s,
                    targets=targets,
                    final=True,
                )
            except Exception as exc:  # noqa: BLE001 - 最终诊断也不能覆盖航线原始结果。
                print(f"[航线最终诊断] 采样失败：{type(exc).__name__}: {exc}", flush=True)

        _print_task_result("前后往返并悬停", route_result)
        if not route_result.ok:
            print("[任务终止] 航线失败，未进入正常降落步骤；接下来执行 finally 安全收尾判定。", flush=True)
            return

        print("[正常降落] 航线成功，开始发送 Land。", flush=True)
        land_result = Plan(drone).land().run(step_timeout_s=60.0)
        _print_task_result("降落", land_result)
        landing_completed = land_result.ok
    finally:
        try:
            cleanup_state = drone.state.snapshot()
            should_cleanup_land = not landing_completed and cleanup_state.in_air is True
            print(
                "[安全收尾判定] "
                f"landing_completed={landing_completed} in_air={cleanup_state.in_air} "
                f"armed={cleanup_state.armed} landed_state={cleanup_state.landed_state or '未知'} "
                f"send_land={should_cleanup_land}",
                flush=True,
            )
            if should_cleanup_land:
                print("[安全收尾] 任务未正常降落且 in_air=True，开始发送 Land。", flush=True)
                cleanup_result = Plan(drone).land().run(step_timeout_s=60.0)
                _print_task_result("异常清理降落", cleanup_result)
            else:
                print("[安全收尾] 未发送 Land，原因见上一行判定字段。", flush=True)
        finally:
            print("[资源关闭] 开始释放设备连接和遥测资源。", flush=True)
            drone.close()
            print("[资源关闭] 已完成。", flush=True)


if __name__ == "__main__":
    main()
