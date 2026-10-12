"""让具备全球定位和 Home 的 PX4 无人机完成起飞确认和返航。"""

import os

from fleetdock.api import DeviceFactory, Plan


def main() -> None:
    """执行室外/SITL 返航计划；RTL 要求有效全球定位和 Home。"""
    endpoint = os.getenv("FLEETDOCK_PX4_ENDPOINT", "udpin://0.0.0.0:14555")
    drone = DeviceFactory.create().px4_generic().params(endpoint=endpoint).build()
    try:
        print(drone.connect())
        result = (
            Plan(drone)
            .wait_until_ready(timeout_s=60.0, require_global_position=True)
            .takeoff(altitude=10.0)
            .ensure_return_to_launch()
            .run()
        )
        print(result)
    finally:
        print("清理返航：", drone.return_to_launch())
        drone.close()


if __name__ == "__main__":
    main()
