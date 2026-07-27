"""Fleet 共享避障示例；展示共享 Provider、容量保护和多设备世界模型。"""

from __future__ import annotations

import time

from fleetdock import (
    AvoidanceDimensions,
    AvoidancePolicy,
    AvoidanceStrategy,
    CoordinateFrame,
    CollisionRiskRaisedEvent,
    DeviceEvent,
    DeviceFactory,
    DeviceMetadata,
    EventType,
    Fleet,
    HeadingMode,
    MotionExecutionMode,
    Obstacle,
    ObstacleCapacityPolicy,
    ObstacleKind,
    ObstacleProvider,
    ObstacleSnapshot,
    Plan,
    Route,
    RouteReplannedEvent,
    RouteMotion,
    TaskStatus,
    UnlockStrategy,
    Vector3,
    Waypoint,
)


DRONE_CONFIGS = (
    ("drone-avoidance-001", "Fleet共享避障无人机 A", "udpin://0.0.0.0:14555"),
    ("drone-avoidance-002", "Fleet共享避障无人机 B", "udpin://0.0.0.0:14556"),
)
TAKEOFF_ALTITUDE_M = 18.0
ROUTE_SPEED_M_S = 3.0
ROUTE_TIMEOUT_S = 120.0
SHARED_OBSTACLE_ID = "demo-shared-obstacle-1"
SHARED_OBSTACLE_HITS: set[str] = set()
REPLANNED_DEVICES: set[str] = set()
ROUTE_MOTION = RouteMotion(
    mode=MotionExecutionMode.SDK,
    acceleration_m_s2=0.8,
    deceleration_m_s2=1.0,
    lookahead_time_s=1.0,
    min_lookahead_m=2.0,
    settle_time_s=0.5,
    settle_velocity_m_s=0.3,
)


class SharedCrossingObstacleProvider(ObstacleProvider):
    """演示用共享 Provider：向整个 Fleet 提供一份 LOCAL_NED 障碍物快照。"""

    def __init__(self, *, obstacle_id: str, x_m: float, y_m: float, altitude_m: float, radius_m: float) -> None:
        self._obstacle_id = obstacle_id
        self._x_m = x_m
        self._y_m = y_m
        # LOCAL_NED 的 z 轴向下为正；航线高度使用向上为正的米制高度。
        self._z_down_m = -abs(altitude_m)
        self._radius_m = radius_m
        self._closed = False

    def snapshot(self) -> ObstacleSnapshot:
        """返回当前共享障碍物；位置/半径单位为米，时间使用进程内单调时钟秒值。"""
        now_s = time.monotonic()
        return ObstacleSnapshot(
            provider_name="demo.fleet-shared-crossing",
            frame=CoordinateFrame.LOCAL_NED,
            observed_at_s=now_s,
            obstacles=(
                Obstacle(
                    obstacle_id=self._obstacle_id,
                    kind=ObstacleKind.STATIC,
                    frame=CoordinateFrame.LOCAL_NED,
                    position_m=Vector3(self._x_m, self._y_m, self._z_down_m),
                    velocity_m_s=Vector3(0.0, 0.0, 0.0),
                    radius_m=self._radius_m,
                    confidence=1.0,
                    observed_at_s=now_s,
                    ttl_s=1.0,
                ),
            ),
        )

    def close(self) -> None:
        """释放 Provider 资源；真实实现可在这里关闭雷达、地图连接或后台线程。"""
        self._closed = True


def build_fleet() -> Fleet:
    """创建双机 Fleet；端点与 ``examples/run_dev_multi.bat`` 的前两个实例一致。"""
    drones = [
        (
            DeviceFactory.create()
            .px4_generic()
            .params(endpoint=endpoint, timeout_s=30.0)
            .metadata(DeviceMetadata(device_id=device_id, name=name))
            .build()
        )
        for device_id, name, endpoint in DRONE_CONFIGS
    ]
    return Fleet(drones, name="Fleet共享避障演示编队")


