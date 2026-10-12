"""启动并托管一架标准无人机的 FleetDock WebSocket 服务。"""

from __future__ import annotations

import argparse
import ipaddress
import logging
from typing import Any, Sequence

from fleetdock import DeviceFactory, DeviceMetadata, SensorSourceKind

from fleetdock_server.media import FfmpegWhipRelay, GStreamerWhipRelay
from fleetdock_server.server import FleetDockServer


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65_535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _positive_float(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return number


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return number


def _non_negative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("value must not be negative")
    return number


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fleetdock-server-drone",
        description="启动 PX4 无人机的 FleetDock WebSocket 控制、遥测及可选视频发布服务。",
    )
    parser.add_argument(
        "--endpoint",
        required=True,
        help="PX4/MAVSDK连接字符串，原样传给适配器，例如 udpin://0.0.0.0:14555",
    )
    parser.add_argument("--device-id", default="drone-001", help="通过WS发布的稳定设备ID")
    parser.add_argument("--device-name", default="FleetDock PX4无人机", help="设备显示名称")
    parser.add_argument("--connect-timeout-s", type=_positive_float, default=10.0, help="PX4连接超时秒数")
    parser.add_argument(
        "--link-profile",
        choices=("simulation", "standard", "low_bandwidth"),
        default="standard",
        help="遥测链路策略；室内低速数传使用low_bandwidth，默认standard",
    )
    parser.add_argument("--mavsdk-server-port", type=_port, help="可选MAVSDK本地gRPC端口")
    parser.add_argument("--camera-rtsp-uri", help="可选RTSP/RTSPS摄像头拉流地址")
    parser.add_argument(
        "--camera-source",
        choices=[item.value for item in SensorSourceKind],
        default=SensorSourceKind.UNKNOWN.value,
        help="摄像头来源性质，默认unknown",
    )
    parser.add_argument("--ws-host", default="127.0.0.1", help="WS监听地址，默认仅本机可访问")
    parser.add_argument("--ws-port", type=_port, default=23045, help="WS监听端口")
    parser.add_argument("--max-message-bytes", type=_positive_int, default=1_048_576, help="单条WS消息字节上限")
    parser.add_argument("--lease-timeout-s", type=_positive_float, default=2.0, help="控制租约心跳超时秒数")
    parser.add_argument("--media-publish-url", help="可选WHIP HTTP(S)发布地址；必须同时提供摄像头地址")
    parser.add_argument(
        "--media-backend",
        choices=("gstreamer", "ffmpeg"),
        default="gstreamer",
        help="媒体发布后端，默认使用进程内GStreamer无转码",
    )
    parser.add_argument(
        "--require-media",
        action="store_true",
        help="媒体发布失败时终止服务；默认保持WS服务可用",
    )
    parser.add_argument(
        "--rtsp-latency-ms",
        type=_non_negative_int,
        default=0,
        help="GStreamer RTSP接收抖动缓冲毫秒数",
    )
    parser.add_argument(
        "--drop-on-latency",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否丢弃超过RTSP缓冲窗口的旧数据",
    )
    parser.add_argument("--ffmpeg-bitrate-kbps", type=_positive_int, default=2_000, help="FFmpeg回退编码码率")
    parser.add_argument("--ffmpeg-gop-frames", type=_positive_int, default=30, help="FFmpeg回退编码GOP帧数")
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="命令行应用日志等级",
    )
    return parser


def _parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    arguments.endpoint = arguments.endpoint.strip()
    if not arguments.endpoint:
        parser.error("--endpoint must not be empty")
    if arguments.media_publish_url and not arguments.camera_rtsp_uri:
        parser.error("--media-publish-url requires --camera-rtsp-uri")
    return arguments


def _create_device(arguments: argparse.Namespace) -> Any:
    return (
        DeviceFactory.create()
        .px4_generic()
        .params(
            endpoint=arguments.endpoint,
            timeout_s=arguments.connect_timeout_s,
            link_profile=arguments.link_profile,
            mavsdk_server_port=arguments.mavsdk_server_port,
            camera_stream_uri=arguments.camera_rtsp_uri,
            camera_stream_source=SensorSourceKind(arguments.camera_source),
        )
        .metadata(DeviceMetadata(device_id=arguments.device_id, name=arguments.device_name))
        .build()
    )


def _create_media_relay(arguments: argparse.Namespace) -> Any | None:
    if not arguments.media_publish_url:
        return None
    if arguments.media_backend == "gstreamer":
        return GStreamerWhipRelay(
            arguments.camera_rtsp_uri,
            arguments.media_publish_url,
            rtsp_latency_ms=arguments.rtsp_latency_ms,
            drop_on_latency=arguments.drop_on_latency,
        )
    return FfmpegWhipRelay(
        arguments.camera_rtsp_uri,
        arguments.media_publish_url,
        bitrate_kbps=arguments.ffmpeg_bitrate_kbps,
        gop_frames=arguments.ffmpeg_gop_frames,
    )


def _is_loopback_host(host: str) -> bool:
    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _run(arguments: argparse.Namespace) -> None:
    device = _create_device(arguments)
    server: FleetDockServer | None = None
    relay: Any | None = None
    registered = False
    try:
        server = FleetDockServer(
            close_devices=True,
            max_message_bytes=arguments.max_message_bytes,
            lease_timeout_s=arguments.lease_timeout_s,
        )
        server.register(device, device_id=arguments.device_id)
        registered = True
        relay = _create_media_relay(arguments)
        if relay is not None:
            try:
                relay.start()
                print(
                    "媒体发布管线已启动（最终在线状态以SRS流列表和WebRTC首帧为准）："
                    f"{relay.snapshot().publish_url}"
                )
            except RuntimeError as exc:
                if arguments.require_media:
                    raise
                print(f"媒体发布启动失败，WS服务将继续运行：{exc}")
        if not _is_loopback_host(arguments.ws_host):
            print("警告：当前监听地址不是本机回环地址；部署前必须由网关提供TLS、认证、授权和访问限制。")
        print(f"设备：{arguments.device_id}（PX4）")
        print(f"FleetDock 控制服务：ws://{arguments.ws_host}:{arguments.ws_port}/ws/control")
        print(f"FleetDock 遥测服务：ws://{arguments.ws_host}:{arguments.ws_port}/ws/telemetry")
        server.run(arguments.ws_host, arguments.ws_port)
    except KeyboardInterrupt:
        pass
    finally:
        if relay is not None:
            relay.close()
        if server is not None:
            server.close()
        if not registered:
            device.close()


def main(argv: Sequence[str] | None = None) -> None:
    """解析参数并阻塞运行单无人机服务。

    ``argv`` 是可选命令行参数序列；省略时读取当前进程参数。退出时释放全部资源，方法无返回值。
    """

    arguments = _parse_arguments(argv)
    logging.basicConfig(level=getattr(logging, arguments.log_level))
    _run(arguments)


if __name__ == "__main__":
    main()
