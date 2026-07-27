import threading
import time
import unittest
from unittest.mock import patch

from fleetdock import (
    AirspeedSample,
    BarometerSample,
    ContactSample,
    CoordinateFrame,
    DeviceDebug,
    DeviceFactory,
    DistanceSample,
    FootForceSample,
    ForceTorqueSample,
    GenericSensorSample,
    GnssSample,
    ImageEncoding,
    ImageFrame,
    ImuSample,
    JointStateSample,
    MagnetometerSample,
    MockSensorBackend,
    OpticalFlowSample,
    PointCloudFrame,
    SensorDescriptor,
    SensorDashboard,
    SensorHub,
    SensorKind,
    StereoFrame,
    Vector3,
)
from fleetdock.debug import _OpenCvDebugDisplay, camera as debug_camera


class FakeDisplay:
    def __init__(self, expected_samples: int = 1) -> None:
        self.expected_samples = expected_samples
        self.opened = False
        self.closed = False
        self.samples = {}
        self.deadline = time.monotonic() + 2.0

    def open(self, title: str) -> None:
        self.opened = True

    def draw(self, title: str, samples) -> None:
        self.samples = dict(samples)

    def should_close(self, delay_ms: int) -> bool:
        time.sleep(0.001)
        if len(self.samples) >= self.expected_samples:
            return True
        if time.monotonic() >= self.deadline:
            raise TimeoutError("Fake debug display did not receive samples.")
        return False

    def close(self) -> None:
        self.closed = True


class CountingSensorBackend(MockSensorBackend):
    def __init__(self, descriptors) -> None:
        super().__init__(descriptors)
        self.native_starts = 0
        self.native_stops = 0

    def subscribe(self, sensor_id, handler):
        self.native_starts += 1
        unsubscribe = super().subscribe(sensor_id, handler)
        closed = False

        def stop() -> None:
            nonlocal closed
            if closed:
                return
            closed = True
            self.native_stops += 1
            unsubscribe()

        return stop


def image_sample(sequence: int = 1) -> ImageFrame:
    return ImageFrame(
        sensor_id="camera.front",
        kind=SensorKind.RGB_CAMERA,
        sequence=sequence,
        frame=CoordinateFrame.BODY_FRD,
        width=2,
        height=1,
        encoding=ImageEncoding.BGR8,
        data=memoryview(bytes((0, 0, 255, 0, 255, 0))),
        stride_bytes=6,
    )


def imu_sample(sequence: int = 1, *, orientation_rpy_deg: Vector3 | None = None) -> ImuSample:
    return ImuSample(
        sensor_id="imu.main",
        kind=SensorKind.IMU,
        sequence=sequence,
        frame=CoordinateFrame.BODY_FRD,
        angular_velocity_rad_s=Vector3(0.1, 0.2, 0.3),
        linear_acceleration_m_s2=Vector3(1.0, 2.0, 9.8),
        orientation_rpy_deg=orientation_rpy_deg,
    )


