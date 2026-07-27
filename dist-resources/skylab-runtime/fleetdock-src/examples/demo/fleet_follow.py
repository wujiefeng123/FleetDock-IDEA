"""三机动态跟随示例；展示 Fleet、leader/follower 关系和跟随策略切换。"""

from __future__ import annotations

from fleetdock import DeviceFactory, DeviceMetadata, Drone, Fleet, FollowMode, FollowPolicy, Plan, Route, Waypoint

from drone_plan import TAKEOFF_ALTITUDE_M, TASK_ROUTE_MOTION
from fleet_plan import (
    FleetDemoSubscriptions,
    configure_drone_plan,
    ensure_connect_ok,
    prepare_and_takeoff_fleet,
    print_route_parameters,
    print_task_step,
)


DEFAULT_DRONES = (
    ("drone-a", "A", "udpin://0.0.0.0:14555"),
    ("drone-b", "B", "udpin://0.0.0.0:14556"),
    ("drone-c", "C", "udpin://0.0.0.0:14557"),
)
FOLLOW_DISTANCE_M = 24.0


def build_demo_drone(device_id: str, name: str, endpoint: str) -> Drone:
    """创建一架连接 PX4 SITL 的跟飞示例无人机。"""
    return (
        DeviceFactory.create()
        .px4_generic()
        .params(endpoint=endpoint, timeout_s=30.0)
        .metadata(DeviceMetadata(device_id=device_id, name=name, tags=("follow-demo",)))
        .build()
    )


def build_fleet() -> Fleet:
    """创建 A-B-C 三机跟飞编队。"""
    return Fleet(
        [build_demo_drone(device_id, name, endpoint) for device_id, name, endpoint in DEFAULT_DRONES],
        name="A-B-C 跟飞编队",
    )


def print_follow_session(fleet: Fleet) -> None:
    """打印当前 Fleet 中全部活动跟随会话。

    输出包含 leader、followers 和每个 follower 的跟随策略；相对偏移策略会额外展示 offset。
    """
    for session in fleet.follow_sessions:
        print(f"跟随会话 {session.id}")
        print(f"  leader: {session.leader.metadata.name} ({session.leader.metadata.device_id})")
        for follower in session.followers:
            policy = session.policy_for(follower)
            mode = getattr(policy, "mode", None)
            mode_text = mode.value if mode is not None else type(policy).__name__
            detail = f"mode={mode_text}"
            if mode is FollowMode.RELATIVE:
                detail += (
                    f" offset=({policy.x_m:.1f}, {policy.y_m:.1f}, {policy.z_m:.1f}) m "
                    f"frame={policy.frame}"
                )
            elif hasattr(policy, "distance_m"):
                detail += f" distance={policy.distance_m:.1f} m"
                if hasattr(policy, "side_m"):
                    detail += f" side={policy.side_m:.1f} m"
                if hasattr(policy, "delay_s"):
                    detail += f" delay={policy.delay_s:.1f} s"
            print(f"  follower: {follower.metadata.name} ({follower.metadata.device_id}) {detail}")


def leader_route() -> Route:
    """返回仅由 leader 执行的本地直线航线；followers 由 FollowSession 控制。"""
    return Route.polyline(
        (
            Waypoint(x_m=0.0, y_m=0.0),
            Waypoint(x_m=FOLLOW_DISTANCE_M, y_m=0.0),
        ),
        name="leader-forward",
    )


def prepare_and_takeoff_follow_fleet(fleet: Fleet, *, altitude_m: float = TAKEOFF_ALTITUDE_M) -> bool:
    """兼容故事示例的准备入口；标准起飞流程由 fleet_plan 的 SDK 计划统一实现。"""
    return prepare_and_takeoff_fleet(fleet, altitude_m=altitude_m)


def main() -> None:
    """运行 A-B-C PX4 跟飞队形 Demo。"""
    fleet = build_fleet()
    drone_a, drone_b, drone_c = fleet.devices
    session = fleet.follow(
        leader=drone_a,
        followers=[drone_b],
        policy=FollowPolicy.relative(x_m=-3.0, y_m=-2.0, z_m=0.0),
    )
    session.add(
        drone_c,
        policy=FollowPolicy.relative(x_m=-3.0, y_m=2.0, z_m=0.0),
    )

    print("A 带 B、C 跟飞：")
    print_follow_session(fleet)

    session.remove(drone_b)
    session.add(
        drone_b,
        policy=FollowPolicy.relative(x_m=-5.0, y_m=0.0, z_m=0.0),
    )

    print("\nB 调整到 A 正后方 5 米：")
    print_follow_session(fleet)

    try:
        with FleetDemoSubscriptions(fleet):
            connect_result = fleet.connect()
            ensure_connect_ok(connect_result)
            if not prepare_and_takeoff_fleet(fleet):
                raise SystemExit(1)

            session.start(interval_s=0.2, default_max_speed_m_s=4.0)
            print("[Fleet 跟飞参数] follow_interval=0.2 s default_max_speed=4.0 m/s", flush=True)
            print_route_parameters(
                leader_route(),
                label="leader 航线",
                altitude_m=TAKEOFF_ALTITUDE_M,
                motion_mode="auto",
            )
            route_result = configure_drone_plan(
                Plan(drone_a, interceptors=[print_task_step])
            ).route(
                leader_route(),
                altitude=TAKEOFF_ALTITUDE_M,
                motion=TASK_ROUTE_MOTION,
            ).run()
            print("[Fleet 跟飞航线结果]")
            print(route_result)

        rtl_result = fleet.drones().return_to_launch()
        print(f"[Fleet 返航结果] {rtl_result.message}")
    finally:
        session.stop()
        print(f"\n跟随已停止，活动会话数量：{len(fleet.follow_sessions)}")
        fleet.events.drain(timeout_s=5.0)
        fleet.close()


if __name__ == "__main__":
    main()
