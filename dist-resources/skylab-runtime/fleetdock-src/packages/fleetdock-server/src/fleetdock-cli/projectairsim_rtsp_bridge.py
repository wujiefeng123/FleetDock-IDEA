"""通过命令行启动 Project AirSim 到 RTSP 的单向视频 Bridge。"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import logging
import sys
from threading import Event

from fleetdock.simulation import ProjectAirSimRtspBridge


def _configure_projectairsim_console_logging(level: int) -> None:
    """让 Project AirSim 日志只输出到控制台，不创建客户端日志文件。"""

    logger = logging.getLogger("projectairsim")
    formatter = logging.Formatter(
        "%(asctime)s:%(msecs)03d [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    for handler in list(logger.handlers):
        if isinstance(handler, logging.FileHandler):
            logger.removeHandler(handler)
            handler.close()
    if not logger.handlers:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    logger.setLevel(level)
    logger.propagate = False


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="把 Project AirSim RGB 相机转换为本地 H.264 RTSP 拉流地址。",
    )
    parser.add_argument("--drone-name", default="Drone1", help="Project AirSim 机器人名称")
    parser.add_argument(
        "--camera-rtsp-uri",
        help="Bridge 创建的本地 RTSP 地址；省略时由 SDK 使用默认地址",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="命令行日志等级",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """启动 Bridge 并保持运行，按 Ctrl+C 后释放仿真订阅和 RTSP 服务。"""

    arguments = _build_parser().parse_args(argv)
    _configure_projectairsim_console_logging(getattr(logging, arguments.log_level))
    bridge = ProjectAirSimRtspBridge(
        arguments.drone_name,
        rtsp_uri=arguments.camera_rtsp_uri,
    )
    try:
        with bridge:
            print(f"RTSP 拉流地址：{bridge.stream_uri}")
            print("Bridge 正在运行；请在另一个终端启动 start_drone_server.bat，按 Ctrl+C 退出。")
            Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
