"""FleetDock 标准设备能力的同步 WebSocket 服务。"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from threading import Event, RLock, Thread
import time
from typing import Any, Callable, Optional
from uuid import uuid4

from fleetdock import CapabilityName, CommandName, SensorDeliveryMode, SensorStreamOptions
from websockets.sync.server import Server, ServerConnection, serve

from fleetdock_server.control import (
    ControlMode,
    InteractiveControlOptions,
    InteractiveControlSession,
    ManualAction,
    _manual_actions_for_device,
)
from fleetdock_server.serialization import to_json_value


logger = logging.getLogger(__name__)
PROTOCOL_VERSION = 1


@dataclass
class _ConnectionContext:
    connection: ServerConnection
    connection_id: str = field(default_factory=lambda: f"conn-{uuid4().hex}")
    send_lock: RLock = field(default_factory=RLock)
    subscriptions: dict[str, Callable[[], None]] = field(default_factory=dict)

    def send(self, message: dict[str, Any]) -> None:
        with self.send_lock:
            self.connection.send(json.dumps(message, ensure_ascii=False, separators=(",", ":")))

    def close_subscriptions(self) -> None:
        while self.subscriptions:
            _subscription_id, unsubscribe = self.subscriptions.popitem()
            try:
                unsubscribe()
            except Exception:
                logger.debug("关闭远程传感器订阅失败", exc_info=True)


@dataclass
class _ControlLease:
    control_id: str
    connection_id: str
    device_id: str
    session: InteractiveControlSession
    last_sequence: int = 0
    last_seen_at_s: float = field(default_factory=time.monotonic)


class FleetDockServer:
    """在一个端口发布 FleetDock 控制与遥测 WebSocket 服务。

    ``/ws/control`` 处理控制权、持续动作、心跳和离散命令；``/ws/telemetry`` 处理设备状态及
    标准传感器发现、订阅和取消。服务端独占注册设备的交互控制权；``close_devices=True`` 时关闭
    服务会同时关闭全部设备。
    """

    def __init__(
        self,
        *,
        close_devices: bool = True,
        max_message_bytes: int = 1_048_576,
        lease_timeout_s: float = 2.0,
    ) -> None:
        """配置设备所有权、消息上限和控制租约超时。

        ``close_devices`` 决定服务关闭时是否关闭托管设备；``max_message_bytes`` 是单条WS消息的
        字节上限。``lease_timeout_s`` 单位为秒，控制连接在此期间没有任何有效序列消息时自动释放
        租约和手动控制，必须是有限正数。
        """

        if max_message_bytes <= 0:
            raise ValueError("max_message_bytes must be greater than zero.")
        if not math_is_positive_finite(lease_timeout_s):
            raise ValueError("lease_timeout_s must be finite and greater than zero.")
        self._lock = RLock()
        self._devices: dict[str, Any] = {}
        self._leases_by_device: dict[str, _ControlLease] = {}
        self._leases_by_id: dict[str, _ControlLease] = {}
        self._server: Optional[Server] = None
        self._thread: Optional[Thread] = None
        self._lease_thread: Optional[Thread] = None
        self._lease_stop = Event()
        self._host = ""
        self._port = 0
        self._close_devices = close_devices
        self._max_message_bytes = max_message_bytes
        self._lease_timeout_s = float(lease_timeout_s)

    @property
    def endpoint(self) -> str:
        """返回服务监听根地址；服务尚未启动时抛出 ``RuntimeError``。"""

        if not self._port:
            raise RuntimeError("FleetDock server is not running.")
        return f"ws://{self._host}:{self._port}"

    def register(self, device: Any, *, device_id: Optional[str] = None) -> str:
        """注册一个由服务端托管的标准设备并返回稳定设备ID。

        ``device_id`` 省略时优先使用设备元数据ID，否则使用实例ID。重复ID、服务启动后注册或已经关闭
        的服务都会抛出异常。调用方在服务运行期间不得再直接控制同一设备。
        """

        selected = str(device_id or device.metadata.device_id or device.instance_id)
        with self._lock:
            if self._server is not None:
                raise RuntimeError("Devices must be registered before the server starts.")
            if selected in self._devices:
                raise ValueError(f"Duplicate device_id: {selected}")
            self._devices[selected] = device
        logger.debug("WS 服务已注册设备: device_id=%s product=%s", selected, device.product_key)
        return selected

    def start(self, host: str = "127.0.0.1", port: int = 0) -> str:
        """在后台线程启动服务并返回根WebSocket地址。

        ``host`` 默认仅监听本机；``port=0`` 时由操作系统选择空闲端口。重复启动会返回现有地址。
        """

        with self._lock:
            if self._server is not None:
                return self.endpoint
            websocket_server = serve(
                self._handle_connection,
                host,
                port,
                max_size=self._max_message_bytes,
                compression=None,
            )
            self._server = websocket_server
            actual_host, actual_port = websocket_server.socket.getsockname()[:2]
            self._host = host if host not in {"0.0.0.0", "::"} else str(actual_host)
            self._port = int(actual_port)
            self._thread = Thread(
                target=websocket_server.serve_forever,
                name="fleetdock-ws-server",
                daemon=True,
            )
            self._thread.start()
            self._start_lease_watchdog()
        logger.debug("FleetDock WS 服务已启动: endpoint=%s", self.endpoint)
        return self.endpoint

    def run(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        """在当前线程运行服务，直到调用 ``close()`` 或进程退出，无返回值。

        ``host`` 是监听地址，默认仅允许本机访问；``port`` 是TCP监听端口。
        """

        with self._lock:
            if self._server is not None:
                raise RuntimeError("FleetDock server is already running.")
            websocket_server = serve(
                self._handle_connection,
                host,
                port,
                max_size=self._max_message_bytes,
                compression=None,
            )
            self._server = websocket_server
            actual_host, actual_port = websocket_server.socket.getsockname()[:2]
            self._host = host if host not in {"0.0.0.0", "::"} else str(actual_host)
            self._port = int(actual_port)
        logger.debug("FleetDock WS 服务开始阻塞运行: endpoint=%s", self.endpoint)
        self._start_lease_watchdog()
        websocket_server.serve_forever()

    def close(self) -> None:
        """停止WS服务、关闭全部控制租约和订阅，并按配置关闭托管设备，无返回值。"""

        with self._lock:
            websocket_server = self._server
            self._server = None
            leases = list(self._leases_by_id.values())
            self._leases_by_id.clear()
            self._leases_by_device.clear()
            devices = list(self._devices.values())
            thread = self._thread
            self._thread = None
            lease_thread = self._lease_thread
            self._lease_thread = None
            self._lease_stop.set()
        for lease in leases:
            lease.session.close()
        if websocket_server is not None:
            websocket_server.shutdown()
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        if lease_thread is not None and lease_thread.is_alive():
            lease_thread.join(timeout=3.0)
        if self._close_devices:
            for device in reversed(devices):
                try:
                    device.close()
                except Exception:
                    logger.debug("关闭WS服务托管设备失败", exc_info=True)
        self._port = 0
        logger.debug("FleetDock WS 服务已关闭")

    def __enter__(self) -> "FleetDockServer":
        """返回当前服务；调用方仍需显式调用 ``start()`` 指定监听地址。"""

        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        """退出上下文时关闭服务和托管资源。"""

        self.close()

    def _handle_connection(self, connection: ServerConnection) -> None:
        context = _ConnectionContext(connection)
        path = connection.request.path if connection.request is not None else ""
        if path not in {"/ws/control", "/ws/telemetry"}:
            context.send(self._error(None, "UNKNOWN_PATH", f"Unsupported WebSocket path: {path}"))
            connection.close(code=1008, reason="unsupported path")
            return
        logger.debug("WS 客户端已连接: connection_id=%s path=%s", context.connection_id, path)
        try:
            for raw_message in connection:
                try:
                    message = self._decode_message(raw_message)
                    response = (
                        self._handle_control(context, message)
                        if path == "/ws/control"
                        else self._handle_telemetry(context, message)
                    )
                except Exception as exc:
                    request_id = message.get("request_id") if "message" in locals() else None
                    logger.debug("处理WS消息失败: connection_id=%s", context.connection_id, exc_info=True)
                    response = self._error(request_id, self._error_code(exc), str(exc))
                if response is not None:
                    context.send(response)
        finally:
            context.close_subscriptions()
            self._release_connection(context.connection_id)
            logger.debug("WS 客户端已断开: connection_id=%s path=%s", context.connection_id, path)

    @staticmethod
    def _decode_message(raw_message: str | bytes) -> dict[str, Any]:
        if not isinstance(raw_message, str):
            raise TypeError("Protocol messages must be UTF-8 JSON text.")
        message = json.loads(raw_message)
        if not isinstance(message, dict):
            raise TypeError("Protocol message must be a JSON object.")
        if message.get("version") != PROTOCOL_VERSION:
            raise ValueError(f"Unsupported protocol version: {message.get('version')}")
        if not isinstance(message.get("type"), str):
            raise ValueError("Protocol message type is required.")
        return message

    def _handle_control(
        self,
        context: _ConnectionContext,
        message: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        message_type = message["type"]
        request_id = message.get("request_id")
        if message_type == "session.hello":
            device_id = message.get("device_id")
            selected_devices = self._selected_devices(device_id)
            devices = [self._device_info(key, value) for key, value in selected_devices]
            for key, device in selected_devices:
                subscription_key = f"state-{key}"
                if subscription_key in context.subscriptions:
                    continue
                acquire = getattr(device, "_acquire_capability_telemetry", None)
                if callable(acquire):
                    context.subscriptions[subscription_key] = acquire(CapabilityName.STATUS)
            return self._response("session.welcome", request_id, connection_id=context.connection_id, devices=devices)
        if message_type == "control.acquire":
            device_id = self._required_string(message, "device_id")
            mode = ControlMode(message.get("mode", ControlMode.LIVE.value))
            options = InteractiveControlOptions(mode=mode)
            lease = self._acquire(context.connection_id, device_id, options)
            return self._response(
                "control.acquired",
                request_id,
                device_id=device_id,
                control_id=lease.control_id,
                heartbeat_interval_ms=100,
                input_timeout_ms=int(options.input_timeout_s * 1000),
                horizontal_speed_m_s=options.horizontal_speed_m_s,
                vertical_speed_m_s=options.vertical_speed_m_s,
                yaw_rate_deg_s=options.yaw_rate_deg_s,
            )
        if message_type == "control.release":
            lease = self._lease_for(context, message)
            self._release_lease(lease)
            return self._response("control.released", request_id, device_id=lease.device_id)

        lease = self._lease_for(context, message)
        self._accept_sequence(lease, message)
        if message_type == "manual.press":
            lease.session.press(ManualAction(self._required_string(message, "action")))
            return self._control_state(request_id, lease)
        if message_type == "manual.release":
            lease.session.release(ManualAction(self._required_string(message, "action")))
            return self._control_state(request_id, lease)
        if message_type == "manual.release_all":
            lease.session.release_all()
            return self._control_state(request_id, lease)
        if message_type == "manual.heartbeat":
            lease.session.heartbeat()
            return self._response("manual.heartbeat.ack", request_id, device_id=lease.device_id)
        if message_type == "manual.configure":
            lease.session.set_speeds(
                horizontal_speed_m_s=message.get("horizontal_speed_m_s"),
                vertical_speed_m_s=message.get("vertical_speed_m_s"),
                yaw_rate_deg_s=message.get("yaw_rate_deg_s"),
            )
            return self._control_state(request_id, lease)
        if message_type == "command.submit":
            command = CommandName(self._required_string(message, "command"))
            params = message.get("params") or {}
            if not isinstance(params, dict):
                raise TypeError("command params must be a JSON object.")
            response = lease.session.execute(command, params)
            return self._response(
                "command.completed",
                request_id,
                device_id=lease.device_id,
                command=command.value,
                status="succeeded" if response.ok else "failed",
                response=to_json_value(response),
            )
        raise ValueError(f"Unsupported control message type: {message_type}")

    def _handle_telemetry(
        self,
        context: _ConnectionContext,
        message: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        message_type = message["type"]
        request_id = message.get("request_id")
        if message_type == "session.hello":
            device_id = message.get("device_id")
            devices = [self._device_info(key, value) for key, value in self._selected_devices(device_id)]
            return self._response("session.welcome", request_id, connection_id=context.connection_id, devices=devices)
        device_id = self._required_string(message, "device_id")
        device = self._device(device_id)
        if message_type == "device.state.get":
            return self._response(
                "device.state",
                request_id,
                device_id=device_id,
                state=to_json_value(device.state.snapshot()),
                diagnostics=to_json_value(device.diagnostics()),
            )
        if message_type == "sensor.list":
            return self._response(
                "sensor.list.result",
                request_id,
                device_id=device_id,
                sensors=to_json_value(device.sensors.list()),
            )
        if message_type == "sensor.subscribe":
            sensor_id = self._required_string(message, "sensor_id")
            max_rate_hz = float(message.get("max_rate_hz", 20.0))
            if not math_is_positive_finite(max_rate_hz):
                raise ValueError("max_rate_hz must be finite and greater than zero.")
            subscription_id = f"sensor-{uuid4().hex}"

            def on_sample(sample: Any) -> None:
                context.send(
                    self._response(
                        "sensor.sample",
                        None,
                        subscription_id=subscription_id,
                        device_id=device_id,
                        sample=to_json_value(sample),
                    )
                )

            unsubscribe = device.sensors.subscribe(
                sensor_id,
                on_sample,
                options=SensorStreamOptions(
                    delivery=SensorDeliveryMode.LATEST_ONLY,
                    capacity=1,
                    max_rate_hz=max_rate_hz,
                ),
            )
            context.subscriptions[subscription_id] = unsubscribe
            return self._response(
                "sensor.subscribed",
                request_id,
                device_id=device_id,
                sensor_id=sensor_id,
                subscription_id=subscription_id,
            )
        if message_type == "sensor.unsubscribe":
            subscription_id = self._required_string(message, "subscription_id")
            unsubscribe = context.subscriptions.pop(subscription_id, None)
            if unsubscribe is None:
                raise LookupError(f"Sensor subscription is not active: {subscription_id}")
            unsubscribe()
            return self._response(
                "sensor.unsubscribed",
                request_id,
                device_id=device_id,
                subscription_id=subscription_id,
            )
        raise ValueError(f"Unsupported telemetry message type: {message_type}")

    def _selected_devices(self, device_id: Any) -> list[tuple[str, Any]]:
        if device_id is None:
            with self._lock:
                return sorted(self._devices.items())
        selected = str(device_id)
        return [(selected, self._device(selected))]

    def _device_info(self, device_id: str, device: Any) -> dict[str, Any]:
        actions = sorted(action.value for action in _manual_actions_for_device(device))
        return {
            "device_id": device_id,
            "product": device.product_key,
            "category": device.profile.category.value,
            "maturity": device.profile.maturity.value,
            "supported_manual_actions": actions,
            "supported_commands": sorted(command.value for command in device.profile.supported_commands),
        }

    def _acquire(
        self,
        connection_id: str,
        device_id: str,
        options: InteractiveControlOptions,
    ) -> _ControlLease:
        device = self._device(device_id)
        with self._lock:
            if device_id in self._leases_by_device:
                raise PermissionError(f"Device control is already acquired: {device_id}")
            lease = _ControlLease(
                control_id=f"control-{uuid4().hex}",
                connection_id=connection_id,
                device_id=device_id,
                session=InteractiveControlSession(device, options),
            )
            self._leases_by_device[device_id] = lease
            self._leases_by_id[lease.control_id] = lease
        logger.debug("WS 控制权已授予: device_id=%s connection_id=%s", device_id, connection_id)
        return lease

    def _lease_for(self, context: _ConnectionContext, message: dict[str, Any]) -> _ControlLease:
        control_id = self._required_string(message, "control_id")
        with self._lock:
            lease = self._leases_by_id.get(control_id)
        if lease is None:
            raise LookupError(f"Control lease is not active: {control_id}")
        if lease.connection_id != context.connection_id:
            raise PermissionError("Control lease belongs to another connection.")
        if message.get("device_id") != lease.device_id:
            raise PermissionError("Control lease does not match device_id.")
        return lease

    @staticmethod
    def _accept_sequence(lease: _ControlLease, message: dict[str, Any]) -> None:
        sequence = message.get("sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= 0:
            raise ValueError("Control sequence must be a positive integer.")
        if sequence <= lease.last_sequence:
            raise ValueError(f"Control sequence is stale: {sequence}")
        lease.last_sequence = sequence
        lease.last_seen_at_s = time.monotonic()

    def _start_lease_watchdog(self) -> None:
        with self._lock:
            if self._lease_thread is not None and self._lease_thread.is_alive():
                return
            self._lease_stop.clear()
            self._lease_thread = Thread(
                target=self._lease_watchdog_loop,
                name="fleetdock-ws-lease-watchdog",
                daemon=True,
            )
            self._lease_thread.start()

    def _lease_watchdog_loop(self) -> None:
        interval_s = min(0.25, self._lease_timeout_s * 0.25)
        while not self._lease_stop.wait(interval_s):
            now_s = time.monotonic()
            with self._lock:
                expired = [
                    lease
                    for lease in self._leases_by_id.values()
                    if now_s - lease.last_seen_at_s > self._lease_timeout_s
                ]
            for lease in expired:
                self._release_lease(lease)
                logger.debug(
                    "WS 控制租约已因心跳超时释放: device_id=%s control_id=%s timeout_s=%.3f",
                    lease.device_id,
                    lease.control_id,
                    self._lease_timeout_s,
                )

    def _release_connection(self, connection_id: str) -> None:
        with self._lock:
            leases = [lease for lease in self._leases_by_id.values() if lease.connection_id == connection_id]
        for lease in leases:
            self._release_lease(lease)

    def _release_lease(self, lease: _ControlLease) -> None:
        with self._lock:
            current = self._leases_by_id.pop(lease.control_id, None)
            if current is None:
                return
            self._leases_by_device.pop(lease.device_id, None)
        lease.session.close()
        logger.debug("WS 控制权已释放: device_id=%s connection_id=%s", lease.device_id, lease.connection_id)

    def _device(self, device_id: str) -> Any:
        with self._lock:
            device = self._devices.get(device_id)
        if device is None:
            raise LookupError(f"Device is not registered: {device_id}")
        return device

    def _control_state(self, request_id: Any, lease: _ControlLease) -> dict[str, Any]:
        return self._response(
            "control.state",
            request_id,
            device_id=lease.device_id,
            control_id=lease.control_id,
            state=to_json_value(lease.session.snapshot()),
        )

    @staticmethod
    def _required_string(message: dict[str, Any], name: str) -> str:
        value = message.get(name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} is required.")
        return value

    @staticmethod
    def _response(message_type: str, request_id: Any, **payload: Any) -> dict[str, Any]:
        response = {"version": PROTOCOL_VERSION, "type": message_type, **payload}
        if request_id is not None:
            response["request_id"] = request_id
        return response

    @classmethod
    def _error(cls, request_id: Any, code: str, message: str) -> dict[str, Any]:
        return cls._response("protocol.error", request_id, code=code, message=message)

    @staticmethod
    def _error_code(exc: Exception) -> str:
        if isinstance(exc, PermissionError):
            return "PERMISSION_DENIED"
        if isinstance(exc, LookupError):
            return "NOT_FOUND"
        if isinstance(exc, (TypeError, ValueError)):
            return "INVALID_MESSAGE"
        return "INTERNAL_ERROR"


def math_is_positive_finite(value: float) -> bool:
    """返回协议 ``value`` 是否为有限正数。"""

    import math

    return math.isfinite(value) and value > 0.0
