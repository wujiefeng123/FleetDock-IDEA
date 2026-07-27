"""多个传感器避障 demo 共用的低空螺旋飞行剧本。"""

from __future__ import annotations

import os
import random

from fleetdock import (
    AvoidanceDimensions,
    AvoidancePolicy,
    AvoidanceStrategy,
    HeadingMode,
    MotionExecutionMode,
    Plan,
    Route,
    RouteMotion,
    TaskStatus,
)


TAKEOFF_ALTITUDE_M = 12.0
ROUTE_SPEED_M_S = 3.0

AVOIDANCE_POLICY = AvoidancePolicy(
    strategy=AvoidanceStrategy.LOCAL_REPLAN,
    fallback_strategy=AvoidanceStrategy.RETURN_TO_LAUNCH,
    dimensions=AvoidanceDimensions.TWO_D,
    lookahead_s=5.0,
    min_clearance_m=3.0,
    device_radius_m=0.7,
    stale_obstacle_timeout_s=1.0,
    replan_lateral_clearance_m=6.0,
    max_replans_per_route=32,
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


def run_avoidance_scenario(drone, *, sensor_label: str) -> bool:
    """使用调用方已显式注册的 Provider 执行低空绕障和必执行返航，并返回是否安全结束。"""

    drone.avoidance.configure(AVOIDANCE_POLICY)
    takeoff = (
        Plan(drone)
        .wait_until_ready(timeout_s=60.0)
        .unlock()
        .takeoff(TAKEOFF_ALTITUDE_M)
        .run(step_timeout_s=90.0)
    )
    if takeoff.status is not TaskStatus.SUCCESS:
        raise RuntimeError(takeoff.message)

    state = drone.state.snapshot()
    if state.local_x_m is None or state.local_y_m is None:
        raise RuntimeError("起飞后缺少本地位置，无法生成避障航线。")
    rng = random.Random(os.environ.get("FLEETDOCK_DEMO_RANDOM_SEED"))
    minimum_length_m = float(os.environ.get("FLEETDOCK_DEMO_ROUTE_MIN_M", "200"))
    maximum_length_m = float(os.environ.get("FLEETDOCK_DEMO_ROUTE_MAX_M", "300"))
    if minimum_length_m <= 0 or maximum_length_m < minimum_length_m:
        raise ValueError("Demo route distance range is invalid.")
    target_length_m = rng.uniform(minimum_length_m, maximum_length_m)
    route = Route.spiral_search(
        target_length_m=target_length_m,
        max_radius_m=min(30.0, target_length_m * 0.16),
        waypoint_spacing_m=3.0,
        start_angle_deg=rng.uniform(-180.0, 180.0),
        clockwise=rng.choice((True, False)),
        center_x_m=state.local_x_m,
        center_y_m=state.local_y_m,
        name=f"{sensor_label}-avoidance-spiral",
    )
    print(
        f"[避障演练] sensor={sensor_label} route={route.estimated_length_m:.1f}m "
        f"altitude={TAKEOFF_ALTITUDE_M:.1f}m",
        flush=True,
    )
    timeout_s = target_length_m / ROUTE_SPEED_M_S + 300.0
    result = (
        Plan(drone)
        .route(
            route,
            altitude=TAKEOFF_ALTITUDE_M,
            speed=ROUTE_SPEED_M_S,
            heading=HeadingMode.TANGENT,
            motion=ROUTE_MOTION,
            avoidance=AVOIDANCE_POLICY,
            timeout_s=timeout_s,
        )
        .ensure_return_to_launch(timeout_s=240.0)
        .run(step_timeout_s=timeout_s)
    )
    cleanup_ok = result.cleanup_result is not None and result.cleanup_result.status is TaskStatus.SUCCESS
    print(
        f"[避障演练结果] status={result.status.value} "
        f"reason={getattr(result.completion_reason, 'value', None)} cleanup_ok={cleanup_ok}",
        flush=True,
    )
    return result.status is TaskStatus.SUCCESS or cleanup_ok
