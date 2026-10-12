from array import array
from types import SimpleNamespace
import threading
import time
import unittest
from unittest.mock import patch

from fleetdock import (
    AvoidancePolicy,
    AirspeedSample,
    BarometerSample,
    CameraIntrinsics,
    CoordinateFrame,
    DepartureDirectionPolicy,
    DepthCameraObstacleProvider,
    DepthObstacleConfig,
    DeviceFactory,
    GenericSensorSample,
    GnssSample,
    ImageEncoding,
    ImageFrame,
    DistanceObstacleProvider,
    DistanceSample,
    ExtensionSensorKind,
    EventType,
    ImuSample,
    MagnetometerSample,
    MockSensorBackend,
    PointCloudFrame,
    PointCloudCorridorAnalyzer,
    PointCloudObstacleTracker,
    RadarDetection,
    RadarFrame,
    RadarObstacleProvider,
    LidarAvoidancePreset,
    LidarObstacleConfig,
    LidarObstacleProvider,
    SensorDeliveryMode,
    SensorDescriptor,
    SensorHub,
    SensorKind,
    SensorMount,
    SensorSourceKind,
    SensorStreamOptions,
    SensorStreamEvent,
    Vector3,
    avoidance,
)
from fleetdock.adapters.opencv_camera import _OpenCvCameraBackend
from fleetdock.adapters.projectairsim_sensors import _ProjectAirSimSensorBackend
from fleetdock.adapters.px4_mavlink.sensors import _Px4IntegratedSensorBackend, _Px4MavsdkSensorBackend


def point_cloud(
    sensor_id: str,
    sequence: int,
    points: tuple[float, ...],
    *,
    frame: CoordinateFrame = CoordinateFrame.LOCAL_NED,
) -> PointCloudFrame:
    buffer = array("f", points)
    return PointCloudFrame(
        sensor_id=sensor_id,
        kind=SensorKind.LIDAR,
        sequence=sequence,
        frame=frame,
        data=memoryview(buffer),
        point_count=len(buffer) // 3,
    )


