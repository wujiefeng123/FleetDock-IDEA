from __future__ import annotations

import unittest
from unittest.mock import patch

from fleetdock.simulation import _gstreamer_rtsp as rtsp


class _FakeCaps:
    @staticmethod
    def from_string(value: str) -> str:
        return value


class _FakeBuffer:
    pts = 0
    dts = 0
    duration = 0

    @staticmethod
    def new_allocate(_allocator, size: int, _params) -> "_FakeBuffer":
        buffer = _FakeBuffer()
        buffer.data = bytes(size)
        return buffer

    def fill(self, _offset: int, data: bytes) -> None:
        self.data = data


class _FakeGst:
    Caps = _FakeCaps
    Buffer = _FakeBuffer

    class FlowReturn:
        OK = "ok"


class _FakeRtsp:
    class RTSPLowerTrans:
        TCP = "tcp"


class _FakeAppSrc:
    def __init__(self) -> None:
        self.caps = None
        self.buffers: list[_FakeBuffer] = []
        self.ended = False

    def set_property(self, name: str, value) -> None:
        if name == "caps":
            self.caps = value

    def emit(self, name: str, value=None):
        if name == "push-buffer":
            self.buffers.append(value)
            return _FakeGst.FlowReturn.OK
        if name == "end-of-stream":
            self.ended = True
        return None


class _FakeFactory:
    def __init__(self) -> None:
        self.shared = False
        self.launch = ""
        self.callback = None
        self.latency = None
        self.protocols = None

    def set_shared(self, value: bool) -> None:
        self.shared = value

    def set_launch(self, value: str) -> None:
        self.launch = value

    def set_latency(self, value: int) -> None:
        self.latency = value

    def set_protocols(self, value) -> None:
        self.protocols = value

    def connect(self, _signal: str, callback) -> None:
        self.callback = callback


class _FakeMountPoints:
    def __init__(self) -> None:
        self.factories = {}

    def add_factory(self, path: str, factory: _FakeFactory) -> None:
        self.factories[path] = factory

    def remove_factory(self, path: str) -> None:
        self.factories.pop(path, None)


class _FakeServer:
    def __init__(self) -> None:
        self.mounts = _FakeMountPoints()
        self.address = None
        self.service = None

    @classmethod
    def new(cls) -> "_FakeServer":
        return cls()

    def set_address(self, value: str) -> None:
        self.address = value

    def set_service(self, value: str) -> None:
        self.service = value

    def attach(self, _context) -> int:
        return 1

    def get_mount_points(self) -> _FakeMountPoints:
        return self.mounts


class _FakeSource:
    def __init__(self) -> None:
        self.destroyed = False

    def destroy(self) -> None:
        self.destroyed = True


class _FakeContext:
    def __init__(self) -> None:
        self.source = _FakeSource()

    @classmethod
    def new(cls) -> "_FakeContext":
        return cls()

    def find_source_by_id(self, _source_id: int) -> _FakeSource:
        return self.source


class _FakeLoop:
    @classmethod
    def new(cls, context: _FakeContext, _running: bool) -> "_FakeLoop":
        loop = cls()
        loop.context = context
        loop.stopped = False
        return loop

    def run(self) -> None:
        return None

    def quit(self) -> None:
        self.stopped = True


class _FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


class _FakeMedia:
    def __init__(self, appsrc: _FakeAppSrc) -> None:
        self.appsrc = appsrc

    def get_element(self):
        appsrc = self.appsrc

        class Pipeline:
            @staticmethod
            def get_by_name(_name: str):
                return appsrc

        return Pipeline()


