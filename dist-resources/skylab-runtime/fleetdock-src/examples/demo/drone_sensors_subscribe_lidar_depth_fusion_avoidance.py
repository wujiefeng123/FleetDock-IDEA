"""显式订阅 LiDAR 与深度相机并执行多 Provider 融合避障演练。"""

from __future__ import annotations

import os

from _projectairsim_logging import configure_projectairsim_console_logging
from _sensor_avoidance_scenario import run_avoidance_scenario
from fleetdock import (
    DeviceFactory,
    ImageFrame,
    PointCloudFrame,
    SensorKind,
    avoidance,
)


def main() -> None:
    """分别订阅 LiDAR 和深度图，让两个 Provider 共同提供标准障碍物。"""

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
    obstacle_providers = []
    unsubscribes = []
    safe = False
    try:
        response = drone.connect()
        if not response.ok:
            raise RuntimeError(response.message)

        lidar_descriptor = drone.sensors.get(SensorKind.LIDAR)
        depth_descriptor = drone.sensors.get(SensorKind.DEPTH_CAMERA)
        lidar = avoidance.LidarObstacleProvider(
            lidar_descriptor,
            pose_provider=drone.state.snapshot,
            config=avoidance.LidarObstacleConfig.for_preset(avoidance.LidarAvoidancePreset.LOW_ALTITUDE),
        )
        obstacle_providers.append(lidar)
        depth = avoidance.DepthCameraObstacleProvider(
            depth_descriptor,
            pose_provider=drone.state.snapshot,
            config=avoidance.DepthObstacleConfig.for_preset(avoidance.VisionAvoidancePreset.LOW_ALTITUDE),
        )
        obstacle_providers.append(depth)
        drone.avoidance.add_provider(lidar).add_provider(depth)

        def on_lidar(frame: PointCloudFrame) -> None:
            """把标准 LiDAR 点云交给 LiDAR Provider。"""
            lidar.observe(frame)

        def on_depth(frame: ImageFrame) -> None:
            """把标准米制深度图交给深度 Provider。"""
            depth.observe(frame)

        unsubscribes.extend(
            (
                drone.sensors.subscribe(lidar_descriptor.sensor_id, on_lidar),
                drone.sensors.subscribe(depth_descriptor.sensor_id, on_depth),
            )
        )
        safe = run_avoidance_scenario(drone, sensor_label="lidar-depth-fusion")
    finally:
        state = drone.state.snapshot()
        if not safe and (state.in_air is True or state.armed is True):
            drone.return_to_launch()
        for unsubscribe in reversed(unsubscribes):
            unsubscribe()
        for provider in reversed(obstacle_providers):
            drone.avoidance.remove_provider(provider)
        drone.close()


if __name__ == "__main__":
    main()
