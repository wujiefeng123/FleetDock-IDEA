"""PX4 单机避障示例；展示障碍物 Provider、策略配置和受保护航线执行。"""

from __future__ import annotations

import time

from fleetdock import (
    AvoidanceDimensions,
    AvoidancePolicy,
    AvoidanceStrategy,
    CoordinateFrame,
    DeviceEvent,
    DeviceFactory,
    DeviceMetadata,
    EventType,
    HeadingMode,
    MotionExecutionMode,
    Obstacle,
    ObstacleKind,
    ObstacleProvider,
    ObstacleSnapshot,
    Plan,
    Route,
    RouteMotion,
    TaskStatus,
    Vector3,
    Waypoint,
)


TAKEOFF_ALTITUDE_M = 18.0
ROUTE_SPEED_M_S = 3.0
ROUTE_TIMEOUT_S = 120.0
PX4_ENDPOINT = "udpin://0.0.0.0:14555"
ROUTE_MOTION = RouteMotion(
    mode=MotionExecutionMode.SDK,
    acceleration_m_s2=0.8,
    deceleration_m_s2=1.0,
    lookahead_time_s=1.0,
    min_lookahead_m=2.0,
    settle_time_s=0.5,
    settle_velocity_m_s=0.3,
)
REPLAN_ROUTE_MOTION = RouteMotion(
    mode=MotionExecutionMode.SDK,
    acceleration_m_s2=0.8,
    deceleration_m_s2=1.0,
    lookahead_time_s=2.0,
    min_lookahead_m=8.0,
    settle_time_s=0.5,
    settle_velocity_m_s=0.3,
)


