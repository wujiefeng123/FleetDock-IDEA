"""深度相机与双目相机的标准障碍物 Provider。"""

from __future__ import annotations

from array import array
from dataclasses import dataclass
from enum import Enum
import importlib
import logging
import math
import time
from threading import Condition, RLock, Thread
from typing import Any, Callable

from fleetdock.events import CoordinateFrame, Vector3
from fleetdock.obstacles import ObstacleSnapshot
from fleetdock.avoidance.point_cloud import PointCloudObstacleTracker
from fleetdock.sensors import (
    CameraIntrinsics,
    ImageEncoding,
    ImageFrame,
    PointCloudEncoding,
    PointCloudFrame,
    SensorDescriptor,
    SensorKind,
    SensorMount,
    StereoCalibration,
    StereoFrame,
)

logger = logging.getLogger(__name__)


class VisionAvoidancePreset(str, Enum):
    """视觉避障的标准处理参数组合。"""

    LOW_ALTITUDE = "low_altitude"  # 低空低速航线，兼顾近场细节和实时性。
    OPEN_AREA = "open_area"  # 室外开阔区域，使用更大采样步长和量程。
    INDOOR = "indoor"  # 室内狭窄区域，保留更多近距离像素。


@dataclass(frozen=True)
class DepthObstacleConfig:
    """深度图转标准障碍物的配置。

    ``intrinsics`` 和 ``sensor_mount`` 可覆盖传感器描述中的标定；距离字段单位为米，``pixel_stride``
    是水平和垂直像素采样步长。``perspective_depth=True`` 表示像素值是相机光心到目标的射线距离，
    否则表示到成像平面的前向距离。
    """

    preset: VisionAvoidancePreset = VisionAvoidancePreset.LOW_ALTITUDE
    intrinsics: CameraIntrinsics | None = None
    sensor_mount: SensorMount | None = None
    min_depth_m: float = 1.0
    max_depth_m: float = 60.0
    pixel_stride: int = 8
    perspective_depth: bool = True
    vertical_half_extent_m: float = 3.0
    voxel_size_m: float = 1.5
    max_obstacles: int = 128
    ttl_s: float = 0.6

    def __post_init__(self) -> None:
        if not isinstance(self.preset, VisionAvoidancePreset):
            object.__setattr__(self, "preset", VisionAvoidancePreset(self.preset))
        if self.min_depth_m < 0 or self.max_depth_m <= self.min_depth_m:
            raise ValueError("Depth avoidance range is invalid.")
        if min(self.pixel_stride, self.max_obstacles) <= 0:
            raise ValueError("Depth avoidance capacities must be greater than 0.")
        if min(self.vertical_half_extent_m, self.voxel_size_m, self.ttl_s) <= 0:
            raise ValueError("Depth avoidance spatial and time values must be greater than 0.")

    @classmethod
    def for_preset(cls, preset: VisionAvoidancePreset | str) -> "DepthObstacleConfig":
        """返回视觉场景 ``preset`` 对应的深度处理配置。"""

        resolved = preset if isinstance(preset, VisionAvoidancePreset) else VisionAvoidancePreset(preset)
        if resolved is VisionAvoidancePreset.OPEN_AREA:
            return cls(preset=resolved, max_depth_m=100.0, pixel_stride=12, voxel_size_m=2.5, max_obstacles=96)
        if resolved is VisionAvoidancePreset.INDOOR:
            return cls(preset=resolved, min_depth_m=0.4, max_depth_m=25.0, pixel_stride=4, voxel_size_m=0.75)
        return cls(preset=resolved)


@dataclass(frozen=True)
class StereoObstacleConfig:
    """双目视差转标准障碍物的配置。

    ``calibration`` 可覆盖传感器描述标定；``min_disparity_px`` 是接受的最低像素视差，
    ``num_disparities`` 必须为 16 的倍数，``block_size`` 是奇数匹配窗口。其余深度过滤参数由
    ``depth`` 提供。构造 Provider 时需要安装可选 ``vision`` 依赖中的 OpenCV 与 NumPy。
    """

    calibration: StereoCalibration | None = None
    depth: DepthObstacleConfig = DepthObstacleConfig()
    min_disparity_px: float = 1.0
    num_disparities: int = 128
    block_size: int = 7

    def __post_init__(self) -> None:
        if self.min_disparity_px <= 0 or self.num_disparities <= 0 or self.num_disparities % 16:
            raise ValueError("Stereo disparity limits are invalid.")
        if self.block_size < 3 or self.block_size % 2 == 0:
            raise ValueError("Stereo block size must be an odd number not smaller than 3.")


