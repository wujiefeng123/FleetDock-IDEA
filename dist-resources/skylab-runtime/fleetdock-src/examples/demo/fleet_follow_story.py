"""Fleet 跟随故事示例；按阶段展示追踪、轨迹、视角和距离带策略。"""

from __future__ import annotations

from fleetdock import (
    FollowExecutionOptions,
    FollowPolicy,
    FollowSessionState,
    Plan,
    Route,
    Waypoint,
)

from fleet_follow import (
    build_fleet,
    prepare_and_takeoff_follow_fleet,
    print_follow_session,
)
from drone_plan import TASK_ROUTE_MOTION
from fleet_plan import (
    FleetDemoSubscriptions,
    configure_drone_plan,
    ensure_connect_ok,
    print_route_parameters,
    print_task_step,
)


LEADER_ROUTE_SPEED_M_S = 2.4
STORY_FLIGHT_ALTITUDE_M = 35.0
FOLLOW_OPTIONS = FollowExecutionOptions(
    interval_s=0.2,
    default_max_speed_m_s=7.0,
    max_follow_distance_m=100.0,
    min_separation_m=6.0,
)


def story_route(name: str, points: tuple[tuple[float, float], ...]) -> Route:
    """把本地坐标点转换为可执行航线；坐标单位为米，x 向前，y 向右。"""
    return Route.polyline(tuple(Waypoint(x_m=x_m, y_m=y_m) for x_m, y_m in points), name=name)


def run_leader_route(drone, name: str, points: tuple[tuple[float, float], ...]) -> None:
    """让 leader 按 ``points`` 本地米制点执行指定名称的演示航线。"""
    route = story_route(name, points)
    print_route_parameters(
        route,
        label=f"{name} leader 航线",
        altitude_m=STORY_FLIGHT_ALTITUDE_M,
        speed_m_s=LEADER_ROUTE_SPEED_M_S,
        motion_mode="auto",
    )
    result = configure_drone_plan(Plan(drone, interceptors=[print_task_step])).routes(
        [route],
        altitude=STORY_FLIGHT_ALTITUDE_M,
        speed=LEADER_ROUTE_SPEED_M_S,
        motion=TASK_ROUTE_MOTION,
    ).run()
    print(f"[跟飞剧本结果] {result.message}", flush=True)
    if not result.ok:
        raise RuntimeError(f"{name} 执行失败。")


def main() -> None:
    """运行 A-B-C 三机跟飞剧本；A 是默认视角机，只跟随 B/C，不做 leader。"""
    fleet = build_fleet()
    drone_a, drone_b, drone_c = fleet.devices
    view_drone = drone_a
    session = None

    print("跟飞剧本：A 固定为视角机，B/C 轮流领航，飞行高度 35 m。", flush=True)
    print(
        "[跟飞剧本参数] "
        f"follow_interval={FOLLOW_OPTIONS.interval_s} s "
        f"default_max_speed={FOLLOW_OPTIONS.default_max_speed_m_s} m/s "
        f"max_follow_distance={FOLLOW_OPTIONS.max_follow_distance_m} m "
        f"min_separation={FOLLOW_OPTIONS.min_separation_m} m",
        flush=True,
    )
    print(
        "视角机绑定："
        f"{view_drone.metadata.name} ({view_drone.metadata.device_id})，"
        "默认对应 run_dev_multi.bat 的 Drone1 / PX4 instance 0 / MAVSDK 14555。",
        flush=True,
    )

    try:
        with FleetDemoSubscriptions(fleet):
            connect_result = fleet.connect()
            ensure_connect_ok(connect_result)
            if not prepare_and_takeoff_follow_fleet(fleet, altitude_m=STORY_FLIGHT_ALTITUDE_M):
                raise SystemExit(1)

            session = fleet.follow(
                leader=drone_b,
                follower_policies={
                    view_drone: FollowPolicy.camera(distance_m=15.0, side_m=5.0, max_speed_m_s=7.0),
                    drone_c: FollowPolicy.trail(delay_s=2.8, distance_m=10.0, max_speed_m_s=6.5),
                },
            )
            print("\n阶段 1：B 领航向前，A 用视角跟随拍摄，C 沿 B 的轨迹跟随。", flush=True)
            print_follow_session(fleet)
            session.start(FOLLOW_OPTIONS)
            run_leader_route(drone_b, "stage-1-b-forward", ((0.0, 0.0), (90.0, 0.0)))
            if session.status().state is FollowSessionState.FAILED:
                raise RuntimeError(session.status().message)
            session.stop()

            session = fleet.follow(
                leader=drone_c,
                follower_policies={
                    view_drone: FollowPolicy.chase(distance_m=14.0, max_speed_m_s=7.0),
                    drone_b: FollowPolicy.relative(x_m=-10.0, y_m=-5.0, z_m=0.0, max_speed_m_s=6.5),
                },
            )
            print("\n阶段 2：C 接棒向右，A 改为追踪跟随并始终朝向 C，B 保持相对队形伴飞。", flush=True)
            print_follow_session(fleet)
            session.start(FOLLOW_OPTIONS)
            run_leader_route(drone_c, "stage-2-c-right", ((0.0, 0.0), (0.0, 70.0)))
            if session.status().state is FollowSessionState.FAILED:
                raise RuntimeError(session.status().message)
            session.stop()

            session = fleet.follow(
                leader=drone_b,
                follower_policies={
                    drone_c: FollowPolicy.relative(x_m=-9.0, y_m=0.0, z_m=0.0, max_speed_m_s=6.5),
                    view_drone: FollowPolicy.camera(distance_m=20.0, side_m=0.0, max_speed_m_s=7.0),
                },
            )
            print("\n阶段 3：B 带队飞一字纵队，C 跟 B，A 在队尾作为视角机跟拍。", flush=True)
            print_follow_session(fleet)
            session.start(FOLLOW_OPTIONS)
            run_leader_route(drone_b, "stage-3-b-line", ((0.0, 0.0), (90.0, 0.0)))
            if session.status().state is FollowSessionState.FAILED:
                raise RuntimeError(session.status().message)
            session.stop()

        rtl_result = fleet.drones().return_to_launch()
        print(f"[Fleet 返航结果] {rtl_result.message}", flush=True)
    finally:
        if session is not None:
            session.stop()
        print(f"\n跟随已停止，活动会话数量：{len(fleet.follow_sessions)}", flush=True)
        fleet.events.drain(timeout_s=5.0)
        fleet.close()


if __name__ == "__main__":
    main()