class CrossingObstacleProvider(ObstacleProvider):
    """演示用障碍物 provider：在无人机路线中间生成一个标准障碍物。"""

    def __init__(self, *, obstacle_id: str, x_m: float, y_m: float, altitude_m: float, radius_m: float) -> None:
        self._obstacle_id = obstacle_id
        self._x_m = x_m
        self._y_m = y_m
        # LOCAL_NED 的 z 轴向下为正；公共 route 高度仍使用向上为正的米。
        self._z_down_m = -abs(altitude_m)
        self._radius_m = radius_m
        self._closed = False

    def snapshot(self) -> ObstacleSnapshot:
        """返回当前模拟障碍物快照；位置和尺寸单位为米，坐标系为 LOCAL_NED。"""
        now_s = time.monotonic()
        return ObstacleSnapshot(
            provider_name="demo.crossing",
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
        """释放 Provider；本示例没有后台资源，因此该操作保持幂等。"""
        self._closed = True


def build_drone():
    """创建单机 PX4 无人机对象；端点与 examples/run_dev.bat 默认 MAVSDK 端口一致。"""
    return (
        DeviceFactory.create()
        .px4_generic()
        .params(endpoint=PX4_ENDPOINT, timeout_s=30.0)
        .metadata(DeviceMetadata(device_id="drone-avoidance-001", name="避障演示无人机"))
        .build()
    )


def build_demo_route(name: str) -> Route:
    """生成一条直线路线，障碍物位于路线中段，便于观察阻断和绕行差异。"""
    return Route.polyline(
        (
            Waypoint(x_m=0.0, y_m=0.0, z_m=TAKEOFF_ALTITUDE_M, acceptance_radius_m=1.5),
            Waypoint(x_m=30.0, y_m=0.0, z_m=TAKEOFF_ALTITUDE_M, acceptance_radius_m=1.5),
        ),
        name=name,
    )


def drone_blocking_policy() -> AvoidancePolicy:
    """返回无人机阻断型避障策略；风险命中时悬停，失败处理器负责返航。"""
    return AvoidancePolicy(
        strategy=AvoidanceStrategy.HOLD_POSITION,
        dimensions=AvoidanceDimensions.THREE_D,
        lookahead_s=3.0,
        min_clearance_m=4.0,
        device_radius_m=0.7,
        stale_obstacle_timeout_s=1.0,
    )


def drone_replan_policy() -> AvoidancePolicy:
    """返回无人机自动绕行策略；风险命中时 SDK 插入临时绕行点并继续原路线。"""
    return AvoidancePolicy(
        strategy=AvoidanceStrategy.LOCAL_REPLAN,
        dimensions=AvoidanceDimensions.THREE_D,
        lookahead_s=5.0,
        min_clearance_m=2.0,
        device_radius_m=0.7,
        stale_obstacle_timeout_s=1.0,
        replan_lateral_clearance_m=6.0,
        max_replans_per_route=4,
    )


def print_event(event: DeviceEvent) -> None:
    """打印 demo 关心的避障和路线事件。"""
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
    print(f"[事件] {event.type.value}: {event.to_dict()['data']}", flush=True)


def print_result(label: str, result) -> None:
    """打印任务结果摘要。"""
    status = "成功" if result.status is TaskStatus.SUCCESS else result.status.value
    print(f"[{label}] 任务状态={status}，消息={result.message}", flush=True)
    for index, step in enumerate(result.steps, start=1):
        step_status = "成功" if step.ok else "失败"
        print(f"[{label}] 步骤 {index}: {step.name} {step_status} - {step.message}", flush=True)


def return_to_launch_and_wait(drone, *, timeout_s: float = 90.0) -> None:
    """使用标准 Plan 发送返航并等待落地或解除解锁确认。"""
    result = Plan(drone).return_to_launch().run(step_timeout_s=timeout_s)
    print(f"返航确认：status={result.status.value} message={result.message}", flush=True)
    if result.status is not TaskStatus.SUCCESS:
        raise RuntimeError(result.message)


def main() -> None:
    """配置单机障碍物 Provider 和避障策略，执行受保护航线后返航。"""
    provider = CrossingObstacleProvider(
        obstacle_id="demo-obstacle-1",
        x_m=12.0,
        y_m=0.0,
        altitude_m=TAKEOFF_ALTITUDE_M,
        radius_m=1.5,
    )
    drone = build_drone()
    unsubscribe = drone.events.subscribe(print_event)

    try:
        drone.avoidance.add_provider(provider)
        connect_response = drone.connect()
        print(f"连接结果：ok={connect_response.ok} message={connect_response.message}", flush=True)
        if not connect_response.ok:
            raise RuntimeError(connect_response.message)

        print("\n=== 第一次演示：阻断型避障，风险触发悬停，然后返航 ===", flush=True)
        drone.avoidance.configure(drone_blocking_policy())
        blocking_result = (
            Plan(drone)
            .timeout(ROUTE_TIMEOUT_S)
            .speed(ROUTE_SPEED_M_S)
            .heading(HeadingMode.TANGENT)
            .unlock()
            .takeoff(TAKEOFF_ALTITUDE_M)
            .route(
                build_demo_route("blocking-avoidance-line"),
                altitude=TAKEOFF_ALTITUDE_M,
                speed=ROUTE_SPEED_M_S,
                motion=ROUTE_MOTION,
                timeout_s=ROUTE_TIMEOUT_S,
            )
            .run(step_timeout_s=ROUTE_TIMEOUT_S, guard_interval_s=0.2)
        )
        print_result("阻断演示", blocking_result)
        return_to_launch_and_wait(drone)

        print("\n=== 第二次演示：自动绕行，SDK 插入临时绕行点并回到原路线 ===", flush=True)
        drone.avoidance.configure(drone_replan_policy())
        replan_result = (
            Plan(drone)
            .timeout(ROUTE_TIMEOUT_S)
            .speed(ROUTE_SPEED_M_S)
            .heading(HeadingMode.TANGENT)
            .unlock()
            .takeoff(TAKEOFF_ALTITUDE_M)
            .route(
                build_demo_route("auto-replan-avoidance-line"),
                altitude=TAKEOFF_ALTITUDE_M,
                speed=ROUTE_SPEED_M_S,
                motion=REPLAN_ROUTE_MOTION,
                timeout_s=ROUTE_TIMEOUT_S,
            )
            .return_to_launch()
            .run(step_timeout_s=ROUTE_TIMEOUT_S, guard_interval_s=0.2)
        )
        print_result("绕行演示", replan_result)
    finally:
        unsubscribe()
        drone.close()


if __name__ == "__main__":
    main()
