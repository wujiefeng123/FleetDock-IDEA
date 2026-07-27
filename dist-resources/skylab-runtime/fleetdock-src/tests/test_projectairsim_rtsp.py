import inspect
import time
import unittest

from fleetdock import ProjectAirSimRtspBridge as RootProjectAirSimRtspBridge
from fleetdock.events import CoordinateFrame
from fleetdock.sensors import ImageEncoding, ImageFrame, SensorDescriptor, SensorKind, SensorSourceKind
from fleetdock.simulation import ProjectAirSimRtspBridge, ProjectAirSimRtspStatus


class _FakePublisher:
    def __init__(self, host, port, path, **settings) -> None:
        self.host = host
        self.port = port
        self.path = path
        self.settings = settings
        self.frames = []
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def push(self, width, height, pixel_format, data) -> bool:
        self.frames.append((width, height, pixel_format, data))
        return True

    def close(self) -> None:
        self.closed = True


class _FakeProjectAirSimBackend:
    def __init__(self, frame: ImageFrame) -> None:
        self.frame = frame
        self.unsubscribed = False
        self.closed = False

    def list_sensors(self):
        return (
            SensorDescriptor(
                "camera.front",
                SensorKind.RGB_CAMERA,
                source=SensorSourceKind.SIMULATED,
                frame=CoordinateFrame.BODY,
                is_primary=True,
            ),
        )

    def subscribe(self, sensor_id, handler):
        handler(self.frame)

        def unsubscribe() -> None:
            self.unsubscribed = True

        return unsubscribe

    def close(self) -> None:
        self.closed = True


def _image_frame(encoding=ImageEncoding.BGR8) -> ImageFrame:
    channels = 3 if encoding in {ImageEncoding.BGR8, ImageEncoding.RGB8} else 4
    return ImageFrame(
        sensor_id="camera.front",
        kind=SensorKind.RGB_CAMERA,
        sequence=1,
        frame=CoordinateFrame.BODY,
        width=2,
        height=1,
        encoding=encoding,
        data=memoryview(bytes(range(channels * 2))),
        stride_bytes=channels * 2,
    )


class ProjectAirSimRtspBridgeTests(unittest.TestCase):
    def test_root_package_keeps_compatibility_export(self) -> None:
        self.assertIs(RootProjectAirSimRtspBridge, ProjectAirSimRtspBridge)

    def test_public_bridge_does_not_expose_scene_id(self) -> None:
        parameters = inspect.signature(ProjectAirSimRtspBridge).parameters
        self.assertNotIn("scene_id", parameters)
        self.assertNotIn("ffmpeg_executable", parameters)
        self.assertNotIn("mediamtx_executable", parameters)

    def test_default_uri_is_single_level_and_safe_for_non_ascii_robot_name(self) -> None:
        bridge = ProjectAirSimRtspBridge("无人机 1", _sensor_backend=_FakeProjectAirSimBackend(_image_frame()))
        try:
            self.assertRegex(bridge.stream_uri, r"^rtsp://127\.0\.0\.1:8554/[A-Za-z0-9_-]+$")
            self.assertNotIn("/camera/", bridge.stream_uri)
        finally:
            bridge.close()

    def test_bridge_publishes_latest_standard_frame_and_releases_resources(self) -> None:
        backend = _FakeProjectAirSimBackend(_image_frame())
        publishers = []

        def create_publisher(*args, **kwargs):
            publisher = _FakePublisher(*args, **kwargs)
            publishers.append(publisher)
            return publisher

        bridge = ProjectAirSimRtspBridge(
            "Drone1",
            _sensor_backend=backend,
            _publisher_factory=create_publisher,
        )
        try:
            self.assertEqual(bridge.start(), bridge.stream_uri)
            deadline = time.monotonic() + 1.0
            while bridge.status().frames_published < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            status = bridge.status()
            self.assertIsInstance(status, ProjectAirSimRtspStatus)
            self.assertTrue(status.running)
            self.assertEqual(status.frames_received, 1)
            self.assertEqual(status.frames_published, 1)
            self.assertIs(bridge.sensor_source, SensorSourceKind.SIMULATED)
            self.assertTrue(publishers[0].started)
            self.assertEqual(publishers[0].host, "127.0.0.1")
            self.assertEqual(publishers[0].port, 8554)
            self.assertEqual(publishers[0].path, "/Drone1_camera_front")
            self.assertEqual(publishers[0].settings["bitrate_kbps"], 4_000)
            self.assertEqual(publishers[0].frames[0][:3], (2, 1, "BGR"))
        finally:
            bridge.close()
            bridge.close()

        self.assertTrue(backend.unsubscribed)
        self.assertTrue(backend.closed)
        self.assertTrue(publishers[0].closed)

    def test_bridge_rejects_non_rgb_video_encoding_before_start_returns(self) -> None:
        frame = ImageFrame(
            sensor_id="camera.front",
            kind=SensorKind.RGB_CAMERA,
            sequence=1,
            width=1,
            height=1,
            encoding=ImageEncoding.DEPTH_F32_M,
            data=memoryview(bytes(4)),
        )
        backend = _FakeProjectAirSimBackend(frame)
        publishers = []

        def create_publisher(*args, **kwargs):
            publisher = _FakePublisher(*args, **kwargs)
            publishers.append(publisher)
            return publisher

        bridge = ProjectAirSimRtspBridge(
            "Drone1",
            _sensor_backend=backend,
            _publisher_factory=create_publisher,
        )
        with self.assertRaisesRegex(RuntimeError, "publisher failed"):
            bridge.start()
        self.assertTrue(backend.closed)
        self.assertTrue(publishers[0].closed)


if __name__ == "__main__":
    unittest.main()
