from __future__ import annotations

from dataclasses import FrozenInstanceError
import sys
import time
import types
import unittest
from unittest.mock import patch

from fleetdock import (
    DeviceFactory,
    EventType,
    Plan,
    TakeoffExecutionState,
    TakeoffMethod,
    TakeoffOptions,
    TakeoffStatus,
)
from fleetdock.adapters.px4_mavlink.client import MavsdkClient
from fleetdock.adapters.control import ControlAuthority, ControlConflictError


class ConfirmedTakeoffClient:
    def __init__(self) -> None:
        self.calls = []

    def connect(self) -> None:
        self.calls.append(("connect",))

    def execute_takeoff(self, options, *, progress_callback=None, cancellation_check=None):
        self.calls.append(("execute_takeoff", options.target_altitude_m))
        status = TakeoffStatus(
            "test-execution",
            TakeoffExecutionState.PRECHECKING,
            options.target_altitude_m,
        )
        for state, method, altitude in (
            (TakeoffExecutionState.PRECHECKING, None, None),
            (TakeoffExecutionState.STARTING, TakeoffMethod.LOCAL_POSITION, 0.0),
            (TakeoffExecutionState.ASCENDING, TakeoffMethod.LOCAL_POSITION, options.target_altitude_m / 2),
            (TakeoffExecutionState.COMPLETED, TakeoffMethod.LOCAL_POSITION, options.target_altitude_m),
        ):
            if cancellation_check is not None:
                cancellation_check()
            status = status.changed(
                state=state,
                method=method,
                current_altitude_m=altitude,
                progress=0.0 if altitude is None else altitude / options.target_altitude_m,
            )
            if progress_callback is not None:
                progress_callback(status)
        return status

    def get_status(self):
        return {}


class _PositionNedYaw:
    def __init__(self, north_m, east_m, down_m, yaw_deg) -> None:
        self.values = (north_m, east_m, down_m, yaw_deg)


class _AdaptiveOffboard:
    def __init__(self, client, calls) -> None:
        self.client = client
        self.calls = calls

    async def set_position_ned(self, position) -> None:
        self.calls.append(("set_position", *position.values))
        with self.client._status_lock:
            self.client._latest_status["local_position"] = {
                "frame": "local_ned",
                "position_m": {
                    "x": position.values[0],
                    "y": position.values[1],
                    "z": position.values[2],
                },
            }
            self.client._status_updated_at["local_position"] = time.monotonic()

    async def start(self) -> None:
        self.calls.append(("offboard_start",))

    async def stop(self) -> None:
        self.calls.append(("offboard_stop",))


class _AdaptiveAction:
    def __init__(self, client, calls) -> None:
        self.client = client
        self.calls = calls

    async def arm(self) -> None:
        self.calls.append(("arm",))
        with self.client._status_lock:
            self.client._latest_status["armed"] = {"armed": True}

    async def hold(self) -> None:
        self.calls.append(("hold",))

    async def land(self) -> None:
        self.calls.append(("land",))


class _NativeAction(_AdaptiveAction):
    async def set_takeoff_altitude(self, altitude_m) -> None:
        self.calls.append(("set_takeoff_altitude", altitude_m))

    async def takeoff(self) -> None:
        self.calls.append(("takeoff",))
        with self.client._status_lock:
            self.client._latest_status["relative_altitude_m"] = 1.0
            self.client._status_updated_at["position"] = time.monotonic()


class _NativeNoProgressAction(_NativeAction):
    async def takeoff(self) -> None:
        self.calls.append(("takeoff",))
        with self.client._status_lock:
            self.client._latest_status["local_position"] = {
                "frame": "local_ned",
                "position_m": {"x": 0.0, "y": 0.0, "z": 0.0},
            }
            self.client._latest_status["health"]["is_local_position_ok"] = True
            self.client._status_updated_at["local_position"] = time.monotonic()


class _MavlinkMessage:
    def __init__(self, *values) -> None:
        self.values = values


class _MavlinkDirect:
    def __init__(self, client, calls) -> None:
        self.client = client
        self.calls = calls

    async def send_message(self, message) -> None:
        self.calls.append(message.values)
        if message.values[0] == "SET_MODE":
            with self.client._status_lock:
                self.client._latest_status["flight_mode"] = {"flight_mode": "offboard"}


