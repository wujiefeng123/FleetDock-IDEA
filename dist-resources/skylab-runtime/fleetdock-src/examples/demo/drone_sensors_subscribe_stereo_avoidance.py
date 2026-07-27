"""显式订阅双目图像、后台计算视差并执行低空避障演练。"""

from __future__ import annotations

import os

from _projectairsim_logging import configure_projectairsim_console_logging
from _sensor_avoidance_scenario import run_avoidance_scenario
from fleetdock import DeviceFactory, SensorKind, StereoFrame, avoidance


def main() -> None:
    """执行“订阅标准双目帧—计算障碍物—自动绕障—返航”的完整剧本。"""

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

        descriptor = drone.sensors.get(SensorKind.STEREO_CAMERA)
        provider = avoidance.StereoObstacleProvider(descriptor, pose_provider=drone.state.snapshot)
        drone.avoidance.add_provider(provider)

        def on_stereo(frame: StereoFrame) -> None:
            """接收已配对的标准双目帧，并交给双目障碍物 Provider。"""
            provider.observe(frame)

        unsubscribe = drone.sensors.subscribe(descriptor.sensor_id, on_stereo)
        safe = run_avoidance_scenario(drone, sensor_label="stereo")
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
