"""使用同步 Python WS 客户端读取无人机能力、状态和传感器列表。"""

import json
import os
from typing import Any

from fleetdock_server import FleetDockClient


def _print_message(title: str, message: dict[str, Any]) -> None:
    """以便于调试的中文标题和缩进 JSON 输出一条协议消息。"""

    print(f"\n{title}：")
    print(json.dumps(message, ensure_ascii=False, indent=2))


def main() -> None:
    """连接已有 WS 服务并执行只读诊断，退出时关闭控制和遥测连接。"""

    endpoint = os.getenv("FLEETDOCK_WS_ENDPOINT", "ws://127.0.0.1:23045")
    device_id = os.getenv("FLEETDOCK_DEVICE_ID", "drone-001")
    with FleetDockClient(endpoint) as client:
        _print_message("设备能力", client.connect(device_id=device_id))
        _print_message("设备状态", client.get_device_state(device_id))
        _print_message("传感器列表", client.list_sensors(device_id))


if __name__ == "__main__":
    main()
