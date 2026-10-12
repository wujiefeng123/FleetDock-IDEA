"""通过进程内 GStreamer 把 H.264 RTSP 无转码发布到 WHIP/WebRTC 服务。"""

from __future__ import annotations

import logging
import re
from threading import Event, RLock, Thread, current_thread
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit, urlunsplit

from fleetdock_server.media.whip_relay import WhipRelaySnapshot, WhipRelayStatus

logger = logging.getLogger(__name__)


GSTREAMER_WHIP_RELAY_ELEMENTS = (
    "rtspsrc",
    "rtph264depay",
    "h264parse",
    "rtph264pay",
    "capsfilter",
    "whipsink",
)
"""GStreamer WHIP 管线使用的元素；其中不包含视频解码器或编码器。"""


class _WhipRelayBackend(Protocol):
    def start(self, on_error: Callable[[BaseException], None]) -> None: ...

    def close(self) -> None: ...


BackendFactory = Callable[[str, str, float, int, bool], _WhipRelayBackend]


def _redact_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        if parsed.username is not None or parsed.password is not None:
            host = f"***@{host}"
        return urlunsplit((parsed.scheme, host, parsed.path, "***" if parsed.query else "", ""))
    except (TypeError, ValueError):
        return "<invalid-media-url>"


def _redact_error(error: BaseException, source_uri: str, publish_url: str) -> str:
    message = str(error)
    for url in (source_uri, publish_url):
        message = message.replace(url, _redact_url(url))
    message = re.sub(r"(?i)((?:rtsps?|https?)://)[^/\s@]+@", r"\1***@", message)
    message = re.sub(r"(?i)((?:rtsps?|https?)://[^\s?#]+)\?[^\s]+", r"\1?***", message)
    return message or error.__class__.__name__


def _validate_url(name: str, url: str, schemes: set[str]) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid URL.") from exc
    if parsed.scheme.lower() not in schemes or not parsed.hostname or not parsed.path.strip("/"):
        raise ValueError(f"{name} must include a supported scheme, host and path.")
    if port is not None and not 1 <= port <= 65_535:
        raise ValueError(f"{name} contains an invalid port.")


