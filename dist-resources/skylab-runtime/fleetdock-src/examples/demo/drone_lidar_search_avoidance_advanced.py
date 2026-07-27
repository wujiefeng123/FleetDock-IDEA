"""高级 LiDAR 演练：选择安全出口、执行低空螺旋搜索并自动绕障返航。"""

from __future__ import annotations

import os
import random
import time
from dataclasses import replace

from _projectairsim_logging import configure_projectairsim_console_logging
from fleetdock import (
    AvoidanceDimensions,
    AvoidancePolicy,
    AvoidanceStrategy,
    DepartureDirectionPolicy,
    DeviceEvent,
    DeviceFactory,
    EventType,
    HeadingMode,
    Plan,
    Route,
    RouteMotion,
    SensorKind,
    MotionExecutionMode,
    TaskCompletionReason,
    TaskStatus,
    avoidance,
)


PX4_ENDPOINT = os.environ.get("FLEETDOCK_PX4_ENDPOINT", "udpin://0.0.0.0:14555")
RANDOM_SEED = os.environ.get("FLEETDOCK_DEMO_RANDOM_SEED")
TAKEOFF_ALTITUDE_M = 18.0
ROUTE_SPEED_M_S = 3.0
ROUTE_MIN_DISTANCE_M = 200.0
ROUTE_MAX_DISTANCE_M = 300.0
LIDAR_STALE_TIMEOUT_S = 2.0
DEPARTURE_SCAN_FRAMES = 30

AVOIDANCE_POLICY = AvoidancePolicy(
    strategy=AvoidanceStrategy.LOCAL_REPLAN,
    fallback_strategy=AvoidanceStrategy.RETURN_TO_LAUNCH,
    dimensions=AvoidanceDimensions.THREE_D,
    lookahead_s=5.0,
    min_clearance_m=1.5,
    device_radius_m=0.7,
    stale_obstacle_timeout_s=1.0,
    replan_lateral_clearance_m=4.0,
    replan_vertical_clearance_m=4.0,
    max_replans_per_route=256,
    replan_retry_timeout_s=10.0,
    replan_retry_interval_s=0.5,
)
ROUTE_MOTION = RouteMotion(
    mode=MotionExecutionMode.SDK,
    acceleration_m_s2=0.8,
    deceleration_m_s2=1.0,
    lookahead_time_s=2.0,
    min_lookahead_m=8.0,
    settle_time_s=0.5,
    settle_velocity_m_s=0.3,
)


def print_event(event: DeviceEvent) -> None:
    """只打印新手需要观察的航线、风险和绕行事件。"""

    interesting = {
        EventType.COLLISION_RISK_RAISED,
        EventType.AVOIDANCE_ACTION_TRIGGERED,
        EventType.OBSTACLE_PROVIDER_STALE,
        EventType.ROUTE_REPLANNED,
        EventType.ROUTE_STARTED,
        EventType.ROUTE_COMPLETED,
    }
    if event.type in interesting:
        print(f"[避障事件] {event.type.value}: {event.to_dict()['data']}", flush=True)


