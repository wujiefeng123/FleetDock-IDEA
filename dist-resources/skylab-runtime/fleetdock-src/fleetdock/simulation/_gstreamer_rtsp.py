"""由进程级 GStreamer Server 托管低延迟 H.264 RTSP 视频流。"""

from __future__ import annotations

from fractions import Fraction
import logging
import socket
from threading import RLock, Thread
import time
from typing import Any

logger = logging.getLogger(__name__)


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_registry_lock = RLock()
_servers: dict[tuple[str, int], "_SharedGStreamerRtspServer"] = {}


def _load_gstreamer() -> tuple[Any, Any, Any, Any]:
    try:
        import gi

        gi.require_version("Gst", "1.0")
        gi.require_version("GstRtsp", "1.0")
        gi.require_version("GstRtspServer", "1.0")
        from gi.repository import GLib, Gst, GstRtsp, GstRtspServer
    except (ImportError, ValueError) as exc:
        raise RuntimeError(
            "GStreamer Python bindings and GstRtspServer are required for Project AirSim RTSP streaming."
        ) from exc
    Gst.init(None)
    missing = tuple(
        name
        for name in ("appsrc", "queue", "videoconvert", "openh264enc", "rtph264pay")
        if Gst.ElementFactory.find(name) is None
    )
    if missing:
        raise RuntimeError(f"Required GStreamer elements are unavailable: {', '.join(missing)}")
    return GLib, Gst, GstRtsp, GstRtspServer


class _GStreamerMount:
    def __init__(
        self,
        gst: Any,
        rtsp: Any,
        rtsp_server: Any,
        frame_rate_hz: float,
        bitrate_kbps: int,
        *,
        gop_frames: int | None = None,
        encoder_complexity: str = "medium",
        enable_frame_skip: bool = True,
    ) -> None:
        self._gst = gst
        self._lock = RLock()
        self._appsrc: Any = None
        self._latest: tuple[int, int, str, bytes] | None = None
        self._format_key: tuple[int, int, str] | None = None
        self._clock_origin_ns: int | None = None
        self._last_pts_ns: int | None = None
        rate = Fraction(frame_rate_hz).limit_denominator(1_000)
        self._rate_numerator = rate.numerator
        self._rate_denominator = rate.denominator
        self._duration_ns = max(1, round(1_000_000_000 / frame_rate_hz))
        gop_size = max(1, round(frame_rate_hz) if gop_frames is None else gop_frames)
        frame_skip = str(enable_frame_skip).lower()
        self.factory = rtsp_server.RTSPMediaFactory()
        self.factory.set_shared(True)
        self.factory.set_latency(0)
        self.factory.set_protocols(rtsp.RTSPLowerTrans.TCP)
        self.factory.set_launch(
            "( appsrc name=source is-live=true block=false format=time max-buffers=1 leaky-type=downstream "
            "! queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 leaky=downstream "
            "! videoconvert ! video/x-raw,format=I420 "
            f"! openh264enc bitrate={bitrate_kbps * 1_000} complexity={encoder_complexity} usage-type=camera "
            f"rate-control=bitrate gop-size={gop_size} enable-frame-skip={frame_skip} "
            "! rtph264pay name=pay0 pt=96 config-interval=1 )"
        )
        self.factory.connect("media-configure", self._on_media_configure)

    def push(self, width: int, height: int, pixel_format: str, data: bytes) -> bool:
        with self._lock:
            self._latest = (width, height, pixel_format, data)
            appsrc = self._appsrc
            if appsrc is None:
                return False
            try:
                return self._push_locked(appsrc, width, height, pixel_format, data)
            except Exception:
                self._appsrc = None
                logger.debug("向 GStreamer RTSP 管线写入视频帧失败", exc_info=True)
                return False

    def close(self) -> None:
        with self._lock:
            appsrc = self._appsrc
            self._appsrc = None
            self._latest = None
        if appsrc is not None:
            try:
                appsrc.emit("end-of-stream")
            except Exception:
                logger.debug("结束 GStreamer appsrc 视频流失败", exc_info=True)

    def _on_media_configure(self, _factory: Any, media: Any) -> None:
        pipeline = media.get_element()
        appsrc = pipeline.get_by_name("source")
        if appsrc is None:
            logger.debug("GStreamer RTSP 媒体管线缺少 appsrc")
            return
        with self._lock:
            self._appsrc = appsrc
            self._format_key = None
            self._clock_origin_ns = None
            self._last_pts_ns = None
            latest = self._latest
            if latest is not None:
                self._push_locked(appsrc, *latest)

    def _push_locked(self, appsrc: Any, width: int, height: int, pixel_format: str, data: bytes) -> bool:
        format_key = (width, height, pixel_format)
        if format_key != self._format_key:
            caps = self._gst.Caps.from_string(
                "video/x-raw,"
                f"format={pixel_format},width={width},height={height},"
                f"framerate={self._rate_numerator}/{self._rate_denominator}"
            )
            appsrc.set_property("caps", caps)
            self._format_key = format_key
        buffer = self._gst.Buffer.new_allocate(None, len(data), None)
        buffer.fill(0, data)
        now_ns = time.monotonic_ns()
        if self._clock_origin_ns is None:
            self._clock_origin_ns = now_ns
        pts_ns = max(0, now_ns - self._clock_origin_ns)
        if self._last_pts_ns is not None and pts_ns <= self._last_pts_ns:
            pts_ns = self._last_pts_ns + 1
        buffer.pts = pts_ns
        buffer.dts = pts_ns
        buffer.duration = self._duration_ns
        self._last_pts_ns = pts_ns
        return appsrc.emit("push-buffer", buffer) == self._gst.FlowReturn.OK


