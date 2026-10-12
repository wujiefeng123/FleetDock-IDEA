"""FleetDock WebSocket 服务的同步 Python 客户端。"""

from __future__ import annotations

import json
import logging
from collections import deque
from threading import Event, RLock, Thread
from typing import Any, Optional
from uuid import uuid4

from websockets.sync.client import ClientConnection, connect

from fleetdock_server.control import ControlMode, ManualAction
from fleetdock_server.server import PROTOCOL_VERSION


logger = logging.getLogger(__name__)


class FleetDockClient:
    """通过统一WebSocket协议访问FleetDock控制、状态和标准传感器能力。

    ``endpoint`` 是不带路径的服务根地址。``connect()`` 建立控制与遥测两条连接；获得控制权后客户端
    自动按服务端建议频率发送心跳。``close()`` 会先释放全部动作和控制权，再关闭连接。
    """

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint.rstrip("/")
        self._control: Optional[ClientConnection] = None
        self._telemetry: Optional[ClientConnection] = None
        self._control_lock = RLock()
        self._telemetry_lock = RLock()
        self._sequence = 0
        self._control_id: Optional[str] = None
        self._device_id: Optional[str] = None
        self._heartbeat_interval_s = 0.1
        self._heartbeat_stop = Event()
        self._heartbeat_thread: Optional[Thread] = None
        self._pending_telemetry: deque[dict[str, Any]] = deque(maxlen=256)

    def connect(self, *, device_id: Optional[str] = None) -> dict[str, Any]:
        """建立控制与遥测连接并返回服务端设备能力握手结果。

        ``device_id`` 用于只发现一个服务端稳定设备ID；省略时返回调用方可见的全部设备。
        """

        if self._control is not None:
            raise RuntimeError("FleetDock client is already connected.")
        self._control = connect(f"{self.endpoint}/ws/control", compression=None)
        self._telemetry = connect(f"{self.endpoint}/ws/telemetry", compression=None)
        control_welcome = self._request_control("session.hello", device_id=device_id)
        self._request_telemetry("session.hello", device_id=device_id)
        return control_welcome

    def acquire_control(
        self,
        device_id: str,
        *,
        mode: ControlMode = ControlMode.LIVE,
    ) -> dict[str, Any]:
        """申请 ``device_id`` 的独占控制权并启动后台心跳，返回租约信息。

        ``mode`` 声明仿真、台架或真机安全环境，影响起飞和强制解锁等危险操作的授权。
        """

        if self._control_id is not None:
            raise RuntimeError("A control lease is already active.")
        response = self._request_control("control.acquire", device_id=device_id, mode=mode.value)
        self._control_id = str(response["control_id"])
        self._device_id = device_id
        self._heartbeat_interval_s = max(0.02, float(response["heartbeat_interval_ms"]) / 1000.0)
        self._heartbeat_stop.clear()
        self._heartbeat_thread = Thread(
            target=self._heartbeat_loop,
            name="fleetdock-ws-client-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()
        return response

    def press(self, action: ManualAction) -> dict[str, Any]:
        """按下 ``action`` 标准持续动作并返回服务端控制状态。"""

        return self._control_action("manual.press", action=ManualAction(action).value)

    def release(self, action: ManualAction) -> dict[str, Any]:
        """弹起 ``action`` 标准持续动作并返回服务端控制状态。"""

        return self._control_action("manual.release", action=ManualAction(action).value)

    def release_all(self) -> dict[str, Any]:
        """立即释放全部持续动作并返回服务端控制状态。"""

        return self._control_action("manual.release_all")

    def configure_manual(
        self,
        *,
        horizontal_speed_m_s: Optional[float] = None,
        vertical_speed_m_s: Optional[float] = None,
        yaw_rate_deg_s: Optional[float] = None,
    ) -> dict[str, Any]:
        """配置服务端权威手动速度并返回控制状态。

        ``horizontal_speed_m_s`` 和 ``vertical_speed_m_s`` 单位为米/秒，
        ``yaw_rate_deg_s`` 单位为度/秒；省略的值保持不变。
        """

        return self._control_action(
            "manual.configure",
            horizontal_speed_m_s=horizontal_speed_m_s,
            vertical_speed_m_s=vertical_speed_m_s,
            yaw_rate_deg_s=yaw_rate_deg_s,
        )

    def submit_command(
        self,
        command: str,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """提交一次标准离散命令并返回完成结果。

        ``command`` 是标准命令名，``params`` 是该命令的标准参数。首版协议在同一个请求中等待核心
        SDK 返回；响应中的 ``response.ok`` 只遵循对应标准命令当前的完成语义，不能自动推断为真机
        物理动作已完成。
        """

        return self._control_action("command.submit", command=command, params=params or {})

    def get_device_state(self, device_id: str) -> dict[str, Any]:
        """读取 ``device_id`` 的最新标准状态和诊断快照并返回协议消息。"""

        return self._request_telemetry("device.state.get", device_id=device_id)

    def list_sensors(self, device_id: str) -> dict[str, Any]:
        """发现并返回 ``device_id`` 的标准传感器描述。"""

        return self._request_telemetry("sensor.list", device_id=device_id)

    def subscribe_sensor(
        self,
        device_id: str,
        sensor_id: str,
        *,
        max_rate_hz: float = 20.0,
    ) -> str:
        """订阅一路标准传感器并返回订阅ID。

        ``device_id`` 是服务端设备ID，``sensor_id`` 是标准传感器ID。样本通过
        ``receive_telemetry()`` 接收；``max_rate_hz`` 单位为赫兹，用于服务端限频。
        """

        response = self._request_telemetry(
            "sensor.subscribe",
            device_id=device_id,
            sensor_id=sensor_id,
            max_rate_hz=max_rate_hz,
        )
        return str(response["subscription_id"])

    def unsubscribe_sensor(self, device_id: str, subscription_id: str) -> None:
        """取消 ``device_id`` 下的 ``subscription_id`` 远程传感器订阅，无返回值。"""

        self._request_telemetry(
            "sensor.unsubscribe",
            device_id=device_id,
            subscription_id=subscription_id,
        )

    def receive_telemetry(self, timeout_s: Optional[float] = None) -> dict[str, Any]:
        """等待并返回一条服务端主动推送的传感器消息。

        ``timeout_s`` 单位为秒，超时行为由WebSocket客户端库以 ``TimeoutError`` 暴露。
        """

        connection = self._require_telemetry()
        with self._telemetry_lock:
            if self._pending_telemetry:
                return self._pending_telemetry.popleft()
        raw_message = connection.recv(timeout=timeout_s)
        return self._decode_response(raw_message)

    def release_control(self) -> None:
        """停止心跳、释放全部动作和当前控制租约；没有租约时安全返回。"""

        control_id = self._control_id
        device_id = self._device_id
        if control_id is None or device_id is None:
            return
        self._stop_heartbeat()
        try:
            self._control_action("manual.release_all")
        finally:
            try:
                self._request_control(
                    "control.release",
                    control_id=control_id,
                    device_id=device_id,
                )
            finally:
                self._control_id = None
                self._device_id = None

    def close(self) -> None:
        """幂等释放控制权并关闭控制和遥测WebSocket连接，无返回值。"""

        try:
            self.release_control()
        finally:
            self._stop_heartbeat()
            control, self._control = self._control, None
            telemetry, self._telemetry = self._telemetry, None
            self._pending_telemetry.clear()
            if telemetry is not None:
                telemetry.close()
            if control is not None:
                control.close()

    def __enter__(self) -> "FleetDockClient":
        """返回已构造客户端；调用方仍需显式调用 ``connect()``。"""

        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        """退出上下文时释放控制权和网络连接。"""

        self.close()

    def _control_action(self, message_type: str, **payload: Any) -> dict[str, Any]:
        if self._control_id is None or self._device_id is None:
            raise RuntimeError("No device control lease is active.")
        with self._control_lock:
            self._sequence += 1
            return self._request(
                self._require_control(),
                message_type,
                {
                    "control_id": self._control_id,
                    "device_id": self._device_id,
                    "sequence": self._sequence,
                    **payload,
                },
            )

    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.wait(self._heartbeat_interval_s):
            try:
                self._control_action("manual.heartbeat")
            except Exception:
                logger.debug("FleetDock WS 客户端心跳失败", exc_info=True)
                return

    def _stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()
        thread = self._heartbeat_thread
        self._heartbeat_thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def _request_control(self, message_type: str, **payload: Any) -> dict[str, Any]:
        connection = self._require_control()
        with self._control_lock:
            return self._request(connection, message_type, payload)

    def _request_telemetry(self, message_type: str, **payload: Any) -> dict[str, Any]:
        connection = self._require_telemetry()
        with self._telemetry_lock:
            request_id = f"req-{uuid4().hex}"
            message = {
                "version": PROTOCOL_VERSION,
                "type": message_type,
                "request_id": request_id,
                **{key: value for key, value in payload.items() if value is not None},
            }
            connection.send(json.dumps(message, ensure_ascii=False, separators=(",", ":")))
            while True:
                response = self._decode_response(connection.recv())
                if response.get("request_id") != request_id:
                    self._pending_telemetry.append(response)
                    continue
                if response.get("type") == "protocol.error":
                    raise RuntimeError(f"{response.get('code')}: {response.get('message')}")
                return response

    @staticmethod
    def _request(
        connection: ClientConnection,
        message_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        request_id = f"req-{uuid4().hex}"
        message = {
            "version": PROTOCOL_VERSION,
            "type": message_type,
            "request_id": request_id,
            **{key: value for key, value in payload.items() if value is not None},
        }
        connection.send(json.dumps(message, ensure_ascii=False, separators=(",", ":")))
        response = FleetDockClient._decode_response(connection.recv())
        if response.get("request_id") != request_id:
            raise RuntimeError("FleetDock response request_id does not match the request.")
        if response.get("type") == "protocol.error":
            raise RuntimeError(f"{response.get('code')}: {response.get('message')}")
        return response

    @staticmethod
    def _decode_response(raw_message: str | bytes) -> dict[str, Any]:
        if not isinstance(raw_message, str):
            raise RuntimeError("FleetDock server returned a non-text protocol message.")
        response = json.loads(raw_message)
        if not isinstance(response, dict) or response.get("version") != PROTOCOL_VERSION:
            raise RuntimeError("FleetDock server returned an invalid protocol message.")
        return response

    def _require_control(self) -> ClientConnection:
        if self._control is None:
            raise RuntimeError("FleetDock client is not connected.")
        return self._control

    def _require_telemetry(self) -> ClientConnection:
        if self._telemetry is None:
            raise RuntimeError("FleetDock client is not connected.")
        return self._telemetry
