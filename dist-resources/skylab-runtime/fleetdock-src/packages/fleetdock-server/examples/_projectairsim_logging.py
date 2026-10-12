"""Project AirSim 服务端示例共用的控制台日志配置。"""

from __future__ import annotations

import logging
import sys


def configure_projectairsim_console_logging(level: int = logging.INFO) -> None:
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
