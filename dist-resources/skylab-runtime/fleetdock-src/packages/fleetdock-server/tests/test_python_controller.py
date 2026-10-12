from __future__ import annotations

import time
import unittest

from fleetdock import DeviceFactory, DeviceMetadata
from fleetdock_server.controller_ui import _Action, _MotionInput
from fleetdock_server import ControlMode, FleetDockServer
from fleetdock_server import PythonController


class _FakeDisplay:
    def __init__(self, events) -> None:
        self.events = list(events)
        self.snapshots = []
        self.opened = False
        self.closed = False
        self.idle_polls = 0

    def open(self, title: str) -> None:
        self.opened = True

    def draw(self, snapshot) -> None:
        self.snapshots.append(snapshot)

    def poll(self, delay_ms: int):
        time.sleep(0.02)
        if self.events:
            return (self.events.pop(0),)
        self.idle_polls += 1
        return ()

    def should_close(self) -> bool:
        return not self.events and self.idle_polls >= 15

    def close(self) -> None:
        self.closed = True


class PythonControllerTests(unittest.TestCase):
    def test_python_ui_controls_mock_drone_only_through_websocket(self) -> None:
        drone = (
            DeviceFactory.create()
            .mock_drone()
            .metadata(DeviceMetadata(device_id="drone-001"))
            .build()
        )
        server = FleetDockServer(close_devices=False)
        server.register(drone)
        endpoint = server.start()
        display = _FakeDisplay(
            (
                _Action.CONNECT,
                _Action.UNLOCK,
                _Action.TAKEOFF,
                _MotionInput(frozenset({_Action.FORWARD, _Action.YAW_LEFT})),
                _MotionInput(frozenset()),
                _Action.HOVER,
            )
        )
        controller = PythonController(
            endpoint,
            "drone-001",
            mode=ControlMode.SIMULATION,
            display_factory=lambda: display,
        )
        try:
            controller.run()
            self.assertTrue(display.opened)
            self.assertTrue(display.closed)
            self.assertGreater(drone.adapter.position["x"], 0.0)
            self.assertNotEqual(drone.adapter.yaw_deg, 0.0)
            self.assertIsNone(drone._manual_control_session)
            self.assertTrue(any(snapshot.product == drone.product_key for snapshot in display.snapshots))
        finally:
            controller.close()
            server.close()
            drone.close()


if __name__ == "__main__":
    unittest.main()
