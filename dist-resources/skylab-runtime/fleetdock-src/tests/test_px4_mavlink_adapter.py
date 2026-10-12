import asyncio
import sys
import threading
import time
import types
import unittest
from dataclasses import replace
from unittest.mock import patch

from fleetdock import (
    AdapterFactory,
    CapabilityName,
    DeviceFactory,
    Drone,
    EventType,
    FirmwareVersion,
    GpsPoint,
    ProductType,
    RelativeMove3D,
    RelativePoint3D,
    Route,
    SensorKind,
    TakeoffMethod,
    TakeoffOptions,
)
from fleetdock.adapters import MavsdkClient, Px4MavlinkAdapter
from fleetdock.errors import FleetDockError
from fleetdock.models import ErrorCode
from fleetdock.registry import ProductRegistry, default_registry


class _FakeMavsdkConnectionClient:
    def __init__(self) -> None:
        self.calls = []

    def connect(self) -> None:
        self.calls.append(("connect",))


class FakeMavsdkClient(_FakeMavsdkConnectionClient):
    def arm(self, force: bool = False) -> None:
        if force:
            self.calls.append(("arm", True))
        else:
            self.calls.append(("arm",))

    def disarm(self) -> None:
        self.calls.append(("disarm",))

    def takeoff(self, altitude: float| None) -> None:
        self.calls.append(("takeoff", altitude))

    def land(self) -> None:
        self.calls.append(("land",))

    def return_to_launch(self) -> None:
        self.calls.append(("return_to_launch",))

    def hold(self) -> None:
        self.calls.append(("hold",))

    def goto_location(self, latitude_deg: float, longitude_deg: float, altitude_m: float, yaw_deg: float) -> None:
        self.calls.append(("goto_location", latitude_deg, longitude_deg, altitude_m, yaw_deg))

    def set_velocity(self, x: float, y: float, z: float, speed: float, yaw_deg=None) -> None:
        if yaw_deg is None:
            self.calls.append(("set_velocity", x, y, z, speed))
        else:
            self.calls.append(("set_velocity", x, y, z, speed, yaw_deg))

    def set_body_velocity(
        self,
        forward_m_s: float,
        right_m_s: float,
        up_m_s: float,
        yaw_rate_deg_s: float,
    ) -> None:
        self.calls.append((
            "set_body_velocity",
            forward_m_s,
            right_m_s,
            up_m_s,
            yaw_rate_deg_s,
        ))

    def set_position(self, x_m: float, y_m: float, z_m: float, yaw_deg=None) -> None:
        self.calls.append(("set_position", x_m, y_m, z_m, yaw_deg))

    def stop(self) -> None:
        self.calls.append(("stop",))

    def emergency_stop(self) -> None:
        self.calls.append(("emergency_stop",))

    def get_status(self):
        return {"calls": list(self.calls)}


class SensorCapableFakeMavsdkClient(FakeMavsdkClient):
    def available_sensor_streams(self):
        return ("imu",)

    def subscribe_sensor(self, stream_name, handler):
        self.calls.append(("subscribe_sensor", stream_name))
        return lambda: None


class DemandAwareFakeMavsdkClient(FakeMavsdkClient):
    def __init__(self) -> None:
        super().__init__()
        self.active_demands: list[frozenset[str]] = []

    def acquire_telemetry(self, stream_names, *, owner="consumer"):
        selected = frozenset(stream_names)
        self.active_demands.append(selected)
        self.calls.append(("acquire_telemetry", owner, tuple(sorted(selected))))
        released = False

        def release() -> None:
            nonlocal released
            if released:
                return
            released = True
            self.active_demands.remove(selected)
            self.calls.append(("release_telemetry", owner, tuple(sorted(selected))))

        return release


class FailingConnectClient(FakeMavsdkClient):
    def connect(self) -> None:
        self.calls.append(("connect",))
        raise RuntimeError("temporary MAVSDK connect failure")


class CustomPx4Adapter(Px4MavlinkAdapter):
    pass


