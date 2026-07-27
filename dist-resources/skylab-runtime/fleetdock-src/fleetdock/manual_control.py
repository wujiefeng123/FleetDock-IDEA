"""跨设备手动速度控制会话。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
import math
from threading import Event, RLock, Thread, current_thread
import time
from typing import TYPE_CHECKING, Optional

from fleetdock.models import StandardResponse

if TYPE_CHECKING:
    from fleetdock.devices import BaseDevice


logger = logging.getLogger(__name__)


class ManualControlFailsafeAction(str, Enum):
    """手动控制输入超时后的标准保护动作。"""

    HOVER = "hover"  # 空中设备退出连续速度控制并保持当前位置。
    STOP = "stop"  # 地面设备或不支持悬停的设备发送标准停止命令。


@dataclass(frozen=True)
class ManualControlOptions:
    """手动控制会话的刷新频率、输入超时和速度边界。

    ``refresh_interval_s`` 是重复发送有效目标的秒数间隔；``input_timeout_s`` 是最后一次开发者
    输入允许保持有效的秒数。水平、垂直速度单位为米/秒，偏航角速度单位为度/秒；会话会把输入
    限制在对应最大值内。输入超时后执行 ``failsafe_action``，默认让无人机悬停。
    """

    refresh_interval_s: float = 0.1
    input_timeout_s: float = 0.5
    max_horizontal_speed_m_s: float = 3.0
    max_vertical_speed_m_s: float = 1.0
    max_yaw_rate_deg_s: float = 45.0
    failsafe_action: ManualControlFailsafeAction = ManualControlFailsafeAction.HOVER

    def __post_init__(self) -> None:
        numeric = (
            self.refresh_interval_s,
            self.input_timeout_s,
            self.max_horizontal_speed_m_s,
            self.max_vertical_speed_m_s,
            self.max_yaw_rate_deg_s,
        )
        if not all(math.isfinite(float(value)) and float(value) > 0.0 for value in numeric):
            raise ValueError("Manual control intervals and speed limits must be finite and greater than zero.")
        if self.input_timeout_s <= self.refresh_interval_s:
            raise ValueError("input_timeout_s must be greater than refresh_interval_s.")


@dataclass(frozen=True)
class ManualControlInput:
    """一次机体系手动控制输入。

    ``forward_m_s``、``right_m_s`` 和 ``up_m_s`` 分别是沿设备机头前方、右方和上方的速度，
    单位为米/秒；``yaw_rate_deg_s`` 是向右为正的偏航角速度，单位为度/秒。对象不可变，不包含
    厂商坐标系或原生控制对象。
    """

    forward_m_s: float = 0.0
    right_m_s: float = 0.0
    up_m_s: float = 0.0
    yaw_rate_deg_s: float = 0.0

    @property
    def moving(self) -> bool:
        """返回是否至少包含一个非零平移或偏航速度。"""
        return any(value != 0.0 for value in (
            self.forward_m_s,
            self.right_m_s,
            self.up_m_s,
            self.yaw_rate_deg_s,
        ))


@dataclass(frozen=True)
class ManualControlStatus:
    """手动控制会话的线程安全状态快照。

    ``active`` 表示会话尚未关闭；``input`` 是最近一次限速后的机体系目标；``input_age_s`` 是该
    输入距查询时的秒数，尚未收到输入时为 ``None``；``failsafe_triggered`` 表示最近输入曾因超时
    触发保护动作；``last_response`` 是最近一次标准控制响应，尚未发送命令时为 ``None``。
    """

    active: bool
    input: ManualControlInput
    input_age_s: Optional[float]
    failsafe_triggered: bool
    last_response: Optional[StandardResponse]


class ManualControlSession:
    """持续刷新标准机体系速度目标并提供输入丢失保护的控制会话。

    调用方通过 ``device.manual_control()`` 创建会话，再周期调用 ``update()`` 更新按键或摇杆输入。
    会话线程只调用设备公共 SDK；它不暴露 PX4 Offboard、DJI 或 Unitree 原生对象。输入超过配置的
    ``input_timeout_s`` 后自动悬停或停止；``release()`` 立即发送零速度但保留会话；``close()``
    结束线程，并在曾发送非零目标时执行配置的保护动作。对象支持 ``with``，重复关闭安全。
    """

    def __init__(self, device: "BaseDevice", options: ManualControlOptions) -> None:
        self._device = device
        self.options = options
        self._lock = RLock()
        self._stop = Event()
        self._wake = Event()
        self._input = ManualControlInput()
        self._input_at_s: Optional[float] = None
        self._last_response: Optional[StandardResponse] = None
        self._failsafe_triggered = False
        self._motion_sent = False
        self._closed = False
        self._thread = Thread(
            target=self._run,
            name=f"fleetdock-manual-control-{device.instance_id}",
            daemon=True,
        )
        self._thread.start()
        logger.debug("手动控制会话已启动: device_id=%s", device.instance_id)

    def __enter__(self) -> "ManualControlSession":
        """返回当前活动会话；进入上下文不会自动发送运动目标。"""
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        """退出上下文时调用 ``close()``，确保停止刷新并执行配置的保护动作。"""
        self.close()

    def update(
        self,
        *,
        forward_m_s: float = 0.0,
        right_m_s: float = 0.0,
        up_m_s: float = 0.0,
        yaw_rate_deg_s: float = 0.0,
    ) -> None:
        """更新机体系手动控制目标并唤醒发送线程，无返回值。

        ``forward_m_s``、``right_m_s``、``up_m_s`` 分别是沿设备前、右、上为正的米/秒速度；
        ``yaw_rate_deg_s`` 单位为度/秒，向右偏航为正。所有值必须有限，超出
        ``ManualControlOptions`` 边界时会被安全裁剪。调用方应在
        按键保持或摇杆输入有效期间持续刷新；会话关闭后调用会抛出 ``RuntimeError``。
        """
        values = tuple(float(value) for value in (
            forward_m_s,
            right_m_s,
            up_m_s,
            yaw_rate_deg_s,
        ))
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Manual control values must be finite.")
        horizontal_norm = math.hypot(values[0], values[1])
        horizontal_scale = min(1.0, self.options.max_horizontal_speed_m_s / horizontal_norm) if horizontal_norm else 1.0
        selected = ManualControlInput(
            forward_m_s=values[0] * horizontal_scale,
            right_m_s=values[1] * horizontal_scale,
            up_m_s=max(-self.options.max_vertical_speed_m_s, min(self.options.max_vertical_speed_m_s, values[2])),
            yaw_rate_deg_s=max(
                -self.options.max_yaw_rate_deg_s,
                min(self.options.max_yaw_rate_deg_s, values[3]),
            ),
        )
        with self._lock:
            if self._closed:
                raise RuntimeError("Manual control session is closed.")
            self._input = selected
            self._input_at_s = time.monotonic()
            self._failsafe_triggered = False
        self._wake.set()

    def release(self) -> None:
        """立即提交零速度输入并保留活动会话，适合鼠标或键盘松开事件，无返回值。"""
        self.update()

    def status(self) -> ManualControlStatus:
        """返回当前手动控制输入、保护状态和最近标准响应的不可变快照。"""
        now_s = time.monotonic()
        with self._lock:
            age_s = None if self._input_at_s is None else max(0.0, now_s - self._input_at_s)
            return ManualControlStatus(
                active=not self._closed,
                input=self._input,
                input_age_s=age_s,
                failsafe_triggered=self._failsafe_triggered,
                last_response=self._last_response,
            )

    def close(self, *, protect: bool = True) -> None:
        """停止刷新线程并按需悬停或停止设备；重复调用安全且无返回值。

        ``protect=True`` 时，若会话曾成功发送非零目标，会执行配置的保护动作；控制器准备立即发送
        降落、返航或急停等更强命令时可以传 ``False``，避免先重复悬停。该方法不会自行返航、降落
        或关闭设备。
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            should_protect = self._motion_sent
        self._stop.set()
        self._wake.set()
        if self._thread.is_alive() and current_thread() is not self._thread:
            self._thread.join(timeout=max(2.0, self.options.refresh_interval_s * 4.0))
        if should_protect and protect:
            self._apply_failsafe()
        self._device._release_manual_control(self)
        logger.debug("手动控制会话已关闭: device_id=%s", self._device.instance_id)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.options.refresh_interval_s)
            self._wake.clear()
            if self._stop.is_set():
                break
            now_s = time.monotonic()
            with self._lock:
                input_at_s = self._input_at_s
                selected = self._input
                expired = input_at_s is not None and now_s - input_at_s > self.options.input_timeout_s
                should_failsafe = expired and not self._failsafe_triggered
                if should_failsafe:
                    self._failsafe_triggered = True
            if input_at_s is None:
                continue
            if should_failsafe:
                self._apply_failsafe()
                continue
            if expired:
                continue
            response = self._device.set_body_velocity(
                forward_m_s=selected.forward_m_s,
                right_m_s=selected.right_m_s,
                up_m_s=selected.up_m_s,
                yaw_rate_deg_s=selected.yaw_rate_deg_s,
            )
            with self._lock:
                self._last_response = response
                if response.ok and selected.moving:
                    self._motion_sent = True

    def _apply_failsafe(self) -> None:
        if self.options.failsafe_action is ManualControlFailsafeAction.HOVER:
            hover = getattr(self._device, "hover", None)
            response = hover() if callable(hover) else self._device.stop()
        else:
            response = self._device.stop()
        with self._lock:
            self._input = ManualControlInput()
            self._last_response = response
        logger.debug(
            "手动控制保护动作已执行: device_id=%s action=%s ok=%s",
            self._device.instance_id,
            self.options.failsafe_action.value,
            response.ok,
        )
