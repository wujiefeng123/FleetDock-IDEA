"""面向应用的 LiDAR 点云避障高层 Provider。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
import logging
import math
import time
from random import Random
from threading import Condition, RLock, Thread
from typing import TYPE_CHECKING, Any, Callable

from fleetdock.events import CoordinateFrame, Vector3
from fleetdock.obstacles import Obstacle, ObstacleKind, ObstacleShape, ObstacleSnapshot
from fleetdock.avoidance.point_cloud import (
    PointCloudCorridorAnalyzer,
    PointCloudObstacleTracker,
)
from fleetdock.sensors import PointCloudFrame, SensorDescriptor, SensorKind, SensorMount

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from fleetdock.avoidance.core import AvoidanceManager, AvoidancePolicy
    from fleetdock.devices import DeviceState


class LidarAvoidancePreset(str, Enum):
    """LiDAR 点云避障的标准参数组合；真机安装外参仍以传感器标定为准。"""

    LOW_ALTITUDE = "low_altitude"  # 低速低空飞行，保留当前高度附近较窄的水平障碍带。
    OPEN_AREA = "open_area"  # 室外开阔区域，使用较大体素和较长量程降低处理负载。
    INDOOR = "indoor"  # 室内或狭窄空间，使用较小体素保留近距离结构细节。
    HIGH_DENSITY = "high_density"  # 高密度点云，强化降采样和容量限制以稳定处理延迟。


@dataclass(frozen=True)
class LidarObstacleConfig:
    """LiDAR 高层 Provider 的点云过滤、融合和容量配置。

    所有空间字段单位为米，时间字段单位为秒。``sensor_mount`` 可覆盖 ``SensorDescriptor.mount``，
    仅用于调用方持有更准确标定的场景；BODY 点云两处都缺少标定时会拒绝创建 Provider。
    ``fusion_window_s`` 是短时多帧融合窗口；``cluster_adjacent_voxels`` 控制是否把同一水平位置的
    垂直相邻体素合并成柱状长方体，既减少重复障碍，又避免把墙角包成覆盖空区的大盒。配置对象不可变且不启动订阅。
    """

    preset: LidarAvoidancePreset = LidarAvoidancePreset.LOW_ALTITUDE
    sensor_mount: SensorMount | None = None
    body_exclusion_half_extents_m: Vector3 = Vector3(2.5, 2.5, 2.0)
    min_range_m: float = 0.5
    max_range_m: float = 100.0
    vertical_half_extent_m: float = 2.0
    corridor_half_width_m: float = 3.0
    corridor_half_height_m: float = 4.0
    voxel_size_m: float = 2.0
    max_input_points: int = 12000
    max_obstacles: int = 96
    ttl_s: float = 1.0
    max_pose_age_s: float = 1.0
    fusion_window_s: float = 0.5
    cluster_adjacent_voxels: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.preset, LidarAvoidancePreset):
            object.__setattr__(self, "preset", LidarAvoidancePreset(self.preset))
        positive = (
            self.max_range_m,
            self.vertical_half_extent_m,
            self.corridor_half_width_m,
            self.corridor_half_height_m,
            self.voxel_size_m,
            self.ttl_s,
            self.max_pose_age_s,
            self.fusion_window_s,
        )
        if self.min_range_m < 0 or self.max_range_m <= self.min_range_m or any(value <= 0 for value in positive):
            raise ValueError("LiDAR obstacle config spatial and time limits are invalid.")
        if self.max_input_points <= 0 or self.max_obstacles <= 0:
            raise ValueError("LiDAR obstacle config capacities must be greater than 0.")

    @classmethod
    def for_preset(cls, preset: LidarAvoidancePreset | str) -> "LidarObstacleConfig":
        """返回 ``preset`` 对应的标准配置；调用方可用 ``dataclasses.replace`` 覆盖少量字段。"""

        resolved = preset if isinstance(preset, LidarAvoidancePreset) else LidarAvoidancePreset(preset)
        if resolved is LidarAvoidancePreset.OPEN_AREA:
            return cls(preset=resolved, voxel_size_m=3.0, max_range_m=120.0, max_obstacles=80)
        if resolved is LidarAvoidancePreset.INDOOR:
            return cls(
                preset=resolved,
                body_exclusion_half_extents_m=Vector3(1.0, 1.0, 1.0),
                max_range_m=35.0,
                vertical_half_extent_m=1.5,
                corridor_half_width_m=1.5,
                voxel_size_m=0.75,
                max_obstacles=160,
            )
        if resolved is LidarAvoidancePreset.HIGH_DENSITY:
            return cls(preset=resolved, voxel_size_m=2.5, max_input_points=6000, max_obstacles=72)
        return cls(preset=resolved)


@dataclass(frozen=True)
class DepartureDirectionPolicy:
    """从 LiDAR 历史点云选择安全起航方向的分级净空策略。

    ``preferred_clearance_m`` 是首选入口净空，``minimum_clearance_m`` 是允许的最低入口净空，
    ``clearance_step_m`` 是逐级放宽步长；``obstacle_range_m`` 要求该方向在此距离内存在回波，单位均为米。
    ``safety_radius_m`` 是设备与障碍物表面的最低水平净空，``departure_probe_m`` 是验证起步方向的距离；
    执行 route 前应让 ``departure_probe_m`` 不小于航线的最小前视距离，避免起航选择与首段避障使用不同范围。
    如果当前位置已进入安全半径，只保留能严格远离该障碍物的方向。最低入口净空不应小于安全半径。
    """

    preferred_clearance_m: float = 15.0
    minimum_clearance_m: float = 4.0
    clearance_step_m: float = 2.0
    obstacle_range_m: float = 80.0
    safety_radius_m: float = 3.7
    departure_probe_m: float = 2.0

    def __post_init__(self) -> None:
        if self.minimum_clearance_m < 0 or self.preferred_clearance_m < self.minimum_clearance_m:
            raise ValueError("Departure direction clearance limits are invalid.")
        if (
            self.clearance_step_m <= 0
            or self.obstacle_range_m <= self.preferred_clearance_m
            or self.safety_radius_m <= 0
            or self.departure_probe_m <= 0
        ):
            raise ValueError("Departure direction step and obstacle range are invalid.")

    def clearance_levels_m(self) -> tuple[float, ...]:
        """返回从首选净空逐级降低到最低净空的米制阈值序列。"""

        values: list[float] = []
        current = self.preferred_clearance_m
        while current > self.minimum_clearance_m:
            values.append(current)
            current -= self.clearance_step_m
        values.append(self.minimum_clearance_m)
        return tuple(values)


@dataclass(frozen=True)
class DepartureDirection:
    """SDK 选出的起航方向。

    ``body_direction`` 与 LiDAR BODY 水平坐标一致，``local_direction`` 使用设备本地 NED/ENU 水平坐标；
    两者均为单位向量。``heading_deg`` 是本地 x 轴到方向的偏航角，``obstacle_distance_m`` 和
    ``selected_clearance_m`` 单位为米。
    """

    body_direction: Vector3
    local_direction: Vector3
    heading_deg: float
    obstacle_distance_m: float
    selected_clearance_m: float


@dataclass(frozen=True)
class LidarProviderReadiness:
    """LiDAR Provider 就绪检查结果；``reason`` 为空表示已可提供本地障碍物快照。"""

    ready: bool
    received_frames: int
    processed_frames: int
    pose_available: bool
    frame_supported: bool
    reason: str | None = None


@dataclass(frozen=True)
class LidarObstacleProviderStatus:
    """LiDAR 高层 Provider 的线程安全运行状态。

    帧计数用于判断回调、后台处理和覆盖情况；点数与障碍物数描述最近成功帧；时间字段使用毫秒或秒，
    ``last_error`` 为空表示最近后台处理没有诊断错误。
    """

    ready: bool
    stale: bool
    received_frames: int
    processed_frames: int
    overwritten_frames: int
    dropped_frames: int
    input_points: int
    retained_points: int
    raw_obstacle_count: int
    obstacle_count: int
    processing_ms: float | None
    latest_sequence: int | None
    last_error: str | None


class LidarObstacleProvider:
    """把显式订阅的标准 LiDAR 点云转换为可供 ``device.avoidance`` 使用的障碍物 Provider。

    ``observe()`` 只保留最新帧并立即返回，坐标变换、点云过滤、短时融合和相邻体素聚类在独立后台线程
    完成，不阻塞传感器订阅线程。调用方负责将 ``observe`` 传给 ``device.sensors.subscribe``，再把本对象
    注册到 ``device.avoidance``；``close()`` 必须在取消订阅后调用。该基础实现面向低速 SDK route，
    不替代生产级语义感知、SLAM 或动态目标跟踪。
    """

    def __init__(
        self,
        descriptor: SensorDescriptor,
        *,
        pose_provider: Callable[[], Any],
        config: LidarObstacleConfig | None = None,
        provider_name: str = "sensor.lidar.obstacle_provider",
    ) -> None:
        self.descriptor = descriptor
        if descriptor.kind is not SensorKind.LIDAR:
            raise ValueError("LidarObstacleProvider requires a SensorKind.LIDAR descriptor.")
        self.config = config or LidarObstacleConfig.for_preset(LidarAvoidancePreset.LOW_ALTITUDE)
        mount = self.config.sensor_mount or descriptor.mount
        if descriptor.frame is CoordinateFrame.BODY and mount is None:
            raise ValueError("BODY-frame LiDAR requires calibrated SensorDescriptor.mount or config.sensor_mount.")
        mount = mount or SensorMount(Vector3(0.0, 0.0, 0.0), parent_frame=descriptor.frame)
        self._pose_provider = pose_provider
        self._provider_name = provider_name
        pose_frame = getattr(pose_provider(), "local_position_frame", CoordinateFrame.UNKNOWN)
        self._output_frame = (
            pose_frame
            if pose_frame in {CoordinateFrame.LOCAL_NED, CoordinateFrame.LOCAL_ENU}
            else CoordinateFrame.LOCAL_NED
        )
        self._corridor = PointCloudCorridorAnalyzer(
            corridor_half_width_m=self.config.corridor_half_width_m,
            corridor_half_height_m=self.config.corridor_half_height_m,
        )
        self._tracker = PointCloudObstacleTracker(
            output_frame=self._output_frame,
            pose_provider=pose_provider,
            sensor_offset_m=mount.position_m,
            sensor_rotation_rpy_deg=mount.rotation_rpy_deg,
            body_exclusion_half_extents_m=self.config.body_exclusion_half_extents_m,
            min_range_m=self.config.min_range_m,
            max_range_m=self.config.max_range_m,
            vertical_half_extent_m=self.config.vertical_half_extent_m,
            voxel_size_m=self.config.voxel_size_m,
            max_input_points=self.config.max_input_points,
            max_obstacles=self.config.max_obstacles,
            ttl_s=self.config.ttl_s,
            max_pose_age_s=self.config.max_pose_age_s,
            provider_name=f"{provider_name}.voxels",
        )
        now_s = time.monotonic()
        self._snapshot = ObstacleSnapshot(provider_name, self._output_frame, now_s, ())
        self._history: deque[ObstacleSnapshot] = deque()
        self._condition = Condition(RLock())
        self._pending: PointCloudFrame | None = None
        self._closed = False
        self._ready = False
        self._received_frames = 0
        self._processed_frames = 0
        self._overwritten_frames = 0
        self._raw_obstacle_count = 0
        self._direction_frames = 0
        self._direction_reset_at = 0
        self._last_error: str | None = None
        self._worker = Thread(target=self._run, name=f"fleetdock-{descriptor.sensor_id}-obstacles", daemon=True)
        self._worker.start()

    def observe(self, sample: PointCloudFrame) -> None:
        """提交一帧 ``PointCloudFrame`` 并立即返回。

        ``sample`` 是显式 LiDAR 订阅收到的标准点云帧，传感器 ID 必须与当前 Provider 描述一致。
        尚未处理的旧帧会被最新帧覆盖以保证实时性；本方法无返回值。
        """

        if not isinstance(sample, PointCloudFrame):
            raise TypeError("LiDAR obstacle provider requires PointCloudFrame samples.")
        if sample.sensor_id != self.descriptor.sensor_id:
            raise ValueError("Point cloud sensor_id does not match LiDAR provider descriptor.")
        with self._condition:
            if self._closed:
                return
            self._received_frames += 1
            if self._pending is not None:
                self._overwritten_frames += 1
            self._pending = sample
            self._condition.notify_all()

    def wait_until_ready(self, timeout_s: float) -> LidarProviderReadiness:
        """等待首帧完成本地坐标转换并返回结构化结果；``timeout_s`` 单位为秒且不得为负数。"""

        if timeout_s < 0:
            raise ValueError("LiDAR provider readiness timeout must not be negative.")
        with self._condition:
            # 单帧转换失败可能只是起飞前位姿尚未就绪；继续等待后续帧，避免把瞬时错误当成永久失败。
            self._condition.wait_for(lambda: self._ready or self._closed, timeout_s)
            status = self._tracker.status()
            frame_supported = self.descriptor.frame in {
                CoordinateFrame.BODY,
                CoordinateFrame.LOCAL_NED,
                CoordinateFrame.LOCAL_ENU,
            }
            pose_available = status.last_error is None or "pose" not in status.last_error
            reason = None if self._ready else self._last_error or status.last_error or "LiDAR provider readiness timed out."
            return LidarProviderReadiness(
                self._ready,
                self._received_frames,
                self._processed_frames,
                pose_available,
                frame_supported,
                reason,
            )

    def candidate_directions(self, policy: DepartureDirectionPolicy) -> tuple[DepartureDirection, ...]:
        """按 ``policy`` 返回首个满足分级净空的本地起航方向集合；没有安全候选时返回空元组。"""

        if not isinstance(policy, DepartureDirectionPolicy):
            raise TypeError("policy must be a DepartureDirectionPolicy.")
        pose = self._pose_provider()
        yaw_deg = float(getattr(pose, "yaw_deg", 0.0) or 0.0)
        for clearance_m in policy.clearance_levels_m():
            candidates = self._corridor.candidate_directions(
                min_obstacle_distance_m=clearance_m,
                max_obstacle_distance_m=policy.obstacle_range_m,
            )
            if not candidates:
                continue
            result = []
            for direction in candidates:
                local = direction.rotated(yaw_deg)
                if not self._departure_is_safe(pose, local, policy):
                    continue
                result.append(
                    DepartureDirection(
                        body_direction=Vector3(direction.x, direction.y, 0.0),
                        local_direction=Vector3(local.x, local.y, 0.0),
                        heading_deg=math.degrees(math.atan2(local.y, local.x)),
                        obstacle_distance_m=float(direction.obstacle_distance_m or 0.0),
                        selected_clearance_m=clearance_m,
                    )
                )
            if result:
                return tuple(result)
        return ()

    def _departure_is_safe(
        self,
        pose: Any,
        direction: Any,
        policy: DepartureDirectionPolicy,
    ) -> bool:
        """判断候选方向在起步探测段内是否保持净空，或能离开已经过近的障碍物。"""

        origin_x = float(getattr(pose, "local_x_m", 0.0) or 0.0)
        origin_y = float(getattr(pose, "local_y_m", 0.0) or 0.0)
        with self._condition:
            obstacles = self._snapshot.obstacles
        for obstacle in obstacles:
            start_m = self._surface_distance_xy(origin_x, origin_y, obstacle)
            samples = tuple(
                self._surface_distance_xy(
                    origin_x + direction.x * policy.departure_probe_m * ratio,
                    origin_y + direction.y * policy.departure_probe_m * ratio,
                    obstacle,
                )
                for ratio in (0.25, 0.5, 0.75, 1.0)
            )
            if start_m < policy.safety_radius_m:
                if samples[0] <= start_m + 0.05 or samples[-1] <= start_m:
                    return False
            elif min(samples) < policy.safety_radius_m:
                return False
        return True

    @staticmethod
    def _surface_distance_xy(x_m: float, y_m: float, obstacle: Obstacle) -> float:
        """返回水平点到标准障碍物表面的保守距离（米）。"""

        if obstacle.shape is ObstacleShape.BOX and obstacle.size_m is not None:
            dx = max(abs(x_m - obstacle.position_m.x) - obstacle.size_m.x * 0.5, 0.0)
            dy = max(abs(y_m - obstacle.position_m.y) - obstacle.size_m.y * 0.5, 0.0)
            return math.hypot(dx, dy)
        radius_m = float(obstacle.radius_m or 0.0)
        return max(math.hypot(x_m - obstacle.position_m.x, y_m - obstacle.position_m.y) - radius_m, 0.0)

    def reset_direction_history(self) -> None:
        """清空起航方向分析历史；不会清空避障快照，适合起飞到目标高度后重新采集环境；无返回值。"""

        self._corridor.clear()
        with self._condition:
            self._direction_reset_at = self._direction_frames

    def wait_for_direction_data(self, *, minimum_frames: int = 5, timeout_s: float = 3.0) -> bool:
        """等待重置后累计 ``minimum_frames`` 帧方向点云，并返回是否在 ``timeout_s`` 秒内满足条件。"""

        if minimum_frames <= 0 or timeout_s < 0:
            raise ValueError("Direction data minimum frames must be positive and timeout must not be negative.")
        with self._condition:
            return self._condition.wait_for(
                lambda: self._direction_frames - self._direction_reset_at >= minimum_frames or self._closed,
                timeout_s,
            ) and not self._closed

    def select_direction(self, policy: DepartureDirectionPolicy, *, rng: Random | None = None) -> DepartureDirection | None:
        """从满足 ``policy`` 的候选中选择一个方向；传入 ``rng`` 时使用其随机状态，否则返回首个候选。"""

        candidates = self.candidate_directions(policy)
        if not candidates:
            return None
        return (rng or Random()).choice(candidates) if rng is not None else candidates[0]

    def select_safe_direction(
        self,
        *,
        avoidance: "AvoidanceManager",
        state: "DeviceState",
        direction_policy: DepartureDirectionPolicy,
        avoidance_policy: "AvoidancePolicy",
        speed_m_s: float,
        rng: Random | None = None,
    ) -> DepartureDirection | None:
        """返回同时通过 LiDAR 净空和标准避障模型复核的任务局部方向。

        ``state`` 是设备当前强类型状态快照，必须包含本地三维位置及坐标系；``direction_policy``
        决定候选方向与起步探测距离，``avoidance_policy`` 使用与后续 route 相同的安全半径和障碍物模型，
        ``speed_m_s`` 单位为米/秒。候选会按 ``rng`` 随机顺序或原顺序逐一复核；没有安全方向时返回
        ``None``，本方法不会替无人机返航、替足式设备停止，也不会直接发送任何运动命令。
        """

        if state.local_x_m is None or state.local_y_m is None or state.local_z_m is None:
            raise ValueError("A complete local position is required to select a safe direction.")
        candidates = list(self.candidate_directions(direction_policy))
        if rng is not None:
            rng.shuffle(candidates)
        start = Vector3(state.local_x_m, state.local_y_m, state.local_z_m)
        for direction in candidates:
            target = Vector3(
                start.x + direction.local_direction.x * direction_policy.departure_probe_m,
                start.y + direction.local_direction.y * direction_policy.departure_probe_m,
                start.z,
            )
            decision = avoidance.evaluate_segment(
                start_m=start,
                target_m=target,
                frame=state.local_position_frame,
                speed_m_s=speed_m_s,
                policy=avoidance_policy,
                allow_replan=False,
            )
            if decision.allowed:
                return direction
        return None

    def snapshot(self) -> ObstacleSnapshot:
        """立即返回最近一次后台处理生成的本地障碍物快照，不等待新点云。"""

        with self._condition:
            return self._snapshot

    def status(self, *, stale_timeout_s: float = 2.0) -> LidarObstacleProviderStatus:
        """返回帧计数、覆盖量、处理耗时和障碍物数量；``stale_timeout_s`` 单位为秒。"""

        if stale_timeout_s <= 0:
            raise ValueError("LiDAR provider stale timeout must be greater than 0.")
        tracker = self._tracker.status()
        with self._condition:
            return LidarObstacleProviderStatus(
                ready=self._ready,
                stale=self._tracker.is_stale(stale_timeout_s),
                received_frames=self._received_frames,
                processed_frames=self._processed_frames,
                overwritten_frames=self._overwritten_frames,
                dropped_frames=tracker.dropped_frames,
                input_points=tracker.input_points,
                retained_points=tracker.retained_points,
                raw_obstacle_count=self._raw_obstacle_count,
                obstacle_count=len(self._snapshot.obstacles),
                processing_ms=(tracker.last_processing_s * 1000.0 if tracker.last_processing_s is not None else None),
                latest_sequence=tracker.last_sequence,
                last_error=self._last_error or tracker.last_error,
            )

    def is_stale(self, timeout_s: float) -> bool:
        """返回最近一次成功点云处理是否超过 ``timeout_s`` 秒；从未就绪时也返回 ``True``。"""

        return self._tracker.is_stale(timeout_s)

    def close(self) -> None:
        """停止后台线程并清空障碍物；调用前应先取消传感器订阅，重复调用安全；无返回值。"""

        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._pending = None
            self._condition.notify_all()
        self._worker.join(timeout=2.0)
        self._tracker.close()
        with self._condition:
            self._snapshot = ObstacleSnapshot(self._provider_name, self._output_frame, time.monotonic(), ())

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._pending is not None or self._closed)
                if self._closed:
                    return
                sample = self._pending
                self._pending = None
            assert sample is not None
            try:
                self._corridor.observe(sample)
                with self._condition:
                    self._direction_frames += 1
                    self._condition.notify_all()
                processed = self._tracker.observe(sample)
                if not processed:
                    error = self._tracker.status().last_error
                    with self._condition:
                        self._last_error = error
                        self._condition.notify_all()
                    continue
                raw = self._tracker.snapshot()
                fused = self._fuse_and_cluster(raw)
                with self._condition:
                    self._snapshot = fused
                    self._processed_frames += 1
                    self._raw_obstacle_count = len(raw.obstacles)
                    self._ready = True
                    self._last_error = None
                    self._condition.notify_all()
            except Exception as exc:
                with self._condition:
                    self._last_error = f"{type(exc).__name__}: {exc}"
                    self._condition.notify_all()
                logger.debug("LiDAR 障碍物后台处理失败: sensor_id=%s", self.descriptor.sensor_id, exc_info=True)

    def _fuse_and_cluster(self, snapshot: ObstacleSnapshot) -> ObstacleSnapshot:
        now_s = snapshot.observed_at_s
        self._history.append(snapshot)
        while self._history and now_s - self._history[0].observed_at_s > self.config.fusion_window_s:
            self._history.popleft()
        latest_by_id: dict[str, Obstacle] = {}
        for item in self._history:
            for obstacle in item.obstacles:
                key = obstacle.obstacle_id or f"anonymous.{id(obstacle)}"
                previous = latest_by_id.get(key)
                if previous is None or obstacle.observed_at_s >= previous.observed_at_s:
                    latest_by_id[key] = obstacle
        obstacles = tuple(latest_by_id.values())
        if self.config.cluster_adjacent_voxels:
            obstacles = self._cluster_voxels(obstacles, now_s)
        pose = self._pose_provider()
        origin = Vector3(
            float(getattr(pose, "local_x_m", 0.0) or 0.0),
            float(getattr(pose, "local_y_m", 0.0) or 0.0),
            float(getattr(pose, "local_z_m", 0.0) or 0.0),
        )
        obstacles = tuple(
            sorted(obstacles, key=lambda item: self._distance_sq(item.position_m, origin))[
                : self.config.max_obstacles
            ]
        )
        return ObstacleSnapshot(self._provider_name, self._output_frame, now_s, obstacles)

    def _cluster_voxels(self, obstacles: tuple[Obstacle, ...], observed_at_s: float) -> tuple[Obstacle, ...]:
        size = self.config.voxel_size_m
        by_key = {
            (
                math.floor(obstacle.position_m.x / size),
                math.floor(obstacle.position_m.y / size),
                math.floor(obstacle.position_m.z / size),
            ): obstacle
            for obstacle in obstacles
        }
        clusters: list[Obstacle] = []
        columns: dict[tuple[int, int], list[int]] = {}
        for x_key, y_key, z_key in by_key:
            columns.setdefault((x_key, y_key), []).append(z_key)
        for column in sorted(columns):
            z_keys = sorted(columns[column])
            components: list[list[tuple[int, int, int]]] = []
            for z_key in z_keys:
                key = (column[0], column[1], z_key)
                if components and z_key == components[-1][-1][2] + 1:
                    components[-1].append(key)
                else:
                    components.append([key])
            for component_items in components:
                component = set(component_items)
                members = tuple(by_key[key] for key in component)
                half = size * 0.5
                min_x = min(item.position_m.x - half for item in members)
                max_x = max(item.position_m.x + half for item in members)
                min_y = min(item.position_m.y - half for item in members)
                max_y = max(item.position_m.y + half for item in members)
                min_z = min(item.position_m.z - half for item in members)
                max_z = max(item.position_m.z + half for item in members)
                anchor = min(component)
                clusters.append(
                    Obstacle(
                        obstacle_id=f"{self._provider_name}.cluster.{anchor[0]}.{anchor[1]}.{anchor[2]}",
                        kind=ObstacleKind.UNKNOWN,
                        shape=ObstacleShape.BOX,
                        frame=self._output_frame,
                        position_m=Vector3((min_x + max_x) * 0.5, (min_y + max_y) * 0.5, (min_z + max_z) * 0.5),
                        size_m=Vector3(max_x - min_x, max_y - min_y, max_z - min_z),
                        observed_at_s=observed_at_s,
                        ttl_s=self.config.ttl_s,
                    )
                )
        return tuple(clusters)

    @staticmethod
    def _distance_sq(point: Vector3, origin: Vector3) -> float:
        return (
            (point.x - origin.x) ** 2
            + (point.y - origin.y) ** 2
            + (point.z - origin.z) ** 2
        )
