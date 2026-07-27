"""使用 PX4 和 Plan 执行圆形航线，展示完整任务结果。"""

import os

from fleetdock.api import DeviceFactory, Plan
from fleetdock.routes import Route


def main() -> None:
    """连接 PX4，起飞后执行五米半径圆形航线，最后返航并释放资源。"""
    endpoint = os.getenv("FLEETDOCK_PX4_ENDPOINT", "udpin://0.0.0.0:14555")
    drone = DeviceFactory.create().px4_generic().params(endpoint=endpoint).build()
    route = Route.circle(radius_m=5.0, segments=12, name="快速开始圆形航线")
    try:
        print(drone.connect())
        result = (
            Plan(drone)
            .wait_until_ready(timeout_s=60.0)
            .unlock()
            .takeoff(altitude=10.0)
            .route(route, altitude=10.0, speed=2.0)
            .return_to_launch()
            .run()
        )
        print(result)
    finally:
        print("清理返航：", drone.return_to_launch())
        drone.close()


if __name__ == "__main__":
    main()