class FakeMavsdkSystem:
    latest = None

    def __init__(self) -> None:
        self.calls = []
        self.core = self
        self.action = self
        self.info = self
        self.offboard = self
        self.telemetry = self
        FakeMavsdkSystem.latest = self

    async def connect(self, system_address: str) -> None:
        self.calls.append(("connect", system_address))

    async def connection_state(self):
        yield types.SimpleNamespace(is_connected=True)

    async def get_version(self):
        return types.SimpleNamespace(flight_sw_major=1)

    async def position(self):
        yield types.SimpleNamespace(
            relative_altitude_m=8.5,
            absolute_altitude_m=108.5,
            latitude_deg=31.2,
            longitude_deg=121.5,
        )

    async def set_rate_position(self, rate_hz: float) -> None:
        self.calls.append(("set_rate_position", rate_hz))

    async def velocity_ned(self):
        yield types.SimpleNamespace(north_m_s=1.0, east_m_s=2.0, down_m_s=-0.5)

    async def position_velocity_ned(self):
        yield types.SimpleNamespace(
            position=types.SimpleNamespace(north_m=3.0, east_m=4.0, down_m=-8.5)
        )

    async def set_rate_position_velocity_ned(self, rate_hz: float) -> None:
        self.calls.append(("set_rate_position_velocity_ned", rate_hz))

    async def home(self):
        yield types.SimpleNamespace(latitude_deg=31.2, longitude_deg=121.5, absolute_altitude_m=100.0)

    async def attitude_euler(self):
        yield types.SimpleNamespace(roll_deg=1.0, pitch_deg=2.0, yaw_deg=3.0)

    async def set_rate_attitude_euler(self, rate_hz: float) -> None:
        self.calls.append(("set_rate_attitude_euler", rate_hz))

    async def battery(self):
        yield types.SimpleNamespace(remaining_percent=0.8, voltage_v=24.0)

    async def set_rate_battery(self, rate_hz: float) -> None:
        self.calls.append(("set_rate_battery", rate_hz))

    async def armed(self):
        yield True

    async def in_air(self):
        yield True

    async def landed_state(self):
        yield types.SimpleNamespace(name="IN_AIR")

    async def flight_mode(self):
        yield types.SimpleNamespace(name="HOLD")

    async def health(self):
        yield types.SimpleNamespace(
            is_local_position_ok=True,
            is_global_position_ok=True,
            is_home_position_ok=True,
        )

    async def arm(self) -> None:
        self.calls.append(("arm",))

    async def arm_force(self) -> None:
        self.calls.append(("arm_force",))

    async def disarm(self) -> None:
        self.calls.append(("disarm",))

    async def set_takeoff_altitude(self, altitude: float) -> None:
        self.calls.append(("set_takeoff_altitude", altitude))

    async def takeoff(self) -> None:
        self.calls.append(("takeoff",))

    async def land(self) -> None:
        self.calls.append(("land",))

    async def return_to_launch(self) -> None:
        self.calls.append(("return_to_launch",))

    async def hold(self) -> None:
        self.calls.append(("hold",))

    async def goto_location(
        self,
        latitude_deg: float,
        longitude_deg: float,
        altitude_m: float,
        yaw_deg: float,
    ) -> None:
        self.calls.append(("goto_location", latitude_deg, longitude_deg, altitude_m, yaw_deg))

    async def kill(self) -> None:
        self.calls.append(("kill",))

    async def set_velocity_ned(self, velocity) -> None:
        self.calls.append(("set_velocity_ned", *velocity.values))

    async def set_velocity_body(self, velocity) -> None:
        self.calls.append(("set_velocity_body", *velocity.values))

    async def set_position_ned(self, position) -> None:
        self.calls.append(("set_position_ned", *position.values))

    async def start(self) -> None:
        self.calls.append(("offboard_start",))

    async def stop(self) -> None:
        self.calls.append(("offboard_stop",))


class FakeVelocityNedYaw:
    def __init__(self, north_m_s: float, east_m_s: float, down_m_s: float, yaw_deg: float) -> None:
        self.values = (north_m_s, east_m_s, down_m_s, yaw_deg)


class FakePositionNedYaw:
    def __init__(self, north_m: float, east_m: float, down_m: float, yaw_deg: float) -> None:
        self.values = (north_m, east_m, down_m, yaw_deg)


class FakeVelocityBodyYawspeed:
    def __init__(self, forward_m_s: float, right_m_s: float, down_m_s: float, yaw_rate_deg_s: float) -> None:
        self.values = (forward_m_s, right_m_s, down_m_s, yaw_rate_deg_s)


class OneWayFakeMavsdkSystem(FakeMavsdkSystem):
    async def get_version(self):
        raise TimeoutError("no command response")


class DelayedVersionFakeMavsdkSystem(FakeMavsdkSystem):
    def __init__(self) -> None:
        super().__init__()
        self.version_attempts = 0

    async def get_version(self):
        self.version_attempts += 1
        if self.version_attempts == 1:
            raise TimeoutError("command channel not ready")
        return types.SimpleNamespace(flight_sw_major=1)


