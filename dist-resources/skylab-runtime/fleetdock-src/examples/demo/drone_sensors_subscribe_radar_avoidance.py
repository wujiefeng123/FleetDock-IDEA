"""显式订阅 Radar 检测与轨迹并执行动态障碍避障演练。"""

from __future__ import annotations

import os

from _projectairsim_logging import configure_projectairsim_console_logging
from _sensor_avoidance_scenario import run_avoidance_scenario
from fleetdock import DeviceFactory, RadarFrame, SensorKind, avoidance


def main() -> None:
    """执行“订阅标准 Radar 帧—转换动态障碍—绕障—返航”的完整剧本。"""

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

        descriptor = drone.sensors.get(SensorKind.RADAR)
        provider = avoidance.RadarObstacleProvider(descriptor, pose_provider=drone.state.snapshot)
        drone.avoidance.add_provider(provider)

        def on_radar(frame: RadarFrame) -> None:
            """接收标准 Radar 检测和轨迹，并交给动态障碍物 Provider。"""
            provider.observe(frame)

        unsubscribe = drone.sensors.subscribe(descriptor.sensor_id, on_radar)
        safe = run_avoidance_scenario(drone, sensor_label="radar")
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