class TakeoffIntegrationTests(unittest.TestCase):
    def test_takeoff_models_are_validated_and_immutable(self) -> None:
        options = TakeoffOptions(target_altitude_m=1.0)
        self.assertEqual(options.control_rate_hz, 10.0)
        with self.assertRaises(FrozenInstanceError):
            options.target_altitude_m = 2.0  # type: ignore[misc]
        with self.assertRaises(ValueError):
            TakeoffOptions(target_altitude_m=1.0, altitude_tolerance_m=1.0)
        with self.assertRaises(ValueError):
            TakeoffOptions(target_altitude_m=1.0, min_battery_fraction=1.1)

    def test_control_authority_rejects_conflict_and_allows_safety_preemption(self) -> None:
        authority = ControlAuthority()
        cancelled = []
        with authority.acquire("route", 40, cancel=lambda: cancelled.append(True)):
            with self.assertRaises(ControlConflictError):
                with authority.acquire("move", 30):
                    pass
            with authority.acquire("land", 80, preempt=True) as lease:
                self.assertEqual(lease.owner, "land")
                self.assertEqual(cancelled, [True])
        self.assertIsNone(authority.status())

    def test_plan_automatically_uses_confirmed_adapter_without_explicit_unlock(self) -> None:
        client = ConfirmedTakeoffClient()
        drone = (
            DeviceFactory.create()
            .px4_generic()
            .managed_object(client)
            .params(endpoint="serial://COM3:57600")
            .build()
        )
        events = []
        unsubscribe = drone.events.subscribe(events.append)
        try:
            self.assertTrue(drone.connect().ok)
            result = Plan(drone).takeoff(altitude=1.0).run(step_timeout_s=2.0)
            self.assertTrue(result.ok)
            self.assertEqual(client.calls, [("connect",), ("execute_takeoff", 1.0)])
            self.assertTrue(drone.events.drain())
            event_types = [event.type for event in events]
            self.assertIn(EventType.TAKEOFF_STARTED, event_types)
            self.assertIn(EventType.TAKEOFF_STRATEGY_SELECTED, event_types)
            self.assertIn(EventType.TAKEOFF_COMPLETED, event_types)
        finally:
            unsubscribe()
            drone.close()

    def test_resolver_uses_fresh_healthy_local_position_not_endpoint_kind(self) -> None:
        client = MavsdkClient("serial://COM3:57600")
        now = time.monotonic()
        client._latest_status.update({
            "local_position": {"position_m": {"x": 0.0, "y": 0.0, "z": 0.0}},
            "attitude": {"yaw_deg": 0.0},
            "health": {"is_local_position_ok": True},
        })
        client._status_updated_at.update({
            "local_position": now,
            "attitude": now,
            "health": now,
        })
        options = TakeoffOptions(target_altitude_m=1.0)
        self.assertEqual(client._resolve_takeoff_method(options), TakeoffMethod.LOCAL_POSITION)

        client._status_updated_at["local_position"] = now - options.telemetry_max_age_s - 1.0
        self.assertEqual(client._resolve_takeoff_method(options), TakeoffMethod.AUTOPILOT)
        client.close()

    def test_local_position_takeoff_starts_control_before_arm_and_confirms_height(self) -> None:
        offboard_module = types.ModuleType("mavsdk.offboard")
        offboard_module.PositionNedYaw = _PositionNedYaw
        client = MavsdkClient("serial://COM3:57600", timeout_s=1.0)
        calls = []
        system = types.SimpleNamespace()
        system.offboard = _AdaptiveOffboard(client, calls)
        system.action = _AdaptiveAction(client, calls)
        client._system = system
        client._connected = True
        client._latest_status.update({
            "local_position": {"frame": "local_ned", "position_m": {"x": 0.0, "y": 0.0, "z": 0.0}},
            "attitude": {"roll_deg": 0.0, "pitch_deg": 0.0, "yaw_deg": 15.0},
            "health": {"is_local_position_ok": True, "is_armable": True},
            "battery": {"remaining_fraction": 0.9},
            "armed": {"armed": False},
            "in_air": {"in_air": False},
        })
        now = time.monotonic()
        client._status_updated_at.update({
            "local_position": now,
            "attitude": now,
            "health": now,
            "battery": now,
        })
        try:
            with patch.dict(sys.modules, {"mavsdk.offboard": offboard_module}):
                status = client.execute_takeoff(
                    TakeoffOptions(
                        target_altitude_m=0.3,
                        altitude_tolerance_m=0.05,
                        timeout_s=3.0,
                        climb_speed_m_s=2.0,
                        control_rate_hz=20.0,
                    )
                )
            self.assertEqual(status.state, TakeoffExecutionState.COMPLETED)
            self.assertEqual(status.method, TakeoffMethod.LOCAL_POSITION)
            self.assertLess(calls.index(("offboard_start",)), calls.index(("arm",)))
            self.assertNotIn(("offboard_stop",), calls)
            self.assertNotIn(("hold",), calls)
            self.assertTrue(client._offboard_started)
        finally:
            client._connected = False
            client._system = None
            client.close()

    def test_native_takeoff_is_selected_when_local_position_is_stale(self) -> None:
        client = MavsdkClient("udpin://0.0.0.0:14555", timeout_s=1.0)
        calls = []
        action = _NativeAction(client, calls)
        client._system = types.SimpleNamespace(
            action=action,
            offboard=types.SimpleNamespace(),
        )
        client._connected = True
        client._latest_status.update({
            "relative_altitude_m": 0.0,
            "position": {"latitude_deg": 1.0, "longitude_deg": 2.0},
            "home": {"latitude_deg": 1.0, "longitude_deg": 2.0, "absolute_altitude_m": 3.0},
            "attitude": {"roll_deg": 0.0, "pitch_deg": 0.0, "yaw_deg": 0.0},
            "health": {
                "is_armable": True,
                "is_global_position_ok": True,
                "is_home_position_ok": True,
            },
            "battery": {"remaining_fraction": 0.9},
            "armed": {"armed": False},
            "in_air": {"in_air": False},
        })
        now = time.monotonic()
        client._status_updated_at.update({
            "position": now,
            "home": now,
            "attitude": now,
            "health": now,
            "battery": now,
        })
        try:
            status = client.execute_takeoff(
                TakeoffOptions(
                    target_altitude_m=1.0,
                    altitude_tolerance_m=0.1,
                    timeout_s=2.0,
                )
            )
            self.assertEqual(status.method, TakeoffMethod.AUTOPILOT)
            self.assertEqual(
                calls,
                [
                    ("arm",),
                    ("set_takeoff_altitude", 1.0),
                    ("takeoff",),
                    ("hold",),
                ],
            )
        finally:
            client._connected = False
            client._system = None
            client.close()

    def test_raw_offboard_and_land_fallback_use_configured_system_ids(self) -> None:
        direct_module = types.ModuleType("mavsdk.mavlink_direct")
        direct_module.MavlinkMessage = _MavlinkMessage
        client = MavsdkClient(
            "serial://COM3:57600",
            sender_system_id=42,
            sender_component_id=43,
            target_system_id=7,
            target_component_id=8,
        )
        calls = []
        direct = _MavlinkDirect(client, calls)

        class RejectLand:
            async def land(self) -> None:
                raise RuntimeError("action rejected")

        client._system = types.SimpleNamespace(
            mavlink_direct=direct,
            action=RejectLand(),
            offboard=types.SimpleNamespace(),
        )
        client._connected = True
        try:
            with patch.dict(sys.modules, {"mavsdk.mavlink_direct": direct_module}):
                client._run(client._start_raw_offboard_fallback(
                    1.0,
                    2.0,
                    3.0,
                    4.0,
                    0.01,
                    time.monotonic() + 1.0,
                    None,
                ))
                client.land()
            self.assertEqual(calls[0][1:5], (42, 43, 7, 8))
            self.assertIn("SET_MODE", [message[0] for message in calls])
            nav_land = calls[-1]
            self.assertEqual(nav_land[0], "COMMAND_LONG")
            self.assertEqual(nav_land[1:5], (42, 43, 7, 8))
            self.assertIn('"command":21', nav_land[5])
        finally:
            client._connected = False
            client._system = None
            client.close()

    def test_native_no_progress_switches_once_before_liftoff(self) -> None:
        offboard_module = types.ModuleType("mavsdk.offboard")
        offboard_module.PositionNedYaw = _PositionNedYaw
        client = MavsdkClient("serial://COM3:57600", timeout_s=1.0)
        calls = []
        client._system = types.SimpleNamespace()
        client._system.offboard = _AdaptiveOffboard(client, calls)
        client._system.action = _NativeNoProgressAction(client, calls)
        client._connected = True
        client._latest_status.update({
            "relative_altitude_m": 0.0,
            "position": {"latitude_deg": 1.0, "longitude_deg": 2.0},
            "home": {"latitude_deg": 1.0, "longitude_deg": 2.0, "absolute_altitude_m": 3.0},
            "attitude": {"roll_deg": 0.0, "pitch_deg": 0.0, "yaw_deg": 0.0},
            "health": {
                "is_armable": True,
                "is_local_position_ok": False,
                "is_global_position_ok": True,
                "is_home_position_ok": True,
            },
            "battery": {"remaining_fraction": 0.9},
            "armed": {"armed": False},
            "in_air": {"in_air": False},
        })
        now = time.monotonic()
        client._status_updated_at.update({
            "position": now,
            "attitude": now,
            "health": now,
            "battery": now,
        })
        try:
            with patch.dict(sys.modules, {"mavsdk.offboard": offboard_module}):
                status = client.execute_takeoff(
                    TakeoffOptions(
                        target_altitude_m=0.2,
                        altitude_tolerance_m=0.05,
                        timeout_s=2.0,
                        climb_speed_m_s=2.0,
                        control_rate_hz=20.0,
                        native_progress_timeout_s=0.05,
                    )
                )
            self.assertEqual(status.method, TakeoffMethod.LOCAL_POSITION)
            self.assertEqual(calls.count(("takeoff",)), 1)
            self.assertIn(("offboard_start",), calls)
        finally:
            client._connected = False
            client._system = None
            client.close()


if __name__ == "__main__":
    unittest.main()
