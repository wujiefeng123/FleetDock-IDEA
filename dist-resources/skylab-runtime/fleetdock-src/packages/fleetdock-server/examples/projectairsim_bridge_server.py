"""显式创建 Project AirSim RTSP Bridge，并启动 PX4 仿真 WS 服务。"""

import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fleetdock.simulation import ProjectAirSimRtspBridge
from fleetdock_server.commands.drone import main as run_drone_server

from _projectairsim_logging import configure_projectairsim_console_logging


def _env_bool(name: str, default: bool) -> bool:
    """读取示例布尔环境变量；支持1/0、true/false、yes/no和on/off。"""

    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value.")


WS_HOST = os.getenv("FLEETDOCK_WS_HOST", "127.0.0.1")
WS_PORT = int(os.getenv("FLEETDOCK_WS_PORT", "23045"))
DEVICE_ID = os.getenv("FLEETDOCK_DEVICE_ID", "drone-001")
PX4_ENDPOINT = os.getenv("FLEETDOCK_PX4_ENDPOINT", "udpin://0.0.0.0:14555")
DRONE_NAME = os.getenv("PROJECTAIRSIM_DRONE_NAME", "Drone1")
LOCAL_RTSP_URI = os.getenv("FLEETDOCK_CAMERA_STREAM_URI")

# 上游 RTSP 编码参数：保持场景原始分辨率，不在这里缩放画面。
CAMERA_FRAME_RATE_HZ = float(os.getenv("FLEETDOCK_CAMERA_FRAME_RATE_HZ", "30"))
CAMERA_BITRATE_KBPS = int(os.getenv("FLEETDOCK_CAMERA_BITRATE_KBPS", "2000"))
CAMERA_GOP_FRAMES = int(os.getenv("FLEETDOCK_CAMERA_GOP_FRAMES", "30"))
CAMERA_ENCODER_COMPLEXITY = os.getenv("FLEETDOCK_CAMERA_ENCODER_COMPLEXITY", "medium")
CAMERA_ENABLE_FRAME_SKIP = _env_bool("FLEETDOCK_CAMERA_ENABLE_FRAME_SKIP", True)

# RTSP 拉流低延迟参数：公网或不稳定网络可把缓冲提高到50～150毫秒。
WHIP_RTSP_LATENCY_MS = int(os.getenv("FLEETDOCK_WHIP_RTSP_LATENCY_MS", "0"))
WHIP_DROP_ON_LATENCY = _env_bool("FLEETDOCK_WHIP_DROP_ON_LATENCY", True)

WHIP_PUBLISH_URL = os.getenv(
    "FLEETDOCK_SRS_WHIP_URL",
    "https://live.jpy.wang/rtc/v1/whip/?app=live&stream=drone-001",
)
WHEP_URL = os.getenv(
    "FLEETDOCK_SRS_WHEP_URL",
    "https://live.jpy.wang/rtc/v1/whep/?app=live&stream=drone-001",
)
MEDIA_BACKEND = os.getenv("FLEETDOCK_MEDIA_BACKEND", "gstreamer").strip().lower()


def _display_url(url: str) -> str:
    """返回适合示例控制台显示的地址，并隐藏认证信息及常见令牌参数。"""

    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    if parsed.username is not None or parsed.password is not None:
        host = f"***@{host}"
    sensitive_keys = {"token", "auth", "authorization", "signature", "secret", "key"}
    query = urlencode(
        [
            (key, "***" if key.lower() in sensitive_keys else value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parsed.scheme, host, parsed.path, query, ""))


def main() -> None:
    """在本机23045端口发布PX4仿真设备，按Ctrl+C退出。"""

    configure_projectairsim_console_logging()
    bridge = ProjectAirSimRtspBridge(
        DRONE_NAME,
        rtsp_uri=LOCAL_RTSP_URI,
        frame_rate_hz=CAMERA_FRAME_RATE_HZ,
        bitrate_kbps=CAMERA_BITRATE_KBPS,
        gop_frames=CAMERA_GOP_FRAMES,
        encoder_complexity=CAMERA_ENCODER_COMPLEXITY,
        enable_frame_skip=CAMERA_ENABLE_FRAME_SKIP,
    )
    with bridge:
        print(f"PX4 仿真端点：{PX4_ENDPOINT}")
        print(f"本地 RTSP 视频：{bridge.stream_uri}")
        video_status = bridge.status()
        resolution = (
            f"{video_status.width_px}×{video_status.height_px}"
            if video_status.width_px is not None and video_status.height_px is not None
            else "等待首帧"
        )
        print(
            "RTSP 编码参数："
            f"{resolution} / {video_status.target_frame_rate_hz:g} FPS / "
            f"目标 {video_status.target_bitrate_kbps / 1000:g} Mbps / "
            f"GOP {video_status.gop_frames} 帧 / "
            f"complexity={video_status.encoder_complexity} / "
            f"frame-skip={video_status.frame_skip_enabled}"
        )
        print(f"WebRTC/WHEP 播放：{_display_url(WHEP_URL)}")
        print("请在 Web 控制器中点击“连接设备”后再解锁和起飞。")
        arguments = [
            "--endpoint",
            PX4_ENDPOINT,
            "--device-id",
            DEVICE_ID,
            "--device-name",
            "Project AirSim无人机",
            "--camera-rtsp-uri",
            bridge.stream_uri,
            "--camera-source",
            bridge.sensor_source.value,
            "--ws-host",
            WS_HOST,
            "--ws-port",
            str(WS_PORT),
            "--media-publish-url",
            WHIP_PUBLISH_URL,
            "--media-backend",
            MEDIA_BACKEND,
            "--rtsp-latency-ms",
            str(WHIP_RTSP_LATENCY_MS),
            "--ffmpeg-bitrate-kbps",
            str(CAMERA_BITRATE_KBPS),
            "--ffmpeg-gop-frames",
            str(CAMERA_GOP_FRAMES),
        ]
        if not WHIP_DROP_ON_LATENCY:
            arguments.append("--no-drop-on-latency")
        run_drone_server(arguments)


if __name__ == "__main__":
    main()
