from __future__ import annotations

from typing import Callable
import unittest

from fleetdock_server import GStreamerWhipRelay, WhipRelayStatus
from fleetdock_server.media import GSTREAMER_WHIP_RELAY_ELEMENTS


class _FakeBackend:
    def __init__(
        self,
        _source_uri: str,
        _publish_url: str,
        _startup_timeout_s: float,
        _rtsp_latency_ms: int = 0,
        _drop_on_latency: bool = True,
    ) -> None:
        self.closed = False
        self.on_error: Callable[[BaseException], None] | None = None
        self.rtsp_latency_ms = _rtsp_latency_ms
        self.drop_on_latency = _drop_on_latency

    def start(self, on_error: Callable[[BaseException], None]) -> None:
        self.on_error = on_error

    def close(self) -> None:
        self.closed = True


class GStreamerWhipRelayTests(unittest.TestCase):
    def test_pipeline_elements_do_not_decode_or_encode_video(self) -> None:
        self.assertIn("whipsink", GSTREAMER_WHIP_RELAY_ELEMENTS)
        self.assertFalse(any("dec" in name and name != "rtph264depay" for name in GSTREAMER_WHIP_RELAY_ELEMENTS))
        self.assertFalse(any("enc" in name for name in GSTREAMER_WHIP_RELAY_ELEMENTS))

    def test_start_and_close_manage_backend_without_process(self) -> None:
        backends: list[_FakeBackend] = []

        def factory(
            source_uri: str,
            publish_url: str,
            startup_timeout_s: float,
            rtsp_latency_ms: int,
            drop_on_latency: bool,
        ) -> _FakeBackend:
            backend = _FakeBackend(
                source_uri,
                publish_url,
                startup_timeout_s,
                rtsp_latency_ms,
                drop_on_latency,
            )
            backends.append(backend)
            return backend

        relay = GStreamerWhipRelay(
            "rtsp://127.0.0.1:8554/camera",
            "https://media.example/rtc/v1/whip/?app=live&stream=drone",
            rtsp_latency_ms=75,
            drop_on_latency=False,
            _backend_factory=factory,
        )
        relay.start()
        self.assertEqual(relay.status, WhipRelayStatus.RUNNING)
        relay.start()
        self.assertEqual(len(backends), 1)
        self.assertEqual(backends[0].rtsp_latency_ms, 75)
        self.assertFalse(backends[0].drop_on_latency)
        relay.close()
        self.assertTrue(backends[0].closed)
        self.assertEqual(relay.status, WhipRelayStatus.STOPPED)

    def test_runtime_failure_is_redacted_and_closes_backend(self) -> None:
        backend = _FakeBackend("", "", 0)
        source_uri = "rtsp://camera:secret@127.0.0.1:8554/camera?token=source"
        publish_url = "https://publisher:secret@media.example/rtc/v1/whip/?token=publish"
        relay = GStreamerWhipRelay(
            source_uri,
            publish_url,
            _backend_factory=lambda *_args: backend,
        )
        relay.start()
        assert backend.on_error is not None
        backend.on_error(RuntimeError(f"failed source={source_uri} publish={publish_url}"))
        snapshot = relay.snapshot()
        self.assertEqual(snapshot.status, WhipRelayStatus.FAILED)
        self.assertTrue(backend.closed)
        self.assertNotIn("secret", snapshot.source_uri)
        self.assertNotIn("source", snapshot.source_uri)
        self.assertNotIn("secret", snapshot.publish_url)
        self.assertNotIn("publish", snapshot.publish_url)
        self.assertNotIn("secret", snapshot.error or "")
        self.assertNotIn("token=source", snapshot.error or "")
        self.assertNotIn("token=publish", snapshot.error or "")

    def test_invalid_configuration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            GStreamerWhipRelay("http://127.0.0.1/camera", "https://media.example/whip")
        with self.assertRaises(ValueError):
            GStreamerWhipRelay("rtsp://127.0.0.1/camera", "rtsp://media.example/whip")
        with self.assertRaises(ValueError):
            GStreamerWhipRelay(
                "rtsp://127.0.0.1/camera",
                "https://media.example/whip",
                startup_timeout_s=0,
            )
        with self.assertRaises(ValueError):
            GStreamerWhipRelay(
                "rtsp://127.0.0.1/camera",
                "https://media.example/whip",
                rtsp_latency_ms=-1,
            )


if __name__ == "__main__":
    unittest.main()
