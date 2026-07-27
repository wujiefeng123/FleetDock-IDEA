"""Project AirSim 原生 topic 到 FleetDock 标准传感器流的适配后端。"""

from __future__ import annotations

from array import array
from dataclasses import replace
import logging
from threading import RLock
from typing import Any, Callable, Mapping, Optional, Sequence

from fleetdock.events import CoordinateFrame, Vector3
from fleetdock.sensors import (
    CameraIntrinsics,
    BarometerSample,
    DistanceSample,
    ExtensionSensorKind,
    GenericSensorSample,
    GnssSample,
    ImageEncoding,
    ImageFrame,
    ImuSample,
    MagnetometerSample,
    PointCloudEncoding,
    PointCloudFrame,
    RadarDetection,
    RadarFrame,
    RadarTrack,
    SensorDescriptor,
    SensorHandler,
    SensorKind,
    SensorMount,
    SensorSample,
    SensorSourceKind,
    SensorType,
    SensorUnsubscribe,
    StereoCalibration,
    StereoFrame,
)

logger = logging.getLogger(__name__)

_PROJECTAIRSIM_POSE_KIND = ExtensionSensorKind("vendor.projectairsim.actual_pose")


class _ProjectAirSimSensorBackend:
    """把指定 Project AirSim 机器人传感器转换为标准 SensorHub 后端。

    ``robot_name`` 是场景中的机器人名称；``scene_id`` 未指定时从当前 topic 自动匹配该机器人。
    ``client`` 可接管调用方已创建的 Project AirSim client；未传时由后端按需导入并创建 client。
    首次发现时连接本机 Project AirSim 服务，最后由 ``device.close()`` 或 ``close()`` 断开。
    """

    _TOPIC_KINDS = {
        "scene_camera": SensorKind.RGB_CAMERA,
        "depth_camera": SensorKind.DEPTH_CAMERA,
        "depth_planar_camera": SensorKind.DEPTH_CAMERA,
        "lidar": SensorKind.LIDAR,
        "imu_kinematics": SensorKind.IMU,
        "gps": SensorKind.GNSS,
        "magnetometer": SensorKind.MAGNETOMETER,
        "barometer": SensorKind.BAROMETER,
        "distance_sensor": SensorKind.DISTANCE,
        "radar_detections": SensorKind.RADAR,
        "radar_tracks": SensorKind.RADAR,
    }
    _STEREO_MAX_SKEW_S = 0.05

    def __init__(
        self,
        robot_name: str,
        *,
        scene_id: Optional[str] = None,
        client: Any = None,
        client_factory: Optional[Callable[[], Any]] = None,
        preserve_native_data: bool = False,
    ) -> None:
        if not robot_name:
            raise ValueError("robot_name must not be empty.")
        self.robot_name = robot_name
        self.scene_id = scene_id
        self._client = client
        self._client_factory = client_factory
        self._owns_client = client is None
        self._preserve_native_data = preserve_native_data
        self._connected = False
        self._closed = False
        self._lock = RLock()
        self._descriptors: tuple[SensorDescriptor, ...] = ()
        self._topics: dict[str, str | tuple[str, str]] = {}
        self._sequences: dict[str, int] = {}
        self._active_topics: set[str] = set()
        self._topic_handlers: dict[str, list[Callable[[Any, Any], None]]] = {}
        self._topic_generations: dict[str, int] = {}

    def list_sensors(self) -> Sequence[SensorDescriptor]:
        """连接仿真器、发现当前机器人的 topic 并返回标准传感器描述。

        发现只读取 topic 元数据，不启动图像或点云回调。找不到匹配传感器时返回空元组；连接失败时保留
        Project AirSim 原生异常，便于调用方定位服务端口或场景状态。
        """

        self._ensure_connected()
        with self._lock:
            if self._descriptors:
                return self._descriptors
        client = self._require_client()
        client.get_topic_info()
        topics = tuple(str(topic) for topic in getattr(client, "topics", {}).keys())
        prefix_fragment = f"/robots/{self.robot_name}/sensors/"
        robot_fragment = f"/robots/{self.robot_name}/"
        if self.scene_id:
            prefix = f"/Sim/{self.scene_id}{prefix_fragment}"
            robot_prefix = f"/Sim/{self.scene_id}{robot_fragment}"
            robot_topics = tuple(topic for topic in topics if topic.startswith(robot_prefix))
            topics = tuple(topic for topic in robot_topics if topic.startswith(prefix))
        else:
            robot_topics = tuple(topic for topic in topics if robot_fragment in topic)
            topics = tuple(topic for topic in robot_topics if prefix_fragment in topic)

        descriptors: list[SensorDescriptor] = []
        topics_by_id: dict[str, str | tuple[str, str]] = {}
        camera_topics: dict[tuple[str, str], str] = {}
        for topic in sorted(topics):
            parts = topic.split("/")
            try:
                sensors_index = parts.index("sensors")
                native_sensor_id = parts[sensors_index + 1]
                stream_name = parts[sensors_index + 2]
            except (ValueError, IndexError):
                continue
            kind = self._TOPIC_KINDS.get(stream_name)
            if kind is None:
                continue
            is_front_camera = native_sensor_id.lower().startswith("front")
            sensor_id = native_sensor_id
            if any(item.sensor_id == sensor_id for item in descriptors):
                sensor_id = f"{native_sensor_id}.{stream_name}"
            descriptor = SensorDescriptor(
                sensor_id=sensor_id,
                kind=kind,
                source=SensorSourceKind.SIMULATED,
                # Project AirSim LiDAR 回波位置相对传感器，轴向采用 NED：X 前、Y 右、Z 下。
                frame=(
                    CoordinateFrame.BODY
                    if kind in {SensorKind.LIDAR, SensorKind.RADAR, SensorKind.DISTANCE}
                    or is_front_camera
                    else CoordinateFrame.UNKNOWN
                ),
                is_primary=(native_sensor_id == "FrontDepthCamera" and kind is SensorKind.DEPTH_CAMERA),
                mount=(
                    SensorMount(position_m=Vector3(0.0, 0.0, -0.2))
                    if kind is SensorKind.LIDAR
                    else SensorMount(position_m=Vector3(0.2, 0.0, 0.0))
                    if kind in {SensorKind.RADAR, SensorKind.DISTANCE}
                    else SensorMount(position_m=Vector3(0.2, 0.0, 0.0))
                    if is_front_camera
                    else None
                ),
                camera_intrinsics=(
                    CameraIntrinsics.from_horizontal_fov(
                        640,
                        480,
                        90.0,
                    )
                    if is_front_camera
                    else None
                ),
                metadata={
                    "provider": "projectairsim",
                    "topic": topic,
                    "native_sensor_id": native_sensor_id,
                    "stream_name": stream_name,
                },
            )
            descriptors.append(descriptor)
            topics_by_id[sensor_id] = topic
            if stream_name == "scene_camera":
                camera_topics[(native_sensor_id.lower(), stream_name)] = topic

        # [UAV-IDE MODIFY] Project AirSim publishes the simulator-authoritative vehicle pose
        # outside the sensors namespace. Expose it as an extension stream so HUD consumers do
        # not infer attitude or local position from accelerometer, magnetometer, or first GNSS.
        actual_pose_topic = next(
            (topic for topic in sorted(robot_topics) if topic.endswith("/actual_pose")),
            None,
        )
        if actual_pose_topic is not None:
            pose_sensor_id = "projectairsim.actual_pose"
            descriptors.append(
                SensorDescriptor(
                    sensor_id=pose_sensor_id,
                    kind=_PROJECTAIRSIM_POSE_KIND,
                    source=SensorSourceKind.SIMULATED,
                    frame=CoordinateFrame.LOCAL_NED,
                    is_primary=True,
                    metadata={
                        "provider": "projectairsim",
                        "topic": actual_pose_topic,
                        "stream_name": "actual_pose",
                    },
                )
            )
            topics_by_id[pose_sensor_id] = actual_pose_topic

        left = next(((key, value) for key, value in camera_topics.items() if "left" in key[0]), None)
        right = next(((key, value) for key, value in camera_topics.items() if "right" in key[0]), None)
        if left is not None and right is not None:
            stereo_id = "stereo"
            descriptors.append(
                SensorDescriptor(
                    sensor_id=stereo_id,
                    kind=SensorKind.STEREO_CAMERA,
                    source=SensorSourceKind.SIMULATED,
                    frame=CoordinateFrame.BODY,
                    is_primary=True,
                    mount=SensorMount(position_m=Vector3(0.2, 0.0, 0.0)),
                    stereo_calibration=StereoCalibration(
                        CameraIntrinsics.from_horizontal_fov(640, 480, 90.0),
                        CameraIntrinsics.from_horizontal_fov(640, 480, 90.0),
                        0.12,
                    ),
                    metadata={
                        "provider": "projectairsim",
                        "left_topic": left[1],
                        "right_topic": right[1],
                        "max_pair_skew_s": self._STEREO_MAX_SKEW_S,
                    },
                )
            )
            topics_by_id[stereo_id] = (left[1], right[1])

        rgb_descriptors = [descriptor for descriptor in descriptors if descriptor.kind is SensorKind.RGB_CAMERA]
        if rgb_descriptors:
            primary_rgb = min(rgb_descriptors, key=self._rgb_camera_priority)
            descriptors = [
                replace(descriptor, is_primary=descriptor.sensor_id == primary_rgb.sensor_id)
                if descriptor.kind is SensorKind.RGB_CAMERA
                else descriptor
                for descriptor in descriptors
            ]

        explicit_primary_kinds = {descriptor.kind for descriptor in descriptors if descriptor.is_primary}
        primary_kinds: set[SensorType] = set(explicit_primary_kinds)
        normalized: list[SensorDescriptor] = []
        for descriptor in descriptors:
            if descriptor.kind not in primary_kinds:
                normalized.append(replace(descriptor, is_primary=True))
                primary_kinds.add(descriptor.kind)
            else:
                normalized.append(descriptor)
        with self._lock:
            self._descriptors = tuple(normalized)
            self._topics = topics_by_id
        logger.debug(
            "Project AirSim 传感器发现完成: robot=%s count=%d",
            self.robot_name,
            len(normalized),
        )
        return self._descriptors

    @staticmethod
    def _rgb_camera_priority(descriptor: SensorDescriptor) -> int:
        native_id = str(descriptor.metadata.get("native_sensor_id", "")).lower()
        if native_id == "frontdepthcamera" or "front" in native_id:
            return 0
        if "left" in native_id:
            return 1
        if native_id == "downcamera" or "down" in native_id:
            return 2
        if native_id == "chase":
            return 100
        return 10

    def subscribe(self, sensor_id: str, handler: SensorHandler) -> SensorUnsubscribe:
        """订阅指定 Project AirSim 传感器并返回幂等取消函数。

        ``sensor_id`` 来自 ``list_sensors()``；``handler`` 接收转换后的标准样本。普通传感器直接映射
        一个原生 topic；标准双目传感器会同时订阅左右相机，并只在两侧都有新帧时
        交付一个 ``StereoFrame``。公共字段使用标准模型；只有构造时显式设置
        ``preserve_native_data=True`` 才会在样本中保留原生对象，避免图像和点云重复占用内存。
        """

        self.list_sensors()
        with self._lock:
            topic_or_pair = self._topics.get(sensor_id)
        if topic_or_pair is None:
            raise LookupError(f"Project AirSim sensor is not available: {sensor_id}")
        descriptor = next(item for item in self._descriptors if item.sensor_id == sensor_id)
        if isinstance(topic_or_pair, tuple):
            return self._subscribe_stereo(descriptor, topic_or_pair, handler)
        def on_native(_: Any, message: Any) -> None:
            handler(self._convert(descriptor, message))

        self._subscribe_topic(topic_or_pair, on_native)
        logger.debug("Project AirSim topic 已订阅: sensor_id=%s topic=%s", sensor_id, topic_or_pair)
        return self._unsubscribe_topics(((topic_or_pair, on_native),))

    def close(self) -> None:
        """取消全部 Project AirSim topic 并按所有权约定断开 client；重复调用安全且无返回值。"""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            topics = tuple(self._active_topics)
            self._active_topics.clear()
            self._topic_handlers.clear()
            for topic in topics:
                self._topic_generations[topic] = self._topic_generations.get(topic, 0) + 1
            client = self._client
        if client is not None:
            for topic in topics:
                try:
                    client.unsubscribe(topic)
                except Exception:
                    logger.debug("取消 Project AirSim topic 失败: topic=%s", topic, exc_info=True)
            if self._owns_client and self._connected:
                try:
                    client.disconnect()
                except Exception:
                    logger.debug("断开 Project AirSim client 失败", exc_info=True)
        self._connected = False

    def _subscribe_stereo(
        self,
        descriptor: SensorDescriptor,
        topics: tuple[str, str],
        handler: SensorHandler,
    ) -> SensorUnsubscribe:
        pair_lock = RLock()
        latest: dict[str, ImageFrame] = {}
        generations = {"left": 0, "right": 0}
        emitted = {"left": 0, "right": 0}

        def receive(side: str, message: Any) -> None:
            image_descriptor = replace(
                descriptor,
                sensor_id=f"{descriptor.sensor_id}.{side}",
                kind=SensorKind.RGB_CAMERA,
            )
            frame = self._convert_image(image_descriptor, message)
            with pair_lock:
                latest[side] = frame
                generations[side] += 1
                if "left" not in latest or "right" not in latest:
                    return
                if generations["left"] == emitted["left"] or generations["right"] == emitted["right"]:
                    return
                left_captured_at_s = latest["left"].captured_at_s
                right_captured_at_s = latest["right"].captured_at_s
                if (
                    left_captured_at_s is not None
                    and right_captured_at_s is not None
                    and abs(left_captured_at_s - right_captured_at_s) > self._STEREO_MAX_SKEW_S
                ):
                    return
                captured_times = tuple(
                    value for value in (left_captured_at_s, right_captured_at_s) if value is not None
                )
                emitted.update(generations)
                sequence = self._next_sequence(descriptor.sensor_id)
                stereo = StereoFrame(
                    sensor_id=descriptor.sensor_id,
                    kind=SensorKind.STEREO_CAMERA,
                    sequence=sequence,
                    frame=descriptor.frame,
                    captured_at_s=max(captured_times) if captured_times else None,
                    received_at_s=max(latest["left"].received_at_s, latest["right"].received_at_s),
                    clock_domain="projectairsim",
                    left=latest["left"],
                    right=latest["right"],
                    native_data=(latest["left"].native_data, latest["right"].native_data)
                    if self._preserve_native_data
                    else None,
                )
            handler(stereo)

        left_handler = lambda _, message: receive("left", message)
        right_handler = lambda _, message: receive("right", message)
        self._subscribe_topic(topics[0], left_handler)
        try:
            self._subscribe_topic(topics[1], right_handler)
        except Exception:
            self._remove_topic_handler(topics[0], left_handler)
            raise
        return self._unsubscribe_topics(((topics[0], left_handler), (topics[1], right_handler)))

    def _subscribe_topic(self, topic: str, handler: Callable[[Any, Any], None]) -> None:
        client = self._require_client()
        with self._lock:
            handlers = self._topic_handlers.setdefault(topic, [])
            first = not handlers
            handlers.append(handler)
            if first:
                generation = self._topic_generations.get(topic, 0) + 1
                self._topic_generations[topic] = generation

                def dispatch(
                    native_topic: Any,
                    message: Any,
                    *,
                    expected_generation: int = generation,
                ) -> None:
                    self._dispatch_topic(
                        native_topic,
                        message,
                        expected_generation=expected_generation,
                    )

                try:
                    client.subscribe(topic, dispatch)
                except Exception:
                    handlers.remove(handler)
                    self._topic_handlers.pop(topic, None)
                    raise
                self._active_topics.add(topic)

    def _dispatch_topic(
        self,
        topic: Any,
        message: Any,
        *,
        expected_generation: int,
    ) -> None:
        topic_name = str(getattr(topic, "path", topic))
        with self._lock:
            if self._topic_generations.get(topic_name, 0) != expected_generation:
                return
            handlers = tuple(self._topic_handlers.get(topic_name, ()))
        for handler in handlers:
            try:
                handler(topic, message)
            except Exception:
                logger.debug("Project AirSim 传感器转换或回调失败: topic=%s", topic_name, exc_info=True)

    def _remove_topic_handler(self, topic: str, handler: Callable[[Any, Any], None]) -> None:
        should_unsubscribe = False
        with self._lock:
            handlers = self._topic_handlers.get(topic)
            if handlers is None:
                return
            try:
                handlers.remove(handler)
            except ValueError:
                return
            if not handlers:
                self._topic_handlers.pop(topic, None)
                self._active_topics.discard(topic)
                # [UAV-IDE MODIFY] Invalidate callbacks already queued by Project AirSim
                # before a close/re-init cycle. A later subscription gets a new generation.
                self._topic_generations[topic] = self._topic_generations.get(topic, 0) + 1
                should_unsubscribe = True
        if should_unsubscribe:
            client = self._require_client()
            client.unsubscribe(topic)

    def _unsubscribe_topics(
        self,
        subscriptions: tuple[tuple[str, Callable[[Any, Any], None]], ...],
    ) -> SensorUnsubscribe:
        closed = False

        def unsubscribe() -> None:
            nonlocal closed
            if closed:
                return
            closed = True
            for topic, handler in subscriptions:
                self._remove_topic_handler(topic, handler)

        return unsubscribe

    def _convert(self, descriptor: SensorDescriptor, message: Any) -> SensorSample:
        if descriptor.kind is SensorKind.LIDAR:
            return self._convert_lidar(descriptor, message)
        if descriptor.kind in {SensorKind.RGB_CAMERA, SensorKind.DEPTH_CAMERA}:
            return self._convert_image(descriptor, message)
        if descriptor.kind is SensorKind.IMU:
            return self._convert_imu(descriptor, message)
        if descriptor.kind is SensorKind.GNSS:
            return self._convert_gnss(descriptor, message)
        if descriptor.kind is SensorKind.MAGNETOMETER:
            return self._convert_magnetometer(descriptor, message)
        if descriptor.kind is SensorKind.BAROMETER:
            return self._convert_barometer(descriptor, message)
        if descriptor.kind is SensorKind.RADAR:
            return self._convert_radar(descriptor, message)
        if descriptor.kind is SensorKind.DISTANCE:
            return self._convert_distance(descriptor, message)
        values = dict(message) if isinstance(message, Mapping) else {"value": message}
        return GenericSensorSample(
            sensor_id=descriptor.sensor_id,
            kind=descriptor.kind,
            sequence=self._next_sequence(descriptor.sensor_id),
            frame=descriptor.frame,
            captured_at_s=self._captured_at(message),
            clock_domain="projectairsim",
            values=values,
            native_data=self._native_data(message),
        )

    def _convert_lidar(self, descriptor: SensorDescriptor, message: Any) -> PointCloudFrame:
        values = message.get("point_cloud", ()) if isinstance(message, Mapping) else ()
        buffer = array("f", (float(value) for value in values))
        return PointCloudFrame(
            sensor_id=descriptor.sensor_id,
            kind=SensorKind.LIDAR,
            sequence=self._next_sequence(descriptor.sensor_id),
            frame=descriptor.frame,
            captured_at_s=self._captured_at(message),
            clock_domain="projectairsim",
            data=memoryview(buffer),
            point_count=len(buffer) // 3,
            encoding=PointCloudEncoding.XYZ_F32,
            native_data=self._native_data(message),
        )

    def _convert_image(self, descriptor: SensorDescriptor, message: Any) -> ImageFrame:
        data = message.get("data", b"") if isinstance(message, Mapping) else message
        shape = tuple(int(value) for value in getattr(data, "shape", ()))
        height = int(message.get("height", shape[0] if len(shape) >= 2 else 0)) if isinstance(message, Mapping) else 0
        width = int(message.get("width", shape[1] if len(shape) >= 2 else 0)) if isinstance(message, Mapping) else 0
        try:
            view = memoryview(data)
        except TypeError:
            view = memoryview(bytes(data))
        native_encoding = str(message.get("encoding", "")) if isinstance(message, Mapping) else ""
        encoding_by_name = {
            "BGR": ImageEncoding.BGR8,
            "RGB": ImageEncoding.RGB8,
            "RGBA": ImageEncoding.RGBA8,
            "GRAY8": ImageEncoding.MONO8,
            "16UC1": ImageEncoding.DEPTH_U16_MM,
            "32FC1": ImageEncoding.DEPTH_F32_M,
        }
        if native_encoding in encoding_by_name:
            encoding = encoding_by_name[native_encoding]
        elif descriptor.kind is SensorKind.DEPTH_CAMERA and getattr(data, "dtype", None) is not None:
            encoding = ImageEncoding.DEPTH_F32_M if str(data.dtype) in {"float32", "<f4", ">f4"} else ImageEncoding.NATIVE
        elif len(shape) >= 3 and shape[2] == 3:
            encoding = ImageEncoding.RGB8
        elif len(shape) >= 3 and shape[2] == 4:
            encoding = ImageEncoding.RGBA8
        else:
            encoding = ImageEncoding.NATIVE
        stride: Optional[int]
        if isinstance(message, Mapping) and message.get("step") is not None:
            stride = int(message["step"])
        else:
            stride = int(getattr(data, "strides", (0,))[0]) if getattr(data, "strides", ()) else None
        bytes_per_pixel = {
            ImageEncoding.BGR8: 3,
            ImageEncoding.RGB8: 3,
            ImageEncoding.RGBA8: 4,
            ImageEncoding.MONO8: 1,
            ImageEncoding.DEPTH_U16_MM: 2,
            ImageEncoding.DEPTH_F32_M: 4,
        }.get(encoding)
        minimum_stride = width * bytes_per_pixel if bytes_per_pixel is not None else 0
        if stride is not None and stride < minimum_stride and height > 0 and view.nbytes % height == 0:
            inferred_stride = view.nbytes // height
            if inferred_stride >= minimum_stride:
                logger.debug(
                    "Project AirSim 图像步长已修正: sensor_id=%s reported=%d inferred=%d",
                    descriptor.sensor_id,
                    stride,
                    inferred_stride,
                )
                stride = inferred_stride
        return ImageFrame(
            sensor_id=descriptor.sensor_id,
            kind=descriptor.kind,
            sequence=self._next_sequence(descriptor.sensor_id),
            frame=descriptor.frame,
            captured_at_s=self._captured_at(message),
            clock_domain="projectairsim",
            width=width,
            height=height,
            encoding=encoding,
            data=view,
            stride_bytes=stride,
            extensions={"native_encoding": native_encoding} if native_encoding else {},
            native_data=self._native_data(message),
        )

    def _convert_imu(self, descriptor: SensorDescriptor, message: Any) -> SensorSample:
        if not isinstance(message, Mapping):
            return GenericSensorSample(
                sensor_id=descriptor.sensor_id,
                kind=SensorKind.IMU,
                sequence=self._next_sequence(descriptor.sensor_id),
                values={"value": message},
                native_data=self._native_data(message),
            )
        angular = self._vector(message.get("angular_velocity") or message.get("angular_velocity_rad_s"))
        linear = self._vector(message.get("linear_acceleration") or message.get("linear_acceleration_m_s2"))
        if angular is None or linear is None:
            return GenericSensorSample(
                sensor_id=descriptor.sensor_id,
                kind=SensorKind.IMU,
                sequence=self._next_sequence(descriptor.sensor_id),
                frame=descriptor.frame,
                captured_at_s=self._captured_at(message),
                clock_domain="projectairsim",
                values=dict(message),
                native_data=self._native_data(message),
            )
        return ImuSample(
            sensor_id=descriptor.sensor_id,
            kind=SensorKind.IMU,
            sequence=self._next_sequence(descriptor.sensor_id),
            frame=descriptor.frame,
            captured_at_s=self._captured_at(message),
            clock_domain="projectairsim",
            angular_velocity_rad_s=angular,
            linear_acceleration_m_s2=linear,
            native_data=self._native_data(message),
        )

    def _convert_gnss(self, descriptor: SensorDescriptor, message: Any) -> SensorSample:
        values = message if isinstance(message, Mapping) else {}
        latitude = values.get("latitude_deg", values.get("latitude"))
        longitude = values.get("longitude_deg", values.get("longitude"))
        if latitude is None or longitude is None:
            return self._generic_sample(descriptor, message)
        return GnssSample(
            sensor_id=descriptor.sensor_id,
            kind=SensorKind.GNSS,
            sequence=self._next_sequence(descriptor.sensor_id),
            frame=CoordinateFrame.GEODETIC,
            captured_at_s=self._captured_at(message),
            clock_domain="projectairsim",
            latitude_deg=float(latitude),
            longitude_deg=float(longitude),
            absolute_altitude_m=self._optional_float(values, "altitude_m", "altitude"),
            horizontal_accuracy_m=self._optional_float(values, "horizontal_accuracy_m", "eph"),
            vertical_accuracy_m=self._optional_float(values, "vertical_accuracy_m", "epv"),
            satellite_count=self._optional_int(values, "satellite_count", "satellites_visible"),
            fix_type=str(values["fix_type"]) if values.get("fix_type") is not None else None,
            native_data=self._native_data(message),
        )

    def _convert_magnetometer(self, descriptor: SensorDescriptor, message: Any) -> SensorSample:
        values = message if isinstance(message, Mapping) else {}
        magnetic = self._vector(values.get("magnetic_field_t") or values.get("magnetic_field"))
        if magnetic is None:
            return self._generic_sample(descriptor, message)
        return MagnetometerSample(
            sensor_id=descriptor.sensor_id,
            kind=SensorKind.MAGNETOMETER,
            sequence=self._next_sequence(descriptor.sensor_id),
            frame=descriptor.frame,
            captured_at_s=self._captured_at(message),
            clock_domain="projectairsim",
            magnetic_field_t=magnetic,
            temperature_c=self._optional_float(values, "temperature_c"),
            native_data=self._native_data(message),
        )

    def _convert_barometer(self, descriptor: SensorDescriptor, message: Any) -> SensorSample:
        values = message if isinstance(message, Mapping) else {}
        pressure = values.get("absolute_pressure_pa", values.get("pressure_pa"))
        if pressure is None:
            return self._generic_sample(descriptor, message)
        return BarometerSample(
            sensor_id=descriptor.sensor_id,
            kind=SensorKind.BAROMETER,
            sequence=self._next_sequence(descriptor.sensor_id),
            frame=descriptor.frame,
            captured_at_s=self._captured_at(message),
            clock_domain="projectairsim",
            absolute_pressure_pa=float(pressure),
            pressure_altitude_m=self._optional_float(values, "pressure_altitude_m", "altitude_m"),
            temperature_c=self._optional_float(values, "temperature_c"),
            native_data=self._native_data(message),
        )

    def _generic_sample(self, descriptor: SensorDescriptor, message: Any) -> GenericSensorSample:
        values = dict(message) if isinstance(message, Mapping) else {"value": message}
        return GenericSensorSample(
            sensor_id=descriptor.sensor_id,
            kind=descriptor.kind,
            sequence=self._next_sequence(descriptor.sensor_id),
            frame=descriptor.frame,
            captured_at_s=self._captured_at(message),
            clock_domain="projectairsim",
            values=values,
            native_data=self._native_data(message),
        )

    def _convert_radar(self, descriptor: SensorDescriptor, message: Any) -> RadarFrame:
        values = message if isinstance(message, Mapping) else {}
        raw_detections = values.get("detections") or values.get("radar_detections") or ()
        raw_tracks = values.get("tracks") or values.get("radar_tracks") or ()
        detections: list[RadarDetection] = []
        for item in raw_detections:
            if not isinstance(item, Mapping):
                continue
            detections.append(
                RadarDetection(
                    range_m=float(item.get("range", item.get("range_m", 0.0))),
                    azimuth_rad=float(item.get("azimuth", item.get("azimuth_rad", 0.0))),
                    elevation_rad=float(item.get("elevation", item.get("elevation_rad", 0.0))),
                    radial_velocity_m_s=float(
                        item.get("velocity", item.get("radial_velocity_m_s", 0.0))
                    ),
                    rcs_m2=(float(item["rcs"]) if item.get("rcs") is not None else None),
                )
            )
        tracks: list[RadarTrack] = []
        for item in raw_tracks:
            if not isinstance(item, Mapping):
                continue
            position = self._vector(item.get("position") or item.get("relative_position"))
            velocity = self._vector(item.get("velocity") or item.get("relative_velocity"))
            if position is None or velocity is None:
                continue
            tracks.append(
                RadarTrack(
                    track_id=str(item.get("id", item.get("track_id", len(tracks)))),
                    position_m=position,
                    velocity_m_s=velocity,
                    acceleration_m_s2=self._vector(item.get("acceleration") or item.get("relative_acceleration")),
                    rcs_m2=(float(item["rcs"]) if item.get("rcs") is not None else None),
                )
            )
        return RadarFrame(
            sensor_id=descriptor.sensor_id,
            kind=SensorKind.RADAR,
            sequence=self._next_sequence(descriptor.sensor_id),
            frame=descriptor.frame,
            captured_at_s=self._captured_at(message),
            clock_domain="projectairsim",
            detections=tuple(detections),
            tracks=tuple(tracks),
            native_data=self._native_data(message),
        )

    def _convert_distance(self, descriptor: SensorDescriptor, message: Any) -> DistanceSample:
        values = message if isinstance(message, Mapping) else {"distance": message}
        distance = values.get("distance_m", values.get("distance", values.get("range")))
        if distance is None:
            raise ValueError("distance sensor message does not contain a distance value")
        return DistanceSample(
            sensor_id=descriptor.sensor_id,
            kind=SensorKind.DISTANCE,
            sequence=self._next_sequence(descriptor.sensor_id),
            frame=descriptor.frame,
            captured_at_s=self._captured_at(message),
            clock_domain="projectairsim",
            distance_m=float(distance),
            min_distance_m=(float(values["min_distance_m"]) if values.get("min_distance_m") is not None else None),
            max_distance_m=(float(values["max_distance_m"]) if values.get("max_distance_m") is not None else None),
            native_data=self._native_data(message),
        )

    @staticmethod
    def _vector(value: Any) -> Optional[Vector3]:
        if isinstance(value, Mapping) and all(axis in value for axis in ("x", "y", "z")):
            return Vector3(float(value["x"]), float(value["y"]), float(value["z"]))
        return None

    @staticmethod
    def _optional_float(values: Mapping[str, Any], *names: str) -> Optional[float]:
        for name in names:
            if values.get(name) is not None:
                return float(values[name])
        return None

    @staticmethod
    def _optional_int(values: Mapping[str, Any], *names: str) -> Optional[int]:
        for name in names:
            if values.get(name) is not None:
                return int(values[name])
        return None

    @staticmethod
    def _captured_at(message: Any) -> Optional[float]:
        if not isinstance(message, Mapping):
            return None
        for name in ("timestamp_s", "time_stamp", "timestamp", "time_usec"):
            if name in message:
                value = float(message[name])
                if name == "time_stamp":
                    return value / 1_000_000_000.0
                if name == "time_usec":
                    return value / 1_000_000.0
                return value
        return None

    def _next_sequence(self, sensor_id: str) -> int:
        with self._lock:
            value = self._sequences.get(sensor_id, 0) + 1
            self._sequences[sensor_id] = value
            return value

    def _native_data(self, message: Any) -> Any:
        return message if self._preserve_native_data else None

    def _ensure_connected(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("Project AirSim sensor backend is closed.")
            if self._connected:
                return
            if self._client is None:
                if self._client_factory is not None:
                    self._client = self._client_factory()
                else:
                    try:
                        from projectairsim import ProjectAirSimClient
                    except ImportError as exc:
                        raise RuntimeError(
                            "projectairsim is required for Project AirSim sensor subscriptions."
                        ) from exc
                    self._client = ProjectAirSimClient()
            client = self._client
        if not bool(getattr(client, "state", False)):
            client.connect()
        with self._lock:
            self._connected = True
        logger.debug("Project AirSim 传感器 client 已连接: robot=%s", self.robot_name)

    def _require_client(self) -> Any:
        self._ensure_connected()
        return self._client