def build_demo_route() -> Route:
    """生成一条直线航线；共享障碍物位于中段，用于稳定触发避障。"""
    return Route.polyline(
        (
            Waypoint(x_m=0.0, y_m=0.0, z_m=TAKEOFF_ALTITUDE_M, acceptance_radius_m=1.5),
            Waypoint(x_m=30.0, y_m=0.0, z_m=TAKEOFF_ALTITUDE_M, acceptance_radius_m=1.5),
        ),
        name="fleet-shared-avoidance-line",
    )


def drone_replan_policy() -> AvoidancePolicy:
    """返回无人机局部绕行策略；发现障碍后自动插入侧向绕行点并继续原航线。"""
    return AvoidancePolicy(
        strategy=AvoidanceStrategy.LOCAL_REPLAN,
        fallback_strategy=AvoidanceStrategy.HOLD_POSITION,
        dimensions=AvoidanceDimensions.THREE_D,
        lookahead_s=3.0,
        min_clearance_m=2.0,
        device_radius_m=0.7,
        replan_lateral_clearance_m=5.0,
        replan_vertical_clearance_m=3.0,
        max_replans_per_route=4,
        stale_obstacle_timeout_s=1.0,
    )


def print_event(event: DeviceEvent) -> None:
    """打印 demo 关心的起飞、航线和避障事件。"""
    interesting = {
        EventType.COLLISION_RISK_RAISED,
        EventType.AVOIDANCE_ACTION_TRIGGERED,
        EventType.OBSTACLE_PROVIDER_STALE,
        EventType.ROUTE_REPLANNED,
        EventType.ROUTE_STARTED,
        EventType.ROUTE_COMPLETED,
    }
    if event.type not in interesting:
        return
    if isinstance(event, CollisionRiskRaisedEvent) and event.violation.metadata.get("obstacle_id") == SHARED_OBSTACLE_ID:
        SHARED_OBSTACLE_HITS.add(event.device_id)
        print(f"[共享验证] {event.device_id} 已识别同一障碍物 {SHARED_OBSTACLE_ID}", flush=True)
    if isinstance(event, RouteReplannedEvent):
        REPLANNED_DEVICES.add(event.device_id)
        print(f"[绕行验证] {event.device_id} 已生成局部绕行航点并继续飞行", flush=True)
    print(f"[事件] {event.type.value}: {event.to_dict()['data']}", flush=True)


def print_task_result(label: str, result) -> None:
    """打印 Fleet 任务摘要；局部绕行成功时航线任务应正常完成。"""
    status = "成功" if result.status is TaskStatus.SUCCESS else result.status.value
    print(f"[{label}] 任务状态：{status}，消息：{result.message}", flush=True)
    for device_id, device_result in getattr(result, "device_results", {}).items():
        device_status = "成功" if device_result.status is TaskStatus.SUCCESS else device_result.status.value
        print(f"[{label}] {device_id}: {device_status} - {device_result.message}", flush=True)


def prepare_and_takeoff(fleet: Fleet) -> None:
    """连接 Fleet、等待仿真遥测就绪、解锁并起飞到指定高度。"""
    connect_result = fleet.connect()
    print(f"[Fleet连接] ok={connect_result.ok} message={connect_result.message}", flush=True)
    if not connect_result.ok:
        raise RuntimeError(connect_result.message)

    takeoff_result = (
        Plan(fleet)
        .for_drones()
        .wait_until_ready(timeout_s=60.0, require_armable=False)
        .unlock(strategy=UnlockStrategy.STANDARD_THEN_FORCE)
        .takeoff(TAKEOFF_ALTITUDE_M)
        .run(step_timeout_s=90.0, guard_interval_s=0.2)
    )
    print_task_result("Fleet起飞", takeoff_result)
    if not takeoff_result.ok:
        raise RuntimeError(takeoff_result.message)


