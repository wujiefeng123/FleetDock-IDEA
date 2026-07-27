"""把 Project AirSim 相机桥接为 RTSP，并通过标准传感器订阅观察飞行过程。"""

import os

from fleetdock import DeviceFactory, Plan, SensorKind
from fleetdock.simulation import ProjectAirSimRtspBridge

from _projectairsim_logging import configure_projectairsim_console_logging


TAKEOFF_ALTITUDE_M = 8.0


def main() -> None:
    """启动仿真视频桥接，显示起飞和返航过程中的机载画面。"""
    configure_projectairsim_console_logging()
    endpoint = os.getenv("FLEETDOCK_PX4_ENDPOINT", "udpin://0.0.0.0:14555")
    bridge = ProjectAirSimRtspBridge(
        os.getenv("PROJECTAIRSIM_DRONE_NAME", "Drone1"),
        rtsp_uri=os.getenv("FLEETDOCK_CAMERA_STREAM_URI"),
    )

    def on_frame(frame) -> None:
        """演示业务回调可以直接读取同一份标准图像帧。"""
        if frame.sequence % 300 == 0:
            print(f"摄像头帧：{frame.sequence}")

    with bridge:
        drone = (
            DeviceFactory.create()
            .px4_generic()
            .params(
                endpoint=endpoint,
                timeout_s=30.0,
                camera_stream_uri=bridge.stream_uri,
                camera_stream_source=bridge.sensor_source,
            )
            .build()
        )
        with drone:
            print(drone.connect())
            dashboard = drone.debug.dashboard(title="FleetDock 无人机摄像头")
            unsubscribe_camera = drone.sensors.subscribe(SensorKind.RGB_CAMERA, dashboard.handler(on_frame))
            try:
                flight = (
                    Plan(drone)
                    .unlock()
                    .takeoff(TAKEOFF_ALTITUDE_M)
                    .hover()
                    .ensure_return_to_launch(timeout_s=240.0)
                    .start()
                )
                print(flight.wait(timeout_s=300.0))
            finally:
                unsubscribe_camera()
                dashboard.close()


if __name__ == "__main__":
    main()
