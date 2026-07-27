import threading
import types
import unittest
from dataclasses import FrozenInstanceError

from fleetdock import (
    BatteryUpdatedEvent,
    CustomEvent,
    DeviceEventStream,
    EventCategory,
    EventType,
    Events,
    EventSource,
    ExtensionEventType,
    GlobalPositionUpdatedEvent,
    NavigationEventType,
    PowerEventType,
    SafetyViolationRaisedEvent,
    VendorEvent,
)


class DeviceEventStreamTest(unittest.TestCase):
    def test_stream_delivers_all_events_in_order_and_isolates_handler_errors(self) -> None:
        stream = DeviceEventStream(
            EventSource(
                device_id="drone-001",
                product="px4.generic",
                device_category="drone",
                adapter="Px4MavlinkAdapter",
            )
        )
        received = []
        delivered = threading.Event()

        def failing_handler(event) -> None:
            raise RuntimeError("handler failed")

        def handler(event) -> None:
            received.append(event)
            if len(received) == 3:
                delivered.set()

        stream.subscribe(failing_handler)
        unsubscribe = stream.subscribe(handler)
        native_data = object()
        stream.publish(
            EventType.GLOBAL_POSITION_UPDATED,
            EventCategory.TELEMETRY,
            {
                "latitude_deg": 31.2,
                "longitude_deg": 121.5,
                "absolute_altitude_m": 108.5,
                "relative_altitude_m": 8.5,
            },
        )
        stream.publish_vendor(
            "vendor.px4.estimator_status.updated",
            EventCategory.TELEMETRY,
            {"valid": True},
            native_data=native_data,
        )
        stream.publish_custom("custom.demo.checkpoint_reached", {"checkpoint_id": "p1"})

        self.assertTrue(delivered.wait(1.0))
        self.assertEqual([event.sequence for event in received], [1, 2, 3])
        self.assertIs(received[0].type, EventType.GLOBAL_POSITION_UPDATED)
        self.assertIsInstance(received[0], GlobalPositionUpdatedEvent)
        self.assertFalse(hasattr(received[0], "payload"))
        self.assertEqual(received[0].position.relative_altitude_m, 8.5)
        self.assertIsInstance(received[1].type, ExtensionEventType)
        self.assertIsInstance(received[1], VendorEvent)
        self.assertEqual(received[1].type, "vendor.px4.estimator_status.updated")
        self.assertEqual(received[1].raw_data, {"valid": True})
        self.assertIs(received[1].native_data, native_data)
        self.assertEqual(received[2].category, EventCategory.CUSTOM)
        self.assertIsInstance(received[2], CustomEvent)
        self.assertEqual(received[2].data, {"checkpoint_id": "p1"})

        unsubscribe()
        stream.close()

    def test_vendor_and_custom_namespaces_are_validated(self) -> None:
        stream = DeviceEventStream(EventSource("dog-001", "mock.robot_dog", "robot_dog", "MockRobotDogAdapter"))
        with self.assertRaisesRegex(ValueError, "vendor"):
            stream.publish_vendor("px4.raw", EventCategory.TELEMETRY)
        with self.assertRaisesRegex(ValueError, "custom"):
            stream.publish_custom("mission.started")
        stream.close()

    def test_stream_can_subscribe_to_specific_event_classes_and_names(self) -> None:
        stream = DeviceEventStream(EventSource("drone-001", "px4.generic", "drone", "Px4MavlinkAdapter"))
        positions = []
        positions_by_name = []
        telemetry = []
        vendor_events = []
        delivered = threading.Event()

        def on_position(event) -> None:
            positions.append(event)

        def on_telemetry(event) -> None:
            telemetry.append(event)

        def on_vendor(event) -> None:
            vendor_events.append(event)
            delivered.set()

        stream.on(GlobalPositionUpdatedEvent, on_position)
        stream.on(EventType.GLOBAL_POSITION_UPDATED, positions_by_name.append)
        stream.subscribe((GlobalPositionUpdatedEvent, BatteryUpdatedEvent), on_telemetry)
        stream.on_name("vendor.px4.estimator_status.updated", on_vendor)
        stream.publish(
            EventType.GLOBAL_POSITION_UPDATED,
            EventCategory.TELEMETRY,
            {
                "latitude_deg": 31.2,
                "longitude_deg": 121.5,
                "absolute_altitude_m": 108.5,
                "relative_altitude_m": 8.5,
            },
        )
        stream.publish(
            EventType.BATTERY_UPDATED,
            EventCategory.TELEMETRY,
            {"remaining_fraction": 0.9},
        )
        stream.publish_vendor(
            "vendor.px4.estimator_status.updated",
            EventCategory.TELEMETRY,
            {"valid": True},
        )

        self.assertTrue(delivered.wait(1.0))
        self.assertEqual(len(positions), 1)
        self.assertEqual(len(positions_by_name), 1)
        self.assertIs(positions_by_name[0].type, EventType.GLOBAL_POSITION_UPDATED)
        self.assertIsInstance(positions[0], GlobalPositionUpdatedEvent)
        self.assertEqual([type(event) for event in telemetry], [GlobalPositionUpdatedEvent, BatteryUpdatedEvent])
        self.assertEqual(vendor_events[0].event_name, "vendor.px4.estimator_status.updated")
        stream.close()

    def test_standard_events_use_single_enum_with_compatibility_aliases(self) -> None:
        from fleetdock import __all__ as public_api

        self.assertIs(Events.EventType, EventType)
        self.assertIs(NavigationEventType, EventType)
        self.assertIs(PowerEventType, EventType)
        self.assertIs(Events.NavigationEventType.ROUTE_STARTED, EventType.ROUTE_STARTED)
        self.assertIn("EventType", public_api)
        self.assertNotIn("NavigationEventType", public_api)

    def test_safety_violation_event_uses_typed_payload(self) -> None:
        stream = DeviceEventStream(EventSource("drone-001", "px4.generic", "drone", "Px4MavlinkAdapter"))
        received = []
        stream.on(SafetyViolationRaisedEvent, received.append)

        stream.publish(
            EventType.SAFETY_VIOLATION_RAISED,
            EventCategory.ALERT,
            {
                "code": "altitude_too_high",
                "message": "超出限高",
                "severity": "blocking",
                "device_id": "drone-001",
                "action": "hover",
                "metric_value": 90.0,
                "limit_value": 80.0,
            },
        )

        self.assertTrue(stream.drain(timeout_s=1.0))
        self.assertEqual(received[0].violation.code, "altitude_too_high")
        self.assertEqual(received[0].violation.limit_value, 80.0)
        stream.close()

    def test_stream_drain_waits_for_queued_events(self) -> None:
        stream = DeviceEventStream(EventSource("drone-001", "px4.generic", "drone", "Px4MavlinkAdapter"))
        received = []
        stream.subscribe(received.append)
        stream.publish_custom("custom.test.event", {"ok": True})

        self.assertTrue(stream.drain(timeout_s=1.0))
        self.assertEqual(len(received), 1)
        stream.close()

    def test_local_position_updates_state_frame_and_freshness(self) -> None:
        from fleetdock import CoordinateFrame, Drone, ProductType

        drone = Drone(ProductType.PX4_GENERIC, params={"endpoint": "udpin://0.0.0.0:14555"})
        drone.events.publish(
            EventType.LOCAL_POSITION_UPDATED,
            EventCategory.TELEMETRY,
            {"frame": CoordinateFrame.LOCAL_NED, "position_m": {"x": 1.0, "y": 2.0, "z": -3.0}},
        )
        self.assertTrue(drone.events.drain(timeout_s=1.0))

        state = drone.state.snapshot()
        self.assertEqual(state.local_position_frame, CoordinateFrame.LOCAL_NED)
        self.assertIsNotNone(state.local_position_updated_at_s)
        with self.assertRaises(FrozenInstanceError):
            state.local_x_m = 9.0
        drone.close()