class Px4MavlinkAdapterTest(unittest.TestCase):
    def test_filtered_event_subscription_transparently_acquires_and_releases_stream(self) -> None:
        client = DemandAwareFakeMavsdkClient()
        drone = Drone.from_managed_object(ProductType.PX4_GENERIC, client)

        unsubscribe = drone.events.on(EventType.GLOBAL_POSITION_UPDATED, lambda _event: None)
        self.assertIn(frozenset({"position"}), client.active_demands)
        unsubscribe()
        self.assertNotIn(frozenset({"position"}), client.active_demands)
        drone.close()

    def test_client_starts_only_baseline_streams_until_demand_is_acquired(self) -> None:
        client = MavsdkClient("udpin://0.0.0.0:14550", timeout_s=1.0, link_profile="low_bandwidth")
        client._system = FakeMavsdkSystem()
        client._connected = True
        received = []
        try:
            client.start_telemetry(lambda name, data, native: received.append((name, data, native)))
            deadline = time.monotonic() + 1.0
            while not client._BASELINE_TELEMETRY_STREAMS.issubset({name for name, _, _ in received}):
                if time.monotonic() >= deadline:
                    self.fail(f"baseline telemetry did not arrive: {received}")
                time.sleep(0.01)
            self.assertFalse({"position", "local_position", "home", "velocity_ned", "attitude"} & {
                name for name, _, _ in received
            })

            release = client.acquire_telemetry({"position", "attitude"}, owner="test.filtered")
            deadline = time.monotonic() + 1.0
            while not {"position", "attitude"}.issubset({name for name, _, _ in received}):
                if time.monotonic() >= deadline:
                    self.fail(f"demand telemetry did not arrive: {received}")
                time.sleep(0.01)
            release()
            diagnostics = client.telemetry_diagnostics()
            self.assertNotIn("position", diagnostics["requested_streams"])
            self.assertEqual(client.link_profile, "low_bandwidth")
        finally:
            client.close()

    def test_standard_command_timeout_limits_mavsdk_wait(self) -> None:
        client = MavsdkClient("udpin://0.0.0.0:14550", timeout_s=1.0)

        async def slow_operation() -> None:
            await asyncio.sleep(1.0)

        started_at_s = time.monotonic()
        try:
            with client._use_command_timeout(0.01):
                with self.assertRaisesRegex(TimeoutError, "within 0.010 seconds"):
                    client._run(slow_operation())
            self.assertLess(time.monotonic() - started_at_s, 0.5)
        finally:
            client.close()

    def test_telemetry_callbacks_are_dispatched_in_order_off_event_loop(self) -> None:
        received = []

        def callback(stream_name, data, native_data) -> None:
            received.append((stream_name, data["sequence"], native_data, threading.get_ident()))

        async def run_dispatch() -> int:
            event_loop_thread_id = threading.get_ident()
            callback_queue = asyncio.Queue()
            dispatcher = asyncio.create_task(
                MavsdkClient._dispatch_telemetry_callbacks(callback_queue, callback)
            )
            callback_queue.put_nowait(("first", {"sequence": 1}, "native-1"))
            callback_queue.put_nowait(("second", {"sequence": 2}, "native-2"))
            await callback_queue.join()
            dispatcher.cancel()
            await asyncio.gather(dispatcher, return_exceptions=True)
            return event_loop_thread_id

        event_loop_thread_id = asyncio.run(run_dispatch())

        self.assertEqual([(item[0], item[1]) for item in received], [("first", 1), ("second", 2)])
        self.assertTrue(all(item[3] != event_loop_thread_id for item in received))

    def test_sensor_stream_requests_rate_before_consuming_samples(self) -> None:
        client = MavsdkClient("udpin://0.0.0.0:14550", timeout_s=0.1)
        calls = []
        received = []

        async def set_rate(rate_hz):
            calls.append(("rate", rate_hz))

        async def stream():
            calls.append(("stream",))
            yield "sample"

        asyncio.run(
            client._consume_sensor_stream(
                "imu",
                stream,
                received.append,
                rate_setter=set_rate,
                rate_hz=20.0,
            )
        )

        self.assertEqual(calls, [("rate", 20.0), ("stream",)])
        self.assertEqual(received, ["sample"])

        async def reject_rate(rate_hz):
            raise RuntimeError("rate unsupported")

        asyncio.run(
            client._consume_sensor_stream(
                "imu",
                stream,
                received.append,
                rate_setter=reject_rate,
                rate_hz=20.0,
            )
        )
        self.assertEqual(received, ["sample", "sample"])
        client.close()

    def test_control_telemetry_continues_when_rate_request_is_rejected(self) -> None:
        client = MavsdkClient("udpin://0.0.0.0:14550", timeout_s=0.1)
        received = []

        async def reject_rate(rate_hz):
            raise RuntimeError(f"rate {rate_hz} unsupported")

        async def stream():
            yield {"x": 3.0}

        asyncio.run(
            client._consume_telemetry(
                "local_position",
                stream,
                lambda stream_name, data, native: received.append((stream_name, data, native)),
                dict,
                rate_setter=reject_rate,
                rate_hz=5.0,
            )
        )

        self.assertEqual(received[0][0], "telemetry_error")
        self.assertEqual(received[0][1]["error"], "rate_configuration_failed")
        self.assertEqual(received[1], ("local_position", {"x": 3.0}, {"x": 3.0}))
        client.close()

    def test_mavsdk_client_discovers_camera_video_stream_uri(self) -> None:
        class FakeCamera:
            async def video_stream_info(self):
                yield types.SimpleNamespace(
                    video_stream_info=types.SimpleNamespace(
                        settings=types.SimpleNamespace(uri="rtsp://camera.example/live")
                    )
                )

        client = MavsdkClient("udpin://0.0.0.0:14550", timeout_s=0.1)
        client._system = types.SimpleNamespace(camera=FakeCamera())
        client._connected = True
        try:
            self.assertEqual(client.camera_stream_uri(), "rtsp://camera.example/live")
            self.assertIn("camera", client.available_sensor_streams())
            self.assertIn("attitude", client.available_sensor_streams())
        finally:
            client._connected = False
            client._system = None
            client.close()

    def test_px4_device_automatically_composes_shared_sensor_backend(self) -> None:
        drone = Drone.from_managed_object(ProductType.PX4_GENERIC, SensorCapableFakeMavsdkClient())

        self.assertEqual(drone.sensors.get(SensorKind.IMU).sensor_id, "px4.imu")

        drone.close()

    def test_factory_selects_px4_mavlink_adapter_by_product(self) -> None:
        drone = Drone(
            product=ProductType.PX4_GENERIC,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )

        self.assertIsInstance(drone.adapter, Px4MavlinkAdapter)
        self.assertIsInstance(drone.adapter.client, MavsdkClient)

    def test_mavsdk_connect_rejects_one_way_heartbeat(self) -> None:
        mavsdk_module = types.ModuleType("mavsdk")
        mavsdk_module.System = OneWayFakeMavsdkSystem
        client = MavsdkClient("udpin://0.0.0.0:14550", timeout_s=1.0)

        try:
            with patch.dict(sys.modules, {"mavsdk": mavsdk_module}):
                with self.assertRaisesRegex(RuntimeError, "bidirectional MAVLink"):
                    client.connect()
        finally:
            client.close()

    def test_mavsdk_connect_retries_bidirectional_verification(self) -> None:
        mavsdk_module = types.ModuleType("mavsdk")
        mavsdk_module.System = DelayedVersionFakeMavsdkSystem
        client = MavsdkClient("udpin://0.0.0.0:14550", timeout_s=2.0)

        try:
            with patch.dict(sys.modules, {"mavsdk": mavsdk_module}):
                client.connect()
        finally:
            client.close()

        self.assertEqual(DelayedVersionFakeMavsdkSystem.latest.version_attempts, 2)

    def test_mavsdk_battery_percent_is_normalized_to_fraction(self) -> None:
        self.assertEqual(
            MavsdkClient._map_battery(
                types.SimpleNamespace(remaining_percent=80.0, voltage_v=24.0)
            ),
            {"remaining_fraction": 0.8, "voltage_v": 24.0},
        )
        self.assertEqual(
            MavsdkClient._map_battery(
                types.SimpleNamespace(remaining_percent=0.8, voltage_v=None)
            ),
            {"remaining_fraction": 0.8},
        )

    @staticmethod
    def _local_takeoff_client(*, battery_age_s: float, remaining_fraction: float = 0.8) -> MavsdkClient:
        client = MavsdkClient("udpin://0.0.0.0:14550")
        now_s = time.monotonic()
        client._latest_status = {
            "local_position": {"position_m": {"x": 0.0, "y": 0.0, "z": -0.5}},
            "attitude": {"roll_deg": 0.0, "pitch_deg": 0.0, "yaw_deg": 0.0},
            "battery": {"remaining_fraction": remaining_fraction},
        }
        client._status_updated_at = {
            "local_position": now_s,
            "attitude": now_s,
            "battery": now_s - battery_age_s,
        }
        return client

    def test_local_takeoff_allows_short_battery_telemetry_delay(self) -> None:
        client = self._local_takeoff_client(battery_age_s=6.0)
        try:
            measured_m = client._validate_local_takeoff_safety(
                TakeoffOptions(target_altitude_m=1.0),
                ground_north_m=0.0,
                ground_east_m=0.0,
                ground_down_m=0.0,
            )
            self.assertAlmostEqual(measured_m, 0.5)
        finally:
            client.close()

    def test_takeoff_precheck_keeps_strict_battery_telemetry_age(self) -> None:
        client = self._local_takeoff_client(battery_age_s=6.0)
        now_s = time.monotonic()
        client._latest_status["health"] = {"is_local_position_ok": True}
        client._status_updated_at["health"] = now_s
        try:
            with self.assertRaisesRegex(TimeoutError, "required takeoff telemetry"):
                asyncio.run(
                    client._wait_for_takeoff_telemetry(
                        deadline=time.monotonic() + 0.01,
                        options=TakeoffOptions(target_altitude_m=1.0),
                        cancellation_check=None,
                    )
                )
        finally:
            client.close()

    def test_local_takeoff_rejects_expired_battery_telemetry(self) -> None:
        client = self._local_takeoff_client(battery_age_s=16.0)
        try:
            with self.assertRaisesRegex(RuntimeError, "stale supervisory telemetry: battery"):
                client._validate_local_takeoff_safety(
                    TakeoffOptions(target_altitude_m=1.0),
                    ground_north_m=0.0,
                    ground_east_m=0.0,
                    ground_down_m=0.0,
                )
        finally:
            client.close()

    def test_local_takeoff_still_rejects_low_battery(self) -> None:
        client = self._local_takeoff_client(battery_age_s=0.0, remaining_fraction=0.1)
        try:
            with self.assertRaisesRegex(RuntimeError, "battery fell below"):
                client._validate_local_takeoff_safety(
                    TakeoffOptions(target_altitude_m=1.0),
                    ground_north_m=0.0,
                    ground_east_m=0.0,
                    ground_down_m=0.0,
                )
        finally:
            client.close()

    def test_outdoor_takeoff_handoff_stops_offboard_without_duplicate_hold(self) -> None:
        system = FakeMavsdkSystem()
        client = MavsdkClient("udpin://0.0.0.0:14550")
        client._system = system
        client._connected = True
        client._offboard_started = True
        now_s = time.monotonic()
        client._latest_status["health"] = {
            "is_global_position_ok": True,
            "is_home_position_ok": True,
        }
        client._latest_status["position"] = {"latitude_deg": 31.2, "longitude_deg": 121.5}
        client._latest_status["home"] = {"absolute_altitude_m": 100.0}
        client._status_updated_at.update({"health": now_s, "position": now_s, "home": now_s})

        reason = asyncio.run(
            client._complete_takeoff_handoff(
                system,
                method=TakeoffMethod.LOCAL_POSITION,
                health_max_age_s=5.0,
            )
        )

        self.assertEqual(system.calls, [("offboard_stop",)])
        self.assertFalse(client._offboard_started)
        self.assertIn("飞控 Hold", reason)

    def test_indoor_takeoff_handoff_keeps_local_offboard_active(self) -> None:
        system = FakeMavsdkSystem()
        client = MavsdkClient("udpin://0.0.0.0:14550")
        client._system = system
        client._offboard_started = True
        now_s = time.monotonic()
        client._latest_status["health"] = {
            "is_local_position_ok": True,
            "is_global_position_ok": False,
            "is_home_position_ok": False,
        }
        client._status_updated_at["health"] = now_s

        reason = asyncio.run(
            client._complete_takeoff_handoff(
                system,
                method=TakeoffMethod.LOCAL_POSITION,
                health_max_age_s=5.0,
            )
        )

        self.assertEqual(system.calls, [])
        self.assertTrue(client._offboard_started)
        self.assertIn("Offboard 保持位置", reason)

    def test_indoor_hold_keeps_offboard_and_locks_current_local_position(self) -> None:
        system = FakeMavsdkSystem()
        client = MavsdkClient("serial://COM3:57600")
        client._system = system
        client._connected = True
        client._offboard_started = True
        now_s = time.monotonic()
        client._latest_status.update({
            "local_position": {"position_m": {"x": 2.0, "y": -3.0, "z": -1.5}},
            "attitude": {"yaw_deg": 45.0},
            "health": {
                "is_local_position_ok": True,
                "is_global_position_ok": False,
                "is_home_position_ok": False,
            },
        })
        client._status_updated_at.update({
            "local_position": now_s,
            "attitude": now_s,
            "health": now_s,
        })
        offboard_module = types.ModuleType("mavsdk.offboard")
        offboard_module.PositionNedYaw = FakePositionNedYaw

        with patch.dict(sys.modules, {"mavsdk.offboard": offboard_module}):
            asyncio.run(client._hold())

        self.assertEqual(system.calls, [("set_position_ned", 2.0, -3.0, -1.5, 45.0)])
        self.assertTrue(client._offboard_started)

    def test_indoor_hold_prestreams_before_starting_offboard(self) -> None:
        system = FakeMavsdkSystem()
        client = MavsdkClient("serial://COM3:57600")
        client._system = system
        client._connected = True
        now_s = time.monotonic()
        client._latest_status.update({
            "local_position": {"position_m": {"x": 1.0, "y": 2.0, "z": -1.0}},
            "attitude": {"yaw_deg": 30.0},
            "health": {"is_local_position_ok": True},
        })
        client._status_updated_at.update({
            "local_position": now_s,
            "attitude": now_s,
            "health": now_s,
        })
        offboard_module = types.ModuleType("mavsdk.offboard")
        offboard_module.PositionNedYaw = FakePositionNedYaw

        async def no_sleep(_delay_s: float) -> None:
            return None

        with (
            patch.dict(sys.modules, {"mavsdk.offboard": offboard_module}),
            patch.object(asyncio, "sleep", new=no_sleep),
        ):
            asyncio.run(client._hold())

        self.assertEqual(system.calls[-1], ("offboard_start",))
        self.assertEqual(system.calls.count(("set_position_ned", 1.0, 2.0, -1.0, 30.0)), 10)
        self.assertTrue(client._offboard_started)

    def test_hold_rejects_when_neither_position_chain_is_safe(self) -> None:
        client = MavsdkClient("serial://COM3:57600")
        client._system = FakeMavsdkSystem()
        client._connected = True

        with self.assertRaises(FleetDockError) as raised:
            asyncio.run(client._hold())

        self.assertEqual(raised.exception.code, ErrorCode.HOLD_STRATEGY_UNAVAILABLE)

    def test_indoor_land_skips_offboard_stop(self) -> None:
        system = FakeMavsdkSystem()
        client = MavsdkClient("udpin://0.0.0.0:14550")
        client._system = system
        client._connected = True
        client._offboard_started = True
        now_s = time.monotonic()
        client._latest_status["health"] = {
            "is_local_position_ok": True,
            "is_global_position_ok": False,
            "is_home_position_ok": False,
        }
        client._status_updated_at["health"] = now_s

        asyncio.run(client._land())

        self.assertEqual(system.calls, [("land",)])
        self.assertFalse(client._offboard_started)

    def test_indoor_takeoff_abort_lands_without_offboard_stop(self) -> None:
        system = FakeMavsdkSystem()
        client = MavsdkClient("udpin://0.0.0.0:14550")
        client._system = system
        client._connected = True
        client._offboard_started = True
        now_s = time.monotonic()
        client._latest_status.update(
            {
                "armed": {"armed": True},
                "in_air": {"in_air": True},
                "health": {
                    "is_local_position_ok": True,
                    "is_global_position_ok": False,
                    "is_home_position_ok": False,
                },
            }
        )
        client._status_updated_at["health"] = now_s

        asyncio.run(client._safe_abort_takeoff())

        self.assertEqual(system.calls, [("land",)])
        self.assertFalse(client._offboard_started)

    def test_mavsdk_client_reuses_one_system_for_standard_commands(self) -> None:
        mavsdk_module = types.ModuleType("mavsdk")
        mavsdk_module.System = FakeMavsdkSystem
        offboard_module = types.ModuleType("mavsdk.offboard")
        offboard_module.VelocityNedYaw = FakeVelocityNedYaw
        offboard_module.PositionNedYaw = FakePositionNedYaw
        offboard_module.VelocityBodyYawspeed = FakeVelocityBodyYawspeed

        with patch.dict(sys.modules, {"mavsdk": mavsdk_module, "mavsdk.offboard": offboard_module}):
            client = MavsdkClient("udpin://0.0.0.0:14550", timeout_s=1.0)
            client.connect()
            received = []
            release_all = client.acquire_telemetry(client._TELEMETRY_STREAMS, owner="test.all")
            client.start_telemetry(lambda name, data, native: received.append((name, data, native)))
            deadline = time.monotonic() + 1.0
            while len({name for name, _, _ in received}) < len(client._TELEMETRY_STREAMS):
                if time.monotonic() >= deadline:
                    self.fail(f"telemetry streams did not arrive: {received}")
                time.sleep(0.01)
            status = client.get_status()
            client.arm()
            client.disarm()
            client.takeoff(12.0)
            client.set_velocity(1.0, 2.0, 0.5, 3.0)
            client.set_velocity(0.0, 1.0, 0.0, 2.0, yaw_deg=90.0)
            client.set_body_velocity(1.0, -0.5, 0.25, 20.0)
            client.set_position(4.0, 5.0, 6.0, yaw_deg=45.0)
            client.hold()
            client.goto_location(31.2, 121.5, 120.0, 0.0)
            client.stop()
            client.land()
            client.return_to_launch()
            client.emergency_stop()
            release_all()
            client.close()

        self.assertEqual(status["relative_altitude_m"], 8.5)
        self.assertEqual(status["absolute_altitude_m"], 108.5)
        self.assertEqual({name for name, _, _ in received}, {
            "position", "home", "velocity_ned", "attitude", "battery", "armed", "in_air",
            "landed_state", "flight_mode", "health", "local_position"
        })

        rate_calls = {call for call in FakeMavsdkSystem.latest.calls if call[0].startswith("set_rate_")}
        self.assertTrue({
            ("set_rate_position_velocity_ned", 5.0),
            ("set_rate_attitude_euler", 5.0),
            ("set_rate_position", 1.0),
            ("set_rate_battery", 1.0),
        }.issubset(rate_calls))
        command_calls = [call for call in FakeMavsdkSystem.latest.calls if not call[0].startswith("set_rate_")]
        self.assertEqual(
            command_calls,
            [
                ("connect", "udpin://0.0.0.0:14550"),
                ("arm",),
                ("disarm",),
                ("set_takeoff_altitude", 12.0),
                ("takeoff",),
                (
                    "set_velocity_ned",
                    1.3093073414159544,
                    2.618614682831909,
                    -0.6546536707079772,
                    3.0,
                ),
                ("offboard_start",),
                ("set_velocity_ned", 0.0, 2.0, -0.0, 90.0),
                ("set_velocity_body", 1.0, -0.5, -0.25, 20.0),
                ("set_position_ned", 4.0, 5.0, -6.0, 45.0),
                ("offboard_stop",),
                ("goto_location", 31.2, 121.5, 120.0, 0.0),
                ("set_velocity_ned", 0.0, 0.0, -0.0, 3.0),
                ("offboard_start",),
                ("offboard_stop",),
                ("land",),
                ("return_to_launch",),
                ("kill",),
            ],
        )

    def test_standard_commands_are_translated_to_managed_mavlink_client_calls(self) -> None:
        client = FakeMavsdkClient()
        drone = Drone.from_managed_object(
            product=ProductType.PX4_GENERIC,
            managed_object=client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )

        self.assertIs(drone.native_client, client)
        self.assertTrue(drone.connect().ok)
        self.assertTrue(drone.unlock().ok)
        self.assertTrue(drone.lock().ok)
        self.assertTrue(drone.unlock().ok)
        self.assertTrue(drone.takeoff(altitude=15).ok)
        self.assertTrue(drone.set_velocity(x=1.0, y=2.0, z=-0.5, speed=3.0).ok)
        self.assertTrue(drone.set_velocity(x=0.0, y=1.0, z=0.0, speed=2.0, yaw_deg=90.0).ok)
        self.assertTrue(
            drone.set_body_velocity(
                forward_m_s=1.0,
                right_m_s=-0.5,
                up_m_s=0.25,
                yaw_rate_deg_s=20.0,
            ).ok
        )
        self.assertTrue(drone.move(RelativePoint3D(10.0, 20.0, 15.0, yaw_deg=45.0)).ok)
        drone.state._update(local_x_m=10.0)
        drone.state._update(local_y_m=20.0)
        drone.state._update(local_z_m=-15.0)
        drone.state._update(yaw_deg=90.0)
        self.assertTrue(drone.move(RelativeMove3D(forward_m=5.0, right_m=2.0, up_m=3.0)).ok)
        self.assertTrue(drone.hover().ok)
        self.assertTrue(drone.move(GpsPoint(31.2, 121.5, 120.0)).ok)
        self.assertTrue(drone.stop().ok)
        self.assertTrue(drone.return_to_launch().ok)
        self.assertTrue(drone.land().ok)

        self.assertEqual(
            client.calls,
            [
                ("connect",),
                ("arm",),
                ("disarm",),
                ("arm",),
                ("takeoff", 15.0),
                (
                    "set_velocity",
                    0.4364357804719848,
                    0.8728715609439696,
                    -0.2182178902359924,
                    3.0,
                ),
                ("set_velocity", 0.0, 1.0, 0.0, 2.0, 90.0),
                ("set_body_velocity", 1.0, -0.5, 0.25, 20.0),
                ("set_position", 10.0, 20.0, 15.0, 45.0),
                ("set_position", 8.0, 25.0, 18.0, 90.0),
                ("hold",),
                ("goto_location", 31.2, 121.5, 120.0, 0.0),
                ("stop",),
                ("return_to_launch",),
                ("land",),
            ],
        )

    def test_velocity_zero_and_invalid_values_have_consistent_semantics(self) -> None:
        client = FakeMavsdkClient()
        drone = Drone.from_managed_object(
            product=ProductType.PX4_GENERIC,
            managed_object=client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )

        self.assertTrue(drone.connect().ok)
        self.assertTrue(drone.set_velocity(x=1.0, speed=0.0).ok)
        self.assertEqual(client.calls[-1], ("set_velocity", 0.0, 0.0, 0.0, 0.0))
        self.assertFalse(drone.set_velocity(x=1.0, speed=-1.0).ok)
        self.assertFalse(drone.set_velocity(speed=1.0).ok)
        self.assertEqual(len(client.calls), 2)

    def test_gps_route_converts_absolute_altitude_to_px4_relative_altitude(self) -> None:
        client = FakeMavsdkClient()
        drone = Drone.from_managed_object(
            product=ProductType.PX4_GENERIC,
            managed_object=client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        route = Route.polyline(
            (
                GpsPoint(31.2, 121.5, 120.0),
                GpsPoint(31.2001, 121.5001, 130.0),
            )
        )
        state = types.SimpleNamespace(
            home_absolute_altitude_m=100.0,
            latitude_deg=31.2,
            longitude_deg=121.5,
            relative_altitude_m=20.0,
        )

        items = drone.adapter._build_route_mission_items(
            route,
            state=state,
            altitude_m=None,
            speed_m_s=2.0,
        )

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["relative_altitude_m"], 20.0)
        self.assertEqual(items[1]["relative_altitude_m"], 30.0)
        drone.close()

    def test_mavsdk_mission_duration_limit_pauses_and_holds(self) -> None:
        calls = []

        class SlowMission:
            def mission_progress(self):
                async def stream():
                    await asyncio.sleep(1.0)
                    yield types.SimpleNamespace(current=0, total=1)

                return stream()

            async def pause_mission(self) -> None:
                calls.append(("pause_mission",))

        class Action:
            async def hold(self) -> None:
                calls.append(("hold",))

        system = types.SimpleNamespace(mission=SlowMission(), action=Action())
        client = object.__new__(MavsdkClient)

        completed = asyncio.run(
            client._wait_mission_complete(
                system,
                total=1,
                timeout_s=1.0,
                max_duration_s=0.01,
                progress_callback=None,
                guard_callback=None,
            )
        )

        self.assertFalse(completed)
        self.assertEqual(calls, [("pause_mission",), ("hold",)])

    def test_force_unlock_uses_managed_client_force_arm(self) -> None:
        client = FakeMavsdkClient()
        drone = Drone.from_managed_object(
            product=ProductType.PX4_GENERIC,
            managed_object=client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )

        self.assertTrue(drone.connect().ok)
        response = drone.unlock(force=True)

        self.assertTrue(response.ok)
        self.assertEqual(response.data["force"], True)
        self.assertEqual(client.calls, [("connect",), ("arm", True)])

    def test_px4_connect_rebuilds_client_after_transient_failure(self) -> None:
        clients = [FailingConnectClient(), FakeMavsdkClient()]

        def build_client(connection):
            return clients.pop(0)

        drone = Drone(
            product=ProductType.PX4_GENERIC,
            params={
                "endpoint": "udpin://0.0.0.0:14550",
                "mavsdk_client_factory": build_client,
                "connect_retry_delay_s": 0.0,
            },
        )

        self.assertTrue(drone.connect().ok)
        self.assertIsInstance(drone.adapter.client, FakeMavsdkClient)
        self.assertEqual(drone.adapter.client.calls, [("connect",)])

    def test_px4_requires_arm_before_takeoff(self) -> None:
        client = FakeMavsdkClient()
        drone = Drone.from_managed_object(
            product=ProductType.PX4_GENERIC,
            managed_object=client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )

        self.assertTrue(drone.connect().ok)
        response = drone.takeoff(altitude=15)

        self.assertFalse(response.ok)
        self.assertEqual(client.calls, [("connect",)])

    def test_px4_product_profile_tracks_capabilities_and_firmware(self) -> None:
        profile = default_registry.get(ProductType.PX4_GENERIC)

        self.assertEqual(ProductType.PX4_GENERIC.display_name, "通用 PX4 无人机")
        self.assertEqual(profile.display_name, "通用 PX4 无人机")
        self.assertIn("PX4", profile.description)
        self.assertTrue(profile.has_capability(CapabilityName.MISSION))
        self.assertTrue(profile.has_capability(CapabilityName.SAFETY_SUPERVISION))
        self.assertTrue(profile.has_capability(CapabilityName.FOLLOW_EXECUTION))
        self.assertTrue(profile.has_capability(CapabilityName.FORMATION_LAYOUT))
        self.assertTrue(profile.supports_event(EventType.SAFETY_VIOLATION_RAISED))
        self.assertTrue(profile.supports_event(EventType.SAFETY_ACTION_TRIGGERED))
        self.assertTrue(profile.supports_firmware(FirmwareVersion.PX4_V0_1))
        self.assertEqual(profile.default_firmware, FirmwareVersion.PX4_V0_1)
        self.assertIn("MAVSDK", profile.supported_firmware[FirmwareVersion.PX4_V0_1])
        self.assertEqual(profile.missing_required_params({}), ("endpoint",))

    def test_device_uses_product_default_firmware_when_not_provided(self) -> None:
        drone = Drone(
            product=ProductType.PX4_GENERIC,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )

        self.assertEqual(drone.firmware_version, FirmwareVersion.PX4_V0_1.value)

    def test_factory_selects_adapter_by_product_and_firmware(self) -> None:
        registry = ProductRegistry()
        profile = default_registry.get(ProductType.PX4_GENERIC)
        registry.register(
            replace(
                profile,
                supported_firmware={
                    **profile.supported_firmware,
                    FirmwareVersion.CUSTOM: "自定义 PX4 固件",
                },
            ),
            adapters={
                FirmwareVersion.PX4_V0_1: Px4MavlinkAdapter,
                FirmwareVersion.CUSTOM: CustomPx4Adapter,
            },
        )
        factory = AdapterFactory(registry)

        drone = Drone(
            product=ProductType.PX4_GENERIC,
            firmware_version=FirmwareVersion.CUSTOM,
            managed_object=FakeMavsdkClient(),
            adapter_factory=factory,
        )

        self.assertIsInstance(drone.adapter, CustomPx4Adapter)

    def test_unsupported_firmware_fails_during_build(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported firmware"):
            DeviceFactory.create().px4_generic().firmware(FirmwareVersion.DJI_MSDK_V5).params(
                endpoint="udpin://0.0.0.0:14550"
            ).build()

    def test_device_factory_builds_px4_drone_with_typed_params(self) -> None:
        drone = (
            DeviceFactory.create()
            .px4_generic()
            .params(endpoint="udpin://0.0.0.0:14550", timeout_s=10.0)
            .build()
        )

        self.assertIsInstance(drone, Drone)
        self.assertEqual(drone.product_key, ProductType.PX4_GENERIC.value)
        self.assertEqual(drone.connection.params["endpoint"], "udpin://0.0.0.0:14550")
        self.assertEqual(drone.connection.params["timeout_s"], 10.0)
        self.assertEqual(drone.firmware_version, FirmwareVersion.PX4_V0_1.value)

    def test_other_product_accepts_custom_firmware_string(self) -> None:
        profile = default_registry.get(ProductType.OTHER)

        self.assertTrue(profile.supports_firmware("vendor-x-fw-2026.06"))

    def test_representative_vendor_profiles_track_sdk_inputs(self) -> None:
        dji = default_registry.get(ProductType.DJI_MATRICE_350_RTK)
        unitree = default_registry.get(ProductType.UNITREE_GO2)

        self.assertEqual(dji.missing_required_params({}), ("sdk_mode",))
        self.assertIn("psdk", dji.metadata["sdk_modes"])
        self.assertEqual(unitree.missing_required_params({}), ("network_interface",))
        self.assertEqual(ProductType.UNITREE_G1.display_name, "宇树 G1 人形机器人")


if __name__ == "__main__":
    unittest.main()
