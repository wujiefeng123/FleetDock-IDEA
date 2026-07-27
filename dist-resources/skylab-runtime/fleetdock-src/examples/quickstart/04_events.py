"""连接 PX4 并订阅标准全球位置事件；无需读取 MAVSDK 原始字段。"""

import os
import time

from fleetdock.api import DeviceFactory
from fleetdock.events import EventType, GlobalPositionUpdatedEvent


def main() -> None:
    """连接后观察十秒位置遥测，最后取消订阅并关闭 MAVSDK 资源。"""
    endpoint = os.getenv("FLEETDOCK_PX4_ENDPOINT", "udpin://0.0.0.0:14555")
    drone = DeviceFactory.create().px4_generic().params(endpoint=endpoint).build()

    def on_position(event: GlobalPositionUpdatedEvent) -> None:
        """打印 WGS84 经纬度和相对 Home 高度。"""
        position = event.position
        print(position.latitude_deg, position.longitude_deg, position.relative_altitude_m)

    unsubscribe = drone.events.on(EventType.GLOBAL_POSITION_UPDATED, on_position)
    try:
        print(drone.connect())
        time.sleep(10.0)
    finally:
        unsubscribe()
        drone.close()


if __name__ == "__main__":
    main()
