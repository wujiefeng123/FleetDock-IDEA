"""把 RTSP 摄像头流以低延迟 H.264 发布到 WHIP/WebRTC 服务。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
import os
import shutil
import subprocess
from threading import Event, RLock, Thread, current_thread
from typing import Callable, Protocol
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)


class WhipRelayStatus(str, Enum):
    """WHIP 发布器生命周期状态。"""

    STOPPED = "stopped"  # 尚未启动或已经安全关闭。
    STARTING = "starting"  # 正在创建媒体发布管线并连接 WHIP 服务。
    RUNNING = "running"  # 正在把视频持续发布到 WHIP 服务。
    FAILED = "failed"  # 发布进程已经异常退出。


@dataclass(frozen=True)
class WhipRelaySnapshot:
    """WHIP 发布状态的不可变快照。

    ``status`` 表示当前生命周期；``source_uri`` 和 ``publish_url`` 已移除用户名、密码和查询参数；
    ``error`` 保存最近一次发布进程异常，没有错误时为 ``None``。
    """

    status: WhipRelayStatus
    source_uri: str
    publish_url: str
    error: str | None = None


class _WhipProcess(Protocol):
    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


ProcessFactory = Callable[[list[str]], _WhipProcess]


def _redact_url(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    if parsed.username is not None or parsed.password is not None:
        host = f"***@{host}"
    return urlunsplit((parsed.scheme, host, parsed.path, "***" if parsed.query else "", ""))


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


class WhipRelay:
    """通过 FFmpeg 子进程把一路 RTSP 视频发布到 WHIP 服务。

    ``source_uri`` 是完整 RTSP 摄像头地址；``publish_url`` 是完整 WHIP HTTP(S) 地址；
    ``ffmpeg_path`` 是 FFmpeg 可执行文件名或路径。
    发布器使用 FFmpeg 将输入统一转换为 H.264 Baseline、YUV420P、无 B 帧的低延迟码流，
    不经过 RTMP。``bitrate_kbps`` 单位是 kbit/s，``gop_frames`` 单位是视频帧；
    ``startup_probe_s`` 单位是秒。调用 ``close()`` 会终止本对象创建的 FFmpeg 子进程。
    """

    def __init__(
        self,
        source_uri: str,
        publish_url: str,
        *,
        ffmpeg_path: str = "ffmpeg",
        bitrate_kbps: int = 2_000,
        gop_frames: int = 30,
        startup_probe_s: float = 1.0,
        _process_factory: ProcessFactory | None = None,
    ) -> None:
        _validate_url("source_uri", source_uri, {"rtsp", "rtsps"})
        _validate_url("publish_url", publish_url, {"http", "https"})
        if bitrate_kbps <= 0:
            raise ValueError("bitrate_kbps must be greater than zero.")
        if gop_frames <= 0:
            raise ValueError("gop_frames must be greater than zero.")
        if startup_probe_s <= 0:
            raise ValueError("startup_probe_s must be greater than zero.")
        self.source_uri = source_uri
        self.publish_url = publish_url
        self.ffmpeg_path = ffmpeg_path
        self.bitrate_kbps = bitrate_kbps
        self.gop_frames = gop_frames
        self.startup_probe_s = startup_probe_s
        self._process_factory = _process_factory
        self._lock = RLock()
        self._stop = Event()
        self._status = WhipRelayStatus.STOPPED
        self._error: str | None = None
        self._process: _WhipProcess | None = None
        self._monitor_thread: Thread | None = None

    @property
    def status(self) -> WhipRelayStatus:
        """返回当前发布状态；不会访问网络或阻塞。"""

        with self._lock:
            return self._status

    def snapshot(self) -> WhipRelaySnapshot:
        """返回当前状态、脱敏地址和最近错误；不会访问网络。"""

        with self._lock:
            return WhipRelaySnapshot(
                status=self._status,
                source_uri=_redact_url(self.source_uri),
                publish_url=_redact_url(self.publish_url),
                error=self._error,
            )

    def start(self) -> None:
        """启动低延迟 WHIP 发布，无返回值。

        重复启动正在运行的实例不会创建第二个进程；FFmpeg 不存在或启动探测期内异常退出时抛出
        ``RuntimeError``。成功返回只表示发布进程正在运行，浏览器仍应以收到首帧作为视频在线依据。
        """

        with self._lock:
            if self._status is WhipRelayStatus.RUNNING:
                return
            if self._status is WhipRelayStatus.STARTING:
                raise RuntimeError("WHIP relay is already starting.")
            self._status = WhipRelayStatus.STARTING
            self._error = None
            self._stop.clear()
        command = self._build_command()
        try:
            process = self._create_process(command)
            with self._lock:
                self._process = process
            if self._stop.wait(self.startup_probe_s):
                self.close()
                return
            return_code = process.poll()
            if return_code is not None:
                raise RuntimeError(f"FFmpeg WHIP publisher exited with code {return_code}.")
            with self._lock:
                self._status = WhipRelayStatus.RUNNING
                thread = Thread(
                    target=self._monitor,
                    args=(process,),
                    name="fleetdock-whip-relay",
                    daemon=True,
                )
                self._monitor_thread = thread
                thread.start()
            logger.debug(
                "WHIP 低延迟发布已启动: source=%s publish=%s",
                _redact_url(self.source_uri),
                _redact_url(self.publish_url),
            )
        except Exception as exc:
            with self._lock:
                failed_process, self._process = self._process, None
                self._status = WhipRelayStatus.FAILED
                self._error = str(exc) or exc.__class__.__name__
            if failed_process is not None and failed_process.poll() is None:
                failed_process.terminate()
            logger.debug("启动 WHIP 低延迟发布失败: %s", self._error, exc_info=True)
            raise RuntimeError(self._error) from exc

    def close(self) -> None:
        """停止发布并释放 FFmpeg 子进程，无返回值；重复调用安全。"""

        with self._lock:
            process, self._process = self._process, None
            thread, self._monitor_thread = self._monitor_thread, None
            self._stop.set()
            self._status = WhipRelayStatus.STOPPED
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        if thread is not None and thread is not current_thread() and thread.is_alive():
            thread.join(timeout=3.0)
        logger.debug("WHIP 低延迟发布已关闭: publish=%s", _redact_url(self.publish_url))

    def _build_command(self) -> list[str]:
        executable = (
            self.ffmpeg_path
            if self._process_factory is not None
            else shutil.which(self.ffmpeg_path)
        )
        if executable is None:
            raise RuntimeError(f"FFmpeg executable was not found: {self.ffmpeg_path}")
        return [
            executable,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-rtsp_transport",
            "tcp",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-i",
            self.source_uri,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-profile:v",
            "baseline",
            "-pix_fmt",
            "yuv420p",
            "-b:v",
            f"{self.bitrate_kbps}k",
            "-g",
            str(self.gop_frames),
            "-keyint_min",
            str(self.gop_frames),
            "-bf",
            "0",
            "-f",
            "whip",
            self.publish_url,
        ]

    def _create_process(self, command: list[str]) -> _WhipProcess:
        if self._process_factory is not None:
            return self._process_factory(command)
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )

    def _monitor(self, process: _WhipProcess) -> None:
        return_code = process.wait()
        with self._lock:
            if self._process is not process or self._stop.is_set():
                return
            self._process = None
            self._monitor_thread = None
            self._status = WhipRelayStatus.FAILED
            self._error = f"FFmpeg WHIP publisher exited with code {return_code}."
        logger.debug("WHIP 发布进程异常退出: code=%d", return_code)


FfmpegWhipRelay = WhipRelay
"""``WhipRelay`` 的明确实现名；保留原名称用于向后兼容。"""


__all__ = ["FfmpegWhipRelay", "WhipRelay", "WhipRelaySnapshot", "WhipRelayStatus"]