class GStreamerWhipRelay:
    """把一路 H.264 RTSP 源无转码发布到 WHIP 服务。

    ``source_uri`` 是完整 RTSP/RTSPS 拉流地址，``publish_url`` 是 SRS 等媒体服务器的完整
    WHIP HTTP(S) 地址。发布器运行在当前 Python 进程内，只执行 RTP 解包、H.264 参数整理、
    RTP 重新封装及 WebRTC/WHIP 传输，不解码或重新编码视频。

    ``startup_timeout_s`` 单位为秒，表示等待 RTSP 和 WHIP 管线进入播放状态的最长时间。
    ``rtsp_latency_ms`` 是 RTSP 接收抖动缓冲毫秒数，设为 ``0`` 延迟最低但对网络抖动最敏感；
    局域网可从 50～150 毫秒起调。``drop_on_latency`` 为 ``True`` 时丢弃超过缓冲窗口的旧包，
    避免网络抖动后延迟持续累积；关闭后更重视帧完整性，但实时控制画面可能越来越慢。
    调用 ``close()`` 会同步停止 GStreamer 管线和错误监听线程；同一实例可在关闭后再次启动。
    """

    def __init__(
        self,
        source_uri: str,
        publish_url: str,
        *,
        startup_timeout_s: float = 10.0,
        rtsp_latency_ms: int = 0,
        drop_on_latency: bool = True,
        _backend_factory: BackendFactory | None = None,
    ) -> None:
        _validate_url("source_uri", source_uri, {"rtsp", "rtsps"})
        _validate_url("publish_url", publish_url, {"http", "https"})
        if startup_timeout_s <= 0:
            raise ValueError("startup_timeout_s must be greater than zero.")
        if rtsp_latency_ms < 0:
            raise ValueError("rtsp_latency_ms must not be negative.")
        self.source_uri = source_uri
        self.publish_url = publish_url
        self.startup_timeout_s = startup_timeout_s
        self.rtsp_latency_ms = int(rtsp_latency_ms)
        self.drop_on_latency = bool(drop_on_latency)
        self._backend_factory = _backend_factory or _GStreamerWhipRelayBackend
        self._lock = RLock()
        self._status = WhipRelayStatus.STOPPED
        self._error: str | None = None
        self._backend: _WhipRelayBackend | None = None

    @property
    def status(self) -> WhipRelayStatus:
        """返回当前发布状态；该属性不会访问网络或阻塞。"""

        with self._lock:
            return self._status

    def snapshot(self) -> WhipRelaySnapshot:
        """返回生命周期、脱敏后的媒体地址和最近错误；不会访问网络。"""

        with self._lock:
            return WhipRelaySnapshot(
                status=self._status,
                source_uri=_redact_url(self.source_uri),
                publish_url=_redact_url(self.publish_url),
                error=self._error,
            )

    def start(self) -> None:
        """同步启动进程内 WHIP 发布，无返回值。

        本方法最多等待 ``startup_timeout_s`` 秒。重复启动正在运行的实例不会创建第二条管线；
        GStreamer 或 WHIP 插件缺失、RTSP 源不可用、服务器拒绝发布或管线启动失败时抛出
        ``RuntimeError``，并把脱敏错误写入状态快照。
        """

        with self._lock:
            if self._status is WhipRelayStatus.RUNNING:
                return
            if self._status is WhipRelayStatus.STARTING:
                raise RuntimeError("GStreamer WHIP relay is already starting.")
            self._status = WhipRelayStatus.STARTING
            self._error = None
        backend: _WhipRelayBackend | None = None
        try:
            backend = self._backend_factory(
                self.source_uri,
                self.publish_url,
                self.startup_timeout_s,
                self.rtsp_latency_ms,
                self.drop_on_latency,
            )
            with self._lock:
                self._backend = backend
            backend.start(self._on_backend_error)
            with self._lock:
                if self._status is WhipRelayStatus.FAILED:
                    raise RuntimeError(self._error or "GStreamer WHIP relay failed during startup.")
                self._status = WhipRelayStatus.RUNNING
            logger.debug(
                "GStreamer WHIP 无转码发布已启动: source=%s publish=%s",
                _redact_url(self.source_uri),
                _redact_url(self.publish_url),
            )
        except Exception as exc:
            message = _redact_error(exc, self.source_uri, self.publish_url)
            with self._lock:
                should_close = backend is not None and self._backend is backend
                self._backend = None
                self._status = WhipRelayStatus.FAILED
                self._error = message
            if should_close and backend is not None:
                backend.close()
            logger.debug("启动 GStreamer WHIP 无转码发布失败: %s", message, exc_info=True)
            raise RuntimeError(message) from exc

    def close(self) -> None:
        """停止发布并释放 GStreamer 管线，无返回值；重复调用安全。"""

        with self._lock:
            backend, self._backend = self._backend, None
            self._status = WhipRelayStatus.STOPPED
        if backend is not None:
            backend.close()
        logger.debug("GStreamer WHIP 无转码发布已关闭: publish=%s", _redact_url(self.publish_url))

    def __enter__(self) -> GStreamerWhipRelay:
        """启动发布并返回当前实例，供 ``with`` 语句管理生命周期。"""

        self.start()
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        """退出 ``with`` 语句时关闭发布，无返回值。"""

        self.close()

    def _on_backend_error(self, error: BaseException) -> None:
        message = _redact_error(error, self.source_uri, self.publish_url)
        with self._lock:
            if self._status is WhipRelayStatus.STOPPED:
                return
            backend, self._backend = self._backend, None
            self._status = WhipRelayStatus.FAILED
            self._error = message
        if backend is not None:
            backend.close()
        logger.debug("GStreamer WHIP 无转码发布运行失败: %s", message)


