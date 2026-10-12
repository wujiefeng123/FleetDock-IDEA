"""与传输协议无关的交互式设备控制会话。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import logging
import math
from threading import Event, RLock, Thread
import time
from typing import Any, Collection, Mapping, Optional

from fleetdock import (
    CapabilityName,
    CommandName,
    DeviceCategory,
    ManualControlFailsafeAction,
    ManualControlInput,
    ManualControlOptions,
    StandardResponse,
)


logger = logging.getLogger(__name__)


def _manual_actions_for_device(device: Any) -> frozenset["ManualAction"]:
    if not device.has_capability(CapabilityName.MANUAL_CONTROL):
        return frozenset()
    actions = {
        ManualAction.FORWARD,
        ManualAction.BACKWARD,
        ManualAction.LEFT,
        ManualAction.RIGHT,
        ManualAction.YAW_LEFT,
        ManualAction.YAW_RIGHT,
    }
    if device.profile.category is DeviceCategory.DRONE:
        actions.update({ManualAction.ASCEND, ManualAction.DESCEND})
    return frozenset(actions)


class ManualAction(str, Enum):
    """交互界面可按下并保持的标准运动动作。"""

    FORWARD = "forward"  # 沿设备机头或正前方持续前进。
    BACKWARD = "backward"  # 沿设备后方持续后退。
    LEFT = "left"  # 沿设备左方持续横移。
    RIGHT = "right"  # 沿设备右方持续横移。
    ASCEND = "ascend"  # 空中设备持续上升。
    DESCEND = "descend"  # 空中设备持续下降。
    YAW_LEFT = "yaw_left"  # 持续向左偏航。
    YAW_RIGHT = "yaw_right"  # 持续向右偏航。


class ControlMode(str, Enum):
    """交互控制所在的安全环境。"""

    SIMULATION = "simulation"  # Mock、SITL 或仿真场景。
    BENCH = "bench"  # 真实设备台架诊断，默认禁止起飞。
    LIVE = "live"  # 真实运行环境，使用最保守的危险操作策略。


@dataclass(frozen=True)
class InteractiveControlOptions:
    """交互式控制速度、心跳时限和运行环境。

    水平与垂直速度单位为米/秒，偏航角速度单位为度/秒；``input_timeout_s`` 是控制端最后一次
    按键变化或心跳允许保持有效的秒数。``mode`` 必须由部署者显式表达，不能根据 PX4 产品键猜测
    当前连接的是真机还是仿真。
    """

    horizontal_speed_m_s: float = 3.0
    vertical_speed_m_s: float = 0.6
    yaw_rate_deg_s: float = 30.0
    input_timeout_s: float = 0.5
    mode: ControlMode = ControlMode.LIVE

    def __post_init__(self) -> None:
        values = (
            self.horizontal_speed_m_s,
            self.vertical_speed_m_s,
            self.yaw_rate_deg_s,
            self.input_timeout_s,
        )
        if not all(math.isfinite(float(value)) and float(value) > 0.0 for value in values):
            raise ValueError("Interactive control speeds and timeout must be finite and greater than zero.")


@dataclass(frozen=True)
class InteractiveControlSnapshot:
    """交互控制会话的不可变状态快照。

    ``active_actions`` 是服务端当前接受的动作集合；``input`` 是由动作合成的机体系速度；
    ``input_age_s`` 是距最后有效输入的秒数，尚未收到输入时为 ``None``。``failsafe_triggered``
    表示本会话或底层手动控制已触发超时保护，``closed`` 表示会话不可继续使用。
    ``horizontal_speed_m_s``、``vertical_speed_m_s`` 是当前水平和升降速度，单位为米/秒；
    ``yaw_rate_deg_s`` 是当前偏航角速度，单位为度/秒。
    """

    active_actions: frozenset[ManualAction]
    input: ManualControlInput
    input_age_s: Optional[float]
    failsafe_triggered: bool
    closed: bool
    horizontal_speed_m_s: float = 3.0
    vertical_speed_m_s: float = 0.6
    yaw_rate_deg_s: float = 30.0


class InteractiveControlSession:
    """把按下、弹起和心跳转换为 FleetDock 标准手动速度控制。

    会话只依赖公共设备能力，不判断厂商品牌。重复按下和弹起是幂等操作；多键组合在本类中统一
    转换为前、右、上和向右偏航为正的机体系速度。底层 ``ManualControlSession`` 负责稳定刷新和
    输入超时保护。
    """

    def __init__(self, device: Any, options: Optional[InteractiveControlOptions] = None) -> None:
        """绑定一个由服务端独占的标准设备。

        ``device`` 必须是 FleetDock 标准设备对象；``options`` 省略时采用保守的 ``LIVE`` 配置。
        构造过程不会连接设备，也不会在尚无持续动作时创建底层手动控制会话。
        """

        self.device = device
        self.options = options or InteractiveControlOptions()
        self._lock = RLock()
        self._active_actions: set[ManualAction] = set()
        self._manual: Any = None
        self._last_input_at_s: Optional[float] = None
        self._failsafe_triggered = False
        self._closed = False
        self._stop = Event()
        self._watchdog = Thread(
            target=self._watchdog_loop,
            name=f"fleetdock-interactive-control-{device.instance_id}",
            daemon=True,
        )
        self._watchdog.start()

    @property
    def supported_manual_actions(self) -> frozenset[ManualAction]:
        """返回当前产品声明能够通过本会话执行的持续动作。"""

        return _manual_actions_for_device(self.device)

    def press(self, action: ManualAction) -> None:
        """按下 ``action`` 持续动作并立即提交合成后的标准速度目标，无返回值。"""

        selected = ManualAction(action)
        self._require_open()
        if selected not in self.supported_manual_actions:
            raise ValueError(f"Manual action is not supported by this device: {selected.value}")
        with self._lock:
            self._active_actions.add(selected)
        self._apply_actions()

    def release(self, action: ManualAction) -> None:
        """弹起 ``action`` 持续动作；其他仍按住的动作继续有效，无返回值。"""

        selected = ManualAction(action)
        self._require_open()
        with self._lock:
            self._active_actions.discard(selected)
        self._apply_actions()

    def sync_actions(self, actions: Collection[ManualAction]) -> None:
        """用 ``actions`` 完整动作集合校准当前输入并立即提交合成速度，无返回值。"""

        self._require_open()
        selected = {ManualAction(action) for action in actions}
        unsupported = selected.difference(self.supported_manual_actions)
        if unsupported:
            names = ", ".join(sorted(action.value for action in unsupported))
            raise ValueError(f"Manual actions are not supported by this device: {names}")
        with self._lock:
            self._active_actions = selected
        self._apply_actions()

    def heartbeat(self) -> None:
        """刷新当前持续输入的存活时间；没有活动动作时不创建底层控制会话，无返回值。"""

        self._require_open()
        with self._lock:
            has_actions = bool(self._active_actions)
        if has_actions:
            self._apply_actions()
        else:
            self._last_input_at_s = time.monotonic()

    def set_speeds(
        self,
        *,
        horizontal_speed_m_s: Optional[float] = None,
        vertical_speed_m_s: Optional[float] = None,
        yaw_rate_deg_s: Optional[float] = None,
    ) -> None:
        """调整后端权威速度并重新提交当前动作，无返回值。

        ``horizontal_speed_m_s`` 和 ``vertical_speed_m_s`` 使用米/秒，
        ``yaw_rate_deg_s`` 使用度/秒；省略的参数保持不变。所有值必须为有限正数。
        """

        self._require_open()
        changes = {
            key: float(value)
            for key, value in {
                "horizontal_speed_m_s": horizontal_speed_m_s,
                "vertical_speed_m_s": vertical_speed_m_s,
                "yaw_rate_deg_s": yaw_rate_deg_s,
            }.items()
            if value is not None
        }
        if not all(math.isfinite(value) and value > 0.0 for value in changes.values()):
            raise ValueError("Interactive control speeds must be finite and greater than zero.")
        with self._lock:
            self.options = replace(
                self.options,
                horizontal_speed_m_s=changes.get(
                    "horizontal_speed_m_s",
                    self.options.horizontal_speed_m_s,
                ),
                vertical_speed_m_s=changes.get(
                    "vertical_speed_m_s",
                    self.options.vertical_speed_m_s,
                ),
                yaw_rate_deg_s=changes.get(
                    "yaw_rate_deg_s",
                    self.options.yaw_rate_deg_s,
                ),
            )
            manual = self._manual
            self._manual = None
            has_actions = bool(self._active_actions)
        if manual is not None:
            manual.close(protect=False)
        if has_actions:
            self._apply_actions()

    def release_all(self) -> None:
        """清空全部持续动作并立即提交零速度，无返回值。"""

        with self._lock:
            self._active_actions.clear()
            manual = self._manual
            self._last_input_at_s = time.monotonic()
            self._failsafe_triggered = False
        if manual is not None:
            manual.release()

    def execute(
        self,
        command: CommandName,
        params: Optional[Mapping[str, Any]] = None,
    ) -> StandardResponse:
        """执行一次标准离散命令并返回标准响应。

        ``command`` 必须由产品声明支持；``params`` 使用对应标准命令参数。台架模式拒绝起飞，
        真实运行模式拒绝强制解锁。降落、返航、停止和急停前先结束持续手动控制，避免旧速度目标
        与更强命令竞争。
        """

        self._require_open()
        selected = CommandName(command)
        selected_params = dict(params or {})
        if not self.device.profile.supports(selected):
            raise ValueError(f"Command is not supported by this device: {selected.value}")
        if self.options.mode is ControlMode.BENCH and selected is CommandName.TAKEOFF:
            raise PermissionError("Bench control mode does not allow takeoff.")
        if (
            self.options.mode is ControlMode.LIVE
            and selected is CommandName.UNLOCK
            and bool(selected_params.get("force"))
        ):
            raise PermissionError("Live control mode does not allow force unlock.")
        if selected in {
            CommandName.LOCK,
            CommandName.HOVER,
            CommandName.LAND,
            CommandName.RETURN_TO_LAUNCH,
            CommandName.STOP,
            CommandName.EMERGENCY_STOP,
        }:
            self._close_manual(protect=False)
        return self.device.execute(selected, selected_params)

    def snapshot(self) -> InteractiveControlSnapshot:
        """返回当前动作、标准速度和底层保护状态快照。"""

        now_s = time.monotonic()
        with self._lock:
            actions = frozenset(self._active_actions)
            manual = self._manual
            age_s = None if self._last_input_at_s is None else max(0.0, now_s - self._last_input_at_s)
            session_failsafe = self._failsafe_triggered
            closed = self._closed
            options = self.options
        if manual is None:
            return InteractiveControlSnapshot(
                active_actions=actions,
                input=self._input_for(actions),
                input_age_s=age_s,
                failsafe_triggered=session_failsafe,
                closed=closed,
                horizontal_speed_m_s=options.horizontal_speed_m_s,
                vertical_speed_m_s=options.vertical_speed_m_s,
                yaw_rate_deg_s=options.yaw_rate_deg_s,
            )
        status = manual.status()
        return InteractiveControlSnapshot(
            active_actions=actions,
            input=status.input,
            input_age_s=status.input_age_s,
            failsafe_triggered=session_failsafe or status.failsafe_triggered,
            closed=closed,
            horizontal_speed_m_s=options.horizontal_speed_m_s,
            vertical_speed_m_s=options.vertical_speed_m_s,
            yaw_rate_deg_s=options.yaw_rate_deg_s,
        )

    def close(self) -> None:
        """释放全部持续动作并关闭底层手动控制会话；重复调用安全，无返回值。"""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._active_actions.clear()
        self._stop.set()
        if self._watchdog.is_alive():
            self._watchdog.join(timeout=2.0)
        self._close_manual(protect=True)
        logger.debug("交互式控制会话已关闭: device_id=%s", self.device.instance_id)

    def _require_open(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("Interactive control session is closed.")

    def _apply_actions(self) -> None:
        with self._lock:
            actions = frozenset(self._active_actions)
            self._last_input_at_s = time.monotonic()
            self._failsafe_triggered = False
        selected = self._input_for(actions)
        if not actions:
            self.release_all()
            return
        manual = self._ensure_manual()
        manual.update(
            forward_m_s=selected.forward_m_s,
            right_m_s=selected.right_m_s,
            up_m_s=selected.up_m_s,
            yaw_rate_deg_s=selected.yaw_rate_deg_s,
        )

    def _input_for(self, actions: Collection[ManualAction]) -> ManualControlInput:
        forward_axis = int(ManualAction.FORWARD in actions) - int(ManualAction.BACKWARD in actions)
        right_axis = int(ManualAction.RIGHT in actions) - int(ManualAction.LEFT in actions)
        horizontal_scale = 2**-0.5 if forward_axis and right_axis else 1.0
        up_axis = int(ManualAction.ASCEND in actions) - int(ManualAction.DESCEND in actions)
        yaw_axis = int(ManualAction.YAW_RIGHT in actions) - int(ManualAction.YAW_LEFT in actions)
        return ManualControlInput(
            forward_m_s=forward_axis * self.options.horizontal_speed_m_s * horizontal_scale,
            right_m_s=right_axis * self.options.horizontal_speed_m_s * horizontal_scale,
            up_m_s=up_axis * self.options.vertical_speed_m_s,
            yaw_rate_deg_s=yaw_axis * self.options.yaw_rate_deg_s,
        )

    def _ensure_manual(self) -> Any:
        with self._lock:
            if self._manual is not None:
                return self._manual
        failsafe = (
            ManualControlFailsafeAction.HOVER
            if self.device.profile.category is DeviceCategory.DRONE
            else ManualControlFailsafeAction.STOP
        )
        manual = self.device.manual_control(
            ManualControlOptions(
                refresh_interval_s=min(0.1, self.options.input_timeout_s * 0.25),
                input_timeout_s=self.options.input_timeout_s,
                max_horizontal_speed_m_s=self.options.horizontal_speed_m_s,
                max_vertical_speed_m_s=self.options.vertical_speed_m_s,
                max_yaw_rate_deg_s=self.options.yaw_rate_deg_s,
                failsafe_action=failsafe,
            )
        )
        with self._lock:
            if self._closed:
                manual.close()
                raise RuntimeError("Interactive control session is closed.")
            self._manual = manual
        return manual

    def _close_manual(self, *, protect: bool) -> None:
        with self._lock:
            manual = self._manual
            self._manual = None
            self._active_actions.clear()
        if manual is not None:
            manual.close(protect=protect)

    def _watchdog_loop(self) -> None:
        interval_s = min(0.1, self.options.input_timeout_s * 0.25)
        while not self._stop.wait(interval_s):
            now_s = time.monotonic()
            with self._lock:
                expired = (
                    bool(self._active_actions)
                    and self._last_input_at_s is not None
                    and now_s - self._last_input_at_s > self.options.input_timeout_s
                )
                if not expired:
                    continue
                self._active_actions.clear()
                self._failsafe_triggered = True
                manual = self._manual
                self._manual = None
            if manual is not None:
                manual.close(protect=True)
            logger.debug(
                "交互式控制输入超时并已释放: device_id=%s timeout_s=%.3f",
                self.device.instance_id,
                self.options.input_timeout_s,
            )
