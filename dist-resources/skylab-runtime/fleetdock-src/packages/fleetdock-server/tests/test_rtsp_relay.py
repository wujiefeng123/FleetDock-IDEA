from __future__ import annotations

import unittest
import time
from typing import Callable

from fleetdock_server import RtspRelay, RtspRelayStatus
from fleetdock_server.media import GSTREAMER_RTSP_RELAY_ELEMENTS


class _FakeBackend:
    def __init__(self, *, start_error: BaseException | None = None) -> None:
        self.start_error = start_error
        self.start_calls = 0
        self.close_calls = 0
        self.on_error: Callable[[BaseException], None] | None = None

    def start(self, on_error: Callable[[BaseException], None]) -> None:
        self.start_calls += 1
        self.on_error = on_error
        if self.start_error is not None:
            raise self.start_error

    def close(self) -> None:
        self.close_calls += 1

    def fail(self, error: BaseException) -> None:
        assert self.on_error is not None
        self.on_error(error)


class RtspRelayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backends: list[_FakeBackend] = []

    def make_relay(
        self,
        *,
        source_uri: str = "rtsp://camera:secret@127.0.0.1:8554/camera?token=source-secret",
        publish_uri: str = "rtsp://publisher:secret@media.example:8554/live/drone?token=publish-secret",
        start_error: BaseException | None = None,
    ) -> RtspRelay:
        def factory(_source: str, _publish: str, _timeout: float) -> _FakeBackend:
            backend = _FakeBackend(start_error=start_error)
            self.backends.append(backend)
            return backend

        return RtspRelay(source_uri, publish_uri, _backend_factory=factory)

    def test_pipeline_contains_no_decoder_or_encoder(self) -> None:
        self.assertEqual(
            GSTREAMER_RTSP_RELAY_ELEMENTS,
            ("rtspsrc", "rtph264depay", "h264parse", "rtspclientsink"),
        )
        self.assertFalse(any("dec" in name and name != "rtph264depay" for name in GSTREAMER_RTSP_RELAY_ELEMENTS))
        self.assertFalse(any("enc" in name for name in GSTREAMER_RTSP_RELAY_ELEMENTS))

    def test_start_and_close_are_idempotent(self) -> None:
        relay = self.make_relay()
        relay.start()
        relay.start()
        self.assertEqual(relay.status, RtspRelayStatus.RUNNING)
        self.assertEqual(self.backends[0].start_calls, 1)

        relay.close()
        relay.close()
        self.assertEqual(relay.status, RtspRelayStatus.STOPPED)
        self.assertEqual(self.backends[0].close_calls, 1)

    def test_context_manager_releases_backend(self) -> None:
        relay = self.make_relay()
        with relay as active:
            self.assertIs(active, relay)
            self.assertEqual(active.status, RtspRelayStatus.RUNNING)
        self.assertEqual(relay.status, RtspRelayStatus.STOPPED)
        self.assertEqual(self.backends[0].close_calls, 1)

    def test_start_failure_is_sanitized_and_backend_is_closed(self) -> None:
        relay = self.make_relay(
            start_error=RuntimeError(
                "publish rejected: rtsp://publisher:secret@media.example:8554/live/drone?token=publish-secret"
            )
        )
        with self.assertRaises(RuntimeError) as raised:
            relay.start()
        self.assertNotIn("publisher:secret", str(raised.exception))
        self.assertNotIn("publish-secret", str(raised.exception))
        self.assertEqual(relay.status, RtspRelayStatus.FAILED)
        self.assertEqual(self.backends[0].close_calls, 1)
        self.assertNotIn("secret", relay.snapshot().publish_uri)

    def test_async_backend_error_updates_snapshot_without_credentials(self) -> None:
        relay = self.make_relay()
        relay.max_retries = 0
        relay.start()
        self.backends[0].fail(
            RuntimeError(
                "read failed: rtsp://camera:secret@127.0.0.1:8554/camera?token=source-secret"
            )
        )
        snapshot = relay.snapshot()
        self.assertEqual(snapshot.status, RtspRelayStatus.FAILED)
        self.assertIsNotNone(snapshot.error)
        self.assertNotIn("camera:secret", snapshot.error or "")
        self.assertNotIn("source-secret", snapshot.error or "")
        self.assertEqual(self.backends[0].close_calls, 1)
        relay.close()
        self.assertEqual(self.backends[0].close_calls, 1)

    def test_runtime_error_reconnects_with_a_new_backend(self) -> None:
        relay = self.make_relay()
        relay.retry_delay_s = 0
        relay.start()
        self.backends[0].fail(RuntimeError("connection lost"))
        deadline = time.monotonic() + 1.0
        while relay.status is not RtspRelayStatus.RUNNING or len(self.backends) < 2:
            if time.monotonic() >= deadline:
                self.fail(f"relay did not reconnect: {relay.snapshot()}")
            time.sleep(0.01)
        self.assertEqual(self.backends[0].close_calls, 1)
        self.assertEqual(self.backends[1].start_calls, 1)
        relay.close()

    def test_close_interrupts_retry_wait(self) -> None:
        relay = self.make_relay()
        relay.retry_delay_s = 10.0
        relay.start()
        self.backends[0].fail(RuntimeError("connection lost"))
        self.assertEqual(relay.status, RtspRelayStatus.RECONNECTING)
        started = time.monotonic()
        relay.close()
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(relay.status, RtspRelayStatus.STOPPED)
        time.sleep(0.02)
        self.assertEqual(len(self.backends), 1)

    def test_reconnect_stops_after_configured_attempts(self) -> None:
        calls = 0

        def factory(_source: str, _publish: str, _timeout: float) -> _FakeBackend:
            nonlocal calls
            calls += 1
            backend = _FakeBackend(
                start_error=None if calls == 1 else RuntimeError(f"attempt {calls} rejected")
            )
            self.backends.append(backend)
            return backend

        relay = RtspRelay(
            "rtsp://127.0.0.1:8554/camera",
            "rtsp://media.example:8554/live/drone",
            max_retries=2,
            retry_delay_s=0,
            _backend_factory=factory,
        )
        relay.start()
        self.backends[0].fail(RuntimeError("connection lost"))
        deadline = time.monotonic() + 1.0
        while relay.status is not RtspRelayStatus.FAILED:
            if time.monotonic() >= deadline:
                self.fail(f"relay did not exhaust retries: {relay.snapshot()}")
            time.sleep(0.01)
        self.assertEqual(len(self.backends), 3)
        self.assertEqual([backend.close_calls for backend in self.backends], [1, 1, 1])
        relay.close()

    def test_invalid_uri_and_timeout_are_rejected_before_backend_creation(self) -> None:
        with self.assertRaises(ValueError):
            RtspRelay("http://127.0.0.1/camera", "rtsp://media.example/live/drone")
        with self.assertRaises(ValueError):
            RtspRelay("rtsp://127.0.0.1/camera", "rtsp://media.example/live/drone", startup_timeout_s=0)
        with self.assertRaises(ValueError):
            RtspRelay("rtsp://127.0.0.1/camera", "rtsp://media.example/live/drone", max_retries=-1)


if __name__ == "__main__":
    unittest.main()