class GStreamerRtspTests(unittest.TestCase):
    def setUp(self) -> None:
        rtsp._servers.clear()
        self.glib = type("FakeGLib", (), {"MainContext": _FakeContext, "MainLoop": _FakeLoop})
        self.rtsp_server = type(
            "FakeRtspServer",
            (),
            {"RTSPMediaFactory": _FakeFactory, "RTSPServer": _FakeServer},
        )

    def tearDown(self) -> None:
        rtsp._servers.clear()

    def test_mount_keeps_latest_frame_and_pushes_after_client_connects(self) -> None:
        mount = rtsp._GStreamerMount(_FakeGst, _FakeRtsp, self.rtsp_server, 30.0, 2_000)
        self.assertEqual(mount.factory.latency, 0)
        self.assertEqual(mount.factory.protocols, _FakeRtsp.RTSPLowerTrans.TCP)
        self.assertIn("max-buffers=1 leaky-type=downstream", mount.factory.launch)
        self.assertIn("gop-size=30 enable-frame-skip=true", mount.factory.launch)
        frame = bytes(2 * 2 * 3)
        self.assertFalse(mount.push(2, 2, "BGR", frame))

        appsrc = _FakeAppSrc()
        with patch.object(
            rtsp.time,
            "monotonic_ns",
            side_effect=(1_000_000_000, 1_150_000_000),
        ):
            mount._on_media_configure(None, _FakeMedia(appsrc))
            self.assertTrue(mount.push(2, 2, "BGR", frame))
        self.assertIn("format=BGR,width=2,height=2", appsrc.caps)
        self.assertEqual(appsrc.buffers[-1].pts, 150_000_000)
        self.assertEqual(appsrc.buffers[-1].duration, round(1_000_000_000 / 30.0))

        mount.close()
        self.assertTrue(appsrc.ended)
        self.assertFalse(mount.push(2, 2, "BGR", frame))

    def test_shared_server_registers_mount_and_rejects_duplicate_path(self) -> None:
        with (
            patch.object(
                rtsp,
                "_load_gstreamer",
                return_value=(self.glib, _FakeGst, _FakeRtsp, self.rtsp_server),
            ),
            patch.object(rtsp.socket, "create_connection", return_value=_FakeConnection()),
        ):
            server = rtsp._SharedGStreamerRtspServer("localhost", 8554, 1.0)
            mount = server.add_mount("/camera", 30.0, 2_000)
            self.assertIn("/camera", server._server.mounts.factories)
            with self.assertRaisesRegex(RuntimeError, "already registered"):
                server.add_mount("/camera", 30.0, 2_000)
            server.remove_mount("/camera")
            self.assertIsInstance(mount, rtsp._GStreamerMount)
            server.close()
            self.assertTrue(server._loop.stopped)

    def test_publishers_share_local_alias_and_release_last_server(self) -> None:
        with (
            patch.object(
                rtsp,
                "_load_gstreamer",
                return_value=(self.glib, _FakeGst, _FakeRtsp, self.rtsp_server),
            ),
            patch.object(rtsp.socket, "create_connection", return_value=_FakeConnection()),
        ):
            first = rtsp.GStreamerRtspPublisher(
                "localhost", 8554, "/front", frame_rate_hz=30.0, bitrate_kbps=2_000, startup_timeout_s=1.0
            )
            second = rtsp.GStreamerRtspPublisher(
                "127.0.0.1", 8554, "/rear", frame_rate_hz=30.0, bitrate_kbps=2_000, startup_timeout_s=1.0
            )
            first.start()
            second.start()
            self.assertEqual(len(rtsp._servers), 1)
            self.assertFalse(first.push(2, 2, "BGR", bytes(12)))

            first.close()
            self.assertEqual(len(rtsp._servers), 1)
            second.close()
            second.close()
            self.assertFalse(rtsp._servers)

    def test_remote_managed_server_is_rejected(self) -> None:
        publisher = rtsp.GStreamerRtspPublisher(
            "192.0.2.1", 8554, "/camera", frame_rate_hz=30.0, bitrate_kbps=2_000, startup_timeout_s=0.1
        )
        with self.assertRaisesRegex(RuntimeError, "local RTSP"):
            publisher.start()


if __name__ == "__main__":
    unittest.main()
