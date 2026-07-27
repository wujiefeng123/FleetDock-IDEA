"""把低延迟 GStreamer RTSP 或普通 OpenCV 视频输入转换为标准 RGB 相机帧。"""

from __future__ import annotations

import logging
import time
from threading import Event, RLock, Thread, current_thread
from typing import Any, Callable, Sequence

from fleetdock.events import CoordinateFrame
from fleetdock.sensors import (
    CameraIntrinsics,
    ImageEncoding,
    ImageFrame,
    SensorDescriptor,
    SensorHandler,
    SensorKind,
    SensorSourceKind,
    SensorUnsubscribe,
)

logger = logging.getLogger(__name__)


class _BgrFrame:
    def __init__(self, width: int, height: int, stride_bytes: int, data: bytes) -> None:
        self.shape = (height, width, 3)
        self.stride_bytes = stride_bytes
        self._data = data

    def tobytes(self) -> bytes:
        return self._data


class _GStreamerH264Capture:
    def __init__(self, source: str, read_timeout_s: float = 2.0) -> None:
        try:
            import gi

            gi.require_version("Gst", "1.0")
            gi.require_version("GstVideo", "1.0")
            from gi.repository import Gst, GstVideo
        except (ImportError, ValueError) as exc:
            raise RuntimeError("GStreamer Python bindings are unavailable.") from exc
        Gst.init(None)
        missing = tuple(
            name
            for name in ("rtspsrc", "rtph264depay", "h264parse", "openh264dec", "videoconvert", "appsink")
            if Gst.ElementFactory.find(name) is None
        )
        if missing:
            raise RuntimeError(f"Required GStreamer capture elements are unavailable: {', '.join(missing)}")
        try:
            pipeline = Gst.parse_launch(
                "rtspsrc name=source protocols=tcp latency=0 drop-on-latency=true buffer-mode=none "
                "! rtph264depay ! h264parse ! openh264dec ! videoconvert "
                "! video/x-raw,format=BGR "
                "! appsink name=sink sync=false max-buffers=1 drop=true"
            )
        except Exception as exc:
            raise RuntimeError("GStreamer RTSP capture pipeline could not be created.") from exc
        rtsp_source = pipeline.get_by_name("source")
        sink = pipeline.get_by_name("sink")
        if rtsp_source is None or sink is None:
            pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("GStreamer RTSP capture pipeline is incomplete.")
        rtsp_source.set_property("location", source)
        self._gst = Gst
        self._gst_video = GstVideo
        self._pipeline = pipeline
        self._sink = sink
        self._read_timeout_ns = max(1, round(read_timeout_s * Gst.SECOND))
        self._opened = pipeline.set_state(Gst.State.PLAYING) != Gst.StateChangeReturn.FAILURE

    def isOpened(self) -> bool:
        return self._opened

    def read(self) -> tuple[bool, _BgrFrame | None]:
        if not self._opened:
            return False, None
        sample = self._sink.emit("try-pull-sample", self._read_timeout_ns)
        if sample is None:
            return False, None
        caps = sample.get_caps()
        buffer = sample.get_buffer()
        if caps is None or buffer is None:
            return False, None
        info = self._gst_video.VideoInfo.new_from_caps(caps)
        data = buffer.extract_dup(0, buffer.get_size())
        return True, _BgrFrame(info.width, info.height, info.stride[0], data)

    def release(self) -> None:
        if not self._opened:
            return
        self._opened = False
        self._pipeline.set_state(self._gst.State.NULL)


