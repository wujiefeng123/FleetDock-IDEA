from __future__ import annotations

from threading import Event
import unittest

from fleetdock_server import FfmpegWhipRelay, WhipRelay, WhipRelayStatus


class _FakeProcess:
    def __init__(self, return_code: int | None = None) -> None:
        self.return_code = return_code
        self.terminated = False
        self.killed = False
        self._exited = Event()
        if return_code is not None:
            self._exited.set()

    def poll(self) -> int | None:
        return self.return_code

    def wait(self, timeout: float | None = None) -> int:
        if not self._exited.wait(timeout):
            raise TimeoutError
        assert self.return_code is not None
        return self.return_code

    def terminate(self) -> None:
        self.terminated = True
        self.return_code = 0
        self._exited.set()

    def kill(self) -> None:
        self.killed = True
        self.return_code = -9
        self._exited.set()


class WhipRelayTests(unittest.TestCase):
    def test_explicit_ffmpeg_name_preserves_compatibility(self) -> None:
        self.assertIs(FfmpegWhipRelay, WhipRelay)

    def test_command_uses_low_latency_h264_without_rtmp(self) -> None:
        commands: list[list[str]] = []
        process = _FakeProcess()

        def factory(command: list[str]) -> _FakeProcess:
            commands.append(command)
            return process

        relay = WhipRelay(
            "rtsp://127.0.0.1:8554/camera",
            "https://media.example/rtc/v1/whip/?app=live&stream=drone",
            startup_probe_s=0.001,
            _process_factory=factory,
        )
        relay.start()
        command = commands[0]
        self.assertIn("libx264", command)
        self.assertIn("ultrafast", command)
        self.assertIn("zerolatency", command)
        self.assertIn("baseline", command)
        self.assertIn("whip", command)
        self.assertNotIn("rtmp", " ".join(command).lower())
        self.assertEqual(relay.status, WhipRelayStatus.RUNNING)
        relay.close()
        self.assertTrue(process.terminated)
        self.assertEqual(relay.status, WhipRelayStatus.STOPPED)

    def test_start_failure_is_reported(self) -> None:
        process = _FakeProcess(return_code=1)
        relay = WhipRelay(
            "rtsp://127.0.0.1:8554/camera",
            "https://media.example/rtc/v1/whip/?app=live&stream=drone",
            startup_probe_s=0.001,
            _process_factory=lambda _command: process,
        )
        with self.assertRaisesRegex(RuntimeError, "exited with code 1"):
            relay.start()
        self.assertEqual(relay.status, WhipRelayStatus.FAILED)

    def test_snapshot_redacts_credentials_and_query(self) -> None:
        relay = WhipRelay(
            "rtsp://camera:secret@127.0.0.1:8554/camera?token=source",
            "https://publisher:secret@media.example/rtc/v1/whip/?token=publish",
            _process_factory=lambda _command: _FakeProcess(),
        )
        snapshot = relay.snapshot()
        self.assertNotIn("secret", snapshot.source_uri)
        self.assertNotIn("source", snapshot.source_uri)
        self.assertNotIn("secret", snapshot.publish_url)
        self.assertNotIn("publish", snapshot.publish_url)

    def test_invalid_configuration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            WhipRelay("http://127.0.0.1/camera", "https://media.example/whip")
        with self.assertRaises(ValueError):
            WhipRelay("rtsp://127.0.0.1/camera", "rtsp://media.example/whip")
        with self.assertRaises(ValueError):
            WhipRelay("rtsp://127.0.0.1/camera", "https://media.example/whip", bitrate_kbps=0)


if __name__ == "__main__":
    unittest.main()