class DeviceDebugTests(unittest.TestCase):
    def test_standalone_camera_debug_does_not_create_a_device(self) -> None:
        handled = []
        frame = image_sample()

        with patch("fleetdock.debug.SensorHub._register_camera") as register_camera:
            with patch("fleetdock.debug.DeviceDebug") as debug_type:
                debug = debug_type.return_value

                def show_camera(sensor_id, handler, *, title=None) -> None:
                    handler(frame)

                debug.camera.side_effect = show_camera
                debug_camera(0, handled.append)

        register_camera.assert_called_once_with(
            0,
            sensor_id="camera.front",
            frame=CoordinateFrame.BODY,
            intrinsics=None,
        )
        self.assertIs(handled[0], frame)
        debug.camera.assert_called_once()
        debug.close.assert_called_once_with()

    def test_camera_sends_same_standard_frame_to_display_and_handler(self) -> None:
        backend = MockSensorBackend((SensorDescriptor("camera.front", SensorKind.RGB_CAMERA, is_primary=True),))
        hub = SensorHub("device-1")
        hub.register_backend(backend)
        display = FakeDisplay()
        debug = DeviceDebug(hub, _display_factory=lambda: display)
        frame = image_sample()
        handled = []
        publisher = threading.Thread(target=lambda: (time.sleep(0.02), backend.publish(frame)))

        publisher.start()
        debug.camera(handled.append)
        publisher.join()

        self.assertIs(display.samples["camera.front"], frame)
        self.assertIs(handled[0], frame)
        self.assertEqual(hub.status("camera.front").subscriber_count, 0)
        self.assertTrue(display.opened)
        self.assertTrue(display.closed)
        debug.close()
        hub.close()

    def test_debug_subscription_shares_existing_native_stream(self) -> None:
        backend = CountingSensorBackend((SensorDescriptor("camera.front", SensorKind.RGB_CAMERA),))
        hub = SensorHub("device-1")
        hub.register_backend(backend)
        existing_samples = []
        unsubscribe = hub.subscribe("camera.front", existing_samples.append)
        display = FakeDisplay()
        debug = DeviceDebug(hub, _display_factory=lambda: display)
        frame = image_sample()
        publisher = threading.Thread(target=lambda: (time.sleep(0.02), backend.publish(frame)))

        publisher.start()
        debug.camera("camera.front")
        publisher.join()

        self.assertEqual(backend.native_starts, 1)
        self.assertEqual(backend.native_stops, 0)
        self.assertEqual(hub.status("camera.front").subscriber_count, 1)
        self.assertIs(existing_samples[0], frame)
        unsubscribe()
        self.assertEqual(backend.native_stops, 1)
        debug.close()
        hub.close()

    def test_dashboard_displays_multiple_standard_sensor_types(self) -> None:
        backend = MockSensorBackend(
            (
                SensorDescriptor("imu.main", SensorKind.IMU),
                SensorDescriptor("gnss.main", SensorKind.GNSS),
            )
        )
        hub = SensorHub("device-1")
        hub.register_backend(backend)
        display = FakeDisplay(expected_samples=2)
        debug = DeviceDebug(hub, _display_factory=lambda: display)
        imu = imu_sample()
        gnss = GnssSample(
            sensor_id="gnss.main",
            kind=SensorKind.GNSS,
            sequence=1,
            latitude_deg=30.0,
            longitude_deg=120.0,
        )
        handled = []

        dashboard = debug.dashboard()
        self.assertIsInstance(dashboard, SensorDashboard)
        unsubscribes = (
            hub.subscribe(SensorKind.IMU, dashboard.handler(handled.append)),
            hub.subscribe(SensorKind.GNSS, dashboard),
        )
        backend.publish(imu)
        backend.publish(gnss)
        self.assertTrue(dashboard._finished.wait(timeout=2.0))

        self.assertEqual(set(display.samples), {"imu.main", "gnss.main"})
        self.assertEqual(handled, [imu])
        for unsubscribe in unsubscribes:
            unsubscribe()
        dashboard.close()
        debug.close()
        hub.close()

    def test_proxy_handler_uses_existing_subscription_and_run_loop(self) -> None:
        backend = CountingSensorBackend((SensorDescriptor("camera.front", SensorKind.RGB_CAMERA),))
        hub = SensorHub("device-1")
        hub.register_backend(backend)
        display = FakeDisplay()
        debug = DeviceDebug(hub, _display_factory=lambda: display)
        handled = []
        unsubscribe = hub.subscribe("camera.front", debug.camera_handler(handled.append))
        frame = image_sample()
        publisher = threading.Thread(target=lambda: (time.sleep(0.02), backend.publish(frame)))

        publisher.start()
        debug.run()
        publisher.join()

        self.assertIs(display.samples["camera.front"], frame)
        self.assertIs(handled[0], frame)
        self.assertEqual(backend.native_starts, 1)
        unsubscribe()
        debug.close()
        hub.close()

    def test_handler_error_is_counted_without_preventing_display(self) -> None:
        backend = MockSensorBackend((SensorDescriptor("imu.main", SensorKind.IMU),))
        hub = SensorHub("device-1")
        hub.register_backend(backend)
        display = FakeDisplay()
        debug = DeviceDebug(hub, _display_factory=lambda: display)

        def fail(sample) -> None:
            raise RuntimeError("developer callback failed")

        dashboard = debug.dashboard()
        unsubscribe = hub.subscribe(SensorKind.IMU, dashboard.handler(fail))
        backend.publish(imu_sample())
        self.assertTrue(dashboard._finished.wait(timeout=2.0))

        self.assertIn("imu.main", display.samples)
        self.assertEqual(hub.status("imu.main").handler_error_count, 1)
        unsubscribe()
        dashboard.close()
        debug.close()
        hub.close()

    def test_display_start_failure_does_not_create_sensor_subscription(self) -> None:
        backend = CountingSensorBackend((SensorDescriptor("imu.main", SensorKind.IMU),))
        hub = SensorHub("device-1")
        hub.register_backend(backend)

        def unavailable_display():
            raise RuntimeError("no desktop")

        debug = DeviceDebug(hub, _display_factory=unavailable_display)
        with self.assertRaisesRegex(RuntimeError, "无法启动"):
            debug.dashboard()

        self.assertEqual(backend.native_starts, 0)
        self.assertEqual(backend.native_stops, 0)
        debug.close()
        hub.close()

    def test_device_exposes_and_closes_debug_entry(self) -> None:
        drone = DeviceFactory.create().mock_drone().build()

        self.assertIsInstance(drone.debug, DeviceDebug)
        drone.close()
        with self.assertRaisesRegex(RuntimeError, "已关闭"):
            drone.debug.run()

    def test_camera_selection_and_dashboard_input_validation(self) -> None:
        backend = MockSensorBackend(
            (
                SensorDescriptor("imu.main", SensorKind.IMU),
                SensorDescriptor("gnss.main", SensorKind.GNSS),
            )
        )
        hub = SensorHub("device-1")
        hub.register_backend(backend)
        debug = DeviceDebug(hub, _display_factory=FakeDisplay)

        with self.assertRaisesRegex(LookupError, "摄像头"):
            debug.camera()
        with self.assertRaisesRegex(ValueError, "不是标准摄像头"):
            debug.camera("imu.main")
        dashboard = debug.dashboard()
        with self.assertRaisesRegex(ValueError, "至少需要一个"):
            dashboard.update()
        with self.assertRaisesRegex(TypeError, "SensorSample"):
            dashboard.update(object())
        with self.assertRaisesRegex(RuntimeError, "already running"):
            debug.dashboard()

        dashboard.close()
        debug.close()
        hub.close()

    def test_camera_handler_rejects_non_image_sample(self) -> None:
        hub = SensorHub("device-1")
        debug = DeviceDebug(hub, _display_factory=FakeDisplay)

        with self.assertRaisesRegex(TypeError, "ImageFrame"):
            debug.camera_handler()(imu_sample())

        debug.close()
        hub.close()


