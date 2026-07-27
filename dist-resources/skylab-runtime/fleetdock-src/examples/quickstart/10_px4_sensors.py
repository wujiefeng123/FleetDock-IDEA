"""连接 PX4，发现并订阅多类标准飞控传感器。"""

import os
import time

from fleetdock.api import DeviceFactory
from fleetdock.sensors import BarometerSample, GnssSample, ImuSample, SensorKind


def main() -> None:
    """连接 PX4 后打印十秒 IMU、GNSS 和气压计数据，最后释放全部订阅。"""
    endpoint = os.getenv("FLEETDOCK_PX4_ENDPOINT", "udpin://0.0.0.0:14555")
    drone = DeviceFactory.create().px4_generic().params(endpoint=endpoint).build()
    unsubscribes = []
    try:
        print(drone.connect())
        for sensor in drone.sensors.list():
            print("发现传感器：", sensor.sensor_id, sensor.kind.value)

        def on_imu(sample: ImuSample) -> None:
            """打印机体 FRD 坐标系的角速度和线加速度。"""
            print(sample.angular_velocity_rad_s, sample.linear_acceleration_m_s2)

        def on_gnss(sample: GnssSample) -> None:
            """打印经纬度和来源提供的绝对高度，缺失高度保持为 None。"""
            print(sample.latitude_deg, sample.longitude_deg, sample.absolute_altitude_m)

        def on_barometer(sample: BarometerSample) -> None:
            """打印帕斯卡气压和可选气压高度。"""
            print(sample.absolute_pressure_pa, sample.pressure_altitude_m)

        unsubscribes.extend(
            (
                drone.sensors.subscribe(SensorKind.IMU, on_imu),
                drone.sensors.subscribe(SensorKind.GNSS, on_gnss),
                drone.sensors.subscribe(SensorKind.BAROMETER, on_barometer),
            )
        )
        time.sleep(10.0)
    finally:
        for unsubscribe in reversed(unsubscribes):
            unsubscribe()
        drone.close()


if __name__ == "__main__":
    main()
