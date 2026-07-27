"""Radar 与单向测距传感器的标准障碍物 Provider。"""

from __future__ import annotations

from dataclasses import dataclass
import math
from threading import RLock
import time
from typing import Any, Callable

from fleetdock.events import CoordinateFrame, Vector3
from fleetdock.obstacles import Obstacle, ObstacleKind, ObstacleShape, ObstacleSnapshot
from fleetdock.sensors import DistanceSample, RadarFrame, SensorDescriptor, SensorKind, SensorMount


@dataclass(frozen=True)
class RadarObstacleConfig:
    """Radar 障碍物转换配置；距离和半径单位为米，``ttl_s`` 单位为秒。"""

    sensor_mount: SensorMount | None = None
    min_range_m: float = 1.0
    max_range_m: float = 300.0
    detection_radius_m: float = 1.5
    track_radius_m: float = 2.0
    ttl_s: float = 1.0
    max_obstacles: int = 128

    def __post_init__(self) -> None:
        if self.min_range_m < 0 or self.max_range_m <= self.min_range_m:
            raise ValueError("Radar obstacle range is invalid.")
        if min(self.detection_radius_m, self.track_radius_m, self.ttl_s, self.max_obstacles) <= 0:
            raise ValueError("Radar obstacle size, TTL and capacity must be greater than 0.")


@dataclass(frozen=True)
class DistanceObstacleConfig:
    """单向测距近场障碍物配置；距离字段单位为米，``ttl_s`` 单位为秒。"""

    sensor_mount: SensorMount | None = None
    min_range_m: float = 0.2
    max_range_m: float = 20.0
    obstacle_radius_m: float = 0.5
    ttl_s: float = 0.3

    def __post_init__(self) -> None:
        if self.min_range_m < 0 or self.max_range_m <= self.min_range_m:
            raise ValueError("Distance obstacle range is invalid.")
        if min(self.obstacle_radius_m, self.ttl_s) <= 0:
            raise ValueError("Distance obstacle radius and TTL must be greater than 0.")


@dataclass(frozen=True)
class RangingObstacleProviderStatus:
    """Radar 或 Distance Provider 的不可变运行状态。

    ``stale`` 按查询时传入的秒级阈值计算；``received_samples`` 是已接收且传感器 ID 匹配的样本数；
    ``obstacle_count`` 是当前快照中的障碍物数量；``last_observed_at_s`` 使用进程单调时钟，尚未成功
    转换时为 ``None``；``last_error`` 记录最近转换异常，最近一次成功后恢复为 ``None``。
    """

    ready: bool
    stale: bool
    received_samples: int
    obstacle_count: int
    latest_sequence: int | None
    last_observed_at_s: float | None
    last_error: str | None


class _BodyObstacleTransform:
    def __init__(self, descriptor: SensorDescriptor, pose_provider: Callable[[], Any], mount: SensorMount | None) -> None:
        self.descriptor = descriptor
        self.pose_provider = pose_provider
        self.mount = mount or descriptor.mount
        if descriptor.frame is CoordinateFrame.BODY and self.mount is None:
            raise ValueError("BODY ranging sensor requires SensorMount calibration.")

    def point(self, sensor_point: Vector3) -> tuple[CoordinateFrame, Vector3, Vector3]:
        pose = self.pose_provider()
        frame = getattr(pose, "local_position_frame", CoordinateFrame.UNKNOWN)
        if frame not in {CoordinateFrame.LOCAL_NED, CoordinateFrame.LOCAL_ENU}:
            raise RuntimeError("Local pose frame is unavailable for ranging obstacle conversion.")
        mount = self.mount or SensorMount(Vector3(0.0, 0.0, 0.0))
        body_delta = self.rotate(sensor_point, mount.rotation_rpy_deg)
        body_point = Vector3(
            body_delta.x + mount.position_m.x,
            body_delta.y + mount.position_m.y,
            body_delta.z + mount.position_m.z,
        )
        attitude = Vector3(
            float(getattr(pose, "roll_deg", 0.0) or 0.0),
            float(getattr(pose, "pitch_deg", 0.0) or 0.0),
            float(getattr(pose, "yaw_deg", 0.0) or 0.0),
        )
        local_delta = self.rotate(body_point, attitude)
        local = Vector3(
            float(getattr(pose, "local_x_m", 0.0) or 0.0) + local_delta.x,
            float(getattr(pose, "local_y_m", 0.0) or 0.0) + local_delta.y,
            float(getattr(pose, "local_z_m", 0.0) or 0.0) + local_delta.z,
        )
        return frame, local, attitude

    @staticmethod
    def rotate(point: Vector3, rpy_deg: Vector3) -> Vector3:
        roll, pitch, yaw = (math.radians(value) for value in (rpy_deg.x, rpy_deg.y, rpy_deg.z))
        cr, sr, cp, sp, cy, sy = math.cos(roll), math.sin(roll), math.cos(pitch), math.sin(pitch), math.cos(yaw), math.sin(yaw)
        return Vector3(
            (cy * cp) * point.x + (cy * sp * sr - sy * cr) * point.y + (cy * sp * cr + sy * sr) * point.z,
            (sy * cp) * point.x + (sy * sp * sr + cy * cr) * point.y + (sy * sp * cr - cy * sr) * point.z,
            (-sp) * point.x + (cp * sr) * point.y + (cp * cr) * point.z,
        )