class _GStreamerWhipRelayBackend:
    def __init__(
        self,
        source_uri: str,
        publish_url: str,
        startup_timeout_s: float,
        rtsp_latency_ms: int,
        drop_on_latency: bool,
    ) -> None:
        self._source_uri = source_uri
        self._publish_url = publish_url
        self._startup_timeout_s = startup_timeout_s
        self._rtsp_latency_ms = rtsp_latency_ms
        self._drop_on_latency = drop_on_latency
        self._gst: Any = None
        self._pipeline: Any = None
        self._whip_sink: Any = None
        self._whip_pad: Any = None
        self._stop = Event()
        self._bus_thread: Thread | None = None

    def start(self, on_error: Callable[[BaseException], None]) -> None:
        Gst, GstRtsp = self._load_gstreamer()
        self._gst = Gst
        pipeline = Gst.Pipeline.new("fleetdock-gstreamer-whip-relay")
        if pipeline is None:
            raise RuntimeError("GStreamer could not create the WHIP relay pipeline.")
        elements = {
            name: Gst.ElementFactory.make(name, name)
            for name in GSTREAMER_WHIP_RELAY_ELEMENTS
        }
        missing = tuple(name for name, element in elements.items() if element is None)
        if missing:
            raise RuntimeError(f"Required GStreamer elements are unavailable: {', '.join(missing)}")
        source = elements["rtspsrc"]
        depay = elements["rtph264depay"]
        parser = elements["h264parse"]
        pay = elements["rtph264pay"]
        caps_filter = elements["capsfilter"]
        whip_sink = elements["whipsink"]
        source.set_property("location", self._source_uri)
        source.set_property("protocols", GstRtsp.RTSPLowerTrans.TCP)
        source.set_property("latency", self._rtsp_latency_ms)
        if source.find_property("drop-on-latency") is not None:
            source.set_property("drop-on-latency", self._drop_on_latency)
        parser.set_property("config-interval", -1)
        pay.set_property("pt", 96)
        pay.set_property("config-interval", 1)
        caps_filter.set_property(
            "caps",
            Gst.Caps.from_string(
                "application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000"
            ),
        )
        whip_sink.set_property("whip-endpoint", self._publish_url)
        if whip_sink.find_property("timeout") is not None:
            whip_sink.set_property("timeout", max(1, round(self._startup_timeout_s)))
        for element in elements.values():
            pipeline.add(element)
        if not depay.link(parser) or not parser.link(pay) or not pay.link(caps_filter):
            pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("GStreamer could not link the H.264 WHIP relay elements.")
        whip_pad = whip_sink.request_pad_simple("sink_%u")
        source_pad = caps_filter.get_static_pad("src")
        if whip_pad is None or source_pad is None or source_pad.link(whip_pad) != Gst.PadLinkReturn.OK:
            pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("GStreamer could not link the RTP stream to whipsink.")
        source.connect("pad-added", self._on_source_pad_added, depay)
        self._pipeline = pipeline
        self._whip_sink = whip_sink
        self._whip_pad = whip_pad
        result = pipeline.set_state(Gst.State.PLAYING)
        if result == Gst.StateChangeReturn.FAILURE:
            self.close()
            raise RuntimeError("GStreamer rejected the WHIP relay PLAYING state.")
        result, current, _pending = pipeline.get_state(int(self._startup_timeout_s * Gst.SECOND))
        if result == Gst.StateChangeReturn.FAILURE or current != Gst.State.PLAYING:
            error = self._pop_bus_error()
            self.close()
            raise error or RuntimeError("Timed out waiting for the GStreamer WHIP relay.")
        self._stop.clear()
        self._bus_thread = Thread(
            target=self._watch_bus,
            args=(on_error,),
            name="fleetdock-gstreamer-whip-relay",
            daemon=True,
        )
        self._bus_thread.start()

    def close(self) -> None:
        self._stop.set()
        pipeline, self._pipeline = self._pipeline, None
        whip_sink, self._whip_sink = self._whip_sink, None
        whip_pad, self._whip_pad = self._whip_pad, None
        if pipeline is not None and self._gst is not None:
            pipeline.set_state(self._gst.State.NULL)
        if whip_sink is not None and whip_pad is not None:
            whip_sink.release_request_pad(whip_pad)
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
                "GStreamer Python bindings, GstRtsp and the whipsink plugin are required for WHIP relay."
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
                on_error(RuntimeError("GStreamer WHIP relay reached end of stream."))
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


__all__ = ["GSTREAMER_WHIP_RELAY_ELEMENTS", "GStreamerWhipRelay"]
