import unittest

from fleetdock import (
    CapabilityName,
    DeviceMetadata,
    Drone,
    EventHub,
    GpsPoint,
    MessageEventType,
    ProductType,
    RelativeMove2D,
    RobotDog,
)


class FakeDroneClient:
    def connect(self) -> None:
        pass

    def arm(self, force: bool = False) -> None:
        pass

    def takeoff(self, altitude: float) -> None:
        self.altitude = altitude

    def land(self) -> None:
        pass

    def set_velocity(self, x: float, y: float, z: float, speed: float) -> None:
        pass

    def stop(self) -> None:
        pass

    def emergency_stop(self) -> None:
        pass

    def get_status(self):
        return {"altitude": getattr(self, "altitude", 0.0)}


class MinimalLoopTest(unittest.TestCase):
    def test_drone_unlock_takeoff_loop(self) -> None:
        drone = Drone(
            product=ProductType.PX4_GENERIC,
            params={"endpoint": "udpin://0.0.0.0:14550"},
            managed_object=FakeDroneClient(),
            metadata=DeviceMetadata(device_id="drone-test", name="test-drone"),
        )

        self.assertEqual(drone.metadata.device_id, "drone-test")
        self.assertTrue(drone.connect().ok)
        self.assertTrue(drone.unlock().ok)
        takeoff = drone.takeoff(altitude=8)

        self.assertTrue(takeoff.ok)
        self.assertEqual(takeoff.data["altitude"], 8)
        self.assertTrue(drone.get_status().data["flying"])

    def test_robot_dog_move_loop(self) -> None:
        dog = RobotDog(
            product=ProductType.UNITREE_GO2,
            params={"network_interface": "eth0"},
            metadata=DeviceMetadata(device_id="dog-test", name="test-robot-dog"),
        )

        self.assertTrue(dog.connect().ok)
        self.assertTrue(dog.stand().ok)
        move = dog.move(RelativeMove2D(forward_m=1.0))

        self.assertTrue(move.ok)
        self.assertEqual(move.data["position"]["x"], 1.0)

        unsupported = dog.move(GpsPoint(31.2, 121.5, 20.0))
        self.assertFalse(unsupported.ok)

    def test_event_hub_custom_topic_subscription(self) -> None:
        hub = EventHub()
        received = []

        hub.subscribe(MessageEventType.CUSTOM, received.append, topic="mission.created")
        hub.publish_custom("mission.created", {"mission_id": "mission-test"})

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].payload["mission_id"], "mission-test")

    def test_product_enum_exposes_capabilities(self) -> None:
        drone = Drone(
            product=ProductType.PX4_GENERIC,
            params={"endpoint": "udpin://0.0.0.0:14550"},
            managed_object=FakeDroneClient(),
        )

        self.assertTrue(drone.has_capability(CapabilityName.TAKEOFF))
        self.assertFalse(drone.has_capability(CapabilityName.SIT))


if __name__ == "__main__":
    unittest.main()
