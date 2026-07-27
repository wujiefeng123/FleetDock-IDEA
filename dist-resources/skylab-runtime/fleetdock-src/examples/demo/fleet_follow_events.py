"""Fleet 级事件订阅驱动的三机协同飞行示例。

本示例强调 `fleet.events.subscribe(...)` 的用法：A/B/C 三机先同时解锁并升高到 30 米；随后
B 机执行约 5 分钟螺旋巡航，C 机通过来源设备和事件类型过滤订阅 B 机飞行开始事件，并沿 B
的轨迹跟随；A 机代表带视觉/摄像头的观察机，使用 camera 跟随策略停在 B 的侧后方并持续朝向 B。
B 在 C、A 跟随期间持续螺旋飞行，直到螺旋巡航结束后才触发三机返航。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, Lock, Thread
import time
from typing import Any, Callable

from fleetdock import DeviceEvent, DeviceFactory, DeviceMetadata, Drone, EventType, Fleet, FollowExecutionOptions, FollowPolicy, FollowStopAction, Plan, Route, RouteMotion, UnlockStrategy
from drone_plan import print_route_parameters


DEFAULT_DRONES = (
    ("drone-a", "A-视觉观察机", "udpin://0.0.0.0:14555"),
    ("drone-b", "B-先导机", "udpin://0.0.0.0:14556"),
    ("drone-c", "C-跟随机", "udpin://0.0.0.0:14557"),
)

TAKEOFF_ALTITUDE_M = 30.0
FLIGHT_SPEED_M_S = 1.0
B_SPIRAL_DURATION_S = 300.0
C_FOLLOW_DELAY_S = 8.0
B_SPIRAL_MAX_RADIUS_M = 28.0
B_SPIRAL_SPACING_M = 2.0
B_SPIRAL_TURNS = 6.0
ALTITUDE_TIMEOUT_S = 75.0
TELEMETRY_LOG_INTERVAL_S = 1.0
FOLLOW_OPTIONS = FollowExecutionOptions(
    interval_s=0.2,
    default_max_speed_m_s=4.0,
    max_follow_distance_m=120.0,
    min_separation_m=6.0,
    stop_action=FollowStopAction.NONE,
)

EVENT_B_FLIGHT_STARTED = "custom.fleet_events.b.flight_started"
EVENT_B_SPIRAL_FINISHED = "custom.fleet_events.b.spiral_finished"
EVENT_C_TAKEOFF_STARTED = "custom.fleet_events.c.takeoff_started"
EVENT_C_NEAR_B = "custom.fleet_events.c.near_b"
EVENT_B_RTL_STARTED = "custom.fleet_events.b.rtl_started"
_last_telemetry_log_at: dict[tuple[str, str], float] = {}


@dataclass
class DemoRuntime:
    """保存示例运行期间需要共享的少量状态。"""

    fleet: Fleet
    drone_a: Drone
    drone_b: Drone
    drone_c: Drone
    finished: Event = field(default_factory=Event)
    return_started: Event = field(default_factory=Event)
    started_flags: set[str] = field(default_factory=set)
    flag_lock: Lock = field(default_factory=Lock)
    unsubscribes: list[Callable[[], None]] = field(default_factory=list)
    follow_session: Any = None


def build_demo_drone(device_id: str, name: str, endpoint: str) -> Drone:
    """使用 DeviceFactory 创建一架 PX4 示例无人机。"""
    return (
        DeviceFactory.create()
        .px4_generic()
        .params(endpoint=endpoint, timeout_s=30.0)
        .metadata(DeviceMetadata(device_id=device_id, name=name, tags=("fleet-events-demo",)))
        .build()
    )


def build_fleet() -> Fleet:
    """创建与 `examples/run_dev_multi.bat` 默认端口对应的 A/B/C 三机 Fleet。"""
    return Fleet(
        [build_demo_drone(device_id, name, endpoint) for device_id, name, endpoint in DEFAULT_DRONES],
        name="Fleet 事件订阅三机协同",
    )


def run_or_raise(label: str, response) -> None:
    """打印标准命令响应；失败时抛出异常，让示例进入返航/释放流程。"""
    ok = bool(getattr(response, "ok", False))
    code = getattr(getattr(response, "code", None), "value", "-")
    message = getattr(response, "message", "")
    print(f"[{label}] ok={ok} code={code} message={message}", flush=True)
    if not ok:
        raise RuntimeError(f"{label} failed: {message}")


def wait_until_event(event: Event, *, label: str) -> None:
    """持续等待事件触发，用于让后台阶段保持存活并打印清晰日志。"""
    print(label, flush=True)
    while not event.wait(timeout=5.0):
        print(label, flush=True)


def fly_spiral_route_for(drone: Drone, *, duration_s: float, altitude_m: float) -> None:
    """通过标准计划 API 执行螺旋航线，达到最长巡航秒数后正常停止。"""
    device_id = drone.metadata.device_id or drone.instance_id
    spiral_route = Route.spiral(
        max_radius_m=B_SPIRAL_MAX_RADIUS_M,
        spacing_m=B_SPIRAL_SPACING_M,
        turns=B_SPIRAL_TURNS,
        acceptance_radius_m=0.8,
        name="fleet_events_b_spiral",
    )
    print_route_parameters(
        spiral_route,
        label=f"{device_id} B 螺旋",
        altitude_m=altitude_m,
        speed_m_s=FLIGHT_SPEED_M_S,
        motion_mode="auto",
    )
    print(
        f"[{device_id}] B 升高完成，开始 SDK 本地螺旋飞行，"
        f"航点数={len(spiral_route.waypoints)}，目标持续约 {duration_s:.0f} 秒。",
        flush=True,
    )
    session = (
        Plan(drone)
        .route(
            spiral_route,
            altitude=altitude_m,
            speed=FLIGHT_SPEED_M_S,
            motion=RouteMotion(mode="auto"),
            max_duration_s=duration_s,
            timeout_s=duration_s + 30.0,
        )
        .start()
    )
    result = session.wait(timeout_s=duration_s + 35.0)
    run_or_raise(f"{device_id} spiral route", result)
    print(f"[{device_id}] B 螺旋飞行时间结束，直接进入返航触发流程。", flush=True)


def emit_stage(drone: Drone, event_name: str, **payload) -> None:
    """向单设备事件流发布演示用自定义事件，由 Fleet 事件总线聚合后触发下一阶段。"""
    device_id = drone.metadata.device_id or drone.instance_id
    drone.events.publish_custom(event_name, {"device_id": device_id, **payload})


def start_once(name: str, flags: set[str], lock: Lock, target: Callable[[], None]) -> None:
    """确保同一阶段只启动一次后台线程。"""
    with lock:
        if name in flags:
            return
        flags.add(name)
    Thread(target=target, name=f"fleet-events-{name}", daemon=True).start()


def run_b_spiral_mission(runtime: DemoRuntime) -> None:
    """B 发布飞行开始事件，持续螺旋飞行，结束后发布螺旋完成事件。"""
    try:
        emit_stage(runtime.drone_b, EVENT_B_FLIGHT_STARTED, duration_s=B_SPIRAL_DURATION_S)
        fly_spiral_route_for(
            runtime.drone_b,
            duration_s=B_SPIRAL_DURATION_S,
            altitude_m=TAKEOFF_ALTITUDE_M,
        )
        emit_stage(runtime.drone_b, EVENT_B_SPIRAL_FINISHED)
    except Exception as exc:
        print(f"[B 阶段失败] {exc}", flush=True)
        runtime.finished.set()


def start_followers_after_b(runtime: DemoRuntime) -> None:
    """B 开始螺旋后，延迟启动 C 的轨迹跟随和 A 的视角跟随。"""
    try:
        print(
            f"[跟随订阅] B 已开始螺旋巡航，A/C 等待 {C_FOLLOW_DELAY_S:.0f} 秒后启动真实跟随会话。",
            flush=True,
        )
        print(
            "[跟随参数] "
            f"interval={FOLLOW_OPTIONS.interval_s} s "
            f"default_max_speed={FOLLOW_OPTIONS.default_max_speed_m_s} m/s "
            f"max_follow_distance={FOLLOW_OPTIONS.max_follow_distance_m} m "
            f"min_separation={FOLLOW_OPTIONS.min_separation_m} m "
            f"stop_action={FOLLOW_OPTIONS.stop_action.value}",
            flush=True,
        )
        time.sleep(C_FOLLOW_DELAY_S)
        runtime.follow_session = runtime.fleet.follow(
            leader=runtime.drone_b,
            follower_policies={
                runtime.drone_c: FollowPolicy.trail(delay_s=2.5, distance_m=10.0, max_speed_m_s=4.0),
                runtime.drone_a: FollowPolicy.camera(distance_m=18.0, side_m=8.0, max_speed_m_s=4.5),
            },
        )
        runtime.follow_session.start(FOLLOW_OPTIONS)
        emit_stage(runtime.drone_c, EVENT_C_TAKEOFF_STARTED)
        print("[跟随订阅] C 正沿 B 的螺旋轨迹跟随，A 作为视觉观察机从侧后方看向 B。", flush=True)
        emit_stage(runtime.drone_c, EVENT_C_NEAR_B, mode="trail_b", camera="a_camera_b")
        wait_until_event(runtime.return_started, label="[跟随订阅] B 仍在螺旋飞行，A/C 跟随会话继续运行。")
    except Exception as exc:
        print(f"[跟随阶段失败] {exc}", flush=True)
        runtime.finished.set()
    finally:
        if runtime.follow_session is not None:
            runtime.follow_session.stop()


def print_a_camera_started(runtime: DemoRuntime) -> None:
    """A 收到 C 阶段事件时，只打印视角跟随已由 FollowSession 接管。"""
    print("[A 订阅] C 已进入跟随阶段；A 的 camera 跟随已经由同一个 FollowSession 接管。", flush=True)


def start_b_return(runtime: DemoRuntime) -> None:
    """B 螺旋结束后触发返航，并通知 A/C 同步返航。"""
    try:
        print("[B 订阅] 螺旋巡航已经结束，触发三机返航。", flush=True)
        runtime.return_started.set()
        emit_stage(runtime.drone_b, EVENT_B_RTL_STARTED)
        run_or_raise("drone-b return_to_launch", runtime.drone_b.return_to_launch())
    except Exception as exc:
        print(f"[B 返航失败] {exc}", flush=True)
        runtime.finished.set()


def return_a_and_c(runtime: DemoRuntime) -> None:
    """A/C 收到 B 返航开始事件后同步返航。"""
    try:
        print("[A/C 订阅] B 已开始返航，A 和 C 也返航。", flush=True)
        run_or_raise("drone-a return_to_launch", runtime.drone_a.return_to_launch())
        run_or_raise("drone-c return_to_launch", runtime.drone_c.return_to_launch())
    except Exception as exc:
        print(f"[A/C 返航失败] {exc}", flush=True)
    finally:
        runtime.finished.set()


def start_runtime_thread(runtime: DemoRuntime, name: str, target: Callable[[], None]) -> None:
    """按阶段名只启动一次后台线程。"""
    start_once(name, runtime.started_flags, runtime.flag_lock, target)


def start_runtime_stage(runtime: DemoRuntime, name: str, stage: Callable[[DemoRuntime], None]) -> None:
    """按阶段名只启动一次接收 runtime 的后台阶段函数。"""
    start_runtime_thread(runtime, name, lambda: stage(runtime))


def event_stage_handler(runtime: DemoRuntime, name: str, stage: Callable[[DemoRuntime], None]) -> Callable[[DeviceEvent], None]:
    """把 Fleet 事件处理器包装成后台阶段启动器。"""
    return lambda event: start_runtime_stage(runtime, name, stage)


def subscribe_demo_events(runtime: DemoRuntime) -> None:
    """注册示例需要的 Fleet 事件订阅。"""
    fleet = runtime.fleet
    runtime.unsubscribes.append(fleet.events.subscribe(print_event_summary))
    runtime.unsubscribes.append(
        fleet.events.subscribe(
            EventType.BATTERY_UPDATED,
            print_battery_low,
            predicate=lambda event: event.battery.remaining_fraction <= 0.25,
        )
    )
    runtime.unsubscribes.append(
        fleet.events.subscribe(
            EVENT_B_FLIGHT_STARTED,
            event_stage_handler(runtime, "followers-after-b", start_followers_after_b),
            device=runtime.drone_b,
        )
    )
    runtime.unsubscribes.append(
        fleet.events.subscribe(
            EVENT_C_TAKEOFF_STARTED,
            event_stage_handler(runtime, "a-follow-c", print_a_camera_started),
            device=runtime.drone_c,
        )
    )
    runtime.unsubscribes.append(
        fleet.events.subscribe(
            event_stage_handler(runtime, "b-return", start_b_return),
            device=runtime.drone_b,
            event_name=EVENT_B_SPIRAL_FINISHED,
        )
    )
    runtime.unsubscribes.append(
        fleet.events.subscribe(
            event_stage_handler(runtime, "a-c-return", return_a_and_c),
            device=runtime.drone_b,
            event_name=EVENT_B_RTL_STARTED,
        )
    )


def prepare_fleet(runtime: DemoRuntime) -> None:
    """连接 Fleet，等待遥测就绪，并让 A/B/C 同时起飞到演示高度。"""
    print("[准备] 连接三架无人机并等待基础遥测。", flush=True)
    connect_result = runtime.fleet.connect()
    run_or_raise("fleet connect", connect_result)
    takeoff_result = (
        Plan(runtime.fleet)
        .for_drones()
        .climb_speed(1.0)
        .altitude_tolerance(1.5)
        .wait_until_ready(timeout_s=60.0, require_armable=False)
        .unlock(strategy=UnlockStrategy.STANDARD_THEN_FORCE)
        .takeoff(altitude=TAKEOFF_ALTITUDE_M)
        .run(step_timeout_s=ALTITUDE_TIMEOUT_S)
    )
    run_or_raise("fleet ready/unlock/takeoff", takeoff_result)


def start_demo(runtime: DemoRuntime) -> None:
    """启动 B 螺旋任务，并等待整段事件驱动流程结束。"""
    print("[启动] A/B/C 已在 30 米高度，订阅已经就绪，现在启动 B 机螺旋巡航任务。", flush=True)
    run_b_spiral_mission(runtime)
    if not runtime.finished.wait(timeout=B_SPIRAL_DURATION_S + 240.0):
        raise TimeoutError("事件驱动三机协同流程超时。")


def cleanup_runtime(runtime: DemoRuntime) -> None:
    """取消订阅、排空事件并关闭 Fleet。"""
    for unsubscribe in reversed(runtime.unsubscribes):
        unsubscribe()
    runtime.fleet.events.drain(timeout_s=5.0)
    runtime.fleet.close()


def print_event_summary(event: DeviceEvent) -> None:
    """打印关键 Fleet 事件，展示订阅系统看到的来源设备和事件类型。"""
    event_type = event.type.value
    device_id = event.source.device_id
    if event_type == EventType.GLOBAL_POSITION_UPDATED.value:
        key = (device_id, "global")
        now = time.monotonic()
        if now - _last_telemetry_log_at.get(key, 0.0) >= TELEMETRY_LOG_INTERVAL_S:
            _last_telemetry_log_at[key] = now
            position = event.position
            relative_altitude = position.relative_altitude_m
            relative_text = "未知" if relative_altitude is None else f"{relative_altitude:.2f} m"
            print(
                f"[Telemetry] source={device_id} global "
                f"lat={position.latitude_deg:.7f} lon={position.longitude_deg:.7f} "
                f"absolute_alt={position.absolute_altitude_m:.2f} m relative_alt={relative_text}",
                flush=True,
            )
        return
    if event_type == EventType.LOCAL_POSITION_UPDATED.value:
        key = (device_id, "local")
        now = time.monotonic()
        if now - _last_telemetry_log_at.get(key, 0.0) >= TELEMETRY_LOG_INTERVAL_S:
            _last_telemetry_log_at[key] = now
            local = event.position
            point = local.position_m
            print(
                f"[Telemetry] source={device_id} local "
                f"frame={local.frame.value} x={point.x:.2f} m y={point.y:.2f} m z={point.z:.2f} m",
                flush=True,
            )
        return
    if event_type.startswith("custom.fleet_events") or event_type.startswith("core.command"):
        print(f"[DeviceEvent] source={event.source.device_id} type={event_type}", flush=True)


def print_battery_low(event: DeviceEvent) -> None:
    """低电量快捷订阅示例；真实项目中可在这里触发中止任务或返航。"""
    print(
        f"[电量告警] {event.source.device_id} remaining={event.battery.remaining_fraction:.2f}",
        flush=True,
    )


def main() -> None:
    """运行事件驱动三机协同流程。"""
    fleet = build_fleet()
    drone_a, drone_b, drone_c = fleet.devices
    runtime = DemoRuntime(fleet=fleet, drone_a=drone_a, drone_b=drone_b, drone_c=drone_c)

    try:
        subscribe_demo_events(runtime)
        prepare_fleet(runtime)
        start_demo(runtime)
    except Exception as exc:
        print(f"[示例失败] {exc}", flush=True)
        print("[保护] 尝试让全部无人机返航。", flush=True)
        fleet.drones().return_to_launch()
    finally:
        cleanup_runtime(runtime)


if __name__ == "__main__":
    main()