class RadarObstacleProvider:
    """把显式订阅的 Radar 检测或轨迹转换为带速度的标准障碍物快照。

    ``descriptor`` 必须来自 ``device.sensors`` 的 Radar 描述；``pose_provider`` 提供本地位置和姿态，
    ``config`` 声明安装外参、量程、保守半径、TTL 和容量。对象不拥有传感器订阅，调用方应显式把
    ``observe`` 注册为回调，并在停止送入样本后调用 ``close()`` 清空快照。
    """

    def __init__(self, descriptor: SensorDescriptor, *, pose_provider: Callable[[], Any], config: RadarObstacleConfig | None = None) -> None:
        if descriptor.kind is not SensorKind.RADAR:
            raise ValueError("RadarObstacleProvider requires RADAR descriptor.")
        self.descriptor = descriptor
        self.config = config or RadarObstacleConfig()
        self._transform = _BodyObstacleTransform(descriptor, pose_provider, self.config.sensor_mount)
        self._lock = RLock()
        self._snapshot = ObstacleSnapshot("sensor.radar.obstacles", CoordinateFrame.LOCAL_NED, time.monotonic(), ())
        self._received_samples = 0
        self._latest_sequence: int | None = None
        self._last_observed_at_s: float | None = None
        self._last_error: str | None = None

    def observe(self, sample: RadarFrame) -> None:
        """把 ``sample`` Radar 检测/轨迹转换为障碍物并立即更新快照；传感器 ID 必须匹配且无返回值。"""

        if not isinstance(sample, RadarFrame) or sample.sensor_id != self.descriptor.sensor_id:
            raise ValueError("Radar sample does not match provider descriptor.")
        now = time.monotonic()
        try:
            obstacles = self._obstacles(sample, now)
        except Exception as exc:
            with self._lock:
                self._received_samples += 1
                self._latest_sequence = sample.sequence
                self._last_error = f"{type(exc).__name__}: {exc}"
            raise
        with self._lock:
            self._received_samples += 1
            self._latest_sequence = sample.sequence
            self._last_observed_at_s = now
            self._last_error = None
            self._snapshot = ObstacleSnapshot("sensor.radar.obstacles", obstacles[0].frame if obstacles else self._snapshot.frame, now, tuple(obstacles[: self.config.max_obstacles]))

    def snapshot(self) -> ObstacleSnapshot:
        """返回最近 Radar 帧生成的不可变障碍物快照。"""

        with self._lock:
            return self._snapshot

    def status(self, *, stale_timeout_s: float = 2.0) -> RangingObstacleProviderStatus:
        """按 ``stale_timeout_s`` 秒阈值返回接收数、新鲜度、障碍物数量和最近错误。"""

        if stale_timeout_s <= 0:
            raise ValueError("stale_timeout_s must be greater than 0.")
        with self._lock:
            observed_at_s = self._last_observed_at_s
            return RangingObstacleProviderStatus(
                ready=observed_at_s is not None,
                stale=observed_at_s is None or time.monotonic() - observed_at_s > stale_timeout_s,
                received_samples=self._received_samples,
                obstacle_count=len(self._snapshot.obstacles),
                latest_sequence=self._latest_sequence,
                last_observed_at_s=observed_at_s,
                last_error=self._last_error,
            )

    def close(self) -> None:
        """清空 Radar 障碍物快照；本对象不拥有订阅，重复调用安全且无返回值。"""

        with self._lock:
            self._snapshot = ObstacleSnapshot("sensor.radar.obstacles", self._snapshot.frame, time.monotonic(), ())
            self._last_observed_at_s = None

    def _obstacles(self, sample: RadarFrame, now: float) -> list[Obstacle]:
        obstacles: list[Obstacle] = []
        for track in sample.tracks:
            if not self._in_range(math.sqrt(track.position_m.x ** 2 + track.position_m.y ** 2 + track.position_m.z ** 2)):
                continue
            frame, position, attitude = self._transform.point(track.position_m)
            velocity = self._transform.rotate(track.velocity_m_s, attitude)
            obstacles.append(Obstacle(f"radar.track.{track.track_id}", ObstacleKind.DYNAMIC, ObstacleShape.SPHERE, frame, position, velocity, self.config.track_radius_m, observed_at_s=now, ttl_s=self.config.ttl_s))
        for index, detection in enumerate(sample.detections):
            if not self._in_range(detection.range_m):
                continue
            horizontal = detection.range_m * math.cos(detection.elevation_rad)
            sensor_point = Vector3(horizontal * math.cos(detection.azimuth_rad), horizontal * math.sin(detection.azimuth_rad), -detection.range_m * math.sin(detection.elevation_rad))
            frame, position, attitude = self._transform.point(sensor_point)
            radial = Vector3(
                math.cos(detection.elevation_rad) * math.cos(detection.azimuth_rad) * detection.radial_velocity_m_s,
                math.cos(detection.elevation_rad) * math.sin(detection.azimuth_rad) * detection.radial_velocity_m_s,
                -math.sin(detection.elevation_rad) * detection.radial_velocity_m_s,
            )
            obstacles.append(Obstacle(f"radar.detection.{sample.sequence}.{index}", ObstacleKind.DYNAMIC, ObstacleShape.SPHERE, frame, position, self._transform.rotate(radial, attitude), self.config.detection_radius_m, observed_at_s=now, ttl_s=self.config.ttl_s))
        return obstacles

    def _in_range(self, distance_m: float) -> bool:
        return math.isfinite(distance_m) and self.config.min_range_m <= distance_m <= self.config.max_range_m


