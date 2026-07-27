"""标准点云传感器流到 FleetDock 障碍物 Provider 的基础桥接。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import logging
from threading import Condition, RLock
import time
from typing import Any, Callable, cast

from fleetdock.events import CoordinateFrame, Vector3
from fleetdock.obstacles import Obstacle, ObstacleKind, ObstacleShape, ObstacleSnapshot
from fleetdock.sensors import PointCloudEncoding, PointCloudFrame

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PointCloudDirection:
    """点云坐标系中的水平单位方向及该方向上的障碍距离。

    ``x`` 和 ``y`` 是点云 XYZ 坐标系水平面的单位方向分量；``obstacle_distance_m`` 是沿该方向
    走廊检测到的最近前向距离，单位为米，未知时为 ``None``。``rotated(yaw_deg)`` 按俯视平面
    正方向旋转，用于在机体系方向与本地 NED 水平方向之间转换。
    """

    x: float
    y: float
    obstacle_distance_m: float | None = None

    def __post_init__(self) -> None:
        length = math.hypot(self.x, self.y)
        if not math.isfinite(length) or length <= 0:
            raise ValueError("Point cloud direction must contain a finite non-zero horizontal vector.")
        object.__setattr__(self, "x", self.x / length)
        object.__setattr__(self, "y", self.y / length)
        if self.obstacle_distance_m is not None:
            if not math.isfinite(self.obstacle_distance_m) or self.obstacle_distance_m < 0:
                raise ValueError("Point cloud obstacle distance must be a finite non-negative value.")

    def rotated(self, yaw_deg: float) -> "PointCloudDirection":
        """把水平单位方向旋转 ``yaw_deg`` 度并返回新对象。

        对 NED 俯视平面，正角度把机体系前向方向旋转到当前偏航对应的本地 NED 方向；传入负偏航角
        可执行反向转换。障碍距离保持不变，原对象不会修改。
        """

        if not math.isfinite(yaw_deg):
            raise ValueError("Point cloud direction yaw must be finite.")
        yaw_rad = math.radians(yaw_deg)
        return PointCloudDirection(
            x=math.cos(yaw_rad) * self.x - math.sin(yaw_rad) * self.y,
            y=math.sin(yaw_rad) * self.x + math.cos(yaw_rad) * self.y,
            obstacle_distance_m=self.obstacle_distance_m,
        )

@dataclass(frozen=True)
class _PointCloudObservation:
    sequence: int
    points: tuple[tuple[float, float, float], ...]


class PointCloudCorridorAnalyzer:
    """线程安全地分析 LiDAR 历史帧中的水平飞行走廊。

    订阅回调应把每个 ``PointCloudFrame`` 传给 ``observe()``。分析器过滤非有限点、过近点以及
    超出 ``corridor_half_height_m`` 的点，并按 ``history_size`` 和 ``max_points_per_frame`` 保持
    有界内存。``nearest_distance()`` 查询指定方向走廊内的最近前向回波；``select_direction()``
    从历史点云选择满足障碍距离区间的方向。所有距离单位为米，方向始终使用输入点云自身的 XYZ
    坐标系；SDK 不会在坐标系未知时猜测安装外参。
    """

    def __init__(
        self,
        *,
        corridor_half_width_m: float = 2.0,
        corridor_half_height_m: float = 2.5,
        min_range_m: float = 0.3,
        history_size: int = 50,
        max_points_per_frame: int = 2000,
        max_analysis_points: int = 2500,
        max_candidate_directions: int = 180,
    ) -> None:
        if corridor_half_width_m <= 0 or corridor_half_height_m <= 0:
            raise ValueError("Point cloud corridor width and height must be greater than 0.")
        if min_range_m < 0:
            raise ValueError("Point cloud minimum range must not be negative.")
        if min(history_size, max_points_per_frame, max_analysis_points, max_candidate_directions) <= 0:
            raise ValueError("Point cloud analyzer capacity limits must be greater than 0.")
        self._corridor_half_width_m = corridor_half_width_m
        self._corridor_half_height_m = corridor_half_height_m
        self._min_range_m = min_range_m
        self._max_points_per_frame = max_points_per_frame
        self._max_analysis_points = max_analysis_points
        self._max_candidate_directions = max_candidate_directions
        self._condition = Condition(RLock())
        self._observations: deque[_PointCloudObservation] = deque(maxlen=history_size)

    @property
    def latest_sequence(self) -> int | None:
        """返回最近已观察点云帧的序号；尚未收到点云时返回 ``None``。"""

        with self._condition:
            return self._observations[-1].sequence if self._observations else None

    def observe(self, sample: PointCloudFrame) -> None:
        """接收并缓存一帧标准点云，供后续走廊查询使用。

        ``sample`` 的 XYZ 坐标单位必须为米；方法可直接从传感器后台订阅线程调用。非
        ``PointCloudFrame`` 会抛出 ``TypeError``，非 ``XYZ_F32`` 编码会抛出 ``ValueError``。
        方法只保留有界抽样后的 Python 点，不长期持有后端原生缓冲区，无返回值。
        """

        if not isinstance(sample, PointCloudFrame):
            raise TypeError("Point cloud corridor analyzer requires PointCloudFrame samples.")
        if sample.encoding is not PointCloudEncoding.XYZ_F32:
            raise ValueError(f"Unsupported point cloud encoding: {sample.encoding.value}")
        points = tuple(
            point
            for point in sample.xyz_points()
            if all(math.isfinite(value) for value in point)
            and abs(point[2]) <= self._corridor_half_height_m
            and math.hypot(point[0], point[1]) >= self._min_range_m
        )
        stride = max(1, math.ceil(len(points) / self._max_points_per_frame))
        observation = _PointCloudObservation(sample.sequence, points[::stride])
        with self._condition:
            self._observations.append(observation)
            self._condition.notify_all()
        logger.debug(
            "点云走廊分析器收到样本: sensor_id=%s sequence=%d retained_points=%d",
            sample.sensor_id,
            sample.sequence,
            len(observation.points),
        )

    def wait_for_sample(self, timeout_s: float, *, after_sequence: int | None = None) -> bool:
        """等待收到点云帧并返回是否在超时前满足条件。

        ``timeout_s`` 是最长等待秒数；``after_sequence`` 为 ``None`` 时等待任意首帧，否则等待序号
        严格大于该值的新帧。该方法只等待订阅回调调用 ``observe()``，不会自行启动传感器订阅。
        """

        if timeout_s < 0:
            raise ValueError("Point cloud sample timeout must not be negative.")
        with self._condition:
            return self._condition.wait_for(
                lambda: bool(self._observations)
                and (after_sequence is None or self._observations[-1].sequence > after_sequence),
                timeout=timeout_s,
            )

    def clear(self) -> None:
        """清空已缓存的历史点云；后续等待和方向选择只使用新收到的帧，无返回值。"""

        with self._condition:
            self._observations.clear()

    def nearest_distance(
        self,
        direction: PointCloudDirection,
        *,
        latest_only: bool = True,
    ) -> float | None:
        """返回指定水平走廊内最近回波的前向距离，单位为米。

        ``direction`` 必须与输入点云使用同一坐标系；``latest_only=True`` 只查询最近帧，适合实时
        停障判断，设为 ``False`` 时查询当前有界历史。走廊没有有效前向回波时返回 ``None``。
        """

        return self._nearest(self._points(latest_only=latest_only), direction)

    def select_direction(
        self,
        *,
        min_obstacle_distance_m: float,
        max_obstacle_distance_m: float,
        preferred_obstacle_distance_m: float | None = None,
    ) -> PointCloudDirection | None:
        """从历史点云选择一条先有净空、随后存在障碍的水平走廊方向。

        障碍最近距离必须位于闭区间 ``min_obstacle_distance_m`` 到
        ``max_obstacle_distance_m``，单位为米；``preferred_obstacle_distance_m`` 用于在多个
        候选中选择最接近期望距离的方向，省略时使用区间中点。没有候选方向时返回 ``None``。
        """

        candidates = self.candidate_directions(
            min_obstacle_distance_m=min_obstacle_distance_m,
            max_obstacle_distance_m=max_obstacle_distance_m,
        )
        preferred_m = preferred_obstacle_distance_m
        if preferred_m is None:
            preferred_m = (min_obstacle_distance_m + max_obstacle_distance_m) / 2.0
        if not min_obstacle_distance_m <= preferred_m <= max_obstacle_distance_m:
            raise ValueError("Preferred obstacle distance must be inside the selection interval.")
        if not candidates:
            logger.debug("点云历史中没有满足距离区间的水平走廊方向")
            return None
        selected = min(
            candidates,
            key=lambda direction: abs((direction.obstacle_distance_m or 0.0) - preferred_m),
        )
        logger.debug(
            "点云走廊方向选择完成: x=%.3f y=%.3f obstacle_distance_m=%.3f",
            selected.x,
            selected.y,
            selected.obstacle_distance_m,
        )
        return selected

    def candidate_directions(
        self,
        *,
        min_obstacle_distance_m: float,
        max_obstacle_distance_m: float,
    ) -> tuple[PointCloudDirection, ...]:
        """返回历史点云中满足障碍距离区间的水平走廊候选方向。

        ``min_obstacle_distance_m`` 表示候选走廊必须具备的前段净空，
        ``max_obstacle_distance_m`` 限制随后可观察障碍的最远距离，单位均为米。返回方向使用输入
        点云坐标系并携带最近障碍距离；无候选时返回空元组。方法不负责随机或业务策略选择，调用方
        可按演示、探索或规划需要自行选择。
        """

        if min_obstacle_distance_m < 0 or max_obstacle_distance_m <= min_obstacle_distance_m:
            raise ValueError("Point cloud direction distance limits are invalid.")
        points = self._points(latest_only=False)
        candidate_stride = max(1, math.ceil(len(points) / self._max_candidate_directions))
        candidates: list[PointCloudDirection] = []
        for x_m, y_m, _ in points[::candidate_stride]:
            direction = PointCloudDirection(x_m, y_m)
            nearest_m = self._nearest(points, direction)
            if nearest_m is not None and min_obstacle_distance_m <= nearest_m <= max_obstacle_distance_m:
                candidates.append(PointCloudDirection(direction.x, direction.y, nearest_m))
        logger.debug(
            "点云走廊候选方向分析完成: candidates=%d min_distance_m=%.3f max_distance_m=%.3f",
            len(candidates),
            min_obstacle_distance_m,
            max_obstacle_distance_m,
        )
        return tuple(candidates)

    def _points(self, *, latest_only: bool) -> tuple[tuple[float, float, float], ...]:
        with self._condition:
            observations = tuple(self._observations)
        if latest_only:
            return observations[-1].points if observations else ()
        points = tuple(point for observation in observations for point in observation.points)
        stride = max(1, math.ceil(len(points) / self._max_analysis_points))
        return points[::stride]

    def _nearest(
        self,
        points: tuple[tuple[float, float, float], ...],
        direction: PointCloudDirection,
    ) -> float | None:
        nearest_m = None
        for x_m, y_m, _ in points:
            forward_m = x_m * direction.x + y_m * direction.y
            lateral_m = abs(-x_m * direction.y + y_m * direction.x)
            if forward_m < self._min_range_m or lateral_m > self._corridor_half_width_m:
                continue
            nearest_m = forward_m if nearest_m is None else min(nearest_m, forward_m)
        return nearest_m


@dataclass(frozen=True)
class PointCloudObstacleTrackerStatus:
    """点云障碍跟踪器的线程安全状态快照。

    ``received_frames`` 是收到的标准点云帧总数，``processed_frames`` 是完成坐标转换和体素化的帧数，
    ``dropped_frames`` 是因姿态、坐标系或关闭状态而拒绝的帧数；点数均描述最近一次成功处理的帧。
    ``last_observed_at_s`` 使用进程单调时钟秒值，``last_error`` 保存最近一次拒绝原因，``None`` 表示
    最近处理没有诊断错误。
    """

    closed: bool
    received_frames: int
    processed_frames: int
    dropped_frames: int
    last_sequence: int | None
    input_points: int
    retained_points: int
    obstacle_count: int
    last_observed_at_s: float | None
    last_processing_s: float | None
    last_error: str | None


class PointCloudObstacleTracker:
    """把手动送入的 LiDAR 点云转换为具有稳定空间 ID 的本地障碍物 Provider。

    调用方通过 ``drone.sensors.subscribe(SensorKind.LIDAR, handler)`` 接收点云，并在回调中调用
    ``observe(sample)``。``pose_provider`` 应返回 ``DeviceState`` 兼容对象，为机体系点云提供本地位置、
    RPY 姿态和 ``local_position_frame``；``sensor_offset_m`` 与 ``sensor_rotation_rpy_deg`` 描述 LiDAR
    相对机体的米制安装外参。``body_exclusion_half_extents_m`` 可声明以机体原点为中心的米制半尺寸包围盒，
    用于剔除旋翼、起落架等机身自反射；调用方应按真实机体外廓配置，不能用它隐藏安全包络外的近距离障碍。
    跟踪器把点云变换到 ``output_frame``，按 ``voxel_size_m`` 聚合为轴对齐长方体障碍物，障碍物 ID 由
    本地体素坐标生成，因此同一静态空间在连续帧中保持稳定。

    ``assume_unknown_body_frame`` 只适合调用方已经确认原始协议坐标语义、但后端尚未声明标准坐标系的
    迁移场景；默认拒绝 ``UNKNOWN``，SDK 不会自行猜测。该基础跟踪器用于低速 SDK route 绕行，不替代
    生产级地面分割、语义识别、SLAM 或动态目标跟踪。
    """

    def __init__(
        self,
        *,
        output_frame: CoordinateFrame,
        pose_provider: Callable[[], Any] | None = None,
        sensor_offset_m: Vector3 = Vector3(0.0, 0.0, 0.0),
        sensor_rotation_rpy_deg: Vector3 = Vector3(0.0, 0.0, 0.0),
        body_exclusion_half_extents_m: Vector3 | None = None,
        assume_unknown_body_frame: bool = False,
        min_range_m: float = 0.5,
        max_range_m: float = 100.0,
        vertical_half_extent_m: float = 4.0,
        voxel_size_m: float = 2.0,
        max_input_points: int = 12000,
        max_obstacles: int = 96,
        ttl_s: float = 1.0,
        max_pose_age_s: float = 1.0,
        provider_name: str = "sensor.lidar.voxel_tracker",
    ) -> None:
        if output_frame not in {CoordinateFrame.LOCAL_NED, CoordinateFrame.LOCAL_ENU}:
            raise ValueError("Point cloud tracker output frame must be LOCAL_NED or LOCAL_ENU.")
        if min_range_m < 0 or max_range_m <= min_range_m:
            raise ValueError("Point cloud tracker range limits are invalid.")
        if vertical_half_extent_m <= 0 or voxel_size_m <= 0 or ttl_s <= 0 or max_pose_age_s <= 0:
            raise ValueError("Point cloud tracker spatial and time limits must be greater than 0.")
        if max_input_points <= 0 or max_obstacles <= 0:
            raise ValueError("Point cloud tracker capacity limits must be greater than 0.")
        if body_exclusion_half_extents_m is not None and any(
            value <= 0
            for value in (
                body_exclusion_half_extents_m.x,
                body_exclusion_half_extents_m.y,
                body_exclusion_half_extents_m.z,
            )
        ):
            raise ValueError("Point cloud tracker body exclusion half extents must be greater than 0.")
        self._frame = output_frame
        self._pose_provider = pose_provider
        self._sensor_offset_m = sensor_offset_m
        self._sensor_rotation_rpy_deg = sensor_rotation_rpy_deg
        self._body_exclusion_half_extents_m = body_exclusion_half_extents_m
        self._assume_unknown_body_frame = assume_unknown_body_frame
        self._min_range_m = min_range_m
        self._max_range_m = max_range_m
        self._vertical_half_extent_m = vertical_half_extent_m
        self._voxel_size_m = voxel_size_m
        self._max_input_points = max_input_points
        self._max_obstacles = max_obstacles
        self._ttl_s = ttl_s
        self._max_pose_age_s = max_pose_age_s
        self._provider_name = provider_name
        self._lock = RLock()
        self._snapshot = ObstacleSnapshot(provider_name, output_frame, time.monotonic(), ())
        self._closed = False
        self._received_frames = 0
        self._processed_frames = 0
        self._dropped_frames = 0
        self._last_sequence: int | None = None
        self._input_points = 0
        self._retained_points = 0
        self._last_observed_at_s: float | None = None
        self._last_processing_s: float | None = None
        self._last_error: str | None = None

    def observe(self, sample: PointCloudFrame) -> bool:
        """处理一帧标准点云并返回是否生成了新的本地障碍物快照。

        ``sample`` 坐标单位必须为米且编码为 ``XYZ_F32``。本地坐标点云可直接聚合；``BODY`` 点云会
        使用构造时的安装外参和 ``pose_provider`` 返回姿态转换。姿态缺失、过期或坐标系不支持时返回
        ``False`` 并更新 ``status()``，不会在传感器后台回调线程抛出业务异常。类型或编码错误仍会
        抛出 ``TypeError``/``ValueError``，便于尽早发现后端契约错误。
        """

        if not isinstance(sample, PointCloudFrame):
            raise TypeError("Point cloud obstacle tracker requires PointCloudFrame samples.")
        if sample.encoding is not PointCloudEncoding.XYZ_F32:
            raise ValueError(f"Unsupported point cloud encoding: {sample.encoding.value}")
        started_at_s = time.monotonic()
        with self._lock:
            self._received_frames += 1
            if self._closed:
                self._drop(sample.sequence, "point cloud obstacle tracker is closed")
                return False
        transform = self._resolve_transform(sample, started_at_s)
        if transform is None:
            return False

        values = sample.data.cast("B").cast("f")
        point_count = min(sample.point_count, len(values) // 3)
        stride = max(1, math.ceil(point_count / self._max_input_points))
        voxels: dict[tuple[int, int, int], list[float]] = {}
        retained_points = 0
        for point_index in range(0, point_count, stride):
            offset = point_index * 3
            point = Vector3(float(values[offset]), float(values[offset + 1]), float(values[offset + 2]))
            if not all(math.isfinite(value) for value in (point.x, point.y, point.z)):
                continue
            range_m = math.sqrt(point.x * point.x + point.y * point.y + point.z * point.z)
            if range_m < self._min_range_m or range_m > self._max_range_m:
                continue
            body_point, local_point = transform(point)
            exclusion = self._body_exclusion_half_extents_m
            if exclusion is not None and (
                abs(body_point.x) <= exclusion.x
                and abs(body_point.y) <= exclusion.y
                and abs(body_point.z) <= exclusion.z
            ):
                continue
            if abs(body_point.z) > self._vertical_half_extent_m:
                continue
            key = (
                math.floor(local_point.x / self._voxel_size_m),
                math.floor(local_point.y / self._voxel_size_m),
                math.floor(local_point.z / self._voxel_size_m),
            )
            bucket = voxels.setdefault(key, [0.0, 0.0, 0.0, 0.0, range_m])
            bucket[0] += local_point.x
            bucket[1] += local_point.y
            bucket[2] += local_point.z
            bucket[3] += 1.0
            bucket[4] = min(bucket[4], range_m)
            retained_points += 1

        observed_at_s = time.monotonic()
        sorted_voxels = sorted(voxels.items(), key=lambda item: item[1][4])[: self._max_obstacles]
        obstacles = tuple(
            self._voxel_obstacle(key, values, observed_at_s)
            for key, values in sorted_voxels
        )
        snapshot = ObstacleSnapshot(self._provider_name, self._frame, observed_at_s, obstacles)
        with self._lock:
            if self._closed:
                self._drop(sample.sequence, "point cloud obstacle tracker closed during processing")
                return False
            self._snapshot = snapshot
            self._processed_frames += 1
            self._last_sequence = sample.sequence
            self._input_points = point_count
            self._retained_points = retained_points
            self._last_observed_at_s = observed_at_s
            self._last_processing_s = observed_at_s - started_at_s
            self._last_error = None
        logger.debug(
            "点云障碍跟踪完成: sensor_id=%s sequence=%d input=%d retained=%d voxels=%d obstacles=%d processing_s=%.4f",
            sample.sensor_id,
            sample.sequence,
            point_count,
            retained_points,
            len(voxels),
            len(obstacles),
            observed_at_s - started_at_s,
        )
        return True

    def snapshot(self) -> ObstacleSnapshot:
        """返回最近一次成功处理生成的本地体素障碍物快照；该方法不阻塞点云处理。"""

        with self._lock:
            return self._snapshot

    def status(self) -> PointCloudObstacleTrackerStatus:
        """返回帧计数、点数、障碍物数量、处理耗时和最近错误的不可变状态快照。"""

        with self._lock:
            return PointCloudObstacleTrackerStatus(
                closed=self._closed,
                received_frames=self._received_frames,
                processed_frames=self._processed_frames,
                dropped_frames=self._dropped_frames,
                last_sequence=self._last_sequence,
                input_points=self._input_points,
                retained_points=self._retained_points,
                obstacle_count=len(self._snapshot.obstacles),
                last_observed_at_s=self._last_observed_at_s,
                last_processing_s=self._last_processing_s,
                last_error=self._last_error,
            )

    def is_stale(self, timeout_s: float) -> bool:
        """返回最近一次成功点云处理是否已超过 ``timeout_s`` 秒；从未成功处理时也返回 ``True``。"""

        if timeout_s <= 0:
            raise ValueError("Point cloud tracker stale timeout must be greater than 0.")
        with self._lock:
            observed_at_s = self._last_observed_at_s
        return observed_at_s is None or time.monotonic() - observed_at_s > timeout_s

    def close(self) -> None:
        """关闭跟踪器并清空障碍物快照；关闭后送入的帧会被拒绝，重复调用安全且无返回值。"""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._snapshot = ObstacleSnapshot(self._provider_name, self._frame, time.monotonic(), ())
        logger.debug("点云障碍跟踪器已关闭: provider=%s", self._provider_name)

    def _resolve_transform(
        self,
        sample: PointCloudFrame,
        now_s: float,
    ) -> Callable[[Vector3], tuple[Vector3, Vector3]] | None:
        if sample.frame is self._frame:
            return lambda point: (Vector3(point.x, point.y, 0.0), point)
        is_body = sample.frame is CoordinateFrame.BODY or (
            sample.frame is CoordinateFrame.UNKNOWN and self._assume_unknown_body_frame
        )
        if not is_body:
            self._drop(sample.sequence, f"unsupported point cloud frame: {sample.frame.value}")
            return None
        if self._pose_provider is None:
            self._drop(sample.sequence, "pose provider is required for body-frame point clouds")
            return None
        try:
            pose = self._pose_provider()
        except Exception as exc:
            self._drop(sample.sequence, f"pose provider failed: {type(exc).__name__}: {exc}")
            return None
        position_values = (
            getattr(pose, "local_x_m", None),
            getattr(pose, "local_y_m", None),
            getattr(pose, "local_z_m", None),
        )
        attitude_values = (
            getattr(pose, "roll_deg", None),
            getattr(pose, "pitch_deg", None),
            getattr(pose, "yaw_deg", None),
        )
        pose_frame = getattr(pose, "local_position_frame", CoordinateFrame.UNKNOWN)
        updated_at_s = getattr(pose, "local_position_updated_at_s", None)
        if pose_frame is not self._frame:
            self._drop(sample.sequence, f"pose frame does not match tracker output: {pose_frame}")
            return None
        if any(value is None or not math.isfinite(float(value)) for value in (*position_values, *attitude_values)):
            self._drop(sample.sequence, "local position or RPY attitude is unavailable")
            return None
        assert all(value is not None for value in (*position_values, *attitude_values))
        if updated_at_s is None or now_s - float(updated_at_s) > self._max_pose_age_s:
            self._drop(sample.sequence, "local pose is stale")
            return None
        position = Vector3(*(float(cast(float, value)) for value in position_values))
        roll_deg, pitch_deg, yaw_deg = (float(cast(float, value)) for value in attitude_values)

        def transform(point: Vector3) -> tuple[Vector3, Vector3]:
            sensor_rotated = self._rotate_rpy(point, self._sensor_rotation_rpy_deg)
            body_point = Vector3(
                sensor_rotated.x + self._sensor_offset_m.x,
                sensor_rotated.y + self._sensor_offset_m.y,
                sensor_rotated.z + self._sensor_offset_m.z,
            )
            local_delta = self._rotate_rpy(body_point, Vector3(roll_deg, pitch_deg, yaw_deg))
            return body_point, Vector3(
                position.x + local_delta.x,
                position.y + local_delta.y,
                position.z + local_delta.z,
            )

        return transform

    def _voxel_obstacle(
        self,
        key: tuple[int, int, int],
        values: list[float],
        observed_at_s: float,
    ) -> Obstacle:
        count = max(values[3], 1.0)
        return Obstacle(
            obstacle_id=f"{self._provider_name}.voxel.{key[0]}.{key[1]}.{key[2]}",
            kind=ObstacleKind.UNKNOWN,
            shape=ObstacleShape.BOX,
            frame=self._frame,
            position_m=Vector3(values[0] / count, values[1] / count, values[2] / count),
            size_m=Vector3(self._voxel_size_m, self._voxel_size_m, self._voxel_size_m),
            confidence=None,
            observed_at_s=observed_at_s,
            ttl_s=self._ttl_s,
        )

    def _drop(self, sequence: int, reason: str) -> None:
        with self._lock:
            self._dropped_frames += 1
            self._last_sequence = sequence
            self._last_error = reason
        logger.debug("点云障碍帧被拒绝: sequence=%d reason=%s", sequence, reason)

    @staticmethod
    def _rotate_rpy(point: Vector3, rpy_deg: Vector3) -> Vector3:
        roll, pitch, yaw = (math.radians(value) for value in (rpy_deg.x, rpy_deg.y, rpy_deg.z))
        cr, sr = math.cos(roll), math.sin(roll)
        cp, sp = math.cos(pitch), math.sin(pitch)
        cy, sy = math.cos(yaw), math.sin(yaw)
        return Vector3(
            (cy * cp) * point.x + (cy * sp * sr - sy * cr) * point.y + (cy * sp * cr + sy * sr) * point.z,
            (sy * cp) * point.x + (sy * sp * sr + cy * cr) * point.y + (sy * sp * cr - cy * sr) * point.z,
            (-sp) * point.x + (cp * sr) * point.y + (cp * cr) * point.z,
        )

