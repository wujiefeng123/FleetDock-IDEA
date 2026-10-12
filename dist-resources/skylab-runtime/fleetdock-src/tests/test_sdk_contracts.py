import copy
from types import SimpleNamespace
import unittest

import fleetdock.event_models as event_models
from fleetdock import (
    CapabilityContractError,
    CapabilityName,
    CommandName,
    DeviceFactory,
    DeviceMetadata,
    RelativeMove2D,
    Route,
    Waypoint,
)
from fleetdock._frozen import FrozenDict, freeze_mapping, freeze_value
from fleetdock.adapters import DeviceAdapter
from fleetdock.factory import AdapterFactory


class _ContractAdapter:
    supports_native_route = DeviceAdapter.supports_native_route

    def __init__(self, commands, *, mission: bool = False) -> None:
        self.profile = SimpleNamespace(
            supported_commands=commands,
            has_capability=lambda capability: mission and capability is CapabilityName.MISSION,
        )


class SdkContractsTest(unittest.TestCase):
    def test_mock_builders_cover_drone_and_robot_dog_beginner_flow(self) -> None:
        with (
            DeviceFactory.create()
            .mock_drone()
            .metadata(DeviceMetadata(device_id="mock-drone-test"))
            .build()
        ) as drone:
            self.assertTrue(drone.connect().ok)
            self.assertTrue(drone.unlock().ok)
            self.assertTrue(drone.takeoff(2.0).ok)
            self.assertTrue(drone.set_velocity(x=1.0, y=1.0, speed=2.0).ok)
            self.assertTrue(drone.return_to_launch().ok)

        with DeviceFactory.create().mock_robot_dog().build() as dog:
            self.assertTrue(dog.connect().ok)
            self.assertTrue(dog.stand().ok)
            self.assertTrue(dog.move(RelativeMove2D(forward_m=1.0)).ok)
            self.assertTrue(dog.sit().ok)

    def test_velocity_rejects_non_finite_and_zero_direction(self) -> None:
        with DeviceFactory.create().mock_drone().build() as drone:
            drone.connect()
            self.assertFalse(drone.set_velocity(x=float("nan"), speed=1.0).ok)
            self.assertFalse(drone.set_velocity(speed=1.0).ok)
            self.assertTrue(drone.set_velocity(x=1.0, speed=0.0).ok)

    def test_command_observer_tracks_standard_command_lifecycle(self) -> None:
        observed = []
        with DeviceFactory.create().mock_drone().build() as drone:
            unsubscribe = drone.add_command_observer(
                lambda stage, command, response=None, error=None: observed.append(
                    (stage, command.command, response.ok if response else None, error)
                )
            )
            self.assertTrue(drone.connect().ok)
            unsubscribe()
            self.assertTrue(drone.unlock().ok)

        self.assertEqual(
            observed,
            [
                ("started", CommandName.CONNECT, None, None),
                ("completed", CommandName.CONNECT, True, None),
            ],
        )

    def test_adapter_contract_rejects_declared_command_without_handler(self) -> None:
        adapter = _ContractAdapter((CommandName.CONNECT,))
        with self.assertRaises(CapabilityContractError) as error:
            AdapterFactory._validate_capability_contracts(adapter)  # type: ignore[arg-type]
        self.assertEqual(error.exception.details["missing_commands"], ("connect",))

    def test_adapter_contract_rejects_mission_without_native_route(self) -> None:
        adapter = _ContractAdapter((), mission=True)
        with self.assertRaises(CapabilityContractError):
            AdapterFactory._validate_capability_contracts(adapter)  # type: ignore[arg-type]

    def test_frozen_helpers_keep_mapping_interface_and_copy_identity(self) -> None:
        frozen = FrozenDict({"nested": {"items": [1, 2]}})

        self.assertEqual(repr(frozen), "{'nested': {'items': (1, 2)}}")
        self.assertIs(copy.deepcopy(frozen), frozen)
        self.assertIs(freeze_value(frozen), frozen)
        self.assertIs(freeze_mapping(frozen), frozen)
        self.assertEqual(freeze_value({1, 2}), frozenset({1, 2}))
        self.assertEqual(len(frozen), 1)
        self.assertEqual(tuple(iter(frozen)), ("nested",))

    def test_route_length_handles_flat_altitude_and_incomplete_points(self) -> None:
        flat = Route.polyline((Waypoint(x_m=0.0, y_m=0.0), Waypoint(x_m=3.0, y_m=4.0)))
        incomplete = Route.polyline((Waypoint(x_m=0.0, y_m=0.0), Waypoint(y_m=4.0)))

        self.assertEqual(flat.estimated_length_m, 5.0)
        self.assertIsNone(incomplete.estimated_length_m)

    def test_standard_event_payloads_are_coerced_and_serialized(self) -> None:
        source = event_models.EventSource("device-1", "mock.drone", "drone", "MockDroneAdapter")
        native = object()
        event = event_models.DeviceEvent(
            event_models.EventType.WARNING_RAISED,
            event_models.EventCategory.ALERT,
            source,
            1,
            event_models.WarningInfo("wind", "风速偏高"),
            extensions={"vendor.mock": {"levels": [1, 2]}},
            original=event_models.OriginalEvent("mock", "warning", {"raw": True}, native),
        )

        serialized = event.to_dict(include_native_data=True)
        self.assertEqual(serialized["data"]["code"], "wind")
        self.assertIn("native_data", serialized["original"])
        self.assertNotIn("original", event.to_dict(include_original=False))

        payloads = {
            event_models.EventType.GLOBAL_POSITION_UPDATED: {
                "latitude_deg": 1,
                "longitude_deg": 2,
                "absolute_altitude_m": 3,
                "relative_altitude_m": 1,
            },
            event_models.EventType.LOCAL_POSITION_UPDATED: {
                "frame": "local_ned",
                "position_m": {"x": 1, "y": 2, "z": 3},
            },
            event_models.EventType.ROUTE_STARTED: {
                "route_id": "route-1",
                "route_name": "巡检",
                "pattern": "polyline",
                "waypoint_count": 2,
            },
            event_models.EventType.ROUTE_REPLANNED: {
                "route_id": "route-1",
                "route_name": "巡检",
                "reason": "obstacle",
                "detour_count": 1,
                "metadata": {"provider": "lidar"},
            },
            event_models.EventType.WAYPOINT_ACTION_REQUESTED: {
                "route_id": "route-1",
                "route_name": "巡检",
                "waypoint_id": "point-1",
                "waypoint_index": 0,
                "waypoint_count": 2,
                "x_m": 1,
                "y_m": 2,
                "z_m": 3,
                "distance_m": 0.2,
                "action": "photo",
                "metadata": {"camera": "front"},
            },
            event_models.EventType.VELOCITY_UPDATED: {
                "frame": "local_ned",
                "linear_velocity_m_s": {"x": 1, "y": 0, "z": 0},
            },
            event_models.EventType.ATTITUDE_UPDATED: {"roll_deg": 1, "pitch_deg": 2, "yaw_deg": 3},
            event_models.EventType.BATTERY_UPDATED: {
                "remaining_fraction": 0.8,
                "voltage_v": 24,
                "current_a": 2,
                "temperature_c": 30,
            },
            event_models.EventType.HEALTH_UPDATED: {"is_armable": True, "is_global_position_ok": True},
            event_models.EventType.COMMAND_STARTED: {"command": "connect", "command_id": "cmd-1"},
            event_models.EventType.COMMAND_COMPLETED: {
                "command": "connect",
                "command_id": "cmd-1",
                "ok": True,
                "code": "ok",
                "message": "完成",
            },
            event_models.EventType.ERROR_OCCURRED: {"error": "断开", "stream": "telemetry"},
            event_models.EventType.HANDLER_FAILED: {
                "event_id": "event-1",
                "event_type": "core.error.occurred",
                "handler_name": "on_event",
                "exception_type": "RuntimeError",
            },
            event_models.EventType.WARNING_RAISED: {"code": "wind", "message": "风速偏高"},
            event_models.EventType.COLLISION_RISK_RAISED: {
                "code": "collision",
                "message": "检测到障碍物",
                "severity": "warning",
                "device_id": "device-1",
                "action": "hover",
                "metric_value": 2,
                "limit_value": 3,
                "metadata": {"obstacle_id": "o-1"},
            },
            event_models.EventType.AVOIDANCE_ACTION_TRIGGERED: {
                "action": "hover",
                "device_id": "device-1",
                "violation_codes": ["collision"],
                "ok": True,
                "message": "已悬停",
            },
            event_models.EventType.OBSTACLE_SNAPSHOT_UPDATED: {
                "provider_name": "lidar",
                "obstacle_count": 2,
                "observed_at_s": 1.5,
            },
            event_models.EventType.OBSTACLE_PROVIDER_STALE: {"provider_name": "lidar", "reason": "timeout"},
            event_models.EventType.FOOT_CONTACT_UPDATED: {"contacts": {"front_left": True}},
            event_models.EventType.ARMED_CHANGED: {"previous": False, "current": True, "reason": "command"},
        }
        coerced = {
            event_type: event_models._coerce_standard_payload(event_type, payload)
            for event_type, payload in payloads.items()
        }

        property_names = {
            event_models.EventType.GLOBAL_POSITION_UPDATED: "position",
            event_models.EventType.LOCAL_POSITION_UPDATED: "position",
            event_models.EventType.ROUTE_STARTED: "progress",
            event_models.EventType.ROUTE_REPLANNED: "replan",
            event_models.EventType.WAYPOINT_ACTION_REQUESTED: "progress",
            event_models.EventType.VELOCITY_UPDATED: "velocity",
            event_models.EventType.ATTITUDE_UPDATED: "attitude",
            event_models.EventType.BATTERY_UPDATED: "battery",
            event_models.EventType.HEALTH_UPDATED: "health",
            event_models.EventType.COMMAND_STARTED: "command",
            event_models.EventType.COMMAND_COMPLETED: "command",
            event_models.EventType.ERROR_OCCURRED: "error",
            event_models.EventType.HANDLER_FAILED: "failure",
            event_models.EventType.WARNING_RAISED: "warning",
            event_models.EventType.COLLISION_RISK_RAISED: "violation",
            event_models.EventType.AVOIDANCE_ACTION_TRIGGERED: "action",
            event_models.EventType.OBSTACLE_SNAPSHOT_UPDATED: "snapshot",
            event_models.EventType.OBSTACLE_PROVIDER_STALE: "stale",
            event_models.EventType.FOOT_CONTACT_UPDATED: "contact",
            event_models.EventType.ARMED_CHANGED: "change",
        }
        for event_type, property_name in property_names.items():
            event_class = event_models.STANDARD_EVENT_CLASSES[event_type]
            typed_event = event_class(event_type, event_models.EventCategory.TELEMETRY, source, 2, coerced[event_type])
            self.assertIs(getattr(typed_event, property_name), coerced[event_type])
            self.assertIs(event_models._coerce_standard_payload(event_type, coerced[event_type]), coerced[event_type])

        vendor_type = event_models.ExtensionEventType("vendor.mock.status")
        vendor_event = event_models.VendorEvent(
            vendor_type,
            event_models.EventCategory.CUSTOM,
            source,
            3,
            {"fallback": True},
            original=event_models.OriginalEvent("mock", "status", {"raw": True}, native),
        )
        self.assertEqual(vendor_event.event_name, "vendor.mock.status")
        self.assertEqual(vendor_event.raw_data, {"raw": True})
        self.assertIs(vendor_event.native_data, native)
        custom_event = event_models.CustomEvent(
            event_models.ExtensionEventType("custom.demo.ready"),
            event_models.EventCategory.CUSTOM,
            source,
            4,
            {"ready": True},
        )
        self.assertEqual(custom_event.event_name, "custom.demo.ready")
        self.assertEqual(custom_event.data, {"ready": True})
        with self.assertRaises(ValueError):
            event_models.ExtensionEventType("invalid.event")

        self.assertIsInstance(coerced[event_models.EventType.ROUTE_REPLANNED], event_models.RouteReplannedInfo)
        self.assertIsInstance(coerced[event_models.EventType.COLLISION_RISK_RAISED], event_models.SafetyViolationInfo)
        self.assertTrue(coerced[event_models.EventType.FOOT_CONTACT_UPDATED].contacts["front_left"])


if __name__ == "__main__":
    unittest.main()