class DistanceObstacleProvider:
    """把显式订阅的单向测距转换为近场球形障碍物，适合悬停或停止安全策略。

    ``descriptor`` 必须来自 ``device.sensors`` 的 Distance 描述；``pose_provider`` 提供本地位置和姿态，
    ``config`` 声明安装外参、有效量程、障碍半径和 TTL。对象不拥有传感器订阅，不能单独代表完整空间
    绕行能力；调用方应显式订阅并在停止送入样本后调用 ``close()``。
    """

    def __init__(self, descriptor: SensorDescriptor, *, pose_provider: Callable[[], Any], config: DistanceObstacleConfig | None = None) -> None:
        if descriptor.kind is not SensorKind.DISTANCE:
            raise ValueError("DistanceObstacleProvider requires DISTANCE descriptor.")
        self.descriptor = descriptor
        self.config = config or DistanceObstacleConfig()
        self._transform = _BodyObstacleTransform(descriptor, pose_provider, self.config.sensor_mount)
        self._lock = RLock()
        self._snapshot = ObstacleSnapshot("sensor.distance.obstacles", CoordinateFrame.LOCAL_NED, time.monotonic(), ())
        self._received_samples = 0
        self._latest_sequence: int | None = None
        self._last_observed_at_s: float | None = None
        self._last_error: str | None = None

    def observe(self, sample: DistanceSample) -> None:
        """把 ``sample`` 米制距离更新为前向障碍物；传感器 ID 必须匹配，超出量程时清空且无返回值。"""

        if not isinstance(sample, DistanceSample) or sample.sensor_id != self.descriptor.sensor_id:
            raise ValueError("Distance sample does not match provider descriptor.")
        now = time.monotonic()
        obstacles: tuple[Obstacle, ...] = ()
        frame = self._snapshot.frame
        try:
            if math.isfinite(sample.distance_m) and self.config.min_range_m <= sample.distance_m <= self.config.max_range_m:
                frame, position, _ = self._transform.point(Vector3(sample.distance_m, 0.0, 0.0))
                obstacles = (Obstacle(f"distance.{self.descriptor.sensor_id}", ObstacleKind.UNKNOWN, ObstacleShape.SPHERE, frame, position, radius_m=self.config.obstacle_radius_m, observed_at_s=now, ttl_s=self.config.ttl_s),)
        except Exception as exc:
            with self._lock:
                self._received_samples += 1
                self._latest_sequence = sample.sequence
                self._last_error = f"{type(exc).__name__}: {exc}"
            raise
        with self._lock:
            self._received_samples += 1
            self._latest_sequence = sample.sequence
            self._last_observed_at_s = now
            self._last_error = None
            self._snapshot = ObstacleSnapshot("sensor.distance.obstacles", frame, now, obstacles)

    def snapshot(self) -> ObstacleSnapshot:
        """返回最近测距样本生成的近场障碍物快照。"""

        with self._lock:
            return self._snapshot

    def status(self, *, stale_timeout_s: float = 2.0) -> RangingObstacleProviderStatus:
        """按 ``stale_timeout_s`` 秒阈值返回接收数、新鲜度、障碍物数量和最近错误。"""

        if stale_timeout_s <= 0:
            raise ValueError("stale_timeout_s must be greater than 0.")
        with self._lock:
            observed_at_s = self._last_observed_at_s
            return RangingObstacleProviderStatus(
                ready=observed_at_s is not None,
                stale=observed_at_s is None or time.monotonic() - observed_at_s > stale_timeout_s,
                received_samples=self._received_samples,
                obstacle_count=len(self._snapshot.obstacles),
                latest_sequence=self._latest_sequence,
                last_observed_at_s=observed_at_s,
                last_error=self._last_error,
            )

    def close(self) -> None:
        """清空测距障碍物快照；本对象不拥有订阅，重复调用安全且无返回值。"""

        with self._lock:
            self._snapshot = ObstacleSnapshot("sensor.distance.obstacles", self._snapshot.frame, time.monotonic(), ())
            self._last_observed_at_s = None
