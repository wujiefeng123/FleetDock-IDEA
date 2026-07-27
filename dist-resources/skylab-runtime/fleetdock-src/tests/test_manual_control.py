import time
import unittest

from fleetdock import (
    CapabilityName,
    DeviceFactory,
    ManualControlOptions,
)


class ManualControlTests(unittest.TestCase):
    def ready_drone(self):
        drone = DeviceFactory.create().mock_drone().build()
        self.assertTrue(drone.connect().ok)
        self.assertTrue(drone.unlock().ok)
        self.assertTrue(drone.takeoff(5.0).ok)
        deadline = time.monotonic() + 1.0
        while not drone.readiness_for(CapabilityName.MANUAL_CONTROL).ready:
            if time.monotonic() >= deadline:
                self.fail("mock drone did not publish manual-control readiness state")
            time.sleep(0.005)
        return drone

    def test_session_clamps_body_velocity_and_protects_on_close(self) -> None:
        drone = self.ready_drone()
        options = ManualControlOptions(
            refresh_interval_s=0.01,
            input_timeout_s=0.2,
            max_horizontal_speed_m_s=2.0,
            max_vertical_speed_m_s=0.5,
            max_yaw_rate_deg_s=20.0,
        )
        session = drone.manual_control(options)
        try:
            session.update(forward_m_s=10.0, up_m_s=3.0, yaw_rate_deg_s=80.0)
            deadline = time.monotonic() + 1.0
            while session.status().last_response is None:
                if time.monotonic() >= deadline:
                    self.fail("manual control did not send a command")
                time.sleep(0.005)
            status = session.status()
            self.assertEqual(status.input.forward_m_s, 2.0)
            self.assertEqual(status.input.up_m_s, 0.5)
            self.assertEqual(status.input.yaw_rate_deg_s, 20.0)
            self.assertTrue(status.last_response.ok)
        finally:
            session.close()
            drone.close()

    def test_input_timeout_triggers_hover_and_allows_new_input(self) -> None:
        drone = self.ready_drone()
        session = drone.manual_control(ManualControlOptions(refresh_interval_s=0.01, input_timeout_s=0.05))
        try:
            session.update(forward_m_s=1.0)
            deadline = time.monotonic() + 1.0
            while not session.status().failsafe_triggered:
                if time.monotonic() >= deadline:
                    self.fail("manual control watchdog did not trigger")
                time.sleep(0.005)
            self.assertIn("hover", session.status().last_response.message.lower())
            session.update(right_m_s=0.5)
            self.assertFalse(session.status().failsafe_triggered)
        finally:
            session.close()
            drone.close()

    def test_only_one_session_can_control_a_device(self) -> None:
        drone = self.ready_drone()
        session = drone.manual_control()
        try:
            with self.assertRaisesRegex(RuntimeError, "already active"):
                drone.manual_control()
        finally:
            session.close()
            drone.close()

    def test_robot_profile_does_not_claim_drone_manual_control(self) -> None:
        dog = DeviceFactory.create().mock_robot_dog().build()
        try:
            self.assertFalse(dog.has_capability(CapabilityName.MANUAL_CONTROL))
            with self.assertRaisesRegex(RuntimeError, "does not declare capability"):
                dog.manual_control()
        finally:
            dog.close()


if __name__ == "__main__":
    unittest.main()
