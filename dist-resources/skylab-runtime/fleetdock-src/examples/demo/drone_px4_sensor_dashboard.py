"""显式订阅 PX4 标准传感器，在 HUD 中观察无人机飞行与返航过程。"""

import os
import time

from fleetdock import DeviceFactory, Plan, SensorKind


TAKEOFF_ALTITUDE_M = 12.0
HUD_OBSERVE_DURATION_S = 15.0


def main() -> None:
    """订阅 IMU、磁力计、GNSS 和气压计，把标准样本交给 HUD，并执行起飞、悬停及返航。"""
    endpoint = os.getenv("FLEETDOCK_PX4_ENDPOINT", "udpin://0.0.0.0:14555")
    drone = DeviceFactory.create().px4_generic().params(endpoint=endpoint, timeout_s=30.0).build()

    with drone:
        response = drone.connect()
        if not response.ok:
            raise RuntimeError(response.message)

        dashboard = drone.debug.dashboard(title="FleetDock 无人机 HUD")

        def on_sensor(sample) -> None:
            """演示业务回调处理标准样本后，再把同一对象交给 HUD。"""
            if sample.sequence % 50 == 0:
                print(f"传感器样本：{sample.sensor_id} #{sample.sequence}")
            dashboard(sample)

        unsubscribes = []
        rtl_requested = False
        try:
            for sensor_kind in (
                SensorKind.IMU,
                SensorKind.MAGNETOMETER,
                SensorKind.GNSS,
                SensorKind.BAROMETER,
            ):
                unsubscribes.append(drone.sensors.subscribe(sensor_kind, on_sensor))

            takeoff = (
                Plan(drone)
                .wait_until_ready(timeout_s=120.0, require_global_position=True)
                .unlock()
                .takeoff(TAKEOFF_ALTITUDE_M)
                .start()
            )
            takeoff_result = takeoff.wait(timeout_s=180.0)
            print(takeoff_result)
            if takeoff_result.ok:
                time.sleep(HUD_OBSERVE_DURATION_S)
                rtl_response = drone.return_to_launch()
                rtl_requested = rtl_response.ok
                print(rtl_response)
        finally:
            if drone.state.snapshot().armed and not rtl_requested:
                print(drone.return_to_launch())
            for unsubscribe in reversed(unsubscribes):
                unsubscribe()
            dashboard.close()


if __name__ == "__main__":
    main()