@dataclass(frozen=True)
class VisionObstacleProviderStatus:
    """视觉障碍物 Provider 状态；耗时单位为毫秒，错误为空表示最近一帧成功。"""

    ready: bool
    stale: bool
    received_frames: int
    processed_frames: int
    overwritten_frames: int
    obstacle_count: int
    processing_ms: float | None
    latest_sequence: int | None
    last_error: str | None


class _VisionObstacleProvider:
    def __init__(
        self,
        descriptor: SensorDescriptor,
        *,
        pose_provider: Callable[[], Any],
        depth_config: DepthObstacleConfig,
        provider_name: str,
    ) -> None:
        intrinsics = depth_config.intrinsics or descriptor.camera_intrinsics
        mount = depth_config.sensor_mount or descriptor.mount
        if intrinsics is None or mount is None:
            raise ValueError("Vision obstacle provider requires camera intrinsics and SensorMount calibration.")
        self.descriptor = descriptor
        self.intrinsics = intrinsics
        self.depth_config = depth_config
        self._pose_provider = pose_provider
        pose_frame = getattr(pose_provider(), "local_position_frame", CoordinateFrame.UNKNOWN)
        output_frame = pose_frame if pose_frame in {CoordinateFrame.LOCAL_NED, CoordinateFrame.LOCAL_ENU} else CoordinateFrame.LOCAL_NED
        self._tracker = PointCloudObstacleTracker(
            output_frame=output_frame,
            pose_provider=pose_provider,
            sensor_offset_m=mount.position_m,
            sensor_rotation_rpy_deg=mount.rotation_rpy_deg,
            body_exclusion_half_extents_m=Vector3(1.0, 1.0, 0.8),
            min_range_m=depth_config.min_depth_m,
            max_range_m=depth_config.max_depth_m,
            vertical_half_extent_m=depth_config.vertical_half_extent_m,
            voxel_size_m=depth_config.voxel_size_m,
            max_input_points=12000,
            max_obstacles=depth_config.max_obstacles,
            ttl_s=depth_config.ttl_s,
            provider_name=provider_name,
        )
        self._condition = Condition(RLock())
        self._pending: ImageFrame | StereoFrame | None = None
        self._closed = False
        self._received = 0
        self._processed = 0
        self._overwritten = 0
        self._processing_ms: float | None = None
        self._latest_sequence: int | None = None
        self._last_error: str | None = None
        self._worker = Thread(target=self._run, name=f"fleetdock-{descriptor.sensor_id}-vision-obstacles", daemon=True)
        self._worker.start()

    def observe(self, sample: ImageFrame | StereoFrame) -> None:
        """提交最新视觉帧并立即返回；后台未处理的旧帧会被覆盖，本方法无返回值。"""

        if sample.sensor_id != self.descriptor.sensor_id:
            raise ValueError("Vision sample sensor_id does not match provider descriptor.")
        with self._condition:
            if self._closed:
                return
            self._received += 1
            if self._pending is not None:
                self._overwritten += 1
            self._pending = sample
            self._condition.notify_all()

    def snapshot(self) -> ObstacleSnapshot:
        """立即返回最近成功视觉帧生成的标准障碍物快照。"""

        return self._tracker.snapshot()

    def status(self, *, stale_timeout_s: float = 2.0) -> VisionObstacleProviderStatus:
        """返回视觉处理计数、耗时和新鲜度；``stale_timeout_s`` 单位为秒。"""

        tracker = self._tracker.status()
        with self._condition:
            return VisionObstacleProviderStatus(
                ready=self._processed > 0,
                stale=self._tracker.is_stale(stale_timeout_s),
                received_frames=self._received,
                processed_frames=self._processed,
                overwritten_frames=self._overwritten,
                obstacle_count=tracker.obstacle_count,
                processing_ms=self._processing_ms,
                latest_sequence=self._latest_sequence,
                last_error=self._last_error or tracker.last_error,
            )

    def is_stale(self, timeout_s: float) -> bool:
        """返回最近成功视觉帧是否已超过 ``timeout_s`` 秒；从未成功时返回 ``True``。"""

        return self._tracker.is_stale(timeout_s)

    def close(self) -> None:
        """停止视觉后台线程并释放障碍物快照；重复调用安全且无返回值。"""

        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._pending = None
            self._condition.notify_all()
        self._worker.join(timeout=2.0)
        self._tracker.close()

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._pending is not None or self._closed)
                if self._closed:
                    return
                sample = self._pending
                self._pending = None
            assert sample is not None
            started = time.monotonic()
            try:
                points = self._points(sample)
                buffer = array("f", (value for point in points for value in point))
                processed = self._tracker.observe(
                    PointCloudFrame(
                        sensor_id=self.descriptor.sensor_id,
                        kind=SensorKind.LIDAR,
                        sequence=sample.sequence,
                        frame=CoordinateFrame.BODY,
                        captured_at_s=sample.captured_at_s,
                        data=memoryview(buffer),
                        point_count=len(points),
                        encoding=PointCloudEncoding.XYZ_F32,
                    )
                )
                with self._condition:
                    if processed:
                        self._processed += 1
                        self._latest_sequence = sample.sequence
                        self._last_error = None
                    else:
                        self._last_error = self._tracker.status().last_error
                    self._processing_ms = (time.monotonic() - started) * 1000.0
            except Exception as exc:
                with self._condition:
                    self._last_error = f"{type(exc).__name__}: {exc}"
                    self._processing_ms = (time.monotonic() - started) * 1000.0
                logger.debug("视觉障碍物处理失败: sensor_id=%s", self.descriptor.sensor_id, exc_info=True)

    def _points(self, sample: ImageFrame | StereoFrame) -> tuple[tuple[float, float, float], ...]:
        raise NotImplementedError

    def _depth_points(self, values: Any, width: int, height: int, *, scale: float = 1.0) -> tuple[tuple[float, float, float], ...]:
        intrinsics = self.intrinsics
        config = self.depth_config
        points: list[tuple[float, float, float]] = []
        for v in range(0, min(height, intrinsics.height_px), config.pixel_stride):
            for u in range(0, min(width, intrinsics.width_px), config.pixel_stride):
                depth_m = float(values[v * width + u]) * scale
                if not math.isfinite(depth_m) or not config.min_depth_m <= depth_m <= config.max_depth_m:
                    continue
                nx = (u - intrinsics.cx_px) / intrinsics.fx_px
                ny = (v - intrinsics.cy_px) / intrinsics.fy_px
                forward_m = depth_m / math.sqrt(1.0 + nx * nx + ny * ny) if config.perspective_depth else depth_m
                points.append((forward_m, nx * forward_m, ny * forward_m))
        return tuple(points)


