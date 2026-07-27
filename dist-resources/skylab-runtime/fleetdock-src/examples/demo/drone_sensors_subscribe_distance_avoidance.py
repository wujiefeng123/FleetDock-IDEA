"""显式订阅单向测距并演示近场悬停/返航保护。"""

from __future__ import annotations

from fleetdock import (
    AvoidancePolicy,
    AvoidanceStrategy,
    DistanceSample,
    Plan,
    SensorKind,
    TaskStatus,
    avoidance,
)


def run_distance_avoidance(drone) -> None:
    """为已连接且提供 DISTANCE 传感器的设备执行近场保护剧本。"""

    descriptor = drone.sensors.get(SensorKind.DISTANCE)
    provider = avoidance.DistanceObstacleProvider(descriptor, pose_provider=drone.state.snapshot)
    drone.avoidance.add_provider(provider)

    def on_distance(sample: DistanceSample) -> None:
        """接收米制单向距离，并更新近场障碍物快照。"""
        provider.observe(sample)

    unsubscribe = drone.sensors.subscribe(descriptor.sensor_id, on_distance)
    policy = AvoidancePolicy(
        strategy=AvoidanceStrategy.HOVER,
        fallback_strategy=AvoidanceStrategy.RETURN_TO_LAUNCH,
    )
    drone.avoidance.configure(policy)
    try:
        result = (
            Plan(drone)
            .wait_until_ready(timeout_s=60.0)
            .unlock()
            .takeoff(8.0)
            .ensure_return_to_launch()
            .run()
        )
        cleanup_ok = result.cleanup_result is not None and result.cleanup_result.status is TaskStatus.SUCCESS
        if result.status is not TaskStatus.SUCCESS and not cleanup_ok:
            raise RuntimeError(result.message)
    finally:
        unsubscribe()
        drone.avoidance.remove_provider(provider)


if __name__ == "__main__":
    raise SystemExit("本 demo 需要由具体设备示例创建并连接带 DISTANCE 传感器的 drone。")
