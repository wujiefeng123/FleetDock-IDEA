"""通过 FleetDock WebSocket 服务工作的 Python/OpenCV 图形控制器。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import logging
from queue import Empty, Queue
from threading import Event, RLock, Thread
import time
from typing import Any, Callable, Optional

from fleetdock import ImageEncoding, ImageFrame, SensorKind
from fleetdock_server.controller_ui import (
    _ACTION_LABELS,
    _Action,
    _ControllerSnapshot,
    _MotionInput,
    _OpenCvControllerDisplay,
)

from fleetdock_server.client import FleetDockClient
from fleetdock_server.control import ControlMode, ManualAction
logger = logging.getLogger(__name__)
_MOTION_MAP = {
    _Action.FORWARD: ManualAction.FORWARD,
    _Action.BACK: ManualAction.BACKWARD,
    _Action.LEFT: ManualAction.LEFT,
    _Action.RIGHT: ManualAction.RIGHT,
    _Action.UP: ManualAction.ASCEND,
    _Action.DOWN: ManualAction.DESCEND,
    _Action.YAW_LEFT: ManualAction.YAW_LEFT,
    _Action.YAW_RIGHT: ManualAction.YAW_RIGHT,
}
_COMMAND_MAP: dict[_Action, tuple[str, dict[str, Any]]] = {
    _Action.CONNECT: ("connect", {}),
    _Action.UNLOCK: ("unlock", {}),
    _Action.FORCE_UNLOCK: ("unlock", {"force": True}),
    _Action.LOCK: ("lock", {}),
    _Action.TAKEOFF: ("takeoff", {"altitude": 5.0}),
    _Action.HOVER: ("hover", {}),
    _Action.LAND: ("land", {}),
    _Action.RTL: ("return_to_launch", {}),
    _Action.STOP: ("stop", {}),
    _Action.EMERGENCY_STOP: ("emergency_stop", {}),
}


@dataclass(frozen=True)
class _RemoteDeviceInfo:
    device_id: str
    product: str
    category: str
    supported_manual_actions: frozenset[str]
    supported_commands: frozenset[str]


class _RtspFrameReader:
    """在后台只保留RTSP最新帧，避免视频读取阻塞控制与心跳。"""

    def __init__(self, uri: str) -> None:
        self._uri = uri
        self._lock = RLock()
        self._stop = Event()
        self._frame: Optional[ImageFrame] = None
        self._fps: Optional[float] = None
        self._last_received_at_s: Optional[float] = None
        self._error: Optional[str] = None
        self._thread = Thread(target=self._run, name="fleetdock-controller-rtsp", daemon=True)

    def start(self) -> None:
        """启动RTSP读取线程，无返回值。"""

        self._thread.start()

    def snapshot(self) -> tuple[Optional[ImageFrame], Optional[float], Optional[str]]:
        """返回最新标准图像、接收帧率和错误摘要。"""

        with self._lock:
            return self._frame, self._fps, self._error

    def close(self) -> None:
        """请求读取线程退出并等待资源释放；重复调用安全。"""

        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def _run(self) -> None:
        try:
            import cv2
        except ImportError:
            with self._lock:
                self._error = 'RTSP画面需要安装 "fleetdock-server[vision]"。'
            return
        capture = cv2.VideoCapture(self._uri)
        sequence = 0
        try:
            if not capture.isOpened():
                raise RuntimeError("无法打开RTSP视频流。")
            while not self._stop.is_set():
                ok, frame = capture.read()
                if not ok:
                    if self._stop.wait(0.05):
                        break
                    continue
                now_s = time.monotonic()
                sequence += 1
                image = ImageFrame(
                    sensor_id="remote.camera",
                    kind=SensorKind.RGB_CAMERA,
                    sequence=sequence,
                    width=int(frame.shape[1]),
                    height=int(frame.shape[0]),
                    encoding=ImageEncoding.BGR8,
                    data=memoryview(frame.tobytes()),
                )
                with self._lock:
                    if self._last_received_at_s is not None:
                        interval_s = now_s - self._last_received_at_s
                        if 0.0 < interval_s <= 1.0:
                            instant_fps = 1.0 / interval_s
                            self._fps = instant_fps if self._fps is None else self._fps * 0.8 + instant_fps * 0.2
                    self._last_received_at_s = now_s
                    self._frame = image
        except Exception as exc:
            with self._lock:
                self._error = str(exc)
            logger.debug("Python WS控制器RTSP读取失败", exc_info=True)
        finally:
            capture.release()


class PythonController:
    """只通过FleetDock WebSocket协议控制设备的Python图形界面。

    ``endpoint`` 是服务根地址，``device_id`` 是服务端注册ID；``mode`` 明确仿真、台架或真机安全
    环境。``rtsp_uri`` 可选，视频使用独立RTSP读取线程，不占用控制或遥测WebSocket。界面关闭后会
    释放全部持续动作、控制权和网络连接，但不会直接调用设备SDK。
    """

    def __init__(
        self,
        endpoint: str,
        device_id: str,
        *,
        mode: ControlMode = ControlMode.LIVE,
        title: str = "FleetDock WS 调试控制器",
        rtsp_uri: Optional[str] = None,
        display_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.endpoint = endpoint
        self.device_id = device_id
        self.mode = mode
        self.title = title
        self.rtsp_uri = rtsp_uri
        self._display_factory = display_factory or _OpenCvControllerDisplay
        self._client = FleetDockClient(endpoint)
        self._device_info: Optional[_RemoteDeviceInfo] = None
        self._state: dict[str, Any] = {}
        self._diagnostics: dict[str, Any] = {}
        self._messages: list[str] = []
        self._motion_actions: frozenset[_Action] = frozenset()
        self._horizontal_speed_m_s = 3.0
        self._vertical_speed_m_s = 0.6
        self._yaw_rate_deg_s = 30.0
        self._stop = Event()
        self._command_queue: Queue[Optional[_Action]] = Queue()
        self._command_thread = Thread(
            target=self._command_worker,
            name="fleetdock-python-controller-command",
            daemon=True,
        )
        self._rtsp: Optional[_RtspFrameReader] = None

    def run(self) -> None:
        """连接WS服务、申请控制权并运行窗口，直到用户关闭或调用 ``close()``，无返回值。"""

        welcome = self._client.connect(device_id=self.device_id)
        info = welcome["devices"][0]
        self._device_info = _RemoteDeviceInfo(
            device_id=str(info["device_id"]),
            product=str(info["product"]),
            category=str(info["category"]),
            supported_manual_actions=frozenset(str(item) for item in info["supported_manual_actions"]),
            supported_commands=frozenset(str(item) for item in info["supported_commands"]),
        )
        self._client.acquire_control(self.device_id, mode=self.mode)
        if self.rtsp_uri:
            self._rtsp = _RtspFrameReader(self.rtsp_uri)
            self._rtsp.start()
        display = self._display_factory()
        display.open(self.title)
        self._command_thread.start()
        next_state_at_s = 0.0
        try:
            while not self._stop.is_set() and not display.should_close():
                now_s = time.monotonic()
                if now_s >= next_state_at_s:
                    self._refresh_state()
                    next_state_at_s = now_s + 0.2
                display.draw(self._snapshot())
                for event in display.poll(20):
                    if isinstance(event, _MotionInput):
                        self._update_motion(event.actions)
                    else:
                        self._handle_ui_action(event)
        finally:
            try:
                self.close()
            finally:
                display.close()

    def close(self) -> None:
        """幂等停止命令线程、视频读取和WS客户端，并释放全部控制输入，无返回值。"""

        if self._stop.is_set():
            return
        self._stop.set()
        self._command_queue.put(None)
        if self._command_thread.is_alive():
            self._command_thread.join(timeout=3.0)
        rtsp, self._rtsp = self._rtsp, None
        try:
            if rtsp is not None:
                rtsp.close()
        finally:
            self._client.close()

    def _handle_ui_action(self, action: _Action) -> None:
        if action is _Action.SPEED_UP:
            self._horizontal_speed_m_s = min(20.0, self._horizontal_speed_m_s + 0.5)
            self._client.configure_manual(horizontal_speed_m_s=self._horizontal_speed_m_s)
            return
        if action is _Action.SPEED_DOWN:
            self._horizontal_speed_m_s = max(0.5, self._horizontal_speed_m_s - 0.5)
            self._client.configure_manual(horizontal_speed_m_s=self._horizontal_speed_m_s)
            return
        if action is _Action.VERTICAL_SPEED_UP:
            self._vertical_speed_m_s = min(2.0, self._vertical_speed_m_s + 0.1)
            self._client.configure_manual(vertical_speed_m_s=self._vertical_speed_m_s)
            return
        if action is _Action.VERTICAL_SPEED_DOWN:
            self._vertical_speed_m_s = max(0.1, self._vertical_speed_m_s - 0.1)
            self._client.configure_manual(vertical_speed_m_s=self._vertical_speed_m_s)
            return
        if action is _Action.RELEASE:
            self._update_motion(frozenset())
            return
        self._command_queue.put(action)

    def _update_motion(self, actions: frozenset[_Action]) -> None:
        pressed = actions.difference(self._motion_actions)
        released = self._motion_actions.difference(actions)
        try:
            for action in sorted(released, key=lambda item: item.value):
                self._client.release(_MOTION_MAP[action])
            for action in sorted(pressed, key=lambda item: item.value):
                self._client.press(_MOTION_MAP[action])
            self._motion_actions = actions
        except Exception as exc:
            self._motion_actions = frozenset()
            try:
                self._client.release_all()
            except Exception:
                logger.debug("WS控制器释放输入失败", exc_info=True)
            self._append_message(f"运动控制失败：{exc}")

    def _command_worker(self) -> None:
        while not self._stop.is_set():
            try:
                action = self._command_queue.get(timeout=0.1)
            except Empty:
                continue
            if action is None:
                return
            try:
                command, params = _COMMAND_MAP[action]
                result = self._client.submit_command(command, params)
                response = result["response"]
                marker = "成功" if response["ok"] else str(response["code"])
                suffix = f" - {response['message']}" if response.get("message") else ""
                self._append_message(f"{_ACTION_LABELS[action]}：{marker}{suffix}")
            except Exception as exc:
                self._append_message(f"{_ACTION_LABELS.get(action, action.value)}：{exc}")

    def _refresh_state(self) -> None:
        try:
            message = self._client.get_device_state(self.device_id)
            self._state = dict(message["state"])
            self._diagnostics = dict(message["diagnostics"])
        except Exception as exc:
            self._append_message(f"状态读取失败：{exc}")

    def _snapshot(self) -> _ControllerSnapshot:
        info = self._device_info
        if info is None:
            raise RuntimeError("Python controller is not connected.")
        state = self._state
        diagnostic = self._diagnostics
        enabled: set[_Action] = {
            _Action.SPEED_UP,
            _Action.SPEED_DOWN,
            _Action.VERTICAL_SPEED_UP,
            _Action.VERTICAL_SPEED_DOWN,
        }
        supported_commands = info.supported_commands
        connected = bool(diagnostic.get("connected"))
        armed = state.get("armed")
        in_air = state.get("in_air")
        if not connected and "connect" in supported_commands:
            enabled.add(_Action.CONNECT)
        if connected and armed is not True and "unlock" in supported_commands:
            enabled.add(_Action.UNLOCK)
            if self.mode is not ControlMode.LIVE:
                enabled.add(_Action.FORCE_UNLOCK)
        if armed is True and in_air is not True:
            if "lock" in supported_commands:
                enabled.add(_Action.LOCK)
            if "takeoff" in supported_commands and self.mode is not ControlMode.BENCH:
                enabled.add(_Action.TAKEOFF)
        if in_air is True:
            for action, command in (
                (_Action.HOVER, "hover"),
                (_Action.LAND, "land"),
                (_Action.RTL, "return_to_launch"),
                (_Action.STOP, "stop"),
                (_Action.EMERGENCY_STOP, "emergency_stop"),
            ):
                if command in supported_commands:
                    enabled.add(action)
            enabled.update(
                ui_action
                for ui_action, manual_action in _MOTION_MAP.items()
                if manual_action.value in info.supported_manual_actions
            )
            enabled.add(_Action.RELEASE)
        camera = None
        camera_fps = None
        if self._rtsp is not None:
            camera, camera_fps, camera_error = self._rtsp.snapshot()
            if camera_error and not any(camera_error in message for message in self._messages):
                self._append_message(f"摄像头不可用：{camera_error}")
        battery = state.get("battery_remaining_fraction")
        return _ControllerSnapshot(
            device_id=info.device_id,
            product=info.product,
            connected=connected,
            armed=armed,
            in_air=in_air,
            flight_mode=state.get("flight_mode"),
            failsafe=state.get("failsafe"),
            battery_percent=None if battery is None else float(battery) * 100.0,
            relative_altitude_m=state.get("relative_altitude_m"),
            roll_deg=state.get("roll_deg"),
            pitch_deg=state.get("pitch_deg"),
            yaw_deg=state.get("yaw_deg"),
            telemetry_age_s=diagnostic.get("telemetry_age_s"),
            camera_fps=camera_fps,
            horizontal_speed_m_s=self._horizontal_speed_m_s,
            vertical_speed_m_s=self._vertical_speed_m_s,
            yaw_rate_deg_s=self._yaw_rate_deg_s,
            manual_active=bool(self._motion_actions),
            camera=camera,
            messages=tuple(self._messages[-6:]),
            enabled=frozenset(enabled),
        )

    def _append_message(self, message: str) -> None:
        self._messages.append(message)
        if len(self._messages) > 50:
            del self._messages[:-50]


def main() -> None:
    """解析命令行参数并启动纯WS Python图形控制器，无返回值。"""

    parser = argparse.ArgumentParser(description="FleetDock WebSocket Python controller")
    parser.add_argument("--endpoint", required=True, help="FleetDock WS服务根地址")
    parser.add_argument("--device", required=True, help="服务端设备ID")
    parser.add_argument("--mode", choices=[item.value for item in ControlMode], default=ControlMode.LIVE.value)
    parser.add_argument("--rtsp-uri", help="可选RTSP视频地址")
    arguments = parser.parse_args()
    PythonController(
        arguments.endpoint,
        arguments.device,
        mode=ControlMode(arguments.mode),
        rtsp_uri=arguments.rtsp_uri,
    ).run()


__all__ = ["PythonController", "main"]