def run_route_with_avoidance(fleet: Fleet) -> None:
    """让两架飞机执行同一航线；遇到共享障碍物后分别绕行并抵达终点。"""
    route_result = (
        Plan(fleet)
        .for_drones()
        .speed(ROUTE_SPEED_M_S)
        .heading(HeadingMode.TANGENT)
        .route(
            build_demo_route(),
            altitude=TAKEOFF_ALTITUDE_M,
            speed=ROUTE_SPEED_M_S,
            motion=ROUTE_MOTION,
            timeout_s=ROUTE_TIMEOUT_S,
        )
        .run(step_timeout_s=ROUTE_TIMEOUT_S, guard_interval_s=0.2)
    )
    print_task_result("Fleet共享障碍物绕行航线", route_result)
    if not route_result.ok:
        raise RuntimeError(f"绕行航线失败：{route_result.message}")


def return_to_launch(fleet: Fleet) -> None:
    """避障演示结束后统一返航，避免仿真无人机停留在航线中段。"""
    rtl_result = Plan(fleet).for_drones().return_to_launch().run(step_timeout_s=90.0, guard_interval_s=0.2)
    print_task_result("Fleet返航", rtl_result)


def main() -> None:
    """演示两架飞机共享同一障碍物，分别局部绕行后继续完成航线。"""
    SHARED_OBSTACLE_HITS.clear()
    REPLANNED_DEVICES.clear()
    fleet = build_fleet()
    should_return_to_launch = False
    provider = SharedCrossingObstacleProvider(
        obstacle_id=SHARED_OBSTACLE_ID,
        x_m=12.0,
        y_m=0.0,
        altitude_m=TAKEOFF_ALTITUDE_M,
        radius_m=1.5,
    )
    unsubscribe = fleet.events.subscribe(print_event)

    try:
        fleet.avoidance.configure_capacity(
            ObstacleCapacityPolicy(
                max_obstacles=512,
                max_obstacles_per_provider=256,
                default_ttl_s=1.0,
            )
        )
        fleet.avoidance.add_provider(provider).configure(
            default_policy=drone_replan_policy(),
            # 本 demo 只验证 Provider 共享，关闭队友动态障碍物以免干扰验证结果。
            include_fleet_devices=False,
            # 多实例仿真中的两架飞机和障碍物使用相同的 LOCAL_NED 世界原点。
            assume_shared_local_origin=True,
        )
        snapshot = fleet.avoidance.snapshot()
        print(
            f"[Fleet共享障碍物] provider 仅注册一次，"
            f"设备数={len(fleet.devices)}，障碍物={[item.obstacle_id for item in snapshot.obstacles]}",
            flush=True,
        )

        prepare_and_takeoff(fleet)
        should_return_to_launch = True
        run_route_with_avoidance(fleet)
        fleet.events.drain(timeout_s=5.0)
        expected_device_ids = {device_id for device_id, _, _ in DRONE_CONFIGS}
        if SHARED_OBSTACLE_HITS != expected_device_ids:
            raise RuntimeError(
                f"共享验证失败：预期 {sorted(expected_device_ids)}，实际 {sorted(SHARED_OBSTACLE_HITS)}"
            )
        if REPLANNED_DEVICES != expected_device_ids:
            raise RuntimeError(
                f"绕行验证失败：预期 {sorted(expected_device_ids)}，实际 {sorted(REPLANNED_DEVICES)}"
            )
        print(
            f"[绕行验证成功] 两架飞机已共享识别障碍物、分别绕行并完成航线：{sorted(REPLANNED_DEVICES)}",
            flush=True,
        )
    finally:
        try:
            if should_return_to_launch:
                return_to_launch(fleet)
        finally:
            unsubscribe()
            fleet.events.drain(timeout_s=5.0)
            fleet.close()


if __name__ == "__main__":
    main()
