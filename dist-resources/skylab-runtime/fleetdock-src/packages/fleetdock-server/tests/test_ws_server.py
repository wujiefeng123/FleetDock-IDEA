from __future__ import annotations

import json
import time
import unittest

from fleetdock import (
    DeviceFactory,
    DeviceMetadata,
    ImuSample,
    MockSensorBackend,
    SensorDescriptor,
    SensorKind,
    Vector3,
)
from fleetdock_server import ControlMode, FleetDockClient, FleetDockServer, ManualAction
from websockets.sync.client import connect


class FleetDockWebSocketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.drone = (
            DeviceFactory.create()
            .mock_drone()
            .metadata(DeviceMetadata(device_id="drone-001", name="WS测试无人机"))
            .build()
        )
        self.sensor_backend = MockSensorBackend((SensorDescriptor("imu.primary", SensorKind.IMU),))
        self.drone.sensors.register_backend(self.sensor_backend)
        self.server = FleetDockServer(close_devices=False)
        self.server.register(self.drone)
        self.endpoint = self.server.start()

    def tearDown(self) -> None:
        self.server.close()
        self.drone.close()

    def test_server_starts_and_client_executes_control_flow(self) -> None:
        with FleetDockClient(self.endpoint) as client:
            welcome = client.connect(device_id="drone-001")
            self.assertEqual(welcome["type"], "session.welcome")
            self.assertEqual(welcome["devices"][0]["device_id"], "drone-001")

            lease = client.acquire_control("drone-001", mode=ControlMode.SIMULATION)
            self.assertEqual(lease["type"], "control.acquired")
            self.assertEqual(lease["horizontal_speed_m_s"], 3.0)
            self.assertEqual(lease["vertical_speed_m_s"], 0.6)
            configured = client.configure_manual(
                horizontal_speed_m_s=4.5,
                vertical_speed_m_s=0.8,
            )
            self.assertEqual(configured["state"]["horizontal_speed_m_s"], 4.5)
            self.assertEqual(configured["state"]["vertical_speed_m_s"], 0.8)
            self.assertTrue(client.submit_command("connect")["response"]["ok"])
            self.assertTrue(client.submit_command("unlock")["response"]["ok"])
            self.assertTrue(client.submit_command("takeoff", {"altitude": 5.0})["response"]["ok"])

            pressed = client.press(ManualAction.FORWARD)
            self.assertEqual(pressed["state"]["active_actions"], ["forward"])
            self.assertEqual(pressed["state"]["input"]["forward_m_s"], 4.5)
            time.sleep(0.15)
            released = client.release(ManualAction.FORWARD)
            self.assertEqual(released["state"]["active_actions"], [])
            self.assertGreater(self.drone.adapter.position["x"], 0.0)

            state = client.get_device_state("drone-001")
            self.assertTrue(state["state"]["in_air"])

    def test_second_client_cannot_take_existing_control_lease(self) -> None:
        first = FleetDockClient(self.endpoint)
        second = FleetDockClient(self.endpoint)
        try:
            first.connect()
            second.connect()
            first.acquire_control("drone-001", mode=ControlMode.SIMULATION)
            with self.assertRaisesRegex(RuntimeError, "PERMISSION_DENIED"):
                second.acquire_control("drone-001", mode=ControlMode.SIMULATION)
        finally:
            second.close()
            first.close()

    def test_sensor_subscription_delivers_typed_standard_sample(self) -> None:
        with FleetDockClient(self.endpoint) as client:
            client.connect()
            sensors = client.list_sensors("drone-001")
            self.assertEqual(sensors["sensors"][0]["sensor_id"], "imu.primary")
            subscription_id = client.subscribe_sensor("drone-001", "imu.primary")
            self.sensor_backend.publish(
                ImuSample(
                    sensor_id="imu.primary",
                    kind=SensorKind.IMU,
                    sequence=1,
                    angular_velocity_rad_s=Vector3(0.1, 0.2, 0.3),
                    linear_acceleration_m_s2=Vector3(0.0, 0.0, 9.81),
                )
            )
            sample = client.receive_telemetry(timeout_s=1.0)
            self.assertEqual(sample["type"], "sensor.sample")
            self.assertEqual(sample["subscription_id"], subscription_id)
            self.assertEqual(sample["sample"]["sensor_id"], "imu.primary")
            client.unsubscribe_sensor("drone-001", subscription_id)

    def test_disconnect_releases_manual_control(self) -> None:
        client = FleetDockClient(self.endpoint)
        client.connect()
        client.acquire_control("drone-001", mode=ControlMode.SIMULATION)
        client.submit_command("connect")
        client.submit_command("unlock")
        client.submit_command("takeoff", {"altitude": 5.0})
        client.press(ManualAction.FORWARD)
        client.close()
        deadline = time.monotonic() + 1.0
        while self.drone._manual_control_session is not None:
            if time.monotonic() >= deadline:
                self.fail("disconnect did not release manual control")
            time.sleep(0.01)

    def test_missing_heartbeat_expires_control_lease(self) -> None:
        self.server.close()
        self.server = FleetDockServer(close_devices=False, lease_timeout_s=0.25)
        self.server.register(self.drone)
        self.endpoint = self.server.start()

        raw = connect(f"{self.endpoint}/ws/control")
        try:
            raw.send(
                json.dumps(
                    {
                        "version": 1,
                        "type": "control.acquire",
                        "request_id": "raw-acquire",
                        "device_id": "drone-001",
                        "mode": "simulation",
                    }
                )
            )
            acquired = json.loads(raw.recv())
            self.assertEqual(acquired["type"], "control.acquired")
            time.sleep(0.45)

            with FleetDockClient(self.endpoint) as replacement:
                replacement.connect()
                lease = replacement.acquire_control("drone-001", mode=ControlMode.SIMULATION)
                self.assertEqual(lease["type"], "control.acquired")
        finally:
            raw.close()


if __name__ == "__main__":
    unittest.main()