class SensorHubTests(unittest.TestCase):
    def test_device_build_does_not_discover_or_subscribe_sensor_backends(self) -> None:
        calls = []

        class LazySensorBackend:
            def list_sensors(self):
                calls.append("list")
                return (SensorDescriptor("imu.lazy", SensorKind.IMU),)

            def subscribe(self, sensor_id, handler):
                calls.append("subscribe")
                return lambda: None

            def close(self) -> None:
                calls.append("close")

        builder = DeviceFactory.create().mock_drone()
        self.assertFalse(hasattr(builder, "sensor_backend"))
        self.assertFalse(hasattr(DeviceFactory.create().px4_generic(), "projectairsim_sensors"))
        drone = builder.build()
        drone.sensors.register_backend(LazySensorBackend())

        self.assertEqual(calls, [])
        self.assertEqual(drone.sensors.list()[0].sensor_id, "imu.lazy")
        self.assertEqual(calls, ["list"])
        drone.close()
        self.assertEqual(calls, ["list", "close"])

    def test_camera_registration_happens_after_device_creation_without_opening_stream(self) -> None:
        drone = DeviceFactory.create().mock_drone().build()

        descriptor = drone.sensors._register_camera(0)

        self.assertEqual(descriptor.sensor_id, "camera.front")
        self.assertIs(descriptor.kind, SensorKind.RGB_CAMERA)
        self.assertIs(descriptor.frame, CoordinateFrame.BODY)
        self.assertEqual(drone.sensors.status(descriptor.sensor_id).subscriber_count, 0)
        self.assertFalse(hasattr(DeviceFactory.create().mock_drone(), "camera_stream"))
        self.assertFalse(hasattr(drone.sensors, "register_camera"))
        drone.close()

    def test_device_sensor_lifecycle_publishes_typed_events(self) -> None:
        backend = MockSensorBackend((SensorDescriptor("imu1", SensorKind.IMU),))
        drone = DeviceFactory.create().mock_drone().build()
        drone.sensors.register_backend(backend)
        events = []
        unsubscribe_events = drone.events.on(SensorStreamEvent, events.append)

        drone.sensors.list()
        unsubscribe_sensor = drone.sensors.subscribe(SensorKind.IMU, lambda sample: None)
        unsubscribe_sensor()
        self.assertTrue(drone.events.drain(timeout_s=1.0))

        self.assertEqual(
            [event.type for event in events],
            [EventType.SENSOR_DISCOVERED, EventType.SENSOR_STREAM_STARTED, EventType.SENSOR_STREAM_STOPPED],
        )
        self.assertEqual(events[0].sensor.sensor_id, "imu1")
        unsubscribe_events()
        drone.close()

    def test_extension_sensor_kind_uses_namespace_and_standard_hub_contract(self) -> None:
        gas_kind = ExtensionSensorKind("custom.inspection.gas_detector")
        backend = MockSensorBackend((SensorDescriptor("gas-front", gas_kind),))
        hub = SensorHub("device-1")
        hub.register_backend(backend)
        received = []
        delivered = threading.Event()

        unsubscribe = hub.subscribe(gas_kind, lambda sample: (received.append(sample), delivered.set()))
        sample = GenericSensorSample(
            sensor_id="gas-front",
            kind=gas_kind,
            sequence=1,
            values={"concentration_ppm": 12.5},
        )
        backend.publish(sample)

        self.assertTrue(delivered.wait(1.0))
        self.assertIs(hub.latest(gas_kind), sample)
        self.assertEqual(received[0].values["concentration_ppm"], 12.5)
        unsubscribe()
        hub.close()

    def test_read_once_waits_for_next_sample_and_releases_temporary_subscription(self) -> None:
        backend = MockSensorBackend((SensorDescriptor("imu1", SensorKind.IMU),))
        hub = SensorHub("device-1")
        hub.register_backend(backend)
        sample = GenericSensorSample(sensor_id="imu1", kind=SensorKind.IMU, sequence=1, values={"ready": True})

        publisher = threading.Thread(target=lambda: (time.sleep(0.02), backend.publish(sample)))
        publisher.start()
        received = hub.read_once(SensorKind.IMU, timeout_s=1.0)
        publisher.join()

        self.assertIs(received, sample)
        self.assertEqual(hub.status(SensorKind.IMU).subscriber_count, 0)
        hub.close()

    def test_enum_subscription_selects_primary_sensor(self) -> None:
        backend = MockSensorBackend(
            (
                SensorDescriptor("front", SensorKind.RGB_CAMERA, is_primary=True),
                SensorDescriptor("down", SensorKind.RGB_CAMERA),
                SensorDescriptor("lidar1", SensorKind.LIDAR),
            )
        )
        hub = SensorHub("device-1")
        hub.register_backend(backend)

        self.assertEqual(hub.get(SensorKind.RGB_CAMERA).sensor_id, "front")
        self.assertEqual([item.sensor_id for item in hub.list(SensorKind.RGB_CAMERA)], ["down", "front"])
        self.assertTrue(hub.supports(SensorKind.LIDAR))
        hub.close()

    def test_enum_subscription_rejects_ambiguous_sensor_kind(self) -> None:
        backend = MockSensorBackend(
            (
                SensorDescriptor("front", SensorKind.RGB_CAMERA),
                SensorDescriptor("rear", SensorKind.RGB_CAMERA),
            )
        )
        hub = SensorHub("device-1")
        hub.register_backend(backend)

        with self.assertRaises(LookupError):
            hub.subscribe(SensorKind.RGB_CAMERA, lambda sample: None)
        self.assertEqual(hub.status("front").received_samples, 0)
        hub.close()

    def test_default_delivery_modes_are_kind_specific_enums(self) -> None:
        image_options = SensorStreamOptions().resolved(SensorKind.RGB_CAMERA)
        imu_options = SensorStreamOptions().resolved(SensorKind.IMU)

        self.assertIs(image_options.delivery, SensorDeliveryMode.LATEST_ONLY)
        self.assertEqual(image_options.capacity, 1)
        self.assertIs(imu_options.delivery, SensorDeliveryMode.BUFFERED)
        self.assertEqual(imu_options.capacity, 128)

    def test_latest_only_drops_backlog_and_delivers_newest_sample(self) -> None:
        backend = MockSensorBackend((SensorDescriptor("lidar1", SensorKind.LIDAR),))
        hub = SensorHub("device-1")
        hub.register_backend(backend)
        entered = threading.Event()
        release = threading.Event()
        delivered = threading.Event()
        sequences: list[int] = []

        def handler(sample) -> None:
            sequences.append(sample.sequence)
            if sample.sequence == 1:
                entered.set()
                release.wait(1.0)
            if sample.sequence == 4:
                delivered.set()

        unsubscribe = hub.subscribe(SensorKind.LIDAR, handler)
        backend.publish(point_cloud("lidar1", 1, (1.0, 0.0, 0.0)))
        self.assertTrue(entered.wait(1.0))
        for sequence in (2, 3, 4):
            backend.publish(point_cloud("lidar1", sequence, (float(sequence), 0.0, 0.0)))
        release.set()

        self.assertTrue(delivered.wait(1.0))
        self.assertEqual(sequences, [1, 4])
        self.assertGreaterEqual(hub.status(SensorKind.LIDAR).dropped_samples, 2)
        unsubscribe()
        hub.close()

    def test_buffered_subscription_preserves_order(self) -> None:
        backend = MockSensorBackend((SensorDescriptor("imu1", SensorKind.IMU),))
        hub = SensorHub("device-1")
        hub.register_backend(backend)
        received: list[int] = []
        complete = threading.Event()

        def handler(sample) -> None:
            received.append(sample.sequence)
            if len(received) == 3:
                complete.set()

        unsubscribe = hub.subscribe(
            SensorKind.IMU,
            handler,
            options=SensorStreamOptions(delivery=SensorDeliveryMode.BUFFERED, capacity=4),
        )
        for sequence in (1, 2, 3):
            backend.publish(
                GenericSensorSample(
                    sensor_id="imu1",
                    kind=SensorKind.IMU,
                    sequence=sequence,
                    values={"sample": sequence},
                )
            )
        self.assertTrue(complete.wait(1.0))
        self.assertEqual(received, [1, 2, 3])
        unsubscribe()
        hub.close()

    def test_device_sensor_hub_owns_registered_backend_lifecycle(self) -> None:
        backend = MockSensorBackend((SensorDescriptor("lidar1", SensorKind.LIDAR),))
        dog = (
            DeviceFactory.create()
            .unitree_go2()
            .params(network_interface="mock0")
            .build()
        )
        dog.sensors.register_backend(backend)
        self.assertTrue(dog.supports_sensor(SensorKind.LIDAR))
        dog.close()
        with self.assertRaises(RuntimeError):
            backend.publish(point_cloud("lidar1", 1, (1.0, 0.0, 0.0)))

    def test_lidar_obstacle_provider_uses_explicit_sensor_subscription(self) -> None:
        backend = MockSensorBackend(
            (
                SensorDescriptor(
                    "lidar1",
                    SensorKind.LIDAR,
                    frame=CoordinateFrame.BODY,
                    mount=SensorMount(Vector3(0.0, 0.0, -0.2)),
                ),
            )
        )
        dog = DeviceFactory.create().unitree_go2().params(network_interface="mock0").build()
        dog.sensors.register_backend(backend)

        descriptor = dog.sensors.get(SensorKind.LIDAR)
        provider = LidarObstacleProvider(descriptor, pose_provider=dog.state.snapshot)
        dog.avoidance.add_provider(provider)
        unsubscribe = dog.sensors.subscribe(descriptor.sensor_id, provider.observe)
        backend.publish(point_cloud("lidar1", 1, (1.0, 0.0, 0.0), frame=CoordinateFrame.BODY))
        deadline = time.monotonic() + 1.0
        while provider.status().received_frames == 0 and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertEqual(provider.descriptor.sensor_id, "lidar1")
        self.assertEqual(provider.status().received_frames, 1)
        unsubscribe()
        dog.avoidance.remove_provider(provider)
        dog.close()

    def test_lidar_obstacle_provider_requires_id_for_multiple_lidars(self) -> None:
        mount = SensorMount(Vector3(0.0, 0.0, -0.2))
        backend = MockSensorBackend(
            (
                SensorDescriptor("front_lidar", SensorKind.LIDAR, frame=CoordinateFrame.BODY, mount=mount),
                SensorDescriptor("rear_lidar", SensorKind.LIDAR, frame=CoordinateFrame.BODY, mount=mount),
            )
        )
        dog = DeviceFactory.create().unitree_go2().params(network_interface="mock0").build()
        dog.sensors.register_backend(backend)

        with self.assertRaisesRegex(LookupError, "select sensor_id explicitly"):
            dog.sensors.get(SensorKind.LIDAR)
        provider = LidarObstacleProvider(
            dog.sensors.get("front_lidar"),
            pose_provider=dog.state.snapshot,
        )

        self.assertEqual(provider.descriptor.sensor_id, "front_lidar")
        provider.close()
        dog.close()


