"""连接已经启动的 FleetDock WS 服务并打开 Python 图形控制器。"""

import os

from fleetdock_server import ControlMode, PythonController


def main() -> None:
    """按环境变量连接无人机服务并运行图形控制器，关闭窗口后释放控制租约。"""

    endpoint = os.getenv("FLEETDOCK_WS_ENDPOINT", "ws://127.0.0.1:23045")
    device_id = os.getenv("FLEETDOCK_DEVICE_ID", "drone-001")
    mode = ControlMode(os.getenv("FLEETDOCK_CONTROL_MODE", ControlMode.SIMULATION.value))
    rtsp_uri = os.getenv("FLEETDOCK_CAMERA_STREAM_URI")
    PythonController(
        endpoint,
        device_id,
        mode=mode,
        rtsp_uri=rtsp_uri,
    ).run()


if __name__ == "__main__":
    main()
