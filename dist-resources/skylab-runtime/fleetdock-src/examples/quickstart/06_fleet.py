"""连接两架 PX4 SITL 无人机并使用 Fleet 汇总连接和状态结果。"""

import os

from fleetdock.api import DeviceFactory, DeviceMetadata, Fleet


def build_drone(device_id: str, endpoint: str):
    """按业务编号和 MAVLink 监听端点创建 PX4 标准无人机。"""
    return (
        DeviceFactory.create()
        .px4_generic()
        .params(endpoint=endpoint)
        .metadata(DeviceMetadata(device_id=device_id))
        .build()
    )


def main() -> None:
    """批量连接两个 PX4 端点并查询状态，最后关闭整个 Fleet。"""
    endpoints = os.getenv(
        "FLEETDOCK_PX4_FLEET_ENDPOINTS",
        "udpin://0.0.0.0:14555,udpin://0.0.0.0:14556",
    ).split(",")
    fleet = Fleet([build_drone(f"px4-{index + 1}", endpoint) for index, endpoint in enumerate(endpoints)])
    try:
        print(fleet.connect())
        for drone in fleet.drones():
            print(drone.metadata.device_id, drone.diagnostics())
    finally:
        fleet.close()


if __name__ == "__main__":
    main()