class FakePx4SensorClient:
    def __init__(self) -> None:
        self.handlers = {}

    def available_sensor_streams(self):
        return ("imu",)

    def subscribe_sensor(self, stream_name, handler):
        self.handlers[stream_name] = handler
        return lambda: self.handlers.pop(stream_name, None)


class AllFakePx4SensorClient(FakePx4SensorClient):
    def available_sensor_streams(self):
        return ("imu", "magnetometer", "gnss", "barometer", "distance", "airspeed")


class AttitudeFakePx4SensorClient(FakePx4SensorClient):
    def available_sensor_streams(self):
        return ("imu", "attitude")


class CameraFakePx4SensorClient(FakePx4SensorClient):
    def __init__(self) -> None:
        super().__init__()
        self.uri_requests = 0
        self.camera_stream_source = SensorSourceKind.SIMULATED

    def available_sensor_streams(self):
        return ("camera",)

    def camera_stream_uri(self):
        self.uri_requests += 1
        return "rtsp://camera.example/live"


class Px4SensorBackendTests(unittest.TestCase):
    def test_px4_integration_does_not_implicitly_merge_projectairsim_camera(self) -> None:
        backend = _Px4IntegratedSensorBackend(CameraFakePx4SensorClient())
        hub = SensorHub("px4-1")
        hub.register_backend(backend)

        descriptor = hub.get(SensorKind.RGB_CAMERA)
        self.assertEqual(descriptor.sensor_id, "px4.camera")
        self.assertFalse(hasattr(backend, "_simulated"))
        hub.close()

    def test_px4_camera_discovers_mavlink_video_uri_and_publishes_standard_frames(self) -> None:
        client = CameraFakePx4SensorClient()
        backend = _Px4MavsdkSensorBackend(client)
        received = []
        opened = []

        class FakeCameraBackend:
            def __init__(self, source, *, sensor_id, frame, source_kind) -> None:
                opened.append((source, sensor_id, frame, source_kind))
                self.closed = False

            def subscribe(self, sensor_id, handler):
                handler(
                    ImageFrame(
                        sensor_id=sensor_id,
                        kind=SensorKind.RGB_CAMERA,
                        sequence=1,
                        frame=CoordinateFrame.BODY_FRD,
                        width=1,
                        height=1,
                        encoding=ImageEncoding.BGR8,
                        data=memoryview(bytes(3)),
                    )
                )
                return lambda: None

            def close(self) -> None:
                self.closed = True

        descriptor = backend.list_sensors()[0]
        self.assertEqual(descriptor.sensor_id, "px4.camera")
        self.assertIs(descriptor.source, SensorSourceKind.SIMULATED)
        with patch("fleetdock.adapters.opencv_camera._OpenCvCameraBackend", FakeCameraBackend):
            unsubscribe = backend.subscribe("px4.camera", received.append)
            unsubscribe()

        self.assertEqual(client.uri_requests, 1)
        self.assertEqual(
            opened,
            [
                (
                    "rtsp://camera.example/live",
                    "px4.camera",
                    CoordinateFrame.BODY_FRD,
                    SensorSourceKind.SIMULATED,
                )
            ],
        )
        self.assertIsInstance(received[0], ImageFrame)
        backend.close()

    def test_px4_fused_imu_is_converted_to_standard_frd_sample(self) -> None:
        client = FakePx4SensorClient()
        hub = SensorHub("px4-1")
        hub.register_backend(_Px4MavsdkSensorBackend(client))
        received = []
        delivered = threading.Event()
        unsubscribe = hub.subscribe(SensorKind.IMU, lambda sample: (received.append(sample), delivered.set()))

        client.handlers["imu"](
            SimpleNamespace(
                timestamp_us=2_000_000,
                acceleration_frd=SimpleNamespace(forward_m_s2=1.0, right_m_s2=2.0, down_m_s2=3.0),
                angular_velocity_frd=SimpleNamespace(forward_rad_s=0.1, right_rad_s=0.2, down_rad_s=0.3),
            )
        )

        self.assertTrue(delivered.wait(1.0))
        self.assertIsInstance(received[0], ImuSample)
        self.assertIs(received[0].frame, CoordinateFrame.BODY_FRD)
        self.assertEqual(received[0].linear_acceleration_m_s2, Vector3(1.0, 2.0, 3.0))
        self.assertEqual(received[0].captured_at_s, 2.0)
        unsubscribe()
        hub.close()

    def test_px4_imu_uses_latest_native_attitude_without_integrating_gyro(self) -> None:
        client = AttitudeFakePx4SensorClient()
        hub = SensorHub("px4-1")
        hub.register_backend(_Px4MavsdkSensorBackend(client))
        received = []
        delivered = threading.Event()
        unsubscribe = hub.subscribe(
            SensorKind.IMU,
            lambda sample: (received.append(sample), delivered.set() if len(received) == 2 else None),
        )

        native_imu = SimpleNamespace(
            timestamp_us=2_000_000,
            acceleration_frd=SimpleNamespace(forward_m_s2=1.0, right_m_s2=2.0, down_m_s2=3.0),
            angular_velocity_frd=SimpleNamespace(forward_rad_s=0.1, right_rad_s=0.2, down_rad_s=0.3),
        )
        with patch(
            "fleetdock.adapters.px4_mavlink.sensors.time.monotonic",
            side_effect=(100.0, 100.1, 101.2),
        ):
            client.handlers["attitude"](SimpleNamespace(roll_deg=12.0, pitch_deg=-4.0, yaw_deg=135.0))
            client.handlers["imu"](native_imu)
            client.handlers["imu"](native_imu)

        self.assertTrue(delivered.wait(1.0))
        self.assertEqual(received[0].orientation_rpy_deg, Vector3(12.0, -4.0, 135.0))
        self.assertIsNone(received[1].orientation_rpy_deg)
        unsubscribe()
        self.assertEqual(client.handlers, {})
        hub.close()

    def test_px4_auxiliary_streams_use_typed_si_samples(self) -> None:
        client = AllFakePx4SensorClient()
        backend = _Px4MavsdkSensorBackend(client)
        hub = SensorHub("px4-1")
        hub.register_backend(backend)
        received = {}
        delivered = threading.Event()

        def collect(sample) -> None:
            received[sample.kind] = sample
            if len(received) == 5:
                delivered.set()

        unsubscribes = [
            hub.subscribe(kind, collect)
            for kind in (
                SensorKind.MAGNETOMETER,
                SensorKind.GNSS,
                SensorKind.BAROMETER,
                SensorKind.DISTANCE,
                SensorKind.AIRSPEED,
            )
        ]
        client.handlers["magnetometer"](
            SimpleNamespace(
                timestamp_us=1_000_000,
                magnetic_field_frd=SimpleNamespace(forward_gauss=1.0, right_gauss=2.0, down_gauss=3.0),
                temperature_degc=24.0,
            )
        )
        client.handlers["gnss"](
            SimpleNamespace(
                timestamp_us=2_000_000,
                latitude_deg=31.2,
                longitude_deg=121.5,
                absolute_altitude_m=100.0,
                horizontal_uncertainty_m=0.5,
                vertical_uncertainty_m=0.8,
                velocity_m_s=4.0,
                cog_deg=90.0,
                fix_type=SimpleNamespace(name="FIX_3D"),
            )
        )
        client.handlers["barometer"](
            SimpleNamespace(
                timestamp_us=3_000_000,
                absolute_pressure_hpa=1013.25,
                differential_pressure_hpa=2.5,
                temperature_deg=23.0,
            )
        )
        client.handlers["distance"](
            SimpleNamespace(
                minimum_distance_m=0.2,
                maximum_distance_m=30.0,
                current_distance_m=4.5,
                orientation="forward",
            )
        )
        client.handlers["airspeed"](SimpleNamespace(airspeed_m_s=12.0, groundspeed_m_s=10.0))

        self.assertTrue(delivered.wait(1.0))
        self.assertIsInstance(received[SensorKind.MAGNETOMETER], MagnetometerSample)
        self.assertEqual(received[SensorKind.MAGNETOMETER].magnetic_field_t.x, 1e-4)
        self.assertIsInstance(received[SensorKind.GNSS], GnssSample)
        self.assertEqual(received[SensorKind.GNSS].fix_type, "fix_3d")
        self.assertIsInstance(received[SensorKind.BAROMETER], BarometerSample)
        self.assertEqual(received[SensorKind.BAROMETER].absolute_pressure_pa, 101325.0)
        self.assertIsInstance(received[SensorKind.DISTANCE], DistanceSample)
        self.assertIsInstance(received[SensorKind.AIRSPEED], AirspeedSample)
        for unsubscribe in unsubscribes:
            unsubscribe()
        hub.close()

    def test_px4_backend_client_replacement_and_lifecycle_boundaries(self) -> None:
        backend = _Px4MavsdkSensorBackend(FakePx4SensorClient())
        replacement = FakePx4SensorClient()
        backend.replace_client(replacement)
        unsubscribe = backend.subscribe("px4.imu", lambda sample: None)
        with self.assertRaises(RuntimeError):
            backend.replace_client(FakePx4SensorClient())
        unsubscribe()
        backend.close()
        backend.close()
        with self.assertRaises(RuntimeError):
            backend.subscribe("px4.imu", lambda sample: None)


