"""让 PX4 无人机起飞后使用强类型本地目标移动并降落。"""

import os

from fleetdock.api import DeviceFactory, Plan, RelativePoint3D


def main() -> None:
    """执行就绪、起飞和本地位置移动；坐标单位为米且 z 轴向上为正。"""
    endpoint = os.getenv("FLEETDOCK_PX4_ENDPOINT", "udpin://0.0.0.0:14555")
    drone = DeviceFactory.create().px4_generic().params(endpoint=endpoint).build()
    try:
        print(drone.connect())
        result = (
            Plan(drone)
            .wait_until_ready(timeout_s=60.0)
            .takeoff(altitude=10.0)
            .move(RelativePoint3D(x_m=3.0, y_m=0.0, z_m=10.0))
            .ensure_land()
            .run()
        )
        print(result)
    finally:
        print("清理降落：", drone.land())
        drone.close()


if __name__ == "__main__":
    main()
