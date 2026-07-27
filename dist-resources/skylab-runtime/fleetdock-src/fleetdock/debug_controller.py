"""基于公共 SDK 的单设备图形调试控制器。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
import os
from queue import Empty, Queue
import sys
from threading import Event, RLock, Thread
import time
from typing import Any, Callable, Optional, Protocol

from fleetdock._debug_ui import (
    _DebugTextRenderer,
    _restore_dpi_context_on_windows,
    _set_unicode_window_title_on_windows,
    _use_physical_pixels_on_windows,
)
from fleetdock.manual_control import ManualControlOptions, ManualControlSession
from fleetdock.models import CapabilityName, StandardResponse
from fleetdock.sensors import ImageFrame, SensorKind, SensorSample, StereoFrame


logger = logging.getLogger(__name__)

_DEFAULT_HORIZONTAL_SPEED_M_S = 3.0
_MAX_HORIZONTAL_SPEED_M_S = 20.0
_MOTION_INPUT_REFRESH_INTERVAL_S = 0.2
_DISPLAY_FRAME_INTERVAL_S = 1.0 / 30.0
_VK_ESCAPE = 0x1B
_CLOSE_VIRTUAL_KEYS = frozenset({_VK_ESCAPE, ord("X")})
_SYSTEM_VIRTUAL_KEYS = frozenset({0x12, 0x5B, 0x5C})  # Alt、左/右 Windows 键。


class _Action(str, Enum):
    CONNECT = "connect"
    UNLOCK = "unlock"
    FORCE_UNLOCK = "force_unlock"
    LOCK = "lock"
    TAKEOFF = "takeoff"
    HOVER = "hover"
    LAND = "land"
    RTL = "rtl"
    STOP = "stop"
    EMERGENCY_STOP = "emergency_stop"
    SPEED_UP = "speed_up"
    SPEED_DOWN = "speed_down"
    ALTITUDE_UP = "altitude_up"
    ALTITUDE_DOWN = "altitude_down"
    FORWARD = "forward"
    BACK = "back"
    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"
    YAW_LEFT = "yaw_left"
    YAW_RIGHT = "yaw_right"
    RELEASE = "release"


_ACTION_LABELS = {
    _Action.CONNECT: "连接",
    _Action.UNLOCK: "解锁",
    _Action.FORCE_UNLOCK: "台架强制解锁",
    _Action.LOCK: "锁定",
    _Action.TAKEOFF: "起飞",
    _Action.HOVER: "悬停",
    _Action.LAND: "降落",
    _Action.RTL: "返航",
    _Action.STOP: "停止",
    _Action.EMERGENCY_STOP: "紧急停止",
    _Action.SPEED_UP: "速度增加",
    _Action.SPEED_DOWN: "速度降低",
    _Action.ALTITUDE_UP: "高度增加",
    _Action.ALTITUDE_DOWN: "高度降低",
    _Action.FORWARD: "前进",
    _Action.BACK: "后退",
    _Action.LEFT: "左移",
    _Action.RIGHT: "右移",
    _Action.UP: "上升",
    _Action.DOWN: "下降",
    _Action.YAW_LEFT: "左偏航",
    _Action.YAW_RIGHT: "右偏航",
    _Action.RELEASE: "松开控制",
}


_MOTION_ACTIONS = {
    _Action.FORWARD,
    _Action.BACK,
    _Action.LEFT,
    _Action.RIGHT,
    _Action.UP,
    _Action.DOWN,
    _Action.YAW_LEFT,
    _Action.YAW_RIGHT,
}


@dataclass(frozen=True)
class _MotionInput:
    """一次完整的多轴运动输入快照；集合为空表示所有运动键均已松开。"""

    actions: frozenset[_Action]


_ControlEvent = _Action | _MotionInput


_VK_LBUTTON = 0x01
_VK_CONTROL = 0x11
_KEYBOARD_MOTION_BINDINGS = (
    ((ord("W"), 0x26), _Action.FORWARD),
    ((ord("S"), 0x28), _Action.BACK),
    ((ord("A"), 0x25), _Action.LEFT),
    ((ord("D"), 0x27), _Action.RIGHT),
    ((ord("R"), 0x21), _Action.UP),
    ((ord("F"), 0x22), _Action.DOWN),
    ((ord("Q"),), _Action.YAW_LEFT),
    ((ord("E"),), _Action.YAW_RIGHT),
)
_KEYBOARD_COMMAND_BINDINGS = {
    ord("C"): _Action.CONNECT,
    ord("U"): _Action.UNLOCK,
    ord("L"): _Action.LOCK,
    0x20: _Action.TAKEOFF,
    ord("H"): _Action.HOVER,
    _VK_CONTROL: _Action.LAND,
    ord("T"): _Action.RTL,
    ord("Z"): _Action.STOP,
    0xBD: _Action.SPEED_DOWN,
    0xBB: _Action.SPEED_UP,
    0xDB: _Action.ALTITUDE_DOWN,
    0xDD: _Action.ALTITUDE_UP,
}
_MONITORED_VIRTUAL_KEYS = frozenset(
    {_VK_LBUTTON, *_KEYBOARD_COMMAND_BINDINGS}
    | {key for keys, _action in _KEYBOARD_MOTION_BINDINGS for key in keys}
    | _CLOSE_VIRTUAL_KEYS
)


def _windows_keyboard_capture() -> Optional[
    tuple[Callable[[], Optional[frozenset[int]]], Callable[[], None]]
]:
    """在线程中截获当前控制器窗口的 Windows 按键，并返回读取和关闭函数。"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        keyboard_callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        class KeyboardEvent(ctypes.Structure):
            _fields_ = [
                ("virtual_key", wintypes.DWORD),
                ("scan_code", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("extra_info", ctypes.c_void_p),
            ]

        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = wintypes.SHORT
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            keyboard_callback_type,
            wintypes.HINSTANCE,
            wintypes.DWORD,
        ]
        user32.SetWindowsHookExW.restype = wintypes.HANDLE
        user32.CallNextHookEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t

        pressed: set[int] = set()
        pressed_lock = RLock()
        ready = Event()
        stop = Event()
        hook_state: dict[str, Any] = {"handle": None, "thread_id": 0, "failed": False}
        key_messages = {0x0100: True, 0x0104: True, 0x0101: False, 0x0105: False}

        def belongs_to_controller() -> bool:
            foreground = user32.GetForegroundWindow()
            if not foreground:
                return False
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(foreground, ctypes.byref(process_id))
            return process_id.value == os.getpid()

        def keyboard_hook(
            code: int,
            message: int,
            event_pointer: int,
        ) -> int:
            if code >= 0 and message in key_messages and belongs_to_controller():
                event = ctypes.cast(
                    event_pointer,
                    ctypes.POINTER(KeyboardEvent),
                ).contents
                virtual_key = int(event.virtual_key)
                if virtual_key in _MONITORED_VIRTUAL_KEYS:
                    with pressed_lock:
                        if key_messages[message]:
                            pressed.add(virtual_key)
                        else:
                            pressed.discard(virtual_key)
                is_system_shortcut = bool(event.flags & 0x20) or (
                    virtual_key in _SYSTEM_VIRTUAL_KEYS
                )
                if not is_system_shortcut:
                    return 1
            return int(user32.CallNextHookEx(None, code, message, event_pointer))

        hook_callback = keyboard_callback_type(keyboard_hook)

        def run_hook() -> None:
            hook_state["thread_id"] = int(ctypes.windll.kernel32.GetCurrentThreadId())
            hook = user32.SetWindowsHookExW(13, hook_callback, None, 0)
            hook_state["handle"] = hook
            hook_state["failed"] = not bool(hook)
            ready.set()
            if not hook:
                return
            message = wintypes.MSG()
            try:
                while not stop.is_set() and user32.GetMessageW(
                    ctypes.byref(message), None, 0, 0
                ) > 0:
                    user32.TranslateMessage(ctypes.byref(message))
                    user32.DispatchMessageW(ctypes.byref(message))
            finally:
                user32.UnhookWindowsHookEx(hook)

        hook_thread = Thread(
            target=run_hook,
            name="fleetdock-debug-controller-keyboard",
            daemon=True,
        )
        hook_thread.start()
        if not ready.wait(2.0) or hook_state["failed"]:
            stop.set()
            return None

        def read() -> Optional[frozenset[int]]:
            if not belongs_to_controller():
                with pressed_lock:
                    pressed.clear()
                return None
            with pressed_lock:
                snapshot = frozenset(pressed)
            if user32.GetAsyncKeyState(_VK_LBUTTON) & 0x8000:
                snapshot |= {_VK_LBUTTON}
            return snapshot

        def close() -> None:
            stop.set()
            thread_id = int(hook_state["thread_id"])
            if thread_id:
                user32.PostThreadMessageW(thread_id, 0x0012, 0, 0)
            if hook_thread.is_alive():
                hook_thread.join(timeout=2.0)

        return read, close
    except (AttributeError, OSError, TypeError, ValueError):
        logger.debug("初始化 Windows 控制器键盘截获线程失败", exc_info=True)
        return None