class _SharedGStreamerRtspServer:
    def __init__(self, host: str, port: int, startup_timeout_s: float) -> None:
        if host not in _LOCAL_HOSTS:
            raise RuntimeError("Managed GStreamer RTSP Server only supports a local RTSP address.")
        self._glib, self._gst, self._rtsp, self._rtsp_server = _load_gstreamer()
        self._host = "::1" if host == "::1" else "127.0.0.1"
        self._port = port
        self._lock = RLock()
        self._mounts: dict[str, _GStreamerMount] = {}
        self._references = 0
        self._context = self._glib.MainContext.new()
        self._loop = self._glib.MainLoop.new(self._context, False)
        self._server = self._rtsp_server.RTSPServer.new()
        self._server.set_address(self._host)
        self._server.set_service(str(port))
        self._source_id = self._server.attach(self._context)
        if not self._source_id:
            raise RuntimeError("GStreamer RTSP Server could not attach to its main context.")
        self._thread = Thread(
            target=self._loop.run,
            name=f"fleetdock-gstreamer-rtsp-{port}",
            daemon=True,
        )
        self._thread.start()
        self._wait_until_ready(startup_timeout_s)
        logger.debug("GStreamer RTSP Server 已启动: host=%s port=%d", self._host, self._port)

    def acquire(self) -> None:
        with self._lock:
            self._references += 1

    def release(self) -> bool:
        with self._lock:
            self._references -= 1
            return self._references == 0

    def add_mount(
        self,
        path: str,
        frame_rate_hz: float,
        bitrate_kbps: int,
        *,
        gop_frames: int | None = None,
        encoder_complexity: str = "medium",
        enable_frame_skip: bool = True,
    ) -> _GStreamerMount:
        mount = _GStreamerMount(
            self._gst,
            self._rtsp,
            self._rtsp_server,
            frame_rate_hz,
            bitrate_kbps,
            gop_frames=gop_frames,
            encoder_complexity=encoder_complexity,
            enable_frame_skip=enable_frame_skip,
        )
        with self._lock:
            if path in self._mounts:
                raise RuntimeError(f"GStreamer RTSP path is already registered: {path}")
            self._server.get_mount_points().add_factory(path, mount.factory)
            self._mounts[path] = mount
        return mount

    def remove_mount(self, path: str) -> None:
        with self._lock:
            mount = self._mounts.pop(path, None)
            if mount is None:
                return
            self._server.get_mount_points().remove_factory(path)
        mount.close()

    def close(self) -> None:
        with self._lock:
            paths = tuple(self._mounts)
        for path in paths:
            self.remove_mount(path)
        source = self._context.find_source_by_id(self._source_id)
        if source is not None:
            source.destroy()
        self._loop.quit()
        if self._thread.is_alive():
            self._thread.join(timeout=3.0)
        logger.debug("GStreamer RTSP Server 已关闭: host=%s port=%d", self._host, self._port)

    def _wait_until_ready(self, startup_timeout_s: float) -> None:
        deadline = time.monotonic() + startup_timeout_s
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((self._host, self._port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.05)
        self.close()
        raise RuntimeError("Timed out waiting for the GStreamer RTSP service.")


class GStreamerRtspPublisher:
    """供仿真桥内部使用的一路 GStreamer RTSP 发布器。"""

    def __init__(
        self,
        host: str,
        port: int,
        path: str,
        *,
        frame_rate_hz: float,
        bitrate_kbps: int,
        gop_frames: int | None = None,
        encoder_complexity: str = "medium",
        enable_frame_skip: bool = True,
        startup_timeout_s: float,
    ) -> None:
        self._host = "::1" if host == "::1" else "127.0.0.1" if host in _LOCAL_HOSTS else host
        self._port = port
        self._path = path if path.startswith("/") else f"/{path}"
        self._frame_rate_hz = frame_rate_hz
        self._bitrate_kbps = bitrate_kbps
        self._gop_frames = gop_frames
        self._encoder_complexity = encoder_complexity
        self._enable_frame_skip = enable_frame_skip
        self._startup_timeout_s = startup_timeout_s
        self._server: _SharedGStreamerRtspServer | None = None
        self._mount: _GStreamerMount | None = None

    def start(self) -> None:
        """注册 RTSP 路径并启动或复用本机共享 Server，无返回值。

        端口冲突、路径重复或 GStreamer 依赖不完整时抛出 ``RuntimeError``。
        """

        key = (self._host, self._port)
        with _registry_lock:
            server = _servers.get(key)
            if server is None:
                server = _SharedGStreamerRtspServer(self._host, self._port, self._startup_timeout_s)
                _servers[key] = server
            server.acquire()
        try:
            mount = server.add_mount(
                self._path,
                self._frame_rate_hz,
                self._bitrate_kbps,
                gop_frames=self._gop_frames,
                encoder_complexity=self._encoder_complexity,
                enable_frame_skip=self._enable_frame_skip,
            )
        except Exception:
            self._release_server(key, server)
            raise
        self._server = server
        self._mount = mount

    def push(self, width: int, height: int, pixel_format: str, data: bytes) -> bool:
        """提交一帧原始图像；``width``、``height`` 是像素宽高，``pixel_format`` 是 GStreamer 格式名。

        ``data`` 是紧密排列的像素字节；存在 RTSP 客户端且写入成功时返回 ``True``，尚无客户端时返回
        ``False``。
        """

        mount = self._mount
        if mount is None:
            raise RuntimeError("GStreamer RTSP publisher is not started.")
        return mount.push(width, height, pixel_format, data)

    def close(self) -> None:
        """注销当前 RTSP 路径，无返回值；最后一路流关闭时同步停止共享 Server。"""

        server = self._server
        if server is None:
            return
        self._server = None
        self._mount = None
        server.remove_mount(self._path)
        self._release_server((self._host, self._port), server)

    @staticmethod
    def _release_server(key: tuple[str, int], server: _SharedGStreamerRtspServer) -> None:
        should_close = False
        with _registry_lock:
            if server.release():
                _servers.pop(key, None)
                should_close = True
        if should_close:
            server.close()
