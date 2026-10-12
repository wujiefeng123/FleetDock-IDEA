"""通过直观的命令行参数启动 PX4 无人机服务。"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fleetdock import SensorSourceKind
from fleetdock_server.commands.drone import main as run_drone_server


def _positive_float(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("参数必须大于零")
    return number


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("参数必须大于零")
    return number


def _non_negative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("参数不能小于零")
    return number


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65_535:
        raise argparse.ArgumentTypeError("端口必须位于 1 到 65535 之间")
    return port


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="使用外部 RTSP 摄像头地址启动 PX4 无人机的 FleetDock 服务。",
    )
    parser.add_argument(
        "--endpoint",
        required=True,
        help="PX4/MAVSDK 连接字符串，例如 udpin://0.0.0.0:14555",
    )
    parser.add_argument("--device-id", default="drone-001", help="FleetDock 稳定设备 ID")
    parser.add_argument("--device-name", default="FleetDock PX4无人机", help="设备显示名称")
    parser.add_argument(
        "--connect-timeout-s",
        type=_positive_float,
        default=10.0,
        help="PX4 连接超时，单位秒",
    )
    parser.add_argument("--mavsdk-server-port", type=_port, help="可选 MAVSDK 本地 gRPC 端口")
    parser.add_argument(
        "--camera-rtsp-uri",
        help="可选的外部 RTSP/RTSPS 摄像头拉流地址；本程序不会创建 RTSP 服务",
    )
    parser.add_argument(
        "--camera-source",
        choices=[item.value for item in SensorSourceKind],
        default=SensorSourceKind.UNKNOWN.value,
        help="摄像头来源性质，默认 unknown",
    )
    parser.add_argument("--ws-host", default="127.0.0.1", help="FleetDock WS 监听地址")
    parser.add_argument("--ws-port", type=_port, default=23045, help="FleetDock WS 监听端口")
    parser.add_argument(
        "--max-message-bytes",
        type=_positive_int,
        default=1_048_576,
        help="单条 WS 消息字节上限",
    )
    parser.add_argument(
        "--lease-timeout-s",
        type=_positive_float,
        default=2.0,
        help="控制租约心跳超时，单位秒",
    )
    parser.add_argument("--media-publish-url", help="可选 SRS WHIP 发布地址")
    parser.add_argument("--whep-url", help="可选 WHEP 播放地址，仅用于启动提示")
    parser.add_argument(
        "--media-backend",
        choices=("gstreamer", "ffmpeg"),
        default="gstreamer",
        help="媒体发布后端",
    )
    parser.add_argument(
        "--rtsp-latency-ms",
        type=_non_negative_int,
        default=0,
        help="媒体发布端的 RTSP 抖动缓冲，单位毫秒",
    )
    parser.add_argument(
        "--drop-on-latency",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否丢弃超过 RTSP 缓冲窗口的旧数据",
    )
    parser.add_argument(
        "--require-media",
        action="store_true",
        help="媒体发布失败时终止服务",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="命令行日志等级",
    )
    return parser


def _display_url(url: str) -> str:
    """隐藏播放地址中的认证信息和常见令牌参数，返回可安全显示的地址。"""

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


def main(argv: Sequence[str] | None = None) -> None:
    """解析 PX4、WS 和外部媒体参数，并阻塞运行无人机服务。"""

    parser = _build_parser()
    arguments = parser.parse_args(argv)
    arguments.endpoint = arguments.endpoint.strip()
    if not arguments.endpoint:
        parser.error("--endpoint 不能为空")
    print(f"PX4 端点：{arguments.endpoint}")
    if arguments.camera_rtsp_uri:
        print(f"RTSP 拉流地址：{_display_url(arguments.camera_rtsp_uri)}")
    if arguments.whep_url:
        print(f"WebRTC/WHEP 播放：{_display_url(arguments.whep_url)}")

    server_arguments = [
        "--endpoint",
        arguments.endpoint,
        "--device-id",
        arguments.device_id,
        "--device-name",
        arguments.device_name,
        "--connect-timeout-s",
        str(arguments.connect_timeout_s),
        "--camera-source",
        arguments.camera_source,
        "--ws-host",
        arguments.ws_host,
        "--ws-port",
        str(arguments.ws_port),
        "--max-message-bytes",
        str(arguments.max_message_bytes),
        "--lease-timeout-s",
        str(arguments.lease_timeout_s),
        "--media-backend",
        arguments.media_backend,
        "--rtsp-latency-ms",
        str(arguments.rtsp_latency_ms),
        "--log-level",
        arguments.log_level,
    ]
    if arguments.mavsdk_server_port is not None:
        server_arguments.extend(["--mavsdk-server-port", str(arguments.mavsdk_server_port)])
    if arguments.camera_rtsp_uri:
        server_arguments.extend(["--camera-rtsp-uri", arguments.camera_rtsp_uri])
    if arguments.media_publish_url:
        server_arguments.extend(["--media-publish-url", arguments.media_publish_url])
    if not arguments.drop_on_latency:
        server_arguments.append("--no-drop-on-latency")
    if arguments.require_media:
        server_arguments.append("--require-media")
    run_drone_server(server_arguments)


if __name__ == "__main__":
    main()