def main() -> None:
    """执行“订阅—起飞—选择出口—螺旋搜索—自动绕障—返航”的高级验收剧本。"""

    configure_projectairsim_console_logging()
    rng = random.Random(RANDOM_SEED)
    drone = (
        DeviceFactory.create()
        .px4_generic()
        .params(endpoint=PX4_ENDPOINT, timeout_s=30.0)
        .build()
    )
    unsubscribe_lidar = None
    lidar = None
    unsubscribe_events = drone.events.subscribe(print_event)
    returned_safely = False
    last_lidar_log_s = 0.0

    try:
        response = drone.connect()
        print(f"[连接] ok={response.ok} message={response.message}", flush=True)
        if not response.ok:
            raise RuntimeError(response.message)

        lidar_descriptor = drone.sensors.get(SensorKind.LIDAR)
        print(
            f"[传感器发现] {lidar_descriptor.sensor_id} frame={lidar_descriptor.frame.value}",
            flush=True,
        )

        # 这是 demo 的核心入口：回调只把标准点云交给 SDK Provider，复杂处理在 SDK 后台完成。
        lidar = avoidance.LidarObstacleProvider(
            lidar_descriptor,
            pose_provider=drone.state.snapshot,
            config=replace(
                avoidance.LidarObstacleConfig.for_preset(avoidance.LidarAvoidancePreset.LOW_ALTITUDE),
                max_obstacles=256,
            ),
        )

        def on_lidar(sample) -> None:
            """提交最新点云，并每5秒打印一次 Provider 性能状态。"""

            nonlocal last_lidar_log_s
            lidar.observe(sample)
            now_s = time.monotonic()
            if now_s - last_lidar_log_s < 5.0:
                return
            status = lidar.status(stale_timeout_s=LIDAR_STALE_TIMEOUT_S)
            print(
                f"[LiDAR订阅] sequence={status.latest_sequence} points={status.input_points} "
                f"retained={status.retained_points} obstacles={status.obstacle_count} "
                f"processing_ms={(status.processing_ms or 0.0):.1f}",
                flush=True,
            )
            last_lidar_log_s = now_s

        unsubscribe_lidar = drone.sensors.subscribe(SensorKind.LIDAR, on_lidar)
        drone.avoidance.add_provider(lidar).configure(AVOIDANCE_POLICY)

        takeoff_result = (
            Plan(drone)
            .wait_until_ready(timeout_s=60.0)
            .unlock()
            .takeoff(altitude=TAKEOFF_ALTITUDE_M)
            .run(step_timeout_s=90.0)
        )
        if takeoff_result.status is not TaskStatus.SUCCESS:
            raise RuntimeError(takeoff_result.message)
        print(f"[飞行剧本] 已起飞到 {TAKEOFF_ALTITUDE_M:.1f} 米低空高度。", flush=True)

        readiness = lidar.wait_until_ready(timeout_s=5.0)
        if not readiness.ready:
            raise RuntimeError(f"LiDAR Provider 未就绪：{readiness.reason}")
        lidar.reset_direction_history()
        if not lidar.wait_for_direction_data(minimum_frames=DEPARTURE_SCAN_FRAMES, timeout_s=3.0):
            raise RuntimeError("起飞后没有收集到足够的 LiDAR 方向数据。")

        departure_policy = DepartureDirectionPolicy(
            preferred_clearance_m=15.0,
            minimum_clearance_m=4.0,
            clearance_step_m=2.0,
            obstacle_range_m=80.0,
            departure_probe_m=ROUTE_MOTION.min_lookahead_m,
        )
        direction = lidar.select_safe_direction(
            avoidance=drone.avoidance,
            state=drone.state.snapshot(),
            direction_policy=departure_policy,
            avoidance_policy=AVOIDANCE_POLICY,
            speed_m_s=ROUTE_SPEED_M_S,
            rng=rng,
        )
        if direction is None:
            print("[航向规划] 周围没有满足安全半径的低空出口，直接返航。", flush=True)
            rtl_result = Plan(drone).return_to_launch().run(step_timeout_s=240.0)
            print(f"[安全返航] status={rtl_result.status.value} message={rtl_result.message}", flush=True)
            if rtl_result.status is not TaskStatus.SUCCESS:
                raise RuntimeError(rtl_result.message)
            returned_safely = True
            return

        distance_m = rng.uniform(ROUTE_MIN_DISTANCE_M, ROUTE_MAX_DISTANCE_M)
        clockwise = rng.choice((True, False))
        takeoff_state = drone.state.snapshot()
        if takeoff_state.local_x_m is None or takeoff_state.local_y_m is None:
            raise RuntimeError("起飞后缺少本地位置，无法以当前位置生成螺旋航线。")
        route = Route.spiral_search(
            target_length_m=distance_m,
            max_radius_m=min(30.0, distance_m * 0.16),
            waypoint_spacing_m=3.0,
            entry_radius_m=ROUTE_MOTION.min_lookahead_m,
            start_angle_deg=direction.heading_deg,
            clockwise=clockwise,
            center_x_m=takeoff_state.local_x_m,
            center_y_m=takeoff_state.local_y_m,
            acceptance_radius_m=2.0,
            name=f"lidar-spiral-search-{rng.randrange(1_000_000):06d}",
        )
        route_timeout_s = distance_m / ROUTE_SPEED_M_S + 300.0
        print(
            f"[螺旋航线] distance={route.estimated_length_m:.1f}m heading={direction.heading_deg:.1f}° "
            f"clockwise={clockwise} entry_clearance={direction.selected_clearance_m:.1f}m",
            flush=True,
        )

        def lidar_is_stale(_context) -> bool:
            """LiDAR超过允许静默时间时触发悬停，再由必返航流程清理。"""

            return lidar.is_stale(LIDAR_STALE_TIMEOUT_S)

        def hover_on_stale(context) -> None:
            """点云失效时先悬停，避免在没有感知数据时继续航行。"""

            context.device.hover()

        result = (
            Plan(drone)
            .when(lidar_is_stale, then=hover_on_stale, reason="LiDAR 点云超过 2 秒未成功处理")
            .route(
                route,
                altitude=TAKEOFF_ALTITUDE_M,
                speed=ROUTE_SPEED_M_S,
                heading=HeadingMode.TANGENT,
                motion=ROUTE_MOTION,
                avoidance=AVOIDANCE_POLICY,
                timeout_s=route_timeout_s,
            )
            .ensure_return_to_launch(timeout_s=240.0)
            .run(step_timeout_s=route_timeout_s, guard_interval_s=0.2)
        )
        print(
            f"[任务结果] status={result.status.value} reason="
            f"{result.completion_reason.value if result.completion_reason else 'unknown'} "
            f"message={result.message}",
            flush=True,
        )
        if result.cleanup_result is not None:
            print(
                f"[清理返航] status={result.cleanup_result.status.value} "
                f"message={result.cleanup_result.message}",
                flush=True,
            )
        cleanup_succeeded = result.cleanup_result is not None and result.cleanup_result.status is TaskStatus.SUCCESS
        returned_safely = result.status is TaskStatus.SUCCESS or cleanup_succeeded
        if not returned_safely:
            raise RuntimeError(result.message)
        if result.completion_reason is TaskCompletionReason.AVOIDANCE_FALLBACK:
            print("[飞行剧本] 局部绕行后无安全路径，已按 SDK 安全策略返航。", flush=True)
        else:
            print("[飞行剧本] 螺旋搜索完成并已返航。", flush=True)
    finally:
        state = drone.state.snapshot()
        if not returned_safely and (state.in_air is True or state.armed is True):
            rtl_result = Plan(drone).return_to_launch().run(step_timeout_s=240.0)
            print(f"[清理返航] status={rtl_result.status.value} message={rtl_result.message}", flush=True)
        if unsubscribe_lidar is not None:
            unsubscribe_lidar()
        unsubscribe_events()
        if lidar is not None:
            lidar.close()
        drone.close()


if __name__ == "__main__":
    main()
