"""把 Project AirSim RGB 相机发布为标准 RTSP 视频流。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import os
from queue import Empty, Full, Queue
import re
from threading import Event, RLock, Thread
import time
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from fleetdock.sensors import ImageEncoding, ImageFrame, SensorDescriptor, SensorKind, SensorSourceKind
from fleetdock.simulation._gstreamer_rtsp import GStreamerRtspPublisher

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectAirSimRtspStatus:
    """Project AirSim 到 RTSP 桥接器的运行状态快照。

    ``running`` 表示桥接器已经启动且尚未关闭；三个帧计数分别表示从仿真器接收、存在 RTSP 客户端时
    成功写入 GStreamer 管线，以及因实时队列覆盖而丢弃的帧数。``last_frame_received_at_s`` 使用当前
    进程单调时钟秒值，没有收到帧时为 ``None``。该对象只用于诊断，不代表下游客户端已经完成解码；
    没有客户端连接时 ``frames_published`` 可以保持为零。
    """

    running: bool
    frames_received: int
    frames_published: int
    dropped_frames: int
    last_frame_received_at_s: Optional[float]


@dataclass(frozen=True)
class _RawVideoFrame:
    width: int
    height: int
    pixel_format: str
    data: bytes


class ProjectAirSimRtspBridge:
    """把 Project AirSim 的一路 RGB 相机转换为低延迟 H.264 RTSP 流。

    桥接器内部使用 Project AirSim client 订阅仿真相机，通过容量为一个样本的实时队列把最新帧交给
    进程级 GStreamer RTSP Server 编码和发布。``robot_name`` 是场景
    机器人名称；``sensor_id`` 为 ``None`` 时选择主 RGB 相机；``rtsp_uri`` 必须是完整 RTSP 发布地址；
    ``frame_rate_hz`` 单位为帧/秒，``bitrate_kbps`` 单位为千比特/秒；``startup_timeout_s`` 是等待
    本机 RTSP Server 和首个仿真帧就绪的最长秒数。

    ``projectairsim_host``、topics/services 端口用于连接仿真服务，未传时读取同名环境变量或使用
    Project AirSim 本机默认端口；高级测试可注入一个已创建的 ``projectairsim_client``，其所有权仍由
    调用方保留。首次调用 ``start()`` 返回可配置给设备的 RTSP URI；``close()`` 会取消仿真订阅、注销
    本路 RTSP 地址，并在最后一路关闭时停止共享 GStreamer Server。
    """

    def __init__(
        self,
        robot_name: str,
        *,
        sensor_id: Optional[str] = None,
        rtsp_uri: Optional[str] = None,
        frame_rate_hz: float = 30.0,
        bitrate_kbps: int = 4_000,
        startup_timeout_s: float = 15.0,
        projectairsim_client: Any = None,
        projectairsim_host: Optional[str] = None,
        projectairsim_topics_port: Optional[int] = None,
        projectairsim_services_port: Optional[int] = None,
        _sensor_backend: Any = None,
        _publisher_factory: Callable[..., Any] = GStreamerRtspPublisher,
    ) -> None:
        if not robot_name.strip():
            raise ValueError("robot_name must not be empty.")
        if frame_rate_hz <= 0:
            raise ValueError("frame_rate_hz must be greater than 0.")
        if bitrate_kbps <= 0:
            raise ValueError("bitrate_kbps must be greater than 0.")
        if startup_timeout_s <= 0:
            raise ValueError("startup_timeout_s must be greater than 0.")
        normalized_robot_name = robot_name.strip()
        stream_name = re.sub(r"[^A-Za-z0-9_-]+", "_", normalized_robot_name).strip("_")
        if stream_name != normalized_robot_name:
            digest = hashlib.sha256(normalized_robot_name.encode("utf-8")).hexdigest()[:8]
            stream_name = f"{stream_name or 'robot'}_{digest}"
        normalized_uri = rtsp_uri or f"rtsp://127.0.0.1:8554/{stream_name}_camera_front"
        parsed_uri = urlsplit(normalized_uri)
        if parsed_uri.scheme.lower() != "rtsp" or not parsed_uri.hostname or not parsed_uri.path.strip("/"):
            raise ValueError("rtsp_uri must include an rtsp host and stream path.")

        self.robot_name = normalized_robot_name
        self.sensor_id = sensor_id
        self._stream_uri = normalized_uri
        self._rtsp_host = parsed_uri.hostname
        self._rtsp_port = parsed_uri.port or 554
        self._rtsp_path = parsed_uri.path
        self._frame_rate_hz = float(frame_rate_hz)
        self._bitrate_kbps = int(bitrate_kbps)
        self._startup_timeout_s = float(startup_timeout_s)
        self._publisher_factory = _publisher_factory
        self._sensor_backend = _sensor_backend
        self._projectairsim_client = projectairsim_client
        self._projectairsim_host = projectairsim_host or os.getenv("PROJECTAIRSIM_HOST", "127.0.0.1")
        topics_port = (
            projectairsim_topics_port
            if projectairsim_topics_port is not None
            else os.getenv("PROJECTAIRSIM_TOPICS_PORT", "8989")
        )
        services_port = (
            projectairsim_services_port
            if projectairsim_services_port is not None
            else os.getenv("PROJECTAIRSIM_SERVICES_PORT", "8990")
        )
        self._projectairsim_topics_port = int(topics_port)
        self._projectairsim_services_port = int(services_port)

        self._lock = RLock()
        self._stop = Event()
        self._publisher_ready = Event()
        self._frames: Queue[ImageFrame] = Queue(maxsize=1)
        self._publisher_thread: Optional[Thread] = None
        self._unsubscribe: Optional[Callable[[], None]] = None
        self._publisher: Any = None
        self._started = False
        self._closed = False
        self._publisher_error: Optional[BaseException] = None
        self._frames_received = 0
        self._frames_published = 0
        self._dropped_frames = 0
        self._last_frame_received_at_s: Optional[float] = None

    @property
    def stream_uri(self) -> str:
        """返回桥接器的 RTSP 拉流地址；地址固定，不会因为启动或重连而改变。"""

        return self._stream_uri

    @property
    def sensor_source(self) -> SensorSourceKind:
        """返回 ``SIMULATED``，用于把桥接后的 RTSP 流继续标记为仿真传感器来源。"""

        return SensorSourceKind.SIMULATED

    def start(self) -> str:
        """启动 RTSP Server、仿真相机订阅和编码发布，并返回 RTSP 拉流地址。

        本方法最多等待 ``startup_timeout_s`` 秒，只有本机 GStreamer RTSP Server 已监听且收到第一个
        仿真帧后才返回。重复调用会直接返回同一地址；对象关闭后不能重新启动。依赖或插件缺失、端口冲突、
        相机不存在或首帧超时时抛出 ``RuntimeError``，已经创建的管线和订阅会在抛出前释放。
        """

        started_at = time.monotonic()
        with self._lock:
            if self._closed:
                raise RuntimeError("Project AirSim RTSP bridge is closed.")
            if self._started:
                return self._stream_uri
            self._started = True
        try:
            self._publisher = self._publisher_factory(
                self._rtsp_host,
                self._rtsp_port,
                self._rtsp_path,
                frame_rate_hz=self._frame_rate_hz,
                bitrate_kbps=self._bitrate_kbps,
                startup_timeout_s=self._startup_timeout_s,
            )
            self._publisher.start()
            backend = self._get_sensor_backend()
            descriptor = self._select_camera(backend)
            self._publisher_thread = Thread(
                target=self._publish_frames,
                name=f"fleetdock-projectairsim-rtsp-{self.robot_name}",
                daemon=True,
            )
            self._publisher_thread.start()
            self._unsubscribe = backend.subscribe(descriptor.sensor_id, self._receive_frame)
            if not self._publisher_ready.wait(self._startup_timeout_s):
                error = self._publisher_error
                if error is not None:
                    raise RuntimeError("Project AirSim RTSP publisher failed to start.") from error
                raise RuntimeError("Timed out waiting for the first Project AirSim camera frame.")
            if self._publisher_error is not None:
                raise RuntimeError("Project AirSim RTSP publisher failed to start.") from self._publisher_error
            stability_deadline = min(started_at + self._startup_timeout_s, time.monotonic() + 0.25)
            while time.monotonic() < stability_deadline:
                if self._publisher_error is not None:
                    raise RuntimeError("Project AirSim RTSP publisher failed to start.") from self._publisher_error
                time.sleep(0.02)
            logger.debug(
                "Project AirSim RTSP 桥接已启动: robot=%s sensor_id=%s",
                self.robot_name,
                descriptor.sensor_id,
            )
            return self._stream_uri
        except Exception:
            self.close()
            raise

    def status(self) -> ProjectAirSimRtspStatus:
        """返回当前运行状态和帧计数的不可变快照，不启动或停止任何资源。"""

        with self._lock:
            return ProjectAirSimRtspStatus(
                running=self._started and not self._closed and self._publisher_error is None,
                frames_received=self._frames_received,
                frames_published=self._frames_published,
                dropped_frames=self._dropped_frames,
                last_frame_received_at_s=self._last_frame_received_at_s,
            )

    def close(self) -> None:
        """取消相机订阅并注销本路 GStreamer RTSP 地址，同时关闭 AirSim 传感器后端。

        本方法无返回值；重复调用安全。共享 GStreamer Server 只在最后一路流注销后停止；调用方传入的
        原生 Project AirSim client 不会被关闭。
        """

        with self._lock:
            if self._closed:
                return
            self._closed = True
            unsubscribe = self._unsubscribe
            self._unsubscribe = None
            publisher_thread = self._publisher_thread
            publisher = self._publisher
            self._publisher = None
        if unsubscribe is not None:
            try:
                unsubscribe()
            except Exception:
                logger.debug("取消 Project AirSim RTSP 源订阅失败", exc_info=True)
        self._stop.set()
        if publisher_thread is not None and publisher_thread.is_alive():
            publisher_thread.join(timeout=3.0)
        if publisher is not None:
            try:
                publisher.close()
            except Exception:
                logger.debug("关闭 GStreamer RTSP 发布器失败", exc_info=True)
        backend = self._sensor_backend
        if backend is not None:
            try:
                backend.close()
            except Exception:
                logger.debug("关闭 Project AirSim RTSP 传感器后端失败", exc_info=True)
        logger.debug("Project AirSim RTSP 桥接已关闭: robot=%s", self.robot_name)

    def __enter__(self) -> "ProjectAirSimRtspBridge":
        """启动桥接器并返回自身，便于使用 ``with`` 管理完整媒体生命周期。"""

        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        """退出上下文时关闭桥接器；异常不会被本对象吞掉。"""

        self.close()

    def _get_sensor_backend(self) -> Any:
        if self._sensor_backend is None:
            from fleetdock.adapters.projectairsim_sensors import _ProjectAirSimSensorBackend

            self._sensor_backend = _ProjectAirSimSensorBackend(
                self.robot_name,
                client=self._projectairsim_client,
                client_factory=(self._create_projectairsim_client if self._projectairsim_client is None else None),
            )
        return self._sensor_backend

    def _create_projectairsim_client(self) -> Any:
        try:
            from projectairsim import ProjectAirSimClient
        except ImportError as exc:
            raise RuntimeError("projectairsim is required for the Project AirSim RTSP bridge.") from exc
        return ProjectAirSimClient(
            address=self._projectairsim_host,
            port_topics=self._projectairsim_topics_port,
            port_services=self._projectairsim_services_port,
        )

    def _select_camera(self, backend: Any) -> SensorDescriptor:
        cameras = tuple(
            descriptor for descriptor in backend.list_sensors() if descriptor.kind is SensorKind.RGB_CAMERA
        )
        if self.sensor_id is not None:
            matches = tuple(descriptor for descriptor in cameras if descriptor.sensor_id == self.sensor_id)
            if len(matches) != 1:
                raise RuntimeError(f"Project AirSim RGB camera is not available: {self.sensor_id}")
            return matches[0]
        primary = tuple(descriptor for descriptor in cameras if descriptor.is_primary)
        if len(primary) == 1:
            return primary[0]
        if len(cameras) == 1:
            return cameras[0]
        if not cameras:
            raise RuntimeError(f"Project AirSim robot has no RGB camera: {self.robot_name}")
        raise RuntimeError("Multiple Project AirSim RGB cameras are available; specify sensor_id.")

    def _receive_frame(self, sample: Any) -> None:
        if not isinstance(sample, ImageFrame):
            return
        with self._lock:
            self._frames_received += 1
            self._last_frame_received_at_s = time.monotonic()
        try:
            # [UAV-IDE MODIFY] Keep the Project AirSim callback O(1). Pixel copying and
            # stride normalization run on the dedicated publisher thread so multi-camera
            # streams cannot build a stale callback backlog after FleetDock reinitializes.
            self._frames.put_nowait(sample)
        except Full:
            try:
                self._frames.get_nowait()
            except Empty:
                pass
            with self._lock:
                self._dropped_frames += 1
            self._frames.put_nowait(frame)

    @staticmethod
    def _raw_frame(frame: ImageFrame) -> _RawVideoFrame:
        formats = {
            ImageEncoding.RGB8: ("RGB", 3),
            ImageEncoding.BGR8: ("BGR", 3),
            ImageEncoding.RGBA8: ("RGBA", 4),
            ImageEncoding.MONO8: ("GRAY8", 1),
        }
        try:
            pixel_format, channels = formats[frame.encoding]
        except KeyError as exc:
            raise ValueError(f"Unsupported RTSP bridge image encoding: {frame.encoding.value}") from exc
        row_bytes = frame.width * channels
        stride_bytes = frame.stride_bytes or row_bytes
        source = frame.data.cast("B")
        required = stride_bytes * frame.height
        if frame.width <= 0 or frame.height <= 0 or stride_bytes < row_bytes or len(source) < required:
            raise ValueError("Project AirSim image dimensions or stride are invalid.")
        if stride_bytes == row_bytes:
            data = bytes(source[: row_bytes * frame.height])
        else:
            data = b"".join(
                bytes(source[offset : offset + row_bytes])
                for offset in range(0, stride_bytes * frame.height, stride_bytes)
            )
        return _RawVideoFrame(frame.width, frame.height, pixel_format, data)

    def _publish_frames(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    sample = self._frames.get(timeout=0.1)
                except Empty:
                    continue
                frame = self._raw_frame(sample)
                publisher = self._publisher
                if publisher is None:
                    raise RuntimeError("GStreamer RTSP publisher is unavailable.")
                published = publisher.push(frame.width, frame.height, frame.pixel_format, frame.data)
                if published:
                    with self._lock:
                        self._frames_published += 1
                self._publisher_ready.set()
        except Exception as exc:
            with self._lock:
                self._publisher_error = exc
            self._publisher_ready.set()
            logger.debug("Project AirSim RTSP 发布线程异常", exc_info=True)
