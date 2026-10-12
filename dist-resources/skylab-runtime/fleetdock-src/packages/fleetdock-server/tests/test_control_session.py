from __future__ import annotations

import time
import unittest

from fleetdock import CapabilityName, CommandName, DeviceFactory
from fleetdock_server import ControlMode, InteractiveControlOptions, InteractiveControlSession, ManualAction


class InteractiveControlSessionTests(unittest.TestCase):
    def ready_drone(self):
        drone = DeviceFactory.create().mock_drone().build()
        self.assertTrue(drone.connect().ok)
        self.assertTrue(drone.unlock().ok)
        self.assertTrue(drone.takeoff(5.0).ok)
        deadline = time.monotonic() + 1.0
        while not drone.readiness_for(CapabilityName.MANUAL_CONTROL).ready:
            if time.monotonic() >= deadline:
                self.fail("mock drone did not become ready for manual control")
            time.sleep(0.005)
        return drone

    def test_press_release_are_idempotent_and_compose_multiple_axes(self) -> None:
        drone = self.ready_drone()
        control = InteractiveControlSession(
            drone,
            InteractiveControlOptions(mode=ControlMode.SIMULATION, input_timeout_s=0.2),
        )
        try:
            control.press(ManualAction.FORWARD)
            control.press(ManualAction.FORWARD)
            control.press(ManualAction.ASCEND)
            snapshot = control.snapshot()
            self.assertEqual(snapshot.active_actions, {ManualAction.FORWARD, ManualAction.ASCEND})
            self.assertEqual(snapshot.input.forward_m_s, 3.0)
            self.assertEqual(snapshot.input.up_m_s, 0.6)

            control.release(ManualAction.FORWARD)
            control.release(ManualAction.FORWARD)
            snapshot = control.snapshot()
            self.assertEqual(snapshot.active_actions, {ManualAction.ASCEND})
            self.assertEqual(snapshot.input.forward_m_s, 0.0)
            self.assertEqual(snapshot.input.up_m_s, 0.6)
        finally:
            control.close()
            drone.close()

    def test_opposite_actions_cancel_and_diagonal_motion_is_normalized(self) -> None:
        drone = self.ready_drone()
        control = InteractiveControlSession(drone, InteractiveControlOptions(mode=ControlMode.SIMULATION))
        try:
            control.sync_actions({ManualAction.FORWARD, ManualAction.BACKWARD})
            self.assertEqual(control.snapshot().input.forward_m_s, 0.0)
            control.sync_actions({ManualAction.FORWARD, ManualAction.RIGHT})
            selected = control.snapshot().input
            self.assertAlmostEqual(selected.forward_m_s, 3.0 * 2**-0.5)
            self.assertAlmostEqual(selected.right_m_s, 3.0 * 2**-0.5)
        finally:
            control.close()
            drone.close()

    def test_control_modes_reject_unsafe_commands(self) -> None:
        drone = DeviceFactory.create().mock_drone().build()
        bench = InteractiveControlSession(drone, InteractiveControlOptions(mode=ControlMode.BENCH))
        try:
            with self.assertRaisesRegex(PermissionError, "does not allow takeoff"):
                bench.execute(CommandName.TAKEOFF, {"altitude": 5.0})
        finally:
            bench.close()
        live = InteractiveControlSession(drone, InteractiveControlOptions(mode=ControlMode.LIVE))
        try:
            with self.assertRaisesRegex(PermissionError, "force unlock"):
                live.execute(CommandName.UNLOCK, {"force": True})
        finally:
            live.close()
            drone.close()

    def test_watchdog_clears_actions_and_does_not_resume_on_late_heartbeat(self) -> None:
        drone = self.ready_drone()
        control = InteractiveControlSession(
            drone,
            InteractiveControlOptions(mode=ControlMode.SIMULATION, input_timeout_s=0.08),
        )
        try:
            control.press(ManualAction.FORWARD)
            deadline = time.monotonic() + 1.0
            while not control.snapshot().failsafe_triggered:
                if time.monotonic() >= deadline:
                    self.fail("interactive control watchdog did not trigger")
                time.sleep(0.01)
            self.assertEqual(control.snapshot().active_actions, frozenset())
            control.heartbeat()
            self.assertEqual(control.snapshot().active_actions, frozenset())
        finally:
            control.close()
            drone.close()

    def test_robot_dog_truthfully_reports_no_manual_control(self) -> None:
        dog = DeviceFactory.create().mock_robot_dog().build()
        control = InteractiveControlSession(dog, InteractiveControlOptions(mode=ControlMode.SIMULATION))
        try:
            self.assertEqual(control.supported_manual_actions, frozenset())
            with self.assertRaisesRegex(ValueError, "not supported"):
                control.press(ManualAction.FORWARD)
            response = control.execute(CommandName.CONNECT)
            self.assertTrue(response.ok)
        finally:
            control.close()
            dog.close()


if __name__ == "__main__":
    unittest.main()