@dataclass(frozen=True)
class _ControllerSnapshot:
    device_id: str
    product: str
    connected: bool
    armed: Optional[bool]
    in_air: Optional[bool]
    flight_mode: Optional[str]
    failsafe: Optional[bool]
    battery_percent: Optional[float]
    relative_altitude_m: Optional[float]
    roll_deg: Optional[float]
    pitch_deg: Optional[float]
    yaw_deg: Optional[float]
    telemetry_age_s: Optional[float]
    camera_fps: Optional[float]
    horizontal_speed_m_s: float
    vertical_speed_m_s: float
    yaw_rate_deg_s: float
    takeoff_altitude_m: float
    manual_active: bool
    camera: Optional[ImageFrame | StereoFrame]
    messages: tuple[str, ...]
    enabled: frozenset[_Action]


class _ControllerDisplay(Protocol):
    def open(self, title: str) -> None: ...

    def draw(self, snapshot: _ControllerSnapshot) -> None: ...

    def poll(self, delay_ms: int) -> tuple[_ControlEvent, ...]: ...

    def should_close(self) -> bool: ...

    def close(self) -> None: ...


ControllerDisplayFactory = Callable[[], _ControllerDisplay]


class _DebugController:
    """协调调试窗口、设备状态、传感器订阅和公共 SDK 命令。"""

    def __init__(
        self,
        device: Any,
        *,
        title: str,
        camera: bool,
        camera_subscribe: Optional[
            Callable[[Callable[[SensorSample], None]], Callable[[], None]]
        ] = None,
        display_factory: Optional[ControllerDisplayFactory] = None,
    ) -> None:
        self._device = device
        self._title = title
        self._camera_enabled = camera
        self._camera_subscribe = camera_subscribe
        self._display_factory = display_factory or _OpenCvControllerDisplay
        self._stop = Event()
        self._lock = RLock()
        self._messages: list[str] = []
        self._camera: Optional[ImageFrame | StereoFrame] = None
        self._camera_fps: Optional[float] = None
        self._last_camera_received_at_s: Optional[float] = None
        self._camera_unsubscribe: Optional[Callable[[], None]] = None
        self._manual: Optional[ManualControlSession] = None
        self._motion_actions: frozenset[_Action] = frozenset()
        self._pending_motion_actions: Optional[frozenset[_Action]] = None
        self._next_motion_input_at_s = 0.0
        self._horizontal_speed_m_s = _DEFAULT_HORIZONTAL_SPEED_M_S
        self._vertical_speed_m_s = 0.6
        self._yaw_rate_deg_s = 30.0
        self._takeoff_altitude_m = 5.0
        self._command_queue: Queue[Optional[_Action]] = Queue()
        self._worker = Thread(
            target=self._command_worker,
            name=f"fleetdock-debug-controller-{device.instance_id}",
            daemon=True,
        )
        self._emergency_confirmation_until_s = 0.0
        self._force_unlock_confirmation_until_s = 0.0
        self._last_observed_armed: Optional[bool] = None
        self._last_successful_action: Optional[_Action] = None

    def run(self) -> None:
        """在当前线程运行控制器窗口，直到用户关闭窗口或设备调试入口请求停止。"""
        display = self._display_factory()
        display.open(self._title)
        self._worker.start()
        if self._camera_enabled:
            self._start_camera_subscription()
        try:
            while not self._stop.is_set() and not display.should_close():
                frame_started_at_s = time.monotonic()
                display.draw(self._snapshot())
                for event in display.poll(20):
                    if isinstance(event, _MotionInput):
                        with self._lock:
                            self._pending_motion_actions = event.actions
                    else:
                        self._command_queue.put(event)
                remaining_s = _DISPLAY_FRAME_INTERVAL_S - (
                    time.monotonic() - frame_started_at_s
                )
                if remaining_s > 0.0:
                    self._stop.wait(remaining_s)
        finally:
            self.close()
            display.close()

    def close(self) -> None:
        """结束窗口循环、手动控制、摄像头订阅和命令线程；重复调用安全且无返回值。"""
        self._stop.set()
        self._command_queue.put(None)
        if self._worker.is_alive():
            self._worker.join(timeout=5.0)
        manual = self._detach_manual()
        if manual is not None:
            manual.close()
        unsubscribe = self._camera_unsubscribe
        self._camera_unsubscribe = None
        if unsubscribe is not None:
            unsubscribe()
        logger.debug("设备图形调试控制器已关闭: device_id=%s", self._device.instance_id)

    def _handle_action(self, event: _ControlEvent) -> None:
        if isinstance(event, _MotionInput):
            self._set_motion(event.actions)
            return
        action = event
        if action is _Action.SPEED_UP:
            self._horizontal_speed_m_s = min(
                _MAX_HORIZONTAL_SPEED_M_S,
                self._horizontal_speed_m_s + 0.5,
            )
            return
        if action is _Action.SPEED_DOWN:
            self._horizontal_speed_m_s = max(0.5, self._horizontal_speed_m_s - 0.5)
            return
        if action is _Action.ALTITUDE_UP:
            self._takeoff_altitude_m = min(30.0, self._takeoff_altitude_m + 1.0)
            return
        if action is _Action.ALTITUDE_DOWN:
            self._takeoff_altitude_m = max(1.0, self._takeoff_altitude_m - 1.0)
            return
        if action in _MOTION_ACTIONS:
            self._set_motion(frozenset({action}))
            return
        if action is _Action.RELEASE:
            self._set_motion(frozenset())
            return
        if action is _Action.FORCE_UNLOCK:
            now_s = time.monotonic()
            if now_s > self._force_unlock_confirmation_until_s:
                self._force_unlock_confirmation_until_s = now_s + 3.0
                self._append_message("仅限台架诊断；飞行请修复预检。3 秒内再次点击确认")
                return
            self._force_unlock_confirmation_until_s = 0.0
        if action is _Action.EMERGENCY_STOP:
            now_s = time.monotonic()
            if now_s > self._emergency_confirmation_until_s:
                self._emergency_confirmation_until_s = now_s + 3.0
                self._append_message("请在 3 秒内再次点击“紧急停止”进行确认")
                return
            self._emergency_confirmation_until_s = 0.0
        response = self._execute_action(action)
        self._append_response(action, response)

    def _set_motion(self, actions: frozenset[_Action]) -> None:
        if actions == self._motion_actions:
            return
        was_moving = bool(self._motion_actions)
        self._motion_actions = actions
        if not actions:
            self._next_motion_input_at_s = 0.0
            if self._manual is not None:
                self._manual.release()
            if was_moving:
                self._append_message("运动：已停止")
            return
        labels = "+".join(_ACTION_LABELS[action] for action in sorted(actions, key=lambda item: item.value))
        self._append_message(f"运动：{labels}（松开对应按键即撤销）")
        logger.debug(
            "调试控制器运动输入已更新: device_id=%s actions=%s refresh_hz=%.1f",
            self._device.instance_id,
            ",".join(sorted(action.value for action in actions)),
            1.0 / _MOTION_INPUT_REFRESH_INTERVAL_S,
        )
        self._update_motion(actions)

    def _update_motion(self, actions: frozenset[_Action]) -> None:
        try:
            manual = self._manual
            if manual is None:
                options = ManualControlOptions(
                    max_horizontal_speed_m_s=_MAX_HORIZONTAL_SPEED_M_S,
                    max_vertical_speed_m_s=2.0,
                    max_yaw_rate_deg_s=60.0,
                )
                manual = self._device.manual_control(options)
                self._manual = manual
            forward_axis = int(_Action.FORWARD in actions) - int(_Action.BACK in actions)
            right_axis = int(_Action.RIGHT in actions) - int(_Action.LEFT in actions)
            horizontal_scale = 2**-0.5 if forward_axis and right_axis else 1.0
            forward_m_s = forward_axis * self._horizontal_speed_m_s * horizontal_scale
            right_m_s = right_axis * self._horizontal_speed_m_s * horizontal_scale
            up_m_s = (
                int(_Action.UP in actions) - int(_Action.DOWN in actions)
            ) * self._vertical_speed_m_s
            yaw_rate_deg_s = (
                int(_Action.YAW_RIGHT in actions) - int(_Action.YAW_LEFT in actions)
            ) * self._yaw_rate_deg_s
            manual.update(
                forward_m_s=forward_m_s,
                right_m_s=right_m_s,
                up_m_s=up_m_s,
                yaw_rate_deg_s=yaw_rate_deg_s,
            )
            self._next_motion_input_at_s = time.monotonic() + _MOTION_INPUT_REFRESH_INTERVAL_S
        except Exception as exc:
            self._motion_actions = frozenset()
            self._append_message(self._exception_message("手动控制不可用", exc))

    def _command_worker(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                pending_motion = self._pending_motion_actions
                self._pending_motion_actions = None
            if pending_motion is not None:
                try:
                    self._set_motion(pending_motion)
                except Exception as exc:
                    self._append_message(self._exception_message("运动", exc))
            if self._motion_actions and time.monotonic() >= self._next_motion_input_at_s:
                self._update_motion(self._motion_actions)
            try:
                action = self._command_queue.get(timeout=0.02)
            except Empty:
                continue
            if action is None:
                return
            try:
                self._handle_action(action)
            except Exception as exc:
                self._append_message(self._exception_message(_ACTION_LABELS[action], exc))

    def _execute_action(self, action: _Action) -> StandardResponse:
        if action in {
            _Action.LOCK,
            _Action.HOVER,
            _Action.LAND,
            _Action.RTL,
            _Action.STOP,
            _Action.EMERGENCY_STOP,
        }:
            self._motion_actions = frozenset()
            manual = self._detach_manual()
            if manual is not None:
                manual.close(protect=False)
        commands = {
            _Action.CONNECT: self._device.connect,
            _Action.UNLOCK: self._device.unlock,
            _Action.FORCE_UNLOCK: lambda: self._device.unlock(force=True),
            _Action.LOCK: self._device.lock,
            _Action.TAKEOFF: lambda: self._device.takeoff(self._takeoff_altitude_m),
            _Action.HOVER: self._device.hover,
            _Action.LAND: self._device.land,
            _Action.RTL: self._device.return_to_launch,
            _Action.STOP: self._device.stop,
            _Action.EMERGENCY_STOP: self._device.emergency_stop,
        }
        return commands[action]()

    def _detach_manual(self) -> Optional[ManualControlSession]:
        manual = self._manual
        self._manual = None
        return manual

    def _start_camera_subscription(self) -> None:
        try:
            if self._camera_subscribe is not None:
                unsubscribe = self._camera_subscribe(self._on_camera)
                if not callable(unsubscribe):
                    raise TypeError("摄像头订阅入口必须返回取消订阅函数。")
                self._camera_unsubscribe = unsubscribe
                self._append_message("摄像头订阅已启动")
                logger.debug(
                    "调试控制器已接入调用方选择的标准摄像头订阅: device_id=%s",
                    self._device.instance_id,
                )
                return
            identifier = next(
                kind
                for kind in (SensorKind.RGB_CAMERA, SensorKind.DEPTH_CAMERA, SensorKind.STEREO_CAMERA)
                if self._device.sensors.supports(kind)
            )
            self._camera_unsubscribe = self._device.sensors.subscribe(identifier, self._on_camera)
            self._append_message("摄像头订阅已启动")
            logger.debug(
                "调试控制器已自动订阅标准摄像头: device_id=%s kind=%s",
                self._device.instance_id,
                identifier.value,
            )
        except StopIteration:
            self._append_message("未发现标准摄像头，控制按钮仍可使用")
        except Exception as exc:
            self._append_message(self._exception_message("摄像头不可用", exc))

    def _on_camera(self, sample: SensorSample) -> None:
        if isinstance(sample, (ImageFrame, StereoFrame)):
            now_s = time.monotonic()
            with self._lock:
                if self._last_camera_received_at_s is not None:
                    interval_s = now_s - self._last_camera_received_at_s
                    if 0.0 < interval_s <= 1.0:
                        instant_fps = 1.0 / interval_s
                        self._camera_fps = (
                            instant_fps
                            if self._camera_fps is None
                            else self._camera_fps * 0.8 + instant_fps * 0.2
                        )
                self._last_camera_received_at_s = now_s
                self._camera = sample

    def _snapshot(self) -> _ControllerSnapshot:
        state = self._device.state.snapshot()
        diagnostic = self._device.diagnostics()
        self._observe_armed_state(state.armed, state.in_air)
        enabled: set[_Action] = set()
        capability_actions = {
            _Action.CONNECT: CapabilityName.CONNECT,
            _Action.UNLOCK: CapabilityName.ARM,
            _Action.FORCE_UNLOCK: CapabilityName.ARM,
            _Action.LOCK: CapabilityName.LOCK,
            _Action.TAKEOFF: CapabilityName.TAKEOFF,
            _Action.HOVER: CapabilityName.HOVER,
            _Action.LAND: CapabilityName.LAND,
            _Action.RTL: CapabilityName.RETURN_TO_LAUNCH,
            _Action.STOP: CapabilityName.STOP,
            _Action.EMERGENCY_STOP: CapabilityName.EMERGENCY_STOP,
        }
        if not diagnostic.connected and self._device.has_capability(CapabilityName.CONNECT):
            enabled.add(_Action.CONNECT)
        if diagnostic.connected:
            if state.armed is not True and self._device.has_capability(CapabilityName.ARM):
                enabled.update({_Action.UNLOCK, _Action.FORCE_UNLOCK})
        if state.armed is True and state.in_air is not True:
            enabled.update(
                action
                for action in (_Action.LOCK, _Action.TAKEOFF, _Action.EMERGENCY_STOP)
                if self._device.has_capability(capability_actions[action])
            )
        if state.in_air is True:
            enabled.update(
                action
                for action in (_Action.HOVER, _Action.LAND, _Action.RTL, _Action.STOP, _Action.EMERGENCY_STOP)
                if self._device.has_capability(capability_actions[action])
            )
            if self._device.has_capability(CapabilityName.MANUAL_CONTROL):
                enabled.update(_MOTION_ACTIONS)
                enabled.add(_Action.RELEASE)
        enabled.update({_Action.SPEED_UP, _Action.SPEED_DOWN, _Action.ALTITUDE_UP, _Action.ALTITUDE_DOWN})
        with self._lock:
            messages = tuple(self._messages[-6:])
            camera = self._camera
            camera_fps = self._camera_fps
        battery_percent = (
            None
            if state.battery_remaining_fraction is None
            else state.battery_remaining_fraction * 100.0
        )
        return _ControllerSnapshot(
            device_id=self._device.metadata.device_id or self._device.instance_id,
            product=self._device.product_key,
            connected=diagnostic.connected,
            armed=state.armed,
            in_air=state.in_air,
            flight_mode=state.flight_mode,
            failsafe=state.failsafe,
            battery_percent=battery_percent,
            relative_altitude_m=state.relative_altitude_m,
            roll_deg=state.roll_deg,
            pitch_deg=state.pitch_deg,
            yaw_deg=state.yaw_deg,
            telemetry_age_s=diagnostic.telemetry_age_s,
            camera_fps=camera_fps,
            horizontal_speed_m_s=self._horizontal_speed_m_s,
            vertical_speed_m_s=self._vertical_speed_m_s,
            yaw_rate_deg_s=self._yaw_rate_deg_s,
            takeoff_altitude_m=self._takeoff_altitude_m,
            manual_active=self._manual is not None,
            camera=camera,
            messages=messages,
            enabled=frozenset(enabled),
        )

    def _append_response(self, action: _Action, response: StandardResponse) -> None:
        if response.ok:
            self._last_successful_action = action
        marker = "成功" if response.ok else response.code.value
        message = response.message
        suffix = f" - {message}" if message else ""
        self._append_message(f"{_ACTION_LABELS[action]}：{marker}{suffix}")
        if action is _Action.FORCE_UNLOCK and response.ok:
            self._append_message("台架强制解锁不等于可起飞，PX4 仍可能立即自动上锁")

    def _observe_armed_state(self, armed: Optional[bool], in_air: Optional[bool]) -> None:
        """识别飞控主动上锁，并把容易误判为控制器操作的状态变化解释给调试人员。"""
        previous = self._last_observed_armed
        self._last_observed_armed = armed
        if previous is not True or armed is not False or in_air is True:
            return
        if self._last_successful_action is _Action.FORCE_UNLOCK:
            self._append_message("飞控已自动上锁：台架强制解锁不能替代飞行预检")
        elif self._last_successful_action is _Action.UNLOCK:
            self._append_message("飞控已自动上锁：解锁后请在飞控允许时间内起飞")

    @staticmethod
    def _exception_message(prefix: str, exc: Exception) -> str:
        """生成适合控制器操作日志展示的本地化错误摘要。"""
        return f"{prefix}：{exc}"

    def _append_message(self, message: str) -> None:
        with self._lock:
            self._messages.append(message)
            if len(self._messages) > 50:
                del self._messages[:-50]


@dataclass(frozen=True)
class _Button:
    action: _Action
    label: str
    x1: int
    y1: int
    x2: int
    y2: int
    danger: bool = False

    def contains(self, x: int, y: int) -> bool:
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2


class _OpenCvControllerDisplay:
    """使用现有 vision 可选依赖绘制控制区、摄像头和状态 HUD。"""

    WIDTH = 1100
    HEIGHT = 700

    def __init__(self) -> None:
        try:
            import cv2
            import numpy
        except ImportError as exc:
            raise RuntimeError(
                '图形调试控制器需要可选依赖，请使用 "fleetdock[vision]" 安装。'
            ) from exc
        from fleetdock.debug import _OpenCvDebugDisplay

        self._cv2 = cv2
        self._np = numpy
        self._text_renderer = _DebugTextRenderer(numpy)
        self._image_renderer = _OpenCvDebugDisplay()
        self._title = ""
        self._closed = False
        self._actions: Queue[_ControlEvent] = Queue()
        self._mouse_motion: Optional[_Action] = None
        self._keyboard_motions: frozenset[_Action] = frozenset()
        self._active_input_motions: frozenset[_Action] = frozenset()
        self._keyboard_pressed: frozenset[int] = frozenset()
        self._keyboard_armed = False
        self._keyboard_reader: Optional[Callable[[], Optional[frozenset[int]]]] = None
        self._close_keyboard_capture: Optional[Callable[[], None]] = None
        self._fallback_release_at_s: Optional[float] = None
        self._snapshot: Optional[_ControllerSnapshot] = None
        self._buttons = self._build_buttons()
        self._previous_dpi_context: Any = None
        self._text_items: list[tuple[str, int, int, tuple[int, int, int], float]] = []

    def open(self, title: str) -> None:
        self._title = title
        try:
            self._previous_dpi_context = _use_physical_pixels_on_windows()
            self._cv2.namedWindow(title, self._cv2.WINDOW_NORMAL)
            self._cv2.resizeWindow(title, self.WIDTH, self.HEIGHT)
            self._cv2.setMouseCallback(title, self._on_mouse)
            _set_unicode_window_title_on_windows(title)
            keyboard_capture = _windows_keyboard_capture()
            if keyboard_capture is not None:
                self._keyboard_reader, self._close_keyboard_capture = keyboard_capture
        except Exception as exc:
            raise RuntimeError("无法打开图形调试控制器，请确认当前环境具有图形桌面。") from exc

    def draw(self, snapshot: _ControllerSnapshot) -> None:
        self._snapshot = snapshot
        self._text_items.clear()
        canvas = self._np.zeros((self.HEIGHT, self.WIDTH, 3), dtype=self._np.uint8)
        canvas[:] = (22, 25, 29)
        self._draw_camera(canvas, snapshot)
        self._draw_status(canvas, snapshot)
        for button in self._buttons:
            self._draw_button(canvas, button, button.action in snapshot.enabled)
        self._draw_messages(canvas, snapshot.messages)
        self._render_text(canvas)
        self._cv2.imshow(self._title, canvas)

    def poll(self, delay_ms: int) -> tuple[_ControlEvent, ...]:
        key = int(self._cv2.waitKey(delay_ms)) & 0xFF
        if key in {27, ord("x"), ord("X")}:
            self._closed = True
        if self._keyboard_reader is not None:
            self._poll_stateful_keyboard()
        else:
            self._poll_fallback_keyboard(key)
        actions: list[_ControlEvent] = []
        while True:
            try:
                actions.append(self._actions.get_nowait())
            except Empty:
                return tuple(actions)

    def should_close(self) -> bool:
        if self._closed:
            return True
        try:
            return float(self._cv2.getWindowProperty(self._title, self._cv2.WND_PROP_VISIBLE)) < 1.0
        except Exception:
            return True

    def close(self) -> None:
        if not self._title:
            return
        try:
            close_keyboard_capture = self._close_keyboard_capture
            self._close_keyboard_capture = None
            if close_keyboard_capture is not None:
                close_keyboard_capture()
            self._cv2.destroyWindow(self._title)
        except Exception:
            logger.debug("关闭图形调试控制器失败: title=%s", self._title, exc_info=True)
        finally:
            _restore_dpi_context_on_windows(self._previous_dpi_context)
            self._previous_dpi_context = None
        self._title = ""

    def _on_mouse(self, event: int, x: int, y: int, flags: int, param: Any = None) -> None:
        snapshot = self._snapshot
        if snapshot is None:
            return
        if event == self._cv2.EVENT_LBUTTONDOWN:
            button = next((item for item in self._buttons if item.contains(x, y)), None)
            if button is not None and button.action in snapshot.enabled:
                if button.action in _MOTION_ACTIONS:
                    self._mouse_motion = button.action
                    self._reconcile_motion()
                else:
                    self._actions.put(button.action)
        elif event == self._cv2.EVENT_LBUTTONUP and self._mouse_motion is not None:
            self._mouse_motion = None
            self._reconcile_motion()

    def _poll_stateful_keyboard(self) -> None:
        reader = self._keyboard_reader
        if reader is None:
            return
        pressed = reader()
        if pressed is None:
            self._keyboard_armed = False
            self._keyboard_pressed = frozenset()
            self._keyboard_motions = frozenset()
            self._mouse_motion = None
            self._reconcile_motion()
            return
        keyboard_keys = pressed - {_VK_LBUTTON}
        if not self._keyboard_armed:
            if not keyboard_keys:
                self._keyboard_armed = True
            self._keyboard_pressed = keyboard_keys
            self._keyboard_motions = frozenset()
        else:
            newly_pressed = keyboard_keys - self._keyboard_pressed
            self._keyboard_pressed = keyboard_keys
            if newly_pressed & _CLOSE_VIRTUAL_KEYS:
                self._closed = True
            snapshot = self._snapshot
            if snapshot is not None:
                for virtual_key in newly_pressed:
                    action = _KEYBOARD_COMMAND_BINDINGS.get(virtual_key)
                    if action is not None and action in snapshot.enabled:
                        self._actions.put(action)
                self._keyboard_motions = frozenset(
                    action
                    for virtual_keys, action in _KEYBOARD_MOTION_BINDINGS
                    if action in snapshot.enabled and any(key in keyboard_keys for key in virtual_keys)
                )
        if self._mouse_motion is not None and _VK_LBUTTON not in pressed:
            self._mouse_motion = None
        self._reconcile_motion()

    def _poll_fallback_keyboard(self, key: int) -> None:
        """在无法读取按键抬起事件的平台上使用短脉冲，保证运动不会持续锁定。"""
        key = ord(chr(key).upper()) if 0 <= key <= 0x7F else key
        snapshot = self._snapshot
        motion_by_character = {
            key_code: action
            for virtual_keys, action in _KEYBOARD_MOTION_BINDINGS
            for key_code in virtual_keys
            if key_code <= 0x7F
        }
        command_by_character = {
            key_code: action for key_code, action in _KEYBOARD_COMMAND_BINDINGS.items() if key_code <= 0x7F
        }
        if snapshot is not None and key in motion_by_character:
            action = motion_by_character[key]
            if action in snapshot.enabled:
                self._keyboard_motions = frozenset({action})
                self._fallback_release_at_s = time.monotonic() + 0.15
                self._reconcile_motion()
        elif snapshot is not None and key in command_by_character:
            action = command_by_character[key]
            if action in snapshot.enabled:
                self._actions.put(action)
        if self._fallback_release_at_s is not None and time.monotonic() >= self._fallback_release_at_s:
            self._fallback_release_at_s = None
            self._keyboard_motions = frozenset()
            self._reconcile_motion()

    def _reconcile_motion(self) -> None:
        desired = self._keyboard_motions | (
            frozenset({self._mouse_motion}) if self._mouse_motion is not None else frozenset()
        )
        if desired == self._active_input_motions:
            return
        self._active_input_motions = desired
        self._actions.put(_MotionInput(desired))

    def _draw_camera(self, canvas: Any, snapshot: _ControllerSnapshot) -> None:
        x1, y1, x2, y2 = 20, 60, 650, 410
        canvas[y1:y2, x1:x2] = (12, 15, 18)
        if snapshot.camera is not None:
            try:
                image = self._image_renderer._camera_image(snapshot.camera)
                image = self._image_renderer._fit(image, x2 - x1, y2 - y1)
                y = y1 + (y2 - y1 - image.shape[0]) // 2
                x = x1 + (x2 - x1 - image.shape[1]) // 2
                canvas[y : y + image.shape[0], x : x + image.shape[1]] = image
            except Exception as exc:
                self._put(canvas, f"相机画面渲染失败：{exc}", x1 + 20, y1 + 40, (60, 80, 255))
        else:
            self._put(canvas, "暂无相机画面，正在等待数据", x1 + 180, y1 + 175, (120, 130, 140))
        hud = (
            f"高度 {self._fmt(snapshot.relative_altitude_m, 'm')}  "
            f"电量 {self._fmt(snapshot.battery_percent, '%')}  "
            f"偏航 {self._fmt(snapshot.yaw_deg, '°')}  "
            f"FPS {self._fmt(snapshot.camera_fps, '')}"
        )
        self._put(canvas, hud, x1 + 12, y2 - 14, (80, 230, 120))

    def _draw_status(self, canvas: Any, snapshot: _ControllerSnapshot) -> None:
        self._put(canvas, "FleetDock 调试控制器", 20, 32, (230, 235, 240), 0.7)
        input_text = "+".join(
            _ACTION_LABELS[action]
            for action in sorted(self._active_input_motions, key=lambda item: item.value)
        ) or "停止"
        lines = (
            f"设备：{snapshot.device_id}",
            f"产品：{snapshot.product}",
            f"已连接：{self._bool_text(snapshot.connected)}  已解锁：{self._bool_text(snapshot.armed)}  "
            f"飞行中：{self._bool_text(snapshot.in_air)}",
            f"模式：{snapshot.flight_mode or '未知'}  失效保护：{self._bool_text(snapshot.failsafe)}",
            f"遥测延迟：{self._fmt(snapshot.telemetry_age_s, 's')}",
            f"起飞高度：{snapshot.takeoff_altitude_m:.1f} m",
            f"水平速度：{snapshot.horizontal_speed_m_s:.1f} m/s",
            f"手动控制：{'已启用' if snapshot.manual_active else '待命'}  当前输入：{input_text}",
        )
        y = 40
        for line in lines:
            self._put(canvas, line, 680, y, (210, 215, 220))
            y += 22

    def _draw_button(self, canvas: Any, button: _Button, enabled: bool) -> None:
        if not enabled:
            color = (55, 58, 62)
        elif button.action in self._active_input_motions:
            color = (180, 115, 35)
        elif button.danger:
            color = (45, 45, 190)
        elif button.action in _MOTION_ACTIONS:
            color = (105, 80, 35)
        else:
            color = (55, 105, 65)
        self._cv2.rectangle(canvas, (button.x1, button.y1), (button.x2, button.y2), color, -1)
        self._cv2.rectangle(canvas, (button.x1, button.y1), (button.x2, button.y2), (120, 125, 130), 1)
        left, top, right, bottom = self._text_bbox(button.label, 0.42)
        text_width = right - left
        text_height = bottom - top
        x = button.x1 + (button.x2 - button.x1 - text_width) // 2 - left
        y = button.y1 + (button.y2 - button.y1 - text_height) // 2 - top
        self._put(canvas, button.label, x, y, (235, 235, 235), 0.42)

    def _draw_messages(self, canvas: Any, messages: tuple[str, ...]) -> None:
        self._put(canvas, "操作日志", 20, 455, (220, 225, 230), 0.55)
        y = 480
        for message in messages:
            visible = message if len(message) <= 125 else message[:122] + "..."
            self._put(canvas, visible, 20, y, (175, 185, 195), 0.42)
            y += 24
        self._put(
            canvas,
            "移动：按住 WASD/方向键，R/F 或 PgUp/PgDn 升降，Q/E 偏航；鼠标和键盘松开即停",
            20,
            658,
            (130, 140, 150),
            0.42,
        )
        self._put(
            canvas,
            "命令：C 连接  U 解锁  L 锁定  空格起飞  Ctrl 降落  H 悬停  T 返航  Z 停止  +/- 速度  [/] 高度  Esc/X 关闭",
            20,
            682,
            (130, 140, 150),
            0.38,
        )

    def _put(self, canvas: Any, text: str, x: int, y: int, color: tuple[int, int, int], scale: float = 0.5) -> None:
        self._text_items.append((text, x, y, color, scale))

    def _render_text(self, canvas: Any) -> None:
        self._text_renderer.render(canvas, self._text_items)

    def _text_bbox(self, text: str, scale: float) -> tuple[int, int, int, int]:
        return self._text_renderer.text_bbox(text, scale)

    @staticmethod
    def _fmt(value: Optional[float], suffix: str) -> str:
        return "未知" if value is None else f"{value:.1f}{suffix}"

    @staticmethod
    def _bool_text(value: Optional[bool]) -> str:
        if value is None:
            return "未知"
        return "是" if value else "否"

    @staticmethod
    def _build_buttons() -> tuple[_Button, ...]:
        return (
            _Button(_Action.CONNECT, "连接", 680, 225, 770, 265),
            _Button(_Action.UNLOCK, "解锁", 780, 225, 870, 265),
            _Button(_Action.FORCE_UNLOCK, "台架强解锁", 880, 225, 980, 265, True),
            _Button(_Action.LOCK, "锁定", 990, 225, 1080, 265),
            _Button(_Action.TAKEOFF, "起飞", 680, 275, 785, 315),
            _Button(_Action.HOVER, "悬停", 795, 275, 900, 315),
            _Button(_Action.LAND, "降落", 910, 275, 1015, 315),
            _Button(_Action.RTL, "返航", 680, 325, 785, 365),
            _Button(_Action.STOP, "停止", 795, 325, 900, 365),
            _Button(_Action.EMERGENCY_STOP, "紧急停止", 910, 325, 1035, 365, True),
            _Button(_Action.ALTITUDE_DOWN, "高度 -", 680, 375, 785, 415),
            _Button(_Action.ALTITUDE_UP, "高度 +", 795, 375, 900, 415),
            _Button(_Action.SPEED_DOWN, "速度 -", 910, 375, 1015, 415),
            _Button(_Action.SPEED_UP, "速度 +", 910, 425, 1015, 465),
            _Button(_Action.FORWARD, "前进", 780, 440, 900, 480),
            _Button(_Action.LEFT, "左移", 650, 490, 770, 530),
            _Button(_Action.RELEASE, "停止移动", 780, 490, 900, 530),
            _Button(_Action.RIGHT, "右移", 910, 490, 1030, 530),
            _Button(_Action.BACK, "后退", 780, 540, 900, 580),
            _Button(_Action.UP, "上升", 650, 590, 750, 630),
            _Button(_Action.DOWN, "下降", 760, 590, 860, 630),
            _Button(_Action.YAW_LEFT, "左偏航", 870, 590, 970, 630),
            _Button(_Action.YAW_RIGHT, "右偏航", 980, 590, 1080, 630),
        )