class FakeCv2:
    WINDOW_NORMAL = 0
    WND_PROP_VISIBLE = 1
    FONT_HERSHEY_SIMPLEX = 2
    LINE_AA = 3
    INTER_AREA = 4
    COLOR_RGB2BGR = 5
    COLOR_RGBA2BGR = 6
    COLOR_GRAY2BGR = 7
    COLORMAP_TURBO = 8

    def __init__(self) -> None:
        self.key = -1
        self.visible = 1.0
        self.destroyed = False
        self.last_image = None

    def namedWindow(self, title, mode) -> None:
        self.title = title

    def resizeWindow(self, title, width, height) -> None:
        self.window_size = (width, height)

    def imshow(self, title, image) -> None:
        self.last_image = image

    def waitKey(self, delay_ms) -> int:
        return self.key

    def getWindowProperty(self, title, prop) -> float:
        return self.visible

    def destroyWindow(self, title) -> None:
        self.destroyed = True

    def putText(self, *args, **kwargs) -> None:
        return None


class OpenCvDebugRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            import cv2  # noqa: F401
            import numpy  # noqa: F401
        except ImportError as exc:
            raise unittest.SkipTest("OpenCV debug rendering dependencies are not installed.") from exc

    def setUp(self) -> None:
        self.display = _OpenCvDebugDisplay()

    def frame(self, encoding, data, *, width=2, height=1, stride_bytes=None) -> ImageFrame:
        return ImageFrame(
            sensor_id="camera.front",
            kind=SensorKind.DEPTH_CAMERA if "depth" in encoding.value else SensorKind.RGB_CAMERA,
            sequence=1,
            width=width,
            height=height,
            encoding=encoding,
            data=memoryview(data),
            stride_bytes=stride_bytes,
        )

    def test_standard_image_encodings_render_to_bgr(self) -> None:
        import numpy as np

        frames = (
            self.frame(ImageEncoding.BGR8, bytes((0, 0, 255, 0, 255, 0)), stride_bytes=6),
            self.frame(ImageEncoding.RGB8, bytes((255, 0, 0, 0, 255, 0)), stride_bytes=6),
            self.frame(ImageEncoding.RGBA8, bytes((255, 0, 0, 255, 0, 255, 0, 255)), stride_bytes=8),
            self.frame(ImageEncoding.MONO8, bytes((0, 255)), stride_bytes=2),
            self.frame(ImageEncoding.DEPTH_U16_MM, np.array((100, 200), dtype=np.uint16).tobytes()),
            self.frame(ImageEncoding.DEPTH_F32_M, np.array((1.0, 2.0), dtype=np.float32).tobytes()),
        )

        images = [self.display._image_frame(frame) for frame in frames]

        self.assertTrue(all(image.shape == (1, 2, 3) for image in images))
        self.assertEqual(images[0].tolist(), images[1].tolist())

    def test_padded_stride_depth_and_stereo_rendering(self) -> None:
        import numpy as np

        padded = self.frame(ImageEncoding.MONO8, bytes((10, 20, 99, 99)), stride_bytes=4)
        equal_depth = self.frame(
            ImageEncoding.DEPTH_F32_M,
            np.array((float("nan"), 3.0), dtype=np.float32).tobytes(),
        )
        right = ImageFrame(
            sensor_id="camera.right",
            kind=SensorKind.RGB_CAMERA,
            sequence=1,
            width=2,
            height=2,
            encoding=ImageEncoding.BGR8,
            data=memoryview(bytes(12)),
        )
        stereo = StereoFrame(
            sensor_id="camera.stereo",
            kind=SensorKind.STEREO_CAMERA,
            sequence=1,
            left=image_sample(),
            right=right,
        )

        self.assertEqual(self.display._image_frame(padded).shape, (1, 2, 3))
        self.assertEqual(self.display._image_frame(equal_depth).shape, (1, 2, 3))
        self.assertEqual(self.display._camera_image(stereo).shape[0], 1)

    def test_native_encoding_and_invalid_stride_are_reported(self) -> None:
        with self.assertRaisesRegex(ValueError, "不支持"):
            self.display._image_frame(self.frame(ImageEncoding.NATIVE, bytes(2)))
        with self.assertRaisesRegex(ValueError, "图像步长"):
            self.display._image_frame(self.frame(ImageEncoding.DEPTH_U16_MM, bytes(4), stride_bytes=3))

        canvas = self.display._compose({"camera.front": self.frame(ImageEncoding.NATIVE, bytes(2))})
        self.assertEqual(canvas.shape, (320, 640, 3))

    def test_camera_uses_full_640_by_320_canvas(self) -> None:
        frame = self.frame(ImageEncoding.BGR8, bytes((20, 40, 60, 80, 100, 120)), stride_bytes=6)

        canvas = self.display._compose({frame.sensor_id: frame})

        self.assertEqual(canvas.shape, (320, 640, 3))
        self.assertFalse((canvas[:, 360:] == (18, 22, 26)).all())

    def test_hud_formats_standard_air_and_legged_samples(self) -> None:
        samples = (
            imu_sample(),
            GnssSample(sensor_id="gnss", kind=SensorKind.GNSS, sequence=1, latitude_deg=1.0, longitude_deg=2.0),
            MagnetometerSample(
                sensor_id="mag", kind=SensorKind.MAGNETOMETER, sequence=1, magnetic_field_t=Vector3(1.0, 2.0, 3.0)
            ),
            BarometerSample(sensor_id="baro", kind=SensorKind.BAROMETER, sequence=1, absolute_pressure_pa=101325.0),
            DistanceSample(sensor_id="range", kind=SensorKind.DISTANCE, sequence=1, distance_m=2.5),
            AirspeedSample(sensor_id="air", kind=SensorKind.AIRSPEED, sequence=1, indicated_airspeed_m_s=12.0),
            OpticalFlowSample(
                sensor_id="flow",
                kind=SensorKind.OPTICAL_FLOW,
                sequence=1,
                integrated_flow_rad=(0.1, 0.2),
                integration_time_s=0.02,
            ),
            JointStateSample(
                sensor_id="joints", kind=SensorKind.JOINT_STATE, sequence=1, joint_names=("hip",), position_rad=(0.5,)
            ),
            ForceTorqueSample(
                sensor_id="force",
                kind=SensorKind.FORCE_TORQUE,
                sequence=1,
                force_n=Vector3(1.0, 2.0, 3.0),
                torque_nm=Vector3(0.1, 0.2, 0.3),
            ),
            FootForceSample(
                sensor_id="foot", kind=SensorKind.FOOT_FORCE, sequence=1, foot_name="fl", force_n=Vector3(0.0, 0.0, 10.0)
            ),
            ContactSample(sensor_id="contact", kind=SensorKind.CONTACT, sequence=1, contact_name="fl", in_contact=True),
            GenericSensorSample(sensor_id="custom", kind=SensorKind.RADAR, sequence=1, values={"value": 1}),
            PointCloudFrame(
                sensor_id="lidar", kind=SensorKind.LIDAR, sequence=1, data=memoryview(bytes(12)), point_count=1
            ),
        )

        lines = [self.display._sample_lines(sample) for sample in samples]
        canvas = self.display._compose({sample.sensor_id: sample for sample in samples})

        self.assertTrue(all(len(item) >= 4 for item in lines))
        self.assertEqual(canvas.shape, (320, 640, 3))
        self.assertEqual(self.display._short_sequence((1, 2, 3, 4, 5)), "1, 2, 3, 4 ...")
        self.assertTrue(self.display._short_value("x" * 80).endswith("..."))

    def test_hud_draws_artificial_horizon_from_real_imu_orientation(self) -> None:
        sample = imu_sample(orientation_rpy_deg=Vector3(18.0, -8.0, 135.0))

        canvas = self.display._compose({sample.sensor_id: sample})

        self.assertEqual(canvas.shape, (320, 640, 3))
        self.assertGreater(int(canvas[:, :, 0].max()), 100)
        self.assertGreater(int(canvas[:, :, 1].max()), 150)
        self.assertGreater(int(canvas[:, :, 2].max()), 200)

    def test_window_adapter_lifecycle_and_keys(self) -> None:
        import numpy as np

        fake_cv2 = FakeCv2()
        display = _OpenCvDebugDisplay()
        display._cv2 = fake_cv2
        display._np = np
        display._title = ""
        display._fps = {}

        display.open("test")
        display.draw("test", {})
        self.assertFalse(display.should_close(1))
        fake_cv2.key = ord("q")
        self.assertTrue(display.should_close(1))
        fake_cv2.key = -1
        fake_cv2.visible = 0.0
        self.assertTrue(display.should_close(1))
        display.close()

        self.assertEqual(fake_cv2.last_image.shape, (320, 640, 3))
        self.assertEqual(fake_cv2.window_size, (640, 320))
        self.assertTrue(fake_cv2.destroyed)

    def test_missing_optional_dependency_has_install_guidance(self) -> None:
        original_import = __import__

        def fail_cv2(name, *args, **kwargs):
            if name == "cv2":
                raise ImportError("missing cv2")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fail_cv2):
            with self.assertRaisesRegex(RuntimeError, "vision"):
                _OpenCvDebugDisplay()


if __name__ == "__main__":
    unittest.main()