class _OpenCvCameraBackend:
    """把 RTSP、视频文件或本机摄像头转换为标准 ``ImageFrame``。

    仿真来源的 H.264 RTSP 优先使用低延迟 GStreamer 接收；其他 ``source`` 传给 OpenCV
    ``VideoCapture``，可使用视频地址、文件路径或整数摄像头索引；
    ``sensor_id`` 在单设备内必须唯一，``frame`` 声明相机图像对应的设备安装坐标系，``source_kind``
    区分物理、仿真或未知来源，``intrinsics`` 为可选标定内参。OpenCV 仅在首次订阅时按需导入；流地址
    不会写入 Descriptor 或日志，避免泄露凭据。
    """

    def __init__(
        self,
        source: str | int,
        *,
        sensor_id: str = "camera.front",
        frame: CoordinateFrame = CoordinateFrame.BODY_FRD,
        source_kind: SensorSourceKind = SensorSourceKind.PHYSICAL,
        intrinsics: CameraIntrinsics | None = None,
        capture_factory: Callable[[str | int], Any] | None = None,
    ) -> None:
        self._source = source
        self._capture_factory = capture_factory
        self._descriptor = SensorDescriptor(
            sensor_id=sensor_id,
            kind=SensorKind.RGB_CAMERA,
            source=source_kind,
            frame=frame,
            is_primary=True,
            camera_intrinsics=intrinsics,
            metadata={"provider": "opencv"},
        )
        self._lock = RLock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._capture: Any = None
        self._closed = False
        self._sequence = 0

    def list_sensors(self) -> Sequence[SensorDescriptor]:
        """返回构造时配置的一路 RGB 相机描述，不打开视频流。"""
        return (self._descriptor,)

    def subscribe(self, sensor_id: str, handler: SensorHandler) -> SensorUnsubscribe:
        """打开视频输入并持续向 ``handler`` 发布 BGR8 标准帧，返回幂等取消函数。

        ``sensor_id`` 必须匹配构造时声明的 ID；输入无法打开时立即抛出 ``RuntimeError``。最后一个
        SensorHub 订阅取消后会停止读取并释放 ``VideoCapture``，设备关闭时也会执行相同清理。
        """
        if sensor_id != self._descriptor.sensor_id:
            raise LookupError(f"Camera sensor is not available: {sensor_id}")
        with self._lock:
            if self._closed:
                raise RuntimeError("Camera backend is closed.")
            if self._thread is not None:
                raise RuntimeError("Camera backend already has an active native stream.")
            capture = self._create_capture()
            if not bool(capture.isOpened()):
                capture.release()
                raise RuntimeError("Camera video source could not be opened.")
            self._capture = capture
            self._stop.clear()
            self._thread = Thread(
                target=self._read_frames,
                args=(handler,),
                name=f"fleetdock-camera-{sensor_id}",
                daemon=True,
            )
            self._thread.start()
        closed = False

        def unsubscribe() -> None:
            nonlocal closed
            if closed:
                return
            closed = True
            self._stop_stream()

        return unsubscribe

    def close(self) -> None:
        """停止视频读取、释放 OpenCV 资源并关闭后端；重复调用安全且无返回值。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._stop_stream()

    def _create_capture(self) -> Any:
        if self._capture_factory is not None:
            return self._capture_factory(self._source)
        if (
            self._descriptor.source is SensorSourceKind.SIMULATED
            and isinstance(self._source, str)
            and self._source.lower().startswith(("rtsp://", "rtsps://"))
        ):
            try:
                return _GStreamerH264Capture(self._source)
            except RuntimeError:
                logger.debug("GStreamer 低延迟 RTSP 接收不可用，回退 OpenCV", exc_info=True)
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("opencv-python is required for camera video streams.") from exc
        return cv2.VideoCapture(self._source)

    def _read_frames(self, handler: SensorHandler) -> None:
        try:
            while not self._stop.is_set():
                with self._lock:
                    capture = self._capture
                if capture is None:
                    return
                ok, frame = capture.read()
                if not ok:
                    logger.debug("相机视频流读取结束: sensor_id=%s", self._descriptor.sensor_id)
                    return
                shape = tuple(int(value) for value in getattr(frame, "shape", ()))
                if len(shape) < 2:
                    logger.debug("忽略尺寸无效的相机帧: sensor_id=%s", self._descriptor.sensor_id)
                    continue
                self._sequence += 1
                data = bytes(frame.tobytes())
                stride_bytes = int(getattr(frame, "stride_bytes", shape[1] * (shape[2] if len(shape) >= 3 else 1)))
                handler(
                    ImageFrame(
                        sensor_id=self._descriptor.sensor_id,
                        kind=SensorKind.RGB_CAMERA,
                        sequence=self._sequence,
                        frame=self._descriptor.frame,
                        captured_at_s=time.monotonic(),
                        clock_domain="host_monotonic",
                        width=shape[1],
                        height=shape[0],
                        encoding=ImageEncoding.BGR8,
                        data=memoryview(data),
                        stride_bytes=stride_bytes,
                    )
                )
        except Exception:
            logger.debug("相机视频流读取异常: sensor_id=%s", self._descriptor.sensor_id, exc_info=True)
        finally:
            self._release_capture()
            with self._lock:
                if self._thread is current_thread():
                    self._thread = None

    def _stop_stream(self) -> None:
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout=2.0)
        self._release_capture()
        with self._lock:
            self._thread = None

    def _release_capture(self) -> None:
        with self._lock:
            capture = self._capture
            self._capture = None
        if capture is not None:
            capture.release()
