"""FleetDock WebSocket 服务命令行入口。"""

from __future__ import annotations

import argparse
import logging

from fleetdock import BaseDevice, DeviceFactory, DeviceMetadata

from fleetdock_server.server import FleetDockServer


def main() -> None:
    """解析设备与监听参数并阻塞运行FleetDock WebSocket服务，无返回值。"""

    parser = argparse.ArgumentParser(description="FleetDock WebSocket service")
    product = parser.add_mutually_exclusive_group(required=True)
    product.add_argument("--mock-drone", action="store_true", help="注册内置Mock无人机")
    product.add_argument("--mock-robot-dog", action="store_true", help="注册内置Mock机械狗")
    product.add_argument("--px4-endpoint", help="注册PX4通用无人机，例如 udpin://0.0.0.0:14555")
    parser.add_argument("--device-id", default="device-001", help="对外发布的稳定设备ID")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，远程监听必须自行配置网络安全边界")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    parser.add_argument("--debug", action="store_true", help="启用DEBUG运行日志")
    arguments = parser.parse_args()
    if arguments.debug:
        logging.basicConfig(level=logging.DEBUG)

    metadata = DeviceMetadata(device_id=arguments.device_id)
    device: BaseDevice
    if arguments.mock_drone:
        device = DeviceFactory.create().mock_drone().metadata(metadata).build()
    elif arguments.mock_robot_dog:
        device = DeviceFactory.create().mock_robot_dog().metadata(metadata).build()
    else:
        device = (
            DeviceFactory.create()
            .px4_generic()
            .params(endpoint=arguments.px4_endpoint)
            .metadata(metadata)
            .build()
        )

    server = FleetDockServer()
    server.register(device)
    try:
        print(f"FleetDock WS服务启动：ws://{arguments.host}:{arguments.port}")
        server.run(arguments.host, arguments.port)
    except KeyboardInterrupt:
        pass
    finally:
        server.close()


__all__ = ["main"]
