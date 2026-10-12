"""在后台执行 PX4 起飞降落计划，并通过 TaskSession 等待结果。"""

import os

from fleetdock.api import DeviceFactory, Plan


def main() -> None:
    """连接 PX4 后启动异步飞行计划，等待完成并在 finally 中释放资源。"""
    endpoint = os.getenv("FLEETDOCK_PX4_ENDPOINT", "udpin://0.0.0.0:14555")
    drone = DeviceFactory.create().px4_generic().params(endpoint=endpoint).build()
    try:
        print(drone.connect())
        session = (
            Plan(drone).wait_until_ready(timeout_s=60.0).takeoff(altitude=10.0).ensure_land().start()
        )
        print("会话状态：", session.state.value)
        print("任务结果：", session.wait(timeout_s=180.0))
    finally:
        print("清理降落：", drone.land())
        drone.close()


if __name__ == "__main__":
    main()
