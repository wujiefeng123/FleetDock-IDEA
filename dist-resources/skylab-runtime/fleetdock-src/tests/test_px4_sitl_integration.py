"""可选 PX4 SITL 烟雾测试；默认跳过，设置 FLEETDOCK_RUN_PX4_SITL=1 后执行。"""

import os
import time
import unittest

from fleetdock import DeviceFactory, DeviceMetadata
from fleetdock.debug_controller import _Action


class _SitlControllerDisplay:
    """按控制器可用状态依次模拟常用按钮，并等待返航落地。"""

    def __init__(self) -> None:
        self.snapshot = None
        self.phase = 0
        self.phase_started_s = time.monotonic()
        self.started_s = self.phase_started_s
        self.done = False
        self.actions: list[str] = []

    def open(self, title: str) -> None:
        self.title = title

    def draw(self, snapshot) -> None:
        self.snapshot = snapshot

    def poll(self, delay_ms: int):
        time.sleep(delay_ms / 1000.0)
        snapshot = self.snapshot
        if snapshot is None:
            return ()
        now_s = time.monotonic()

        def fire(action: _Action, next_phase: int):
            self.actions.append(action.value)
            self.phase = next_phase
            self.phase_started_s = now_s
            return (action,)

        if self.phase == 0 and _Action.CONNECT in snapshot.enabled:
            return fire(_Action.CONNECT, 1)
        if self.phase == 1 and _Action.UNLOCK in snapshot.enabled:
            return fire(_Action.UNLOCK, 2)
        if self.phase == 2 and _Action.TAKEOFF in snapshot.enabled:
            return fire(_Action.TAKEOFF, 3)
        if (
            self.phase == 3
            and _Action.FORWARD in snapshot.enabled
            and (snapshot.relative_altitude_m or 0.0) >= 3.0
        ):
            return fire(_Action.FORWARD, 4)
        if self.phase == 4 and now_s - self.phase_started_s >= 2.0:
            return fire(_Action.RELEASE, 5)
        if self.phase == 5 and _Action.HOVER in snapshot.enabled:
            return fire(_Action.HOVER, 6)
        if self.phase == 6 and _Action.YAW_RIGHT in snapshot.enabled:
            return fire(_Action.YAW_RIGHT, 7)
        if self.phase == 7 and now_s - self.phase_started_s >= 2.0:
            return fire(_Action.RELEASE, 8)
        if self.phase == 8 and _Action.RTL in snapshot.enabled:
            return fire(_Action.RTL, 9)
        if self.phase == 9 and snapshot.in_air is False and snapshot.armed is False:
            self.done = True
        if now_s - self.started_s > 100.0:
            raise TimeoutError(f"SITL 控制器按钮序列超时，当前阶段：{self.phase}")
        return ()

    def should_close(self) -> bool:
        return self.done

    def close(self) -> None:
        return None


@unittest.skipUnless(os.getenv("FLEETDOCK_RUN_PX4_SITL") == "1", "未启用 PX4 SITL 集成测试")
class Px4SitlIntegrationTests(unittest.TestCase):
    def test_connect_and_readiness_snapshot(self) -> None:
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

    def test_debug_controller_button_sequence(self) -> None:
        endpoint = os.getenv("FLEETDOCK_PX4_ENDPOINT", "udpin://0.0.0.0:14555")
        drone = (
            DeviceFactory.create()
            .px4_generic()
            .params(endpoint=endpoint, timeout_s=20.0)
            .metadata(DeviceMetadata(device_id="px4-sitl-controller"))
            .build()
        )
        display = _SitlControllerDisplay()
        drone.debug._controller_display_factory = lambda: display
        try:
            drone.debug.controller(camera=False, title="FleetDock SITL Controller Test")
            self.assertTrue(display.done)
            self.assertEqual(
                display.actions,
                [
                    "connect",
                    "unlock",
                    "takeoff",
                    "forward",
                    "release",
                    "hover",
                    "yaw_right",
                    "release",
                    "rtl",
                ],
            )
        finally:
            if drone.state.snapshot().in_air is True:
                drone.return_to_launch()
            drone.close()


if __name__ == "__main__":
    unittest.main()
