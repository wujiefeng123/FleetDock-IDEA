"""可选 PX4 SITL 烟雾测试；默认跳过，设置 FLEETDOCK_RUN_PX4_SITL=1 后执行。"""

import os
import unittest

from fleetdock import DeviceFactory, DeviceMetadata


@unittest.skipUnless(os.getenv("FLEETDOCK_RUN_PX4_SITL") == "1", "未启用 PX4 SITL 集成测试")
class Px4SitlIntegrationTests(unittest.TestCase):
    """验证核心 SDK 可以连接 PX4 SITL 并读取标准状态。"""

    def test_connect_and_readiness_snapshot(self) -> None:
        """连接 SITL、读取一次标准状态，并在结束时关闭设备。"""

        endpoint = os.getenv("FLEETDOCK_PX4_ENDPOINT", "udpin://0.0.0.0:14555")
        drone = (
            DeviceFactory.create()
            .px4_generic()
            .params(endpoint=endpoint, timeout_s=20.0)
            .metadata(DeviceMetadata(device_id="px4-sitl-ci"))
            .build()
        )
        try:
            self.assertTrue(drone.connect().ok)
            self.assertIsNotNone(drone.state.snapshot())
        finally:
            drone.close()


if __name__ == "__main__":
    unittest.main()