class FakeCameraFrame:
    shape = (1, 2, 3)

    @staticmethod
    def tobytes():
        return bytes((0, 1, 2, 3, 4, 5))


class FakeVideoCapture:
    def __init__(self) -> None:
        self.released = False
        self._frames = [FakeCameraFrame()]

    def isOpened(self):
        return True

    def read(self):
        if self._frames:
            return True, self._frames.pop(0)
        return False, None

    def release(self):
        self.released = True


class OpenCvCameraBackendTests(unittest.TestCase):
    def test_simulated_rtsp_prefers_gstreamer_low_latency_capture(self) -> None:
        capture = FakeVideoCapture()
        backend = _OpenCvCameraBackend(
            "rtsp://127.0.0.1:8554/camera",
            source_kind=SensorSourceKind.SIMULATED,
        )

        with patch("fleetdock.adapters.opencv_camera._GStreamerH264Capture", return_value=capture) as create:
            selected = backend._create_capture()

        self.assertIs(selected, capture)
        create.assert_called_once_with("rtsp://127.0.0.1:8554/camera")
        backend.close()

    def test_camera_backend_converts_bgr_frame_without_exposing_stream_address(self) -> None:
        capture = FakeVideoCapture()
        backend = _OpenCvCameraBackend(
            "rtsp://user:secret@example.invalid/live",
            capture_factory=lambda source: capture,
        )
        hub = SensorHub("camera-device")
        hub.register_backend(backend)
        descriptor = hub.get(SensorKind.RGB_CAMERA)
        self.assertNotIn("source", descriptor.metadata)
        received = []
        delivered = threading.Event()

        unsubscribe = hub.subscribe(SensorKind.RGB_CAMERA, lambda frame: (received.append(frame), delivered.set()))

        self.assertTrue(delivered.wait(1.0))
        self.assertIsInstance(received[0], ImageFrame)
        self.assertEqual((received[0].width, received[0].height), (2, 1))
        unsubscribe()
        hub.close()
        self.assertTrue(capture.released)


class FakeProjectAirSimClient:
    def __init__(self) -> None:
        self.topics = {
            "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/lidar1/lidar": {},
            "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/Front/scene_camera": {},
            "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/Front/depth_camera": {},
            "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/Left/scene_camera": {},
            "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/Right/scene_camera": {},
            "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/radar1/radar_detections": {},
            "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/imu1/imu_kinematics": {},
            "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/gps1/gps": {},
            "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/mag1/magnetometer": {},
            "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/baro1/barometer": {},
            "/Sim/SceneFleetDock1Drone/robots/Drone1/actual_pose": {},
        }
        self.callbacks = {}
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def get_topic_info(self) -> None:
        return None

    def subscribe(self, topic, callback) -> None:
        self.callbacks[topic] = callback

    def unsubscribe(self, topic) -> None:
        self.callbacks.pop(topic, None)

    def disconnect(self) -> None:
        self.connected = False


class FakeProjectAirSimTopic:
    def __init__(self, path: str) -> None:
        self.path = path


