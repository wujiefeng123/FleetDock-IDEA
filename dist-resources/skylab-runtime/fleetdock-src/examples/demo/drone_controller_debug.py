"""把 Project AirSim 相机桥接为 RTSP，并打开 PX4 无人机图形调试控制器。"""

import os

from fleetdock import DeviceFactory, SensorKind
from fleetdock.simulation import ProjectAirSimRtspBridge

from _projectairsim_logging import configure_projectairsim_console_logging


def main() -> None:
    """启动仿真视频桥接并打开标准控制器，窗口关闭后释放全部 SDK 和媒体资源。"""
    configure_projectairsim_console_logging()
    bridge = ProjectAirSimRtspBridge(
        os.getenv("PROJECTAIRSIM_DRONE_NAME", "Drone1"),
        rtsp_uri=os.getenv("FLEETDOCK_CAMERA_STREAM_URI"),
    )
    with bridge:
        drone = (
            DeviceFactory.create()
            .px4_generic()
            .params(
                endpoint=os.getenv("FLEETDOCK_PX4_ENDPOINT", "udpin://0.0.0.0:14555"),
                camera_stream_uri=bridge.stream_uri,
                camera_stream_source=bridge.sensor_source,
            )
            .build()
        )
        with drone:
            drone.debug.controller(
                camera_subscribe=lambda handler: drone.sensors.subscribe(
                    SensorKind.RGB_CAMERA,
                    handler,
                ),
            )


if __name__ == "__main__":
    main()
