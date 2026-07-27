"""连接 PX4 并查看产品成熟度、能力、就绪诊断和参数错误响应。"""

import os

from fleetdock.api import CapabilityName, DeviceFactory


def main() -> None:
    """读取 PX4 强类型诊断，并用非法起飞高度演示标准错误码。"""
    endpoint = os.getenv("FLEETDOCK_PX4_ENDPOINT", "udpin://0.0.0.0:14555")
    drone = DeviceFactory.create().px4_generic().params(endpoint=endpoint).build()
    try:
        print("产品成熟度：", drone.profile.maturity.value)
        print("支持起飞：", drone.has_capability(CapabilityName.TAKEOFF))
        print("连接结果：", drone.connect())
        print("设备诊断：", drone.diagnostics())
        print("起飞就绪：", drone.readiness_for(CapabilityName.TAKEOFF))

        response = drone.takeoff(altitude=-1.0)
        print("非法高度响应：", response.code.value, response.message)
    finally:
        drone.close()


if __name__ == "__main__":
    main()