class Px4EventConversionTest(unittest.TestCase):
    def test_px4_telemetry_is_converted_and_unknown_data_is_passed_through(self) -> None:
        from tests.test_px4_mavlink_adapter import FakeMavsdkClient
        from fleetdock import Drone, ProductType

        drone = Drone.from_managed_object(ProductType.PX4_GENERIC, FakeMavsdkClient())
        received = []
        delivered = threading.Event()

        def handler(event) -> None:
            received.append(event)
            if len(received) >= 6:
                delivered.set()

        drone.events.subscribe(handler)
        self.assertTrue(drone.connect().ok)
        native_position = types.SimpleNamespace(
            latitude_deg=31.2,
            longitude_deg=121.5,
            absolute_altitude_m=108.5,
            relative_altitude_m=8.5,
            vendor_extra="preserved",
        )
        drone.adapter._handle_telemetry(
            "position",
            {
                "latitude_deg": 31.2,
                "longitude_deg": 121.5,
                "absolute_altitude_m": 108.5,
                "relative_altitude_m": 8.5,
            },
            native_position,
        )
        drone.adapter._handle_telemetry("armed", {"armed": True})
        drone.adapter._handle_telemetry("armed", {"armed": True})
        drone.adapter._handle_telemetry("estimator_status", {"valid": True})

        self.assertTrue(delivered.wait(1.0))
        event_types = [event.type for event in received]
        self.assertIn(EventType.GLOBAL_POSITION_UPDATED.value, event_types)
        self.assertEqual(event_types.count(EventType.ARMED_CHANGED.value), 1)
        self.assertIn("vendor.px4.estimator_status.updated", event_types)
        position_event = next(event for event in received if isinstance(event, GlobalPositionUpdatedEvent))
        self.assertEqual(position_event.position.relative_altitude_m, 8.5)
        self.assertEqual(position_event.original.event_name, "mavsdk.telemetry.position")
        self.assertEqual(position_event.original.raw_data["vendor_extra"], "preserved")
        self.assertIs(position_event.original.native_data, native_position)
        self.assertEqual(drone.get_status().data["telemetry"]["position"]["relative_altitude_m"], 8.5)
        drone.close()


class CrossDeviceEventTest(unittest.TestCase):
    def test_robot_dog_uses_shared_event_stream_and_legged_events(self) -> None:
        from fleetdock import DeviceFactory

        dog = DeviceFactory.create().unitree_go2().params(network_interface="test0").build()
        received = []
        delivered = threading.Event()

        def handler(event) -> None:
            received.append(event)
            if event.type == EventType.VELOCITY_UPDATED:
                delivered.set()

        dog.events.subscribe(handler)
        self.assertTrue(dog.supports_event(EventType.POSTURE_CHANGED))
        self.assertIn(EventType.POSTURE_CHANGED, dog.supported_events)
        self.assertTrue(dog.connect().ok)
        self.assertTrue(dog.stand().ok)
        self.assertTrue(dog.set_velocity(x=1.0, speed=0.5).ok)
        self.assertTrue(delivered.wait(1.0))
        self.assertIn(EventType.POSTURE_CHANGED.value, [event.type for event in received])
        dog.close()


if __name__ == "__main__":
    unittest.main()