class ProjectAirSimSensorBackendTests(unittest.TestCase):
    def test_projectairsim_registration_is_not_exposed_by_sensor_hub(self) -> None:
        drone = DeviceFactory.create().mock_drone().build()

        self.assertFalse(hasattr(drone.sensors, "register_projectairsim"))

        drone.close()

    def test_discovers_and_converts_lidar_without_exposing_topic(self) -> None:
        client = FakeProjectAirSimClient()
        backend = _ProjectAirSimSensorBackend(
            "Drone1",
            scene_id="SceneFleetDock1Drone",
            client=client,
        )
        hub = SensorHub("drone-1")
        hub.register_backend(backend)
        received = []
        delivered = threading.Event()

        descriptors = hub.list()
        self.assertEqual(
            {item.kind for item in descriptors},
            {
                SensorKind.LIDAR,
                SensorKind.RGB_CAMERA,
                SensorKind.DEPTH_CAMERA,
                SensorKind.STEREO_CAMERA,
                SensorKind.RADAR,
                SensorKind.IMU,
                SensorKind.GNSS,
                SensorKind.MAGNETOMETER,
                SensorKind.BAROMETER,
                ExtensionSensorKind("vendor.projectairsim.actual_pose"),
            },
        )
        self.assertIs(hub.get(SensorKind.LIDAR).frame, CoordinateFrame.BODY)
        unsubscribe = hub.subscribe(
            SensorKind.LIDAR,
            lambda sample: (received.append(sample), delivered.set()),
        )
        topic = "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/lidar1/lidar"
        client.callbacks[topic](
            FakeProjectAirSimTopic(topic),
            {"point_cloud": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]},
        )

        self.assertTrue(delivered.wait(1.0))
        self.assertIsInstance(received[0], PointCloudFrame)
        self.assertEqual(received[0].point_count, 2)
        self.assertEqual(received[0].xyz_points()[1], (4.0, 5.0, 6.0))
        self.assertIsNone(received[0].native_data)
        unsubscribe()
        hub.close()

    def test_exposes_projectairsim_actual_pose_without_estimating_vehicle_state(self) -> None:
        client = FakeProjectAirSimClient()
        hub = SensorHub("drone-1")
        hub.register_backend(
            _ProjectAirSimSensorBackend(
                "Drone1",
                scene_id="SceneFleetDock1Drone",
                client=client,
            )
        )
        received = []
        delivered = threading.Event()
        descriptor = hub.get(ExtensionSensorKind("vendor.projectairsim.actual_pose"))
        self.assertEqual(descriptor.sensor_id, "projectairsim.actual_pose")
        self.assertIs(descriptor.frame, CoordinateFrame.LOCAL_NED)
        unsubscribe = hub.subscribe(
            descriptor.sensor_id,
            lambda sample: (received.append(sample), delivered.set()),
        )
        topic = "/Sim/SceneFleetDock1Drone/robots/Drone1/actual_pose"
        client.callbacks[topic](
            FakeProjectAirSimTopic(topic),
            {
                "translation": {"x": 12.0, "y": -3.5, "z": -8.0},
                "rotation": {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0},
            },
        )

        self.assertTrue(delivered.wait(1.0))
        self.assertIsInstance(received[0], GenericSensorSample)
        self.assertEqual(received[0].values["translation"]["z"], -8.0)
        self.assertEqual(received[0].values["rotation"]["w"], 1.0)
        unsubscribe()
        hub.close()

    def test_projectairsim_reinitialize_discards_callbacks_from_old_subscription_generation(self) -> None:
        client = FakeProjectAirSimClient()
        backend = _ProjectAirSimSensorBackend(
            "Drone1",
            scene_id="SceneFleetDock1Drone",
            client=client,
        )
        backend.list_sensors()
        topic = "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/imu1/imu_kinematics"
        first_samples = []
        unsubscribe_first = backend.subscribe("imu1", first_samples.append)
        stale_callback = client.callbacks[topic]
        unsubscribe_first()

        second_samples = []
        unsubscribe_second = backend.subscribe("imu1", second_samples.append)
        message = {
            "angular_velocity": {"x": 0.1, "y": 0.2, "z": 0.3},
            "linear_acceleration": {"x": 1.0, "y": 2.0, "z": 3.0},
        }
        stale_callback(FakeProjectAirSimTopic(topic), message)
        self.assertEqual(first_samples, [])
        self.assertEqual(second_samples, [])

        client.callbacks[topic](FakeProjectAirSimTopic(topic), message)
        self.assertEqual(len(second_samples), 1)
        unsubscribe_second()
        backend.close()

    def test_converts_projectairsim_image_metadata_and_nanosecond_time(self) -> None:
        client = FakeProjectAirSimClient()
        hub = SensorHub("drone-1")
        hub.register_backend(_ProjectAirSimSensorBackend("Drone1", scene_id="SceneFleetDock1Drone", client=client))
        received = []
        delivered = threading.Event()
        unsubscribe = hub.subscribe(
            SensorKind.RGB_CAMERA,
            lambda sample: (received.append(sample), delivered.set()),
        )
        topic = "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/Front/scene_camera"
        client.callbacks[topic](
            topic,
            {
                "width": 2,
                "height": 1,
                # Project AirSim 0.2.0 的 RGB 消息可能把 step 报告为 1，SDK 应按缓冲区恢复实际行步长。
                "step": 1,
                "encoding": "BGR",
                "time_stamp": 2_000_000_000,
                "data": bytes((0, 1, 2, 3, 4, 5)),
            },
        )

        self.assertTrue(delivered.wait(1.0))
        self.assertIsInstance(received[0], ImageFrame)
        self.assertIs(received[0].encoding, ImageEncoding.BGR8)
        self.assertEqual((received[0].width, received[0].height, received[0].stride_bytes), (2, 1, 6))
        self.assertEqual(received[0].captured_at_s, 2.0)
        unsubscribe()
        hub.close()

    def test_stereo_and_single_camera_share_native_topic_lifecycle(self) -> None:
        client = FakeProjectAirSimClient()
        hub = SensorHub("drone-1")
        hub.register_backend(_ProjectAirSimSensorBackend("Drone1", scene_id="SceneFleetDock1Drone", client=client))
        single_received = []
        single_event = threading.Event()
        stereo_event = threading.Event()
        unsubscribe_single = hub.subscribe(
            "Left",
            lambda sample: (single_received.append(sample), single_event.set()),
        )
        unsubscribe_stereo = hub.subscribe(SensorKind.STEREO_CAMERA, lambda sample: stereo_event.set())
        left_topic = "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/Left/scene_camera"
        right_topic = "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/Right/scene_camera"
        message = {"width": 1, "height": 1, "step": 3, "encoding": "BGR", "data": b"\x00\x00\x00"}
        client.callbacks[left_topic](left_topic, message)
        client.callbacks[right_topic](right_topic, message)
        self.assertTrue(single_event.wait(1.0))
        self.assertTrue(stereo_event.wait(1.0))

        unsubscribe_stereo()
        single_event.clear()
        client.callbacks[left_topic](left_topic, message)
        self.assertTrue(single_event.wait(1.0))
        self.assertGreaterEqual(len(single_received), 2)
        unsubscribe_single()
        hub.close()

    def test_converts_projectairsim_radar_detection_to_strong_frame(self) -> None:
        client = FakeProjectAirSimClient()
        hub = SensorHub("drone-1")
        hub.register_backend(_ProjectAirSimSensorBackend("Drone1", scene_id="SceneFleetDock1Drone", client=client))
        received = []
        delivered = threading.Event()
        unsubscribe = hub.subscribe(SensorKind.RADAR, lambda sample: (received.append(sample), delivered.set()))
        topic = "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/radar1/radar_detections"
        client.callbacks[topic](
            topic,
            {"detections": [{"range": 12.0, "azimuth": 0.1, "elevation": 0.0, "velocity": -2.0}]},
        )

        self.assertTrue(delivered.wait(1.0))
        self.assertIsInstance(received[0], RadarFrame)
        self.assertEqual(received[0].detections[0].range_m, 12.0)
        unsubscribe()
        hub.close()

    def test_prefers_onboard_camera_over_external_chase_camera(self) -> None:
        client = FakeProjectAirSimClient()
        client.topics = {
            "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/Chase/scene_camera": {},
            "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/DownCamera/scene_camera": {},
        }
        backend = _ProjectAirSimSensorBackend(
            "Drone1",
            scene_id="SceneFleetDock1Drone",
            client=client,
        )
        hub = SensorHub("drone-1")
        hub.register_backend(backend)

        self.assertEqual(hub.get(SensorKind.RGB_CAMERA).sensor_id, "DownCamera")

        hub.close()

    def test_prefers_front_onboard_camera_over_down_camera(self) -> None:
        client = FakeProjectAirSimClient()
        client.topics = {
            "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/Chase/scene_camera": {},
            "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/DownCamera/scene_camera": {},
            "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/FrontDepthCamera/scene_camera": {},
            "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/FrontCamera/scene_camera": {},
        }
        backend = _ProjectAirSimSensorBackend(
            "Drone1",
            scene_id="SceneFleetDock1Drone",
            client=client,
        )
        hub = SensorHub("drone-1")
        hub.register_backend(backend)

        primary = hub.get(SensorKind.RGB_CAMERA)
        self.assertEqual(primary.sensor_id, "FrontCamera")
        self.assertEqual(primary.frame, CoordinateFrame.BODY)
        self.assertEqual((primary.camera_intrinsics.width_px, primary.camera_intrinsics.height_px), (960, 540))

        hub.close()

    def test_converts_projectairsim_navigation_and_inertial_sensors_to_typed_samples(self) -> None:
        client = FakeProjectAirSimClient()
        hub = SensorHub("drone-1")
        hub.register_backend(_ProjectAirSimSensorBackend("Drone1", scene_id="SceneFleetDock1Drone", client=client))
        received = {}
        delivered = threading.Event()

        def collect(sample) -> None:
            received[sample.kind] = sample
            if len(received) == 4:
                delivered.set()

        unsubscribes = [
            hub.subscribe(kind, collect)
            for kind in (SensorKind.IMU, SensorKind.GNSS, SensorKind.MAGNETOMETER, SensorKind.BAROMETER)
        ]
        messages = {
            "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/imu1/imu_kinematics": {
                "angular_velocity": {"x": 0.1, "y": 0.2, "z": 0.3},
                "linear_acceleration": {"x": 1.0, "y": 2.0, "z": 3.0},
            },
            "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/gps1/gps": {
                "latitude_deg": 31.2,
                "longitude_deg": 121.5,
                "altitude_m": 80.0,
                "satellite_count": 12,
            },
            "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/mag1/magnetometer": {
                "magnetic_field_t": {"x": 1e-5, "y": 2e-5, "z": 3e-5},
            },
            "/Sim/SceneFleetDock1Drone/robots/Drone1/sensors/baro1/barometer": {
                "absolute_pressure_pa": 101325.0,
                "temperature_c": 22.0,
            },
        }
        for topic, message in messages.items():
            client.callbacks[topic](topic, message)

        self.assertTrue(delivered.wait(1.0))
        self.assertIsInstance(received[SensorKind.IMU], ImuSample)
        self.assertIsInstance(received[SensorKind.GNSS], GnssSample)
        self.assertIsInstance(received[SensorKind.MAGNETOMETER], MagnetometerSample)
        self.assertIsInstance(received[SensorKind.BAROMETER], BarometerSample)
        for unsubscribe in unsubscribes:
            unsubscribe()
        hub.close()

    def test_point_cloud_corridor_analyzer_selects_and_tracks_direction(self) -> None:
        analyzer = PointCloudCorridorAnalyzer(
            corridor_half_width_m=1.0,
            corridor_half_height_m=1.0,
            history_size=2,
        )
        analyzer.observe(
            point_cloud(
                "lidar1",
                1,
                (10.0, 0.0, 0.0, 10.0, 3.0, 0.0, float("nan"), 0.0, 0.0),
            )
        )

        self.assertTrue(analyzer.wait_for_sample(0.0))
        candidates = analyzer.candidate_directions(
            min_obstacle_distance_m=8.0,
            max_obstacle_distance_m=12.0,
        )
        self.assertGreaterEqual(len(candidates), 1)

        direction = analyzer.select_direction(
            min_obstacle_distance_m=8.0,
            max_obstacle_distance_m=12.0,
            preferred_obstacle_distance_m=10.0,
        )
        self.assertIsNotNone(direction)
        assert direction is not None
        self.assertAlmostEqual(direction.x, 1.0)
        self.assertAlmostEqual(direction.y, 0.0)
        self.assertAlmostEqual(analyzer.nearest_distance(direction), 10.0)

        rotated = direction.rotated(90.0)
        self.assertAlmostEqual(rotated.x, 0.0, places=6)
        self.assertAlmostEqual(rotated.y, 1.0, places=6)
        self.assertEqual(rotated.obstacle_distance_m, 10.0)

    def test_point_cloud_corridor_analyzer_waits_for_newer_sequence(self) -> None:
        analyzer = PointCloudCorridorAnalyzer()
        analyzer.observe(point_cloud("lidar1", 3, (2.0, 0.0, 0.0)))

        self.assertFalse(analyzer.wait_for_sample(0.0, after_sequence=3))
        analyzer.observe(point_cloud("lidar1", 4, (1.0, 0.0, 0.0)))
        self.assertTrue(analyzer.wait_for_sample(0.0, after_sequence=3))
        self.assertEqual(analyzer.latest_sequence, 4)

    def test_point_cloud_tracker_transforms_body_points_and_keeps_voxel_ids_stable(self) -> None:
        pose = SimpleNamespace(
            local_x_m=100.0,
            local_y_m=50.0,
            local_z_m=-10.0,
            local_position_frame=CoordinateFrame.LOCAL_NED,
            local_position_updated_at_s=time.monotonic(),
            roll_deg=0.0,
            pitch_deg=0.0,
            yaw_deg=90.0,
        )
        tracker = PointCloudObstacleTracker(
            output_frame=CoordinateFrame.LOCAL_NED,
            pose_provider=lambda: pose,
            assume_unknown_body_frame=True,
            min_range_m=0.1,
            voxel_size_m=2.0,
            max_pose_age_s=5.0,
        )

        self.assertTrue(
            tracker.observe(
                point_cloud(
                    "lidar1",
                    1,
                    (10.0, 0.0, 0.0, 10.0, -0.2, 0.0),
                    frame=CoordinateFrame.UNKNOWN,
                )
            )
        )
        first = tracker.snapshot()
        self.assertEqual(len(first.obstacles), 1)
        self.assertAlmostEqual(first.obstacles[0].position_m.x, 100.1, places=1)
        self.assertAlmostEqual(first.obstacles[0].position_m.y, 60.0, places=1)
        obstacle_id = first.obstacles[0].obstacle_id

        pose.local_position_updated_at_s = time.monotonic()
        self.assertTrue(
            tracker.observe(
                point_cloud(
                    "lidar1",
                    2,
                    (10.1, -0.1, 0.0),
                    frame=CoordinateFrame.UNKNOWN,
                )
            )
        )
        self.assertEqual(tracker.snapshot().obstacles[0].obstacle_id, obstacle_id)
        status = tracker.status()
        self.assertEqual((status.received_frames, status.processed_frames, status.dropped_frames), (2, 2, 0))
        self.assertEqual(status.obstacle_count, 1)
        tracker.close()

    def test_point_cloud_tracker_rejects_stale_pose_without_replacing_snapshot(self) -> None:
        pose = SimpleNamespace(
            local_x_m=0.0,
            local_y_m=0.0,
            local_z_m=-5.0,
            local_position_frame=CoordinateFrame.LOCAL_NED,
            local_position_updated_at_s=time.monotonic() - 10.0,
            roll_deg=0.0,
            pitch_deg=0.0,
            yaw_deg=0.0,
        )
        tracker = PointCloudObstacleTracker(
            output_frame=CoordinateFrame.LOCAL_NED,
            pose_provider=lambda: pose,
            assume_unknown_body_frame=True,
            max_pose_age_s=1.0,
        )

        processed = tracker.observe(
            point_cloud("lidar1", 1, (5.0, 0.0, 0.0), frame=CoordinateFrame.UNKNOWN)
        )

        self.assertFalse(processed)
        self.assertEqual(tracker.snapshot().obstacles, ())
        self.assertEqual(tracker.status().dropped_frames, 1)
        self.assertIn("stale", tracker.status().last_error or "")

    def test_point_cloud_tracker_excludes_airframe_self_returns(self) -> None:
        pose = SimpleNamespace(
            local_x_m=0.0,
            local_y_m=0.0,
            local_z_m=-10.0,
            local_position_frame=CoordinateFrame.LOCAL_NED,
            local_position_updated_at_s=time.monotonic(),
            roll_deg=0.0,
            pitch_deg=0.0,
            yaw_deg=0.0,
        )
        tracker = PointCloudObstacleTracker(
            output_frame=CoordinateFrame.LOCAL_NED,
            pose_provider=lambda: pose,
            body_exclusion_half_extents_m=Vector3(2.0, 2.0, 1.0),
            min_range_m=0.1,
            max_pose_age_s=5.0,
        )

        self.assertTrue(
            tracker.observe(
                point_cloud(
                    "lidar1",
                    1,
                    (1.5, 0.0, 0.0, 5.0, 0.0, 0.0),
                    frame=CoordinateFrame.BODY,
                )
            )
        )
        self.assertEqual(tracker.status().retained_points, 1)
        self.assertEqual(len(tracker.snapshot().obstacles), 1)
        self.assertAlmostEqual(tracker.snapshot().obstacles[0].position_m.x, 5.0)

    def test_lidar_obstacle_provider_processes_latest_frame_and_clusters_voxels(self) -> None:
        pose = SimpleNamespace(
            local_x_m=0.0,
            local_y_m=0.0,
            local_z_m=-12.0,
            local_position_frame=CoordinateFrame.LOCAL_NED,
            local_position_updated_at_s=time.monotonic(),
            roll_deg=0.0,
            pitch_deg=0.0,
            yaw_deg=0.0,
        )
        descriptor = SensorDescriptor(
            "lidar1",
            SensorKind.LIDAR,
            frame=CoordinateFrame.BODY,
            mount=SensorMount(Vector3(0.0, 0.0, 0.0)),
        )
        config = LidarObstacleConfig.for_preset(LidarAvoidancePreset.LOW_ALTITUDE)
        provider = LidarObstacleProvider(descriptor, pose_provider=lambda: pose, config=config)
        provider.observe(
            point_cloud(
                "lidar1",
                1,
                (5.0, 0.0, 0.1, 5.0, 0.0, -1.1, 20.0, 0.0, 0.0),
                frame=CoordinateFrame.BODY,
            )
        )

        readiness = provider.wait_until_ready(1.0)
        directions = provider.candidate_directions(
            DepartureDirectionPolicy(
                preferred_clearance_m=5.0,
                minimum_clearance_m=4.0,
                clearance_step_m=1.0,
                obstacle_range_m=30.0,
                safety_radius_m=1.0,
            )
        )

        self.assertTrue(readiness.ready, readiness.reason)
        self.assertGreaterEqual(provider.status().raw_obstacle_count, 2)
        self.assertLess(len(provider.snapshot().obstacles), provider.status().raw_obstacle_count)
        self.assertTrue(directions)
        self.assertAlmostEqual(directions[0].heading_deg, 0.0, places=5)
        provider.close()

    def test_lidar_departure_probe_matches_route_lookahead(self) -> None:
        """确保出口验证覆盖航线前视段内、但不在原始点云走廊内的侧向体素。"""

        pose = SimpleNamespace(
            local_x_m=0.0,
            local_y_m=0.0,
            local_z_m=-12.0,
            local_position_frame=CoordinateFrame.LOCAL_NED,
            local_position_updated_at_s=time.monotonic(),
            roll_deg=0.0,
            pitch_deg=0.0,
            yaw_deg=0.0,
        )
        descriptor = SensorDescriptor(
            "lidar1",
            SensorKind.LIDAR,
            frame=CoordinateFrame.BODY,
            mount=SensorMount(Vector3(0.0, 0.0, 0.0)),
        )
        provider = LidarObstacleProvider(descriptor, pose_provider=lambda: pose)
        provider.observe(
            point_cloud(
                "lidar1",
                1,
                (10.0, 0.0, 0.0, 6.0, 3.2, 0.0),
                frame=CoordinateFrame.BODY,
            )
        )
        self.assertTrue(provider.wait_until_ready(1.0).ready)

        short_probe = provider.candidate_directions(
            DepartureDirectionPolicy(departure_probe_m=2.0)
        )
        route_probe = provider.candidate_directions(
            DepartureDirectionPolicy(departure_probe_m=8.0)
        )

        self.assertTrue(any(abs(item.heading_deg) < 5.0 for item in short_probe))
        self.assertFalse(any(abs(item.heading_deg) < 5.0 for item in route_probe))
        provider.close()

    def test_lidar_safe_direction_reuses_standard_avoidance_evaluation(self) -> None:
        pose = SimpleNamespace(
            local_x_m=1.0,
            local_y_m=2.0,
            local_z_m=-8.0,
            local_position_frame=CoordinateFrame.LOCAL_NED,
            local_position_updated_at_s=time.monotonic(),
            roll_deg=0.0,
            pitch_deg=0.0,
            yaw_deg=0.0,
        )
        descriptor = SensorDescriptor(
            "lidar1",
            SensorKind.LIDAR,
            frame=CoordinateFrame.BODY,
            mount=SensorMount(Vector3(0.0, 0.0, 0.0)),
        )
        provider = LidarObstacleProvider(descriptor, pose_provider=lambda: pose)
        provider.observe(
            point_cloud(
                "lidar1",
                1,
                (5.0, 0.0, 0.1, 5.0, 0.0, -1.1, 20.0, 0.0, 0.0),
                frame=CoordinateFrame.BODY,
            )
        )
        self.assertTrue(provider.wait_until_ready(1.0).ready)
        calls = []

        class AllowingAvoidance:
            def evaluate_segment(self, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(allowed=True)

        direction = provider.select_safe_direction(
            avoidance=AllowingAvoidance(),
            state=pose,
            direction_policy=DepartureDirectionPolicy(
                preferred_clearance_m=5.0,
                minimum_clearance_m=4.0,
                clearance_step_m=1.0,
                obstacle_range_m=30.0,
                safety_radius_m=1.0,
                departure_probe_m=2.0,
            ),
            avoidance_policy=AvoidancePolicy(),
            speed_m_s=2.0,
        )

        self.assertIsNotNone(direction)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["frame"], CoordinateFrame.LOCAL_NED)
        self.assertFalse(calls[0]["allow_replan"])
        provider.close()

class AdditionalAvoidanceProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pose = SimpleNamespace(
            local_x_m=0.0,
            local_y_m=0.0,
            local_z_m=-10.0,
            local_position_frame=CoordinateFrame.LOCAL_NED,
            local_position_updated_at_s=time.monotonic(),
            roll_deg=0.0,
            pitch_deg=0.0,
            yaw_deg=0.0,
        )

    def test_avoidance_namespace_exposes_stable_sensor_converters(self) -> None:
        self.assertIs(avoidance.LidarObstacleProvider, LidarObstacleProvider)
        self.assertIs(avoidance.DepthCameraObstacleProvider, DepthCameraObstacleProvider)
        self.assertIs(avoidance.RadarObstacleProvider, RadarObstacleProvider)
        self.assertIs(avoidance.DistanceObstacleProvider, DistanceObstacleProvider)
        self.assertEqual(avoidance.LidarObstacleProvider.__module__, "fleetdock.avoidance.lidar")
        self.assertEqual(avoidance.DepthCameraObstacleProvider.__module__, "fleetdock.avoidance.vision")
        self.assertEqual(avoidance.RadarObstacleProvider.__module__, "fleetdock.avoidance.ranging")

    def test_depth_camera_provider_converts_metric_pixels(self) -> None:
        intrinsics = CameraIntrinsics.from_horizontal_fov(8, 8, 90.0)
        descriptor = SensorDescriptor(
            "depth1",
            SensorKind.DEPTH_CAMERA,
            frame=CoordinateFrame.BODY,
            mount=SensorMount(Vector3(0.2, 0.0, 0.0)),
            camera_intrinsics=intrinsics,
        )
        provider = DepthCameraObstacleProvider(
            descriptor,
            pose_provider=lambda: self.pose,
            config=DepthObstacleConfig(intrinsics=intrinsics, pixel_stride=1, voxel_size_m=1.0),
        )
        values = array("f", [8.0] * 64)
        provider.observe(
            ImageFrame(
                sensor_id="depth1",
                kind=SensorKind.DEPTH_CAMERA,
                sequence=1,
                frame=CoordinateFrame.BODY,
                width=8,
                height=8,
                encoding=ImageEncoding.DEPTH_F32_M,
                data=memoryview(values),
            )
        )
        deadline = time.monotonic() + 1.0
        while provider.status().processed_frames == 0 and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertGreater(provider.status().obstacle_count, 0, provider.status().last_error)
        provider.close()

    def test_radar_and_distance_providers_output_standard_obstacles(self) -> None:
        mount = SensorMount(Vector3(0.0, 0.0, 0.0))
        radar_descriptor = SensorDescriptor("radar1", SensorKind.RADAR, frame=CoordinateFrame.BODY, mount=mount)
        radar = RadarObstacleProvider(radar_descriptor, pose_provider=lambda: self.pose)
        self.assertIsInstance(radar, avoidance.SensorObstacleProvider)
        radar.observe(
            RadarFrame(
                sensor_id="radar1",
                kind=SensorKind.RADAR,
                sequence=1,
                frame=CoordinateFrame.BODY,
                detections=(RadarDetection(12.0, 0.0, 0.0, -2.0),),
            )
        )
        self.assertEqual(len(radar.snapshot().obstacles), 1)
        self.assertAlmostEqual(radar.snapshot().obstacles[0].position_m.x, 12.0)
        self.assertTrue(radar.status().ready)
        self.assertFalse(radar.status().stale)
        self.assertEqual(radar.status().received_samples, 1)
        self.assertIsInstance(radar.status(), avoidance.ObstacleProviderStatus)

        distance_descriptor = SensorDescriptor("distance1", SensorKind.DISTANCE, frame=CoordinateFrame.BODY, mount=mount)
        distance = DistanceObstacleProvider(distance_descriptor, pose_provider=lambda: self.pose)
        distance.observe(
            DistanceSample(
                sensor_id="distance1",
                kind=SensorKind.DISTANCE,
                sequence=1,
                frame=CoordinateFrame.BODY,
                distance_m=5.0,
            )
        )
        self.assertEqual(len(distance.snapshot().obstacles), 1)
        self.assertAlmostEqual(distance.snapshot().obstacles[0].position_m.x, 5.0)
        self.assertTrue(distance.status().ready)
        self.assertFalse(distance.status().stale)
        self.assertEqual(distance.status().latest_sequence, 1)
        radar.close()
        distance.close()


if __name__ == "__main__":
    unittest.main()