class DepthCameraObstacleProvider(_VisionObstacleProvider):
    """把显式订阅的米制深度图转换为标准障碍物，供 SDK route 避障使用。

    ``descriptor`` 必须来自 ``device.sensors.get(SensorKind.DEPTH_CAMERA)``；``pose_provider`` 应返回
    当前设备本地位置和姿态快照，``config`` 声明相机内参、安装外参与米制过滤范围。对象不会订阅传感器，
    调用方应把 ``observe`` 注册为传感器回调，并在取消订阅后调用 ``close()`` 停止后台处理线程。
    """

    def __init__(self, descriptor: SensorDescriptor, *, pose_provider: Callable[[], Any], config: DepthObstacleConfig | None = None) -> None:
        if descriptor.kind is not SensorKind.DEPTH_CAMERA:
            raise ValueError("DepthCameraObstacleProvider requires DEPTH_CAMERA descriptor.")
        super().__init__(descriptor, pose_provider=pose_provider, depth_config=config or DepthObstacleConfig(), provider_name="sensor.depth_camera.obstacles")

    def _points(self, sample: ImageFrame | StereoFrame) -> tuple[tuple[float, float, float], ...]:
        if not isinstance(sample, ImageFrame):
            raise TypeError("Depth camera provider requires ImageFrame.")
        if sample.encoding is ImageEncoding.DEPTH_F32_M:
            values_f32 = sample.data.cast("B").cast("f")
            return self._depth_points(values_f32, sample.width, sample.height)
        if sample.encoding is ImageEncoding.DEPTH_U16_MM:
            values_u16 = sample.data.cast("B").cast("H")
            return self._depth_points(values_u16, sample.width, sample.height, scale=0.001)
        raise ValueError(f"Depth image encoding must be metric, got {sample.encoding.value}.")


