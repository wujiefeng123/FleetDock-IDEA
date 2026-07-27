"""PX4 MAVSDK 原生遥测流到 FleetDock 标准传感器样本的转换后端。"""

from __future__ import annotations

from dataclasses import replace
import logging
from threading import RLock
import time
from typing import Any, Sequence

from fleetdock.events import CoordinateFrame, Vector3
from fleetdock.sensors import (
    AirspeedSample,
    BarometerSample,
    DistanceSample,
    GnssSample,
    ImuSample,
    MagnetometerSample,
    SensorDescriptor,
    SensorHandler,
    SensorKind,
    SensorSourceKind,
    SensorUnsubscribe,
)

logger = logging.getLogger(__name__)


class _Px4MavsdkSensorBackend:
    """把同一 PX4 ``MavsdkClient`` 的原生传感器流转换为标准样本。

    本后端复用控制适配器已经建立的 MAVSDK/MAVLink 连接，不额外占用 UDP 端口；公开传感器 ID
    在单设备内稳定。``list_sensors()`` 表示当前客户端可订阅的流，实际数据新鲜度由
    ``device.sensors.status(...)`` 判断。资源由设备 ``close()`` 统一释放。
    """

    _DESCRIPTORS = {
        "imu": SensorDescriptor(
            "px4.imu",
            SensorKind.IMU,
            source=SensorSourceKind.UNKNOWN,
            frame=CoordinateFrame.BODY_FRD,
            is_primary=True,
            metadata={"provider": "mavsdk", "stream": "imu"},
        ),
        "magnetometer": SensorDescriptor(
            "px4.magnetometer",
            SensorKind.MAGNETOMETER,
            source=SensorSourceKind.UNKNOWN,
            frame=CoordinateFrame.BODY_FRD,
            is_primary=True,
            metadata={"provider": "mavsdk", "stream": "imu"},
        ),
        "gnss": SensorDescriptor(
            "px4.gnss",
            SensorKind.GNSS,
            source=SensorSourceKind.UNKNOWN,
            frame=CoordinateFrame.GEODETIC,
            is_primary=True,
            metadata={"provider": "mavsdk", "stream": "raw_gps"},
        ),
        "barometer": SensorDescriptor(
            "px4.barometer",
            SensorKind.BAROMETER,
            source=SensorSourceKind.UNKNOWN,
            frame=CoordinateFrame.BODY_FRD,
            is_primary=True,
            metadata={"provider": "mavsdk", "stream": "scaled_pressure"},
        ),
        "distance": SensorDescriptor(
            "px4.distance",
            SensorKind.DISTANCE,
            source=SensorSourceKind.UNKNOWN,
            frame=CoordinateFrame.BODY_FRD,
            is_primary=True,
            metadata={"provider": "mavsdk", "stream": "distance_sensor"},
        ),
        "airspeed": SensorDescriptor(
            "px4.airspeed",
            SensorKind.AIRSPEED,
            source=SensorSourceKind.UNKNOWN,
            frame=CoordinateFrame.BODY_FRD,
            is_primary=True,
            metadata={"provider": "mavsdk", "stream": "fixedwing_metrics"},
        ),
        "camera": SensorDescriptor(
            "px4.camera",
            SensorKind.RGB_CAMERA,
            source=SensorSourceKind.UNKNOWN,
            frame=CoordinateFrame.BODY_FRD,
            is_primary=True,
            metadata={"provider": "mavsdk", "stream": "video_stream_info"},
        ),
    }

    def __init__(self, client: Any) -> None:
        self._client = client
        self._lock = RLock()
        self._sequences: dict[str, int] = {}
        self._unsubscribes: list[SensorUnsubscribe] = []
        self._latest_orientation_rpy_deg: Vector3 | None = None
        self._latest_orientation_received_at_s: float | None = None
        self._closed = False

    def list_sensors(self) -> Sequence[SensorDescriptor]:
        """返回当前 ``MavsdkClient`` 暴露的标准 PX4 传感器描述，不启动原生流。"""
        available = set(self._client.available_sensor_streams())
        descriptors = []
        for name, descriptor in self._DESCRIPTORS.items():
            if name not in available:
                continue
            if name == "camera":
                descriptor = replace(
                    descriptor,
                    source=getattr(self._client, "camera_stream_source", SensorSourceKind.UNKNOWN),
                )
            descriptors.append(descriptor)
        return tuple(descriptors)

    def replace_client(self, client: Any) -> None:
        """在 PX4 连接重试重建 client 后切换共享客户端。

        ``client`` 必须提供标准 PX4 传感器流协议；存在活动订阅时抛出 ``RuntimeError``，成功时无返回值。
        """
        with self._lock:
            if self._unsubscribes:
                raise RuntimeError("Cannot replace PX4 sensor client while streams are active.")
            self._client = client
            self._latest_orientation_rpy_deg = None
            self._latest_orientation_received_at_s = None

    def subscribe(self, sensor_id: str, handler: SensorHandler) -> SensorUnsubscribe:
        """订阅 ``sensor_id`` 对应的 PX4 流并返回幂等取消函数。

        设备必须先连接；``handler`` 接收已经完成 SI 单位和坐标系声明的标准样本。原生流异常由
        ``MavsdkClient`` 记录，调用方可通过通道状态的最新接收时间判断失效。
        """
        if self._closed:
            raise RuntimeError("PX4 sensor backend is closed.")
        stream_name = next(
            (name for name, descriptor in self._DESCRIPTORS.items() if descriptor.sensor_id == sensor_id),
            None,
        )
        if stream_name is None or stream_name not in set(self._client.available_sensor_streams()):
            raise LookupError(f"PX4 sensor is not available: {sensor_id}")

        if stream_name == "camera":
            from fleetdock.adapters.opencv_camera import _OpenCvCameraBackend

            camera_backend = _OpenCvCameraBackend(
                self._client.camera_stream_uri(),
                sensor_id=self._DESCRIPTORS[stream_name].sensor_id,
                frame=self._DESCRIPTORS[stream_name].frame,
                source_kind=getattr(self._client, "camera_stream_source", SensorSourceKind.UNKNOWN),
            )
            unsubscribe_camera = camera_backend.subscribe(sensor_id, handler)

            def native_unsubscribe() -> None:
                unsubscribe_camera()
                camera_backend.close()

        else:
            def on_native(native: Any) -> None:
                handler(self._convert(stream_name, native))

            native_unsubscribes: list[SensorUnsubscribe] = []
            try:
                if stream_name == "imu" and "attitude" in set(self._client.available_sensor_streams()):
                    with self._lock:
                        self._latest_orientation_rpy_deg = None
                        self._latest_orientation_received_at_s = None
                    native_unsubscribes.append(
                        self._client.subscribe_sensor("attitude", self._update_orientation)
                    )
                native_unsubscribes.append(self._client.subscribe_sensor(stream_name, on_native))
            except Exception:
                for pending_unsubscribe in reversed(native_unsubscribes):
                    pending_unsubscribe()
                raise

            def native_unsubscribe() -> None:
                for stream_unsubscribe in reversed(native_unsubscribes):
                    stream_unsubscribe()
        with self._lock:
            self._unsubscribes.append(native_unsubscribe)
        closed = False

        def unsubscribe() -> None:
            nonlocal closed
            if closed:
                return
            closed = True
            with self._lock:
                try:
                    self._unsubscribes.remove(native_unsubscribe)
                except ValueError:
                    pass
            native_unsubscribe()

        return unsubscribe

    def close(self) -> None:
        """取消本后端建立的全部 PX4 传感器流；不关闭共享 ``MavsdkClient``，重复调用安全且无返回值。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            unsubscribes = tuple(self._unsubscribes)
            self._unsubscribes.clear()
        for unsubscribe in unsubscribes:
            unsubscribe()

    def _convert(self, stream_name: str, native: Any) -> Any:
        descriptor = self._DESCRIPTORS[stream_name]
        sequence = self._next_sequence(descriptor.sensor_id)
        captured_at_s = self._timestamp_s(native)
        common = {
            "sensor_id": descriptor.sensor_id,
            "kind": descriptor.kind,
            "sequence": sequence,
            "frame": descriptor.frame,
            "captured_at_s": captured_at_s,
            "clock_domain": "px4_boot" if captured_at_s is not None else "unknown",
            "native_data": native,
        }
        if stream_name == "imu":
            acceleration = native.acceleration_frd
            angular = native.angular_velocity_frd
            with self._lock:
                orientation_age_s = (
                    None
                    if self._latest_orientation_received_at_s is None
                    else time.monotonic() - self._latest_orientation_received_at_s
                )
                orientation_rpy_deg = (
                    self._latest_orientation_rpy_deg
                    if orientation_age_s is not None and orientation_age_s <= 1.0
                    else None
                )
            return ImuSample(
                **common,
                angular_velocity_rad_s=Vector3(
                    float(angular.forward_rad_s),
                    float(angular.right_rad_s),
                    float(angular.down_rad_s),
                ),
                linear_acceleration_m_s2=Vector3(
                    float(acceleration.forward_m_s2),
                    float(acceleration.right_m_s2),
                    float(acceleration.down_m_s2),
                ),
                orientation_rpy_deg=orientation_rpy_deg,
            )
        if stream_name == "magnetometer":
            magnetic = native.magnetic_field_frd
            return MagnetometerSample(
                **common,
                magnetic_field_t=Vector3(
                    float(magnetic.forward_gauss) * 1e-4,
                    float(magnetic.right_gauss) * 1e-4,
                    float(magnetic.down_gauss) * 1e-4,
                ),
                temperature_c=self._optional_float(native, "temperature_degc"),
            )
        if stream_name == "gnss":
            fix_type = getattr(getattr(native, "fix_type", None), "name", None)
            return GnssSample(
                **common,
                latitude_deg=float(native.latitude_deg),
                longitude_deg=float(native.longitude_deg),
                absolute_altitude_m=self._optional_float(native, "absolute_altitude_m"),
                horizontal_accuracy_m=self._optional_float(native, "horizontal_uncertainty_m"),
                vertical_accuracy_m=self._optional_float(native, "vertical_uncertainty_m"),
                ground_speed_m_s=self._optional_float(native, "velocity_m_s"),
                heading_deg=self._optional_float(native, "cog_deg"),
                fix_type=str(fix_type).lower() if fix_type else None,
            )
        if stream_name == "barometer":
            return BarometerSample(
                **common,
                absolute_pressure_pa=float(native.absolute_pressure_hpa) * 100.0,
                differential_pressure_pa=self._scaled_optional(native, "differential_pressure_hpa", 100.0),
                temperature_c=self._optional_float(native, "temperature_deg"),
            )
        if stream_name == "distance":
            return DistanceSample(
                **common,
                distance_m=float(native.current_distance_m),
                min_distance_m=self._optional_float(native, "minimum_distance_m"),
                max_distance_m=self._optional_float(native, "maximum_distance_m"),
                extensions={"orientation": str(getattr(native, "orientation", "unknown"))},
            )
        if stream_name == "airspeed":
            return AirspeedSample(
                **common,
                indicated_airspeed_m_s=float(native.airspeed_m_s),
                groundspeed_m_s=self._optional_float(native, "groundspeed_m_s"),
            )
        raise LookupError(f"PX4 sensor stream is not supported: {stream_name}")

    def _update_orientation(self, native: Any) -> None:
        orientation = Vector3(
            float(native.roll_deg),
            float(native.pitch_deg),
            float(native.yaw_deg),
        )
        with self._lock:
            self._latest_orientation_rpy_deg = orientation
            self._latest_orientation_received_at_s = time.monotonic()

    def _next_sequence(self, sensor_id: str) -> int:
        with self._lock:
            sequence = self._sequences.get(sensor_id, 0) + 1
            self._sequences[sensor_id] = sequence
            return sequence

    @staticmethod
    def _timestamp_s(native: Any) -> float | None:
        value = getattr(native, "timestamp_us", None)
        return float(value) / 1_000_000.0 if value is not None else None

    @staticmethod
    def _optional_float(native: Any, name: str) -> float | None:
        value = getattr(native, name, None)
        return float(value) if value is not None else None

    @classmethod
    def _scaled_optional(cls, native: Any, name: str, scale: float) -> float | None:
        value = cls._optional_float(native, name)
        return value * scale if value is not None else None


class _Px4IntegratedSensorBackend:
    """只暴露 PX4/MAVSDK 原生传感器和显式配置的视频流。

    Project AirSim 等仿真器不得仅因本机端口可用而被自动并入真实 PX4 设备。仿真 RGB 画面应先由
    ``ProjectAirSimRtspBridge`` 转换为 RTSP，再通过设备的 ``camera_stream_uri`` 显式接入；这样真机、
    仿真器和其他品牌设备共享相同的视频输入边界。
    """

    def __init__(self, client: Any) -> None:
        self._native = _Px4MavsdkSensorBackend(client)

    def replace_client(self, client: Any) -> None:
        self._native.replace_client(client)

    def list_sensors(self) -> Sequence[SensorDescriptor]:
        return self._native.list_sensors()

    def subscribe(self, sensor_id: str, handler: SensorHandler) -> SensorUnsubscribe:
        return self._native.subscribe(sensor_id, handler)

    def close(self) -> None:
        self._native.close()


def _create_px4_integrated_sensor_backend(client: Any) -> _Px4IntegratedSensorBackend:
    return _Px4IntegratedSensorBackend(client)
