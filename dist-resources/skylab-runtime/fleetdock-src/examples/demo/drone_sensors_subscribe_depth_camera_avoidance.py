"""显式订阅米制深度图并执行低空螺旋避障演练。"""

from __future__ import annotations

import os

from _projectairsim_logging import configure_projectairsim_console_logging
from _sensor_avoidance_scenario import run_avoidance_scenario
from fleetdock import (
    DeviceFactory,
    ImageFrame,
    SensorKind,
    avoidance,
)


def main() -> None:
    """执行“订阅标准深度图—转换障碍物—自动绕障—返航”的完整剧本。"""

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

        descriptor = drone.sensors.get(SensorKind.DEPTH_CAMERA)
        provider = avoidance.DepthCameraObstacleProvider(
            descriptor,
            pose_provider=drone.state.snapshot,
            config=avoidance.DepthObstacleConfig.for_preset(avoidance.VisionAvoidancePreset.LOW_ALTITUDE),
        )
        drone.avoidance.add_provider(provider)

        def on_depth(frame: ImageFrame) -> None:
            """接收米制标准深度图，并交给深度障碍物 Provider。"""
            provider.observe(frame)

        unsubscribe = drone.sensors.subscribe(descriptor.sensor_id, on_depth)
        safe = run_avoidance_scenario(drone, sensor_label="depth-camera")
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
