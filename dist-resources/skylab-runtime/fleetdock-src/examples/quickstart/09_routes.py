"""为 PX4 无人机生成标准航线；本示例只检查能力，不发送飞行命令。"""

import os

from fleetdock.api import CapabilityName, DeviceFactory
from fleetdock.routes import Route


def main() -> None:
    """创建 PX4 设备并生成三种航线，打印能力、航线类型和航点数量。"""
    endpoint = os.getenv("FLEETDOCK_PX4_ENDPOINT", "udpin://0.0.0.0:14555")
    drone = DeviceFactory.create().px4_generic().params(endpoint=endpoint).build()
    routes = (
        Route.circle(radius_m=5.0, segments=12, name="圆形巡检"),
        Route.grid(width_m=10.0, height_m=6.0, spacing_m=2.0, name="网格覆盖"),
        Route.spiral(max_radius_m=8.0, spacing_m=2.0, turns=2.0, name="螺旋搜索"),
    )
    try:
        print("PX4 支持标准航线：", drone.has_capability(CapabilityName.ROUTE))
        for route in routes:
            print(f"{route.name}: pattern={route.pattern.value}, waypoints={len(route.waypoints)}")
    finally:
        drone.close()


if __name__ == "__main__":
    main()
