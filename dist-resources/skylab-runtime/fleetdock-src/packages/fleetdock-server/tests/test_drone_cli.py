from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
from io import StringIO
from pathlib import Path
import tomllib
import unittest
from unittest.mock import patch

from fleetdock_server.commands import drone as drone_cli


class _FakeDevice:
    pass


class _FakeServer:
    instances: list["_FakeServer"] = []

    def __init__(self, **options) -> None:
        self.options = options
        self.registered = None
        self.ran = None
        self.closed = False
        self.instances.append(self)

    def register(self, device, *, device_id: str) -> str:
        self.registered = (device, device_id)
        return device_id

    def run(self, host: str, port: int) -> None:
        self.ran = (host, port)

    def close(self) -> None:
        self.closed = True


class _FakeRelay:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True
        if self.fail:
            raise RuntimeError("redacted media failure")

    def snapshot(self):
        return type("_Snapshot", (), {"publish_url": "https://media.example/whip?***"})()

    def close(self) -> None:
        self.closed = True


class DroneCliTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeServer.instances.clear()

    def test_drone_extra_installs_px4_runtime(self) -> None:
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        configuration = tomllib.loads(pyproject.read_text(encoding="utf-8"))

        self.assertIn("fleetdock[px4]==0.4.0", configuration["project"]["optional-dependencies"]["drone"])

    def test_windows_demos_use_crlf_without_bom_and_call_python_wrappers(self) -> None:
        examples = Path(__file__).resolve().parents[1] / "examples"
        server_content = (examples / "start_drone_server.bat").read_bytes()
        bridge_content = (examples / "start_projectairsim_rtsp_bridge.bat").read_bytes()

        for content in (server_content, bridge_content):
            self.assertFalse(content.startswith(b"\xef\xbb\xbf"))
            self.assertEqual(content.count(b"\n"), content.count(b"\r\n"))
        self.assertIn(b"src\\fleetdock-cli\\drone_server.py", server_content)
        self.assertIn(b'--endpoint "%FLEETDOCK_PX4_ENDPOINT%"', server_content)
        self.assertNotIn(b"ProjectAirSimRtspBridge", server_content)
        self.assertNotIn(b"--drone-name", server_content)
        self.assertNotIn(b"--camera-frame-rate-hz", server_content)
        self.assertNotIn(b"python -m fleetdock_server", server_content)
        self.assertIn(b"src\\fleetdock-cli\\projectairsim_rtsp_bridge.py", bridge_content)
        self.assertNotIn(b"drone_server.py", bridge_content)

    def test_python_wrapper_delegates_external_rtsp_uri_to_drone_command(self) -> None:
        script = Path(__file__).resolve().parents[1] / "src" / "fleetdock-cli" / "drone_server.py"
        spec = importlib.util.spec_from_file_location("fleetdock_drone_server_script", script)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        delegated: list[str] = []
        with patch.object(module, "run_drone_server", side_effect=delegated.extend), redirect_stdout(StringIO()):
            module.main(
                [
                    "--endpoint",
                    "udpin://0.0.0.0:14555",
                    "--device-id",
                    "drone-test",
                    "--camera-rtsp-uri",
                    "rtsp://127.0.0.1:8554/requested",
                    "--camera-source",
                    "physical",
                    "--ws-port",
                    "24000",
                    "--media-publish-url",
                    "https://media.example/whip",
                ]
            )

        self.assertEqual(delegated[delegated.index("--endpoint") + 1], "udpin://0.0.0.0:14555")
        self.assertEqual(delegated[delegated.index("--device-id") + 1], "drone-test")
        self.assertEqual(delegated[delegated.index("--ws-port") + 1], "24000")
        self.assertEqual(
            delegated[delegated.index("--camera-rtsp-uri") + 1],
            "rtsp://127.0.0.1:8554/requested",
        )
        self.assertEqual(delegated[delegated.index("--camera-source") + 1], "physical")

    def test_udp_and_serial_endpoints_remain_opaque_strings(self) -> None:
        udp = drone_cli._parse_arguments(["--endpoint", "udpin://0.0.0.0:14555"])
        serial = drone_cli._parse_arguments(["--endpoint", "serial:///dev/ttyUSB0:57600"])

        self.assertEqual(udp.endpoint, "udpin://0.0.0.0:14555")
        self.assertEqual(serial.endpoint, "serial:///dev/ttyUSB0:57600")

    def test_device_builder_receives_connection_and_camera_parameters(self) -> None:
        class _Builder:
            def __init__(self) -> None:
                self.parameters = None
                self.device_metadata = None

            def params(self, **parameters):
                self.parameters = parameters
                return self

            def metadata(self, metadata):
                self.device_metadata = metadata
                return self

            def build(self):
                return _FakeDevice()

        builder = _Builder()
        factory = type(
            "_Factory",
            (),
            {"create": staticmethod(lambda: type("_Root", (), {"px4_generic": lambda _self: builder})())},
        )
        arguments = drone_cli._parse_arguments(
            [
                "--endpoint",
                "serial:///dev/ttyUSB0:57600",
                "--camera-rtsp-uri",
                "rtsp://127.0.0.1/camera",
                "--camera-source",
                "physical",
                "--link-profile",
                "low_bandwidth",
            ]
        )
        with patch.object(drone_cli, "DeviceFactory", factory):
            device = drone_cli._create_device(arguments)

        self.assertIsInstance(device, _FakeDevice)
        self.assertEqual(builder.parameters["endpoint"], "serial:///dev/ttyUSB0:57600")
        self.assertEqual(builder.parameters["camera_stream_uri"], "rtsp://127.0.0.1/camera")
        self.assertEqual(builder.parameters["camera_stream_source"].value, "physical")
        self.assertEqual(builder.parameters["link_profile"], "low_bandwidth")
        self.assertEqual(builder.device_metadata.device_id, "drone-001")

    def test_media_publish_requires_camera_source(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            drone_cli._parse_arguments(
                [
                    "--endpoint",
                    "udpin://0.0.0.0:14555",
                    "--media-publish-url",
                    "https://media.example/whip",
                ]
            )

        self.assertEqual(raised.exception.code, 2)

    def test_invalid_ws_port_is_rejected(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            drone_cli._parse_arguments(
                ["--endpoint", "udpin://0.0.0.0:14555", "--ws-port", "70000"]
            )

        self.assertEqual(raised.exception.code, 2)

    def test_run_starts_ws_and_closes_all_resources(self) -> None:
        arguments = drone_cli._parse_arguments(
            [
                "--endpoint",
                "udpin://0.0.0.0:14555",
                "--camera-rtsp-uri",
                "rtsp://127.0.0.1/camera",
                "--media-publish-url",
                "https://media.example/whip",
                "--ws-port",
                "23045",
            ]
        )
        device = _FakeDevice()
        relay = _FakeRelay()
        with (
            patch.object(drone_cli, "_create_device", return_value=device),
            patch.object(drone_cli, "_create_media_relay", return_value=relay),
            patch.object(drone_cli, "FleetDockServer", _FakeServer),
            redirect_stdout(StringIO()),
        ):
            drone_cli._run(arguments)

        server = _FakeServer.instances[0]
        self.assertEqual(server.registered, (device, "drone-001"))
        self.assertEqual(server.ran, ("127.0.0.1", 23045))
        self.assertTrue(relay.started)
        self.assertTrue(relay.closed)
        self.assertTrue(server.closed)

    def test_optional_media_failure_keeps_ws_available(self) -> None:
        arguments = drone_cli._parse_arguments(
            [
                "--endpoint",
                "udpin://0.0.0.0:14555",
                "--camera-rtsp-uri",
                "rtsp://127.0.0.1/camera",
                "--media-publish-url",
                "https://media.example/whip",
            ]
        )
        relay = _FakeRelay(fail=True)
        with (
            patch.object(drone_cli, "_create_device", return_value=_FakeDevice()),
            patch.object(drone_cli, "_create_media_relay", return_value=relay),
            patch.object(drone_cli, "FleetDockServer", _FakeServer),
            redirect_stdout(StringIO()),
        ):
            drone_cli._run(arguments)

        self.assertEqual(_FakeServer.instances[0].ran, ("127.0.0.1", 23045))
        self.assertTrue(relay.closed)

    def test_required_media_failure_closes_resources_and_aborts(self) -> None:
        arguments = drone_cli._parse_arguments(
            [
                "--endpoint",
                "udpin://0.0.0.0:14555",
                "--camera-rtsp-uri",
                "rtsp://127.0.0.1/camera",
                "--media-publish-url",
                "https://media.example/whip",
                "--require-media",
            ]
        )
        relay = _FakeRelay(fail=True)
        with (
            patch.object(drone_cli, "_create_device", return_value=_FakeDevice()),
            patch.object(drone_cli, "_create_media_relay", return_value=relay),
            patch.object(drone_cli, "FleetDockServer", _FakeServer),
            redirect_stdout(StringIO()),
            self.assertRaisesRegex(RuntimeError, "redacted media failure"),
        ):
            drone_cli._run(arguments)

        self.assertIsNone(_FakeServer.instances[0].ran)
        self.assertTrue(relay.closed)
        self.assertTrue(_FakeServer.instances[0].closed)


if __name__ == "__main__":
    unittest.main()
