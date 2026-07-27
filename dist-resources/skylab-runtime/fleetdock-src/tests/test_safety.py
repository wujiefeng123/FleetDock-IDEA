import unittest

from fleetdock import (
    CoordinateFrame,
    DeviceMetadata,
    Drone,
    FollowExecutionOptions,
    ProductType,
    RobotDog,
    SafetyAction,
    SafetyPolicy,
    SafetySeverity,
    SafetySupervisor,
)


class SafetySupervisorTest(unittest.TestCase):
    def make_drone(self, device_id: str = "drone-001"):
        drone = Drone.from_managed_object(
            ProductType.PX4_GENERIC,
            object(),
            params={"endpoint": "udpin://0.0.0.0:14550"},
            metadata=DeviceMetadata(device_id=device_id),
        )
        drone.state._update(local_x_m=0.0)
        drone.state._update(local_y_m=0.0)
        drone.state._update(local_position_frame=CoordinateFrame.LOCAL_NED)
        return drone

    def make_robot_dog(self):
        dog = RobotDog(
            ProductType.UNITREE_GO2,
            params={"network_interface": "test0"},
            metadata=DeviceMetadata(device_id="dog-001"),
        )
        dog.state._update(local_x_m=0.0)
        dog.state._update(local_y_m=0.0)
        dog.state._update(local_position_frame=CoordinateFrame.LOCAL_ENU)
        return dog

    def test_drone_altitude_limit_blocks_command(self) -> None:
        drone = self.make_drone()
        drone.state._update(relative_altitude_m=90.0)
        supervisor = SafetySupervisor(SafetyPolicy(max_relative_altitude_m=80.0))

        decision = supervisor.evaluate_device(drone, now_s=10.0)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.violations[0].code, "altitude_too_high")
        self.assertEqual(decision.violations[0].severity, SafetySeverity.BLOCKING)
        drone.close()

    def test_robot_dog_ignores_altitude_limit_but_keeps_common_rules(self) -> None:
        dog = self.make_robot_dog()
        dog.state._update(relative_altitude_m=90.0)
        dog.state._update(battery_remaining_fraction=0.2)
        supervisor = SafetySupervisor(
            SafetyPolicy(
                max_relative_altitude_m=1.0,
                battery_warn_fraction=0.25,
                battery_return_fraction=0.1,
            )
        )

        decision = supervisor.evaluate_device(dog, now_s=10.0)

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.violations[0].code, "battery_low")
        dog.close()

    def test_fleet_spacing_blocks_close_devices(self) -> None:
        drone_a = self.make_drone()
        drone_b = self.make_drone("drone-002")
        drone_b.state._update(local_x_m=2.0)
        drone_b.state._update(local_y_m=0.0)
        supervisor = SafetySupervisor(SafetyPolicy(min_separation_m=5.0))

        decision = supervisor.evaluate_fleet_spacing([drone_a, drone_b], now_s=10.0)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.action, SafetyAction.HOVER)
        self.assertEqual(decision.violations[0].code, "min_separation_breached")
        drone_a.close()
        drone_b.close()

    def test_follow_options_keep_explicit_policy_disabled_spacing(self) -> None:
        options = FollowExecutionOptions(
            safety_policy=SafetyPolicy(
                max_distance_from_leader_m=120.0,
                min_separation_m=None,
            )
        )

        self.assertEqual(options.safety_policy.max_distance_from_leader_m, 120.0)
        self.assertIsNone(options.safety_policy.min_separation_m)


if __name__ == "__main__":
    unittest.main()
