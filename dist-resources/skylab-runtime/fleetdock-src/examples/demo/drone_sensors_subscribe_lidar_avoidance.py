"""显式订阅 LiDAR 点云并执行低空避障演练。"""

from __future__ import annotations

import os

from _projectairsim_logging import configure_projectairsim_console_logging
from _sensor_avoidance_scenario import run_avoidance_scenario
from fleetdock import (
    DeviceFactory,
    PointCloudFrame,
    SensorKind,
    avoidance,
)


def main() -> None:
    """执行“订阅标准点云—转换障碍物—避障飞行—返航”的 LiDAR 剧本。"""

    configure_projectairsim_console_logging()
    drone = (
        DeviceFactory.create()
        .px4_generic()
        .params(
            endpoint=os.environ.get("FLEETDOCK_PX4_ENDPOINT", "udpin://0.0.0.0:14555"),
            timeout_s=30.0,
        )
        .build()
    )
    provider = None
    unsubscribe = None
    safe = False
    try:
        response = drone.connect()
        if not response.ok:
            raise RuntimeError(response.message)

        descriptor = drone.sensors.get(SensorKind.LIDAR)
        provider = avoidance.LidarObstacleProvider(
            descriptor,
            pose_provider=drone.state.snapshot,
            config=avoidance.LidarObstacleConfig.for_preset(avoidance.LidarAvoidancePreset.LOW_ALTITUDE),
        )
        drone.avoidance.add_provider(provider)

        def on_lidar(frame: PointCloudFrame) -> None:
            """接收米制标准点云，并把同一帧交给 LiDAR 障碍物 Provider。"""
            provider.observe(frame)

        unsubscribe = drone.sensors.subscribe(descriptor.sensor_id, on_lidar)
        safe = run_avoidance_scenario(drone, sensor_label="lidar")
    finally:
        state = drone.state.snapshot()
        if not safe and (state.in_air is True or state.armed is True):
            drone.return_to_launch()
        if unsubscribe is not None:
            unsubscribe()
        if provider is not None:
            drone.avoidance.remove_provider(provider)
        drone.close()


if __name__ == "__main__":
    main()
