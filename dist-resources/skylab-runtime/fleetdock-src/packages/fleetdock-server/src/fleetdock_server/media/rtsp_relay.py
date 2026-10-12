"""通过 GStreamer 无转码转推一路 H.264 RTSP 视频。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
import re
from threading import Event, RLock, Thread, current_thread
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)


GSTREAMER_RTSP_RELAY_ELEMENTS = (
    "rtspsrc",
    "rtph264depay",
    "h264parse",
    "rtspclientsink",
)
"""RTSP 转推管线使用的 GStreamer 元素；其中不包含视频解码器或编码器。"""


class RtspRelayStatus(str, Enum):
    """RTSP 转推器生命周期状态。"""

    STOPPED = "stopped"  # 尚未启动或已经安全关闭。
    STARTING = "starting"  # 正在创建并连接 GStreamer 管线。
    RECONNECTING = "reconnecting"  # 运行期中断后正在执行有限次数重连。
    RUNNING = "running"  # GStreamer 管线已进入播放状态并持续转推。
    FAILED = "failed"  # 管线启动或运行失败，具体原因可从快照的 error 读取。


@dataclass(frozen=True)
class RtspRelaySnapshot:
    """RTSP 转推器的不可变状态快照。

    ``status`` 表示当前生命周期状态；``source_uri`` 和 ``publish_uri`` 均已移除认证信息及查询参数；
    ``error`` 为脱敏后的最近一次错误，没有错误时为 ``None``。
    """

    status: RtspRelayStatus
    source_uri: str
    publish_uri: str
    error: str | None = None


class _RtspRelayBackend(Protocol):
    def start(self, on_error: Callable[[BaseException], None]) -> None: ...

    def close(self) -> None: ...


BackendFactory = Callable[[str, str, float], _RtspRelayBackend]


def _redact_uri(uri: str) -> str:
    try:
        parsed = urlsplit(uri)
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        if parsed.username is not None or parsed.password is not None:
            host = f"***@{host}"
        query = "***" if parsed.query else ""
        return urlunsplit((parsed.scheme, host, parsed.path, query, ""))
    except (TypeError, ValueError):
        return "<invalid-rtsp-uri>"


def _redact_error(error: BaseException, source_uri: str, publish_uri: str) -> str:
    message = str(error)
    for uri in (source_uri, publish_uri):
        message = message.replace(uri, _redact_uri(uri))
    message = re.sub(r"(?i)(rtsps?://)[^/\s@]+@", r"\1***@", message)
    message = re.sub(r"(?i)(rtsps?://[^\s?#]+)\?[^\s]+", r"\1?***", message)
    return message or error.__class__.__name__


def _validate_rtsp_uri(name: str, uri: str) -> None:
    try:
        parsed = urlsplit(uri)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid RTSP URI.") from exc
    if parsed.scheme.lower() not in {"rtsp", "rtsps"} or not parsed.hostname or not parsed.path.strip("/"):
        raise ValueError(f"{name} must include an RTSP host and stream path.")
    if port is not None and not 1 <= port <= 65_535:
        raise ValueError(f"{name} contains an invalid port.")


class RtspRelay:
    """把一路 H.264 RTSP 源无转码发布到另一个 RTSP 地址。

    ``source_uri`` 是待拉取的完整 RTSP 地址，``publish_uri`` 是支持 ``ANNOUNCE/RECORD`` 的完整
    RTSP 发布地址。两端都使用 TCP 传输；媒体数据只进行 RTP 解包、H.264 解析和由
    ``rtspclientsink`` 执行的发布封装，不经过解码或重新编码。``startup_timeout_s``、``retry_delay_s``
    的单位是秒；``max_retries`` 是运行期中断后的最大重连次数，设为 0 可禁用重连。
    """

    def __init__(
        self,
        source_uri: str,
        publish_uri: str,
        *,
        startup_timeout_s: float = 10.0,
        max_retries: int = 3,
        retry_delay_s: float = 1.0,
        _backend_factory: BackendFactory | None = None,
    ) -> None:
        _validate_rtsp_uri("source_uri", source_uri)
        _validate_rtsp_uri("publish_uri", publish_uri)
        if startup_timeout_s <= 0:
            raise ValueError("startup_timeout_s must be greater than zero.")
        if max_retries < 0:
            raise ValueError("max_retries must be greater than or equal to zero.")
        if retry_delay_s < 0:
            raise ValueError("retry_delay_s must be greater than or equal to zero.")
        self.source_uri = source_uri
        self.publish_uri = publish_uri
        self.startup_timeout_s = startup_timeout_s
        self.max_retries = max_retries
        self.retry_delay_s = retry_delay_s
        self._backend_factory = _backend_factory or _GStreamerRtspRelayBackend
        self._lock = RLock()
        self._stop = Event()
        self._status = RtspRelayStatus.STOPPED
        self._error: str | None = None
        self._backend: _RtspRelayBackend | None = None
        self._retry_thread: Thread | None = None

    @property
    def status(self) -> RtspRelayStatus:
        """返回当前转推生命周期状态；该属性不会阻塞或访问网络。"""

        with self._lock:
            return self._status

    def snapshot(self) -> RtspRelaySnapshot:
        """返回当前状态、脱敏后的源/发布地址和最近错误；不会访问网络。"""

        with self._lock:
            return RtspRelaySnapshot(
                status=self._status,
                source_uri=_redact_uri(self.source_uri),
                publish_uri=_redact_uri(self.publish_uri),
                error=self._error,
            )

    def start(self) -> None:
        """同步启动 RTSP 转推，无返回值。

        本方法最多等待 ``startup_timeout_s`` 秒。重复启动正在运行的实例不会创建第二条管线；依赖
        缺失、服务器拒绝发布或管线启动失败时抛出 ``RuntimeError``，并把脱敏错误写入状态快照。
        """

        with self._lock:
            if self._status is RtspRelayStatus.RUNNING:
                return
            if self._status in {RtspRelayStatus.STARTING, RtspRelayStatus.RECONNECTING}:
                raise RuntimeError("RTSP relay is already starting or reconnecting.")
            self._stop.clear()
            self._status = RtspRelayStatus.STARTING
            self._error = None
        backend: _RtspRelayBackend | None = None
        try:
            backend = self._backend_factory(self.source_uri, self.publish_uri, self.startup_timeout_s)
            with self._lock:
                self._backend = backend
            backend.start(self._on_backend_error)
            with self._lock:
                if self._status is RtspRelayStatus.FAILED:
                    raise RuntimeError(self._error or "RTSP relay failed during startup.")
                if self._status is not RtspRelayStatus.RECONNECTING:
                    self._backend = backend
                    self._status = RtspRelayStatus.RUNNING
            logger.debug(
                "RTSP 无转码转推已启动: source=%s publish=%s",
                _redact_uri(self.source_uri),
                _redact_uri(self.publish_uri),
            )
        except Exception as exc:
            message = _redact_error(exc, self.source_uri, self.publish_uri)
            with self._lock:
                should_close = backend is not None and self._backend is backend
                self._backend = None
                self._status = RtspRelayStatus.FAILED
                self._error = message
            if should_close and backend is not None:
                backend.close()
            logger.debug("启动 RTSP 无转码转推失败: %s", message, exc_info=True)
            raise RuntimeError(message) from exc

    def close(self) -> None:
        """停止转推并释放 GStreamer 管线，无返回值；重复调用安全。"""

        with self._lock:
            backend, self._backend = self._backend, None
            thread, self._retry_thread = self._retry_thread, None
            self._stop.set()
            self._status = RtspRelayStatus.STOPPED
        if backend is not None:
            backend.close()
        if thread is not None and thread is not current_thread() and thread.is_alive():
            thread.join(timeout=self.startup_timeout_s + 2.0)
        logger.debug(
            "RTSP 无转码转推已关闭: source=%s publish=%s",
            _redact_uri(self.source_uri),
            _redact_uri(self.publish_uri),
        )

    def __enter__(self) -> RtspRelay:
        """启动转推并返回当前实例，供 ``with`` 语句管理生命周期。"""

        self.start()
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        """退出 ``with`` 语句时关闭转推，无返回值。"""

        self.close()

    def _on_backend_error(self, error: BaseException) -> None:
        message = _redact_error(error, self.source_uri, self.publish_uri)
        failed_backend: _RtspRelayBackend | None = None
        with self._lock:
            if self._status not in {RtspRelayStatus.RUNNING, RtspRelayStatus.STARTING}:
                return
            self._error = message
            if self.max_retries == 0 or self._stop.is_set():
                failed_backend, self._backend = self._backend, None
                self._status = RtspRelayStatus.FAILED
            else:
                backend, self._backend = self._backend, None
                self._status = RtspRelayStatus.RECONNECTING
                thread = Thread(
                    target=self._retry,
                    args=(backend,),
                    name="fleetdock-rtsp-relay-reconnect",
                    daemon=True,
                )
                self._retry_thread = thread
                thread.start()
        if failed_backend is not None:
            failed_backend.close()
            logger.debug("RTSP 无转码转推运行失败: %s", message)
            return
        logger.debug("RTSP 无转码转推中断，准备有限重连: %s", message)

    def _retry(self, previous_backend: _RtspRelayBackend | None) -> None:
        if previous_backend is not None:
            previous_backend.close()
        last_error = self._error or "RTSP relay was interrupted."
        for attempt in range(1, self.max_retries + 1):
            if self._stop.wait(self.retry_delay_s):
                return
            backend: _RtspRelayBackend | None = None
            try:
                backend = self._backend_factory(self.source_uri, self.publish_uri, self.startup_timeout_s)
                startup_error: list[BaseException] = []
                startup_phase = Event()
                startup_phase.set()

                def on_error(error: BaseException) -> None:
                    with self._lock:
                        if startup_phase.is_set():
                            startup_error.append(error)
                            return
                    if not startup_phase.is_set():
                        self._on_backend_error(error)

                with self._lock:
                    if self._stop.is_set():
                        return
                    self._backend = backend
                backend.start(on_error)
                with self._lock:
                    if startup_error:
                        raise startup_error[-1]
                    if self._stop.is_set():
                        self._backend = None
                    else:
                        self._backend = backend
                        self._retry_thread = None
                        self._status = RtspRelayStatus.RUNNING
                        self._error = None
                        startup_phase.clear()
                        backend = None
                if backend is not None:
                    backend.close()
                    return
                logger.debug("RTSP 无转码转推重连成功: attempt=%d", attempt)
                return
            except Exception as exc:
                last_error = _redact_error(exc, self.source_uri, self.publish_uri)
                with self._lock:
                    if self._backend is backend:
                        self._backend = None
                    self._error = last_error
                if backend is not None:
                    backend.close()
                logger.debug("RTSP 无转码转推重连失败: attempt=%d error=%s", attempt, last_error)
        with self._lock:
            if not self._stop.is_set():
                self._retry_thread = None
                self._status = RtspRelayStatus.FAILED
                self._error = last_error
        logger.debug("RTSP 无转码转推运行失败: %s", last_error)


class _GStreamerRtspRelayBackend:
    def __init__(self, source_uri: str, publish_uri: str, startup_timeout_s: float) -> None:
        self._source_uri = source_uri
        self._publish_uri = publish_uri
        self._startup_timeout_s = startup_timeout_s
        self._gst: Any = None
        self._pipeline: Any = None
        self._stop = Event()
        self._bus_thread: Thread | None = None

    def start(self, on_error: Callable[[BaseException], None]) -> None:
        Gst, GstRtsp = self._load_gstreamer()
        self._gst = Gst
        pipeline = Gst.Pipeline.new("fleetdock-rtsp-relay")
        if pipeline is None:
            raise RuntimeError("GStreamer could not create the RTSP relay pipeline.")
        elements = {
            name: Gst.ElementFactory.make(name, name)
            for name in GSTREAMER_RTSP_RELAY_ELEMENTS
        }
        missing = tuple(name for name, element in elements.items() if element is None)
        if missing:
            raise RuntimeError(f"Required GStreamer elements are unavailable: {', '.join(missing)}")
        source = elements["rtspsrc"]
        depay = elements["rtph264depay"]
        parser = elements["h264parse"]
        sink = elements["rtspclientsink"]
        source.set_property("location", self._source_uri)
        source.set_property("protocols", GstRtsp.RTSPLowerTrans.TCP)
        source.set_property("latency", 0)
        if source.find_property("drop-on-latency") is not None:
            source.set_property("drop-on-latency", True)
        parser.set_property("config-interval", -1)
        sink.set_property("location", self._publish_uri)
        sink.set_property("protocols", GstRtsp.RTSPLowerTrans.TCP)
        sink.set_property("latency", 0)
        for element in elements.values():
            pipeline.add(element)
        if not depay.link(parser) or not parser.link(sink):
            pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("GStreamer could not link the H.264 RTSP relay elements.")
        source.connect("pad-added", self._on_source_pad_added, depay)
        self._pipeline = pipeline
        result = pipeline.set_state(Gst.State.PLAYING)
        if result == Gst.StateChangeReturn.FAILURE:
            self.close()
            raise RuntimeError("GStreamer rejected the RTSP relay PLAYING state.")
        result, current, _pending = pipeline.get_state(int(self._startup_timeout_s * Gst.SECOND))
        if result == Gst.StateChangeReturn.FAILURE or current != Gst.State.PLAYING:
            error = self._pop_bus_error()
            self.close()
            raise error or RuntimeError("Timed out waiting for the GStreamer RTSP relay.")
        self._stop.clear()
        self._bus_thread = Thread(
            target=self._watch_bus,
            args=(on_error,),
            name="fleetdock-rtsp-relay",
            daemon=True,
        )
        self._bus_thread.start()

    def close(self) -> None:
        self._stop.set()
        pipeline, self._pipeline = self._pipeline, None
        if pipeline is not None and self._gst is not None:
            pipeline.set_state(self._gst.State.NULL)
        thread, self._bus_thread = self._bus_thread, None
        if thread is not None and thread is not current_thread() and thread.is_alive():
            thread.join(timeout=2.0)

    @staticmethod
    def _load_gstreamer() -> tuple[Any, Any]:
        try:
            import gi

            gi.require_version("Gst", "1.0")
            gi.require_version("GstRtsp", "1.0")
            from gi.repository import Gst, GstRtsp
        except (ImportError, ValueError) as exc:
            raise RuntimeError(
                "GStreamer Python bindings, GstRtsp and rtspclientsink are required for RTSP relay."
            ) from exc
        Gst.init(None)
        return Gst, GstRtsp

    @staticmethod
    def _on_source_pad_added(_source: Any, pad: Any, depay: Any) -> None:
        sink_pad = depay.get_static_pad("sink")
        if sink_pad is None or sink_pad.is_linked():
            return
        caps = pad.get_current_caps() or pad.query_caps(None)
        if caps is None or caps.get_size() == 0:
            return
        structure = caps.get_structure(0)
        if (
            structure.get_name() != "application/x-rtp"
            or structure.get_string("media") != "video"
            or (structure.get_string("encoding-name") or "").upper() != "H264"
        ):
            return
        pad.link(sink_pad)

    def _watch_bus(self, on_error: Callable[[BaseException], None]) -> None:
        Gst = self._gst
        pipeline = self._pipeline
        if Gst is None or pipeline is None:
            return
        bus = pipeline.get_bus()
        mask = Gst.MessageType.ERROR | Gst.MessageType.EOS
        while not self._stop.is_set():
            message = bus.timed_pop_filtered(200 * Gst.MSECOND, mask)
            if message is None:
                continue
            if message.type == Gst.MessageType.ERROR:
                error, debug = message.parse_error()
                on_error(RuntimeError(f"{error}: {debug or ''}".strip()))
            else:
                on_error(RuntimeError("GStreamer RTSP relay reached end of stream."))
            return

    def _pop_bus_error(self) -> RuntimeError | None:
        Gst = self._gst
        pipeline = self._pipeline
        if Gst is None or pipeline is None:
            return None
        message = pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
        if message is None:
            return None
        error, debug = message.parse_error()
        return RuntimeError(f"{error}: {debug or ''}".strip())
