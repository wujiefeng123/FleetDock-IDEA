"""让 PX4 无人机完成就绪检查、解锁、起飞确认和返航。"""

import os

from fleetdock.api import DeviceFactory, Plan


def main() -> None:
    """执行最小安全飞行计划；任务结果成功表示各步骤已按 Plan 语义确认。"""
    endpoint = os.getenv("FLEETDOCK_PX4_ENDPOINT", "udpin://0.0.0.0:14555")
    drone = DeviceFactory.create().px4_generic().params(endpoint=endpoint).build()
    try:
        print(drone.connect())
        result = Plan(drone).wait_until_ready(timeout_s=60.0).unlock().takeoff(altitude=10.0).return_to_launch().run()
        print(result)
    finally:
        print("清理返航：", drone.return_to_launch())
        drone.close()


if __name__ == "__main__":
    main()
