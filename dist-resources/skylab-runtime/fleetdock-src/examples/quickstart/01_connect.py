"""连接 PX4 SITL/真机并读取标准状态；默认监听 UDP 14555。"""

import os

from fleetdock.api import DeviceFactory, DeviceMetadata


def main() -> None:
    """创建 PX4 无人机，连接、打印状态并释放 MAVSDK 资源。"""
    endpoint = os.getenv("FLEETDOCK_PX4_ENDPOINT", "udpin://0.0.0.0:14555")
    drone = (
        DeviceFactory.create()
        .px4_generic()
        .params(endpoint=endpoint)
        .metadata(DeviceMetadata(device_id="px4-001", name="快速开始无人机"))
        .build()
    )
    try:
        print(drone.connect())
        print(drone.get_status())
    finally:
        drone.close()


if __name__ == "__main__":
    main()