class StereoObstacleProvider(_VisionObstacleProvider):
    """用 OpenCV 双目匹配把显式订阅的 ``StereoFrame`` 转换为标准障碍物。

    ``descriptor`` 必须是已声明 ``StereoCalibration`` 的双目传感器描述；``pose_provider`` 提供当前本地
    位姿，``config`` 控制视差和深度过滤。构造时需要可选 NumPy 与 OpenCV 依赖。对象不会订阅传感器，
    调用方应显式订阅 ``descriptor.sensor_id``，并在取消订阅后调用 ``close()``。
    """

    def __init__(self, descriptor: SensorDescriptor, *, pose_provider: Callable[[], Any], config: StereoObstacleConfig | None = None) -> None:
        if descriptor.kind is not SensorKind.STEREO_CAMERA:
            raise ValueError("StereoObstacleProvider requires STEREO_CAMERA descriptor.")
        self.config = config or StereoObstacleConfig()
        calibration = self.config.calibration or descriptor.stereo_calibration
        if calibration is None:
            raise ValueError("Stereo obstacle provider requires StereoCalibration.")
        self.calibration = calibration
        depth_config = self.config.depth
        if depth_config.intrinsics is None:
            depth_config = DepthObstacleConfig(**{**depth_config.__dict__, "intrinsics": calibration.left})
        try:
            cv2: Any = importlib.import_module("cv2")
            numpy: Any = importlib.import_module("numpy")
        except ImportError as exc:
            raise RuntimeError("Stereo avoidance requires FleetDock vision dependencies: numpy and opencv-python.") from exc
        self._cv2 = cv2
        self._np = numpy
        self._matcher = cv2.StereoSGBM_create(
            minDisparity=0,
            numDisparities=self.config.num_disparities,
            blockSize=self.config.block_size,
            P1=8 * 3 * self.config.block_size ** 2,
            P2=32 * 3 * self.config.block_size ** 2,
        )
        super().__init__(descriptor, pose_provider=pose_provider, depth_config=depth_config, provider_name="sensor.stereo.obstacles")

    def _points(self, sample: ImageFrame | StereoFrame) -> tuple[tuple[float, float, float], ...]:
        if not isinstance(sample, StereoFrame):
            raise TypeError("Stereo obstacle provider requires StereoFrame.")
        left = self._image_array(sample.left)
        right = self._image_array(sample.right)
        disparity = self._matcher.compute(left, right).astype(self._np.float32) / 16.0
        disparity[disparity < self.config.min_disparity_px] = self._np.nan
        depth = self.calibration.left.fx_px * self.calibration.baseline_m / disparity
        return self._depth_points(depth.reshape(-1), sample.left.width, sample.left.height)

    def _image_array(self, frame: ImageFrame) -> Any:
        channels = 4 if frame.encoding is ImageEncoding.RGBA8 else 3
        if frame.encoding is ImageEncoding.MONO8:
            return self._np.frombuffer(frame.data, dtype=self._np.uint8).reshape(frame.height, frame.width)
        if frame.encoding not in {ImageEncoding.RGB8, ImageEncoding.BGR8, ImageEncoding.RGBA8}:
            raise ValueError(f"Unsupported stereo image encoding: {frame.encoding.value}")
        image: Any = self._np.frombuffer(frame.data, dtype=self._np.uint8).reshape(frame.height, frame.width, channels)
        code = self._cv2.COLOR_RGBA2GRAY if channels == 4 else (
            self._cv2.COLOR_RGB2GRAY if frame.encoding is ImageEncoding.RGB8 else self._cv2.COLOR_BGR2GRAY
        )
        return self._cv2.cvtColor(image, code)
