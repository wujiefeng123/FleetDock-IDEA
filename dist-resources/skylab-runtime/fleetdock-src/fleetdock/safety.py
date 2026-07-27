"""FleetDock 通用安全策略与安全前置检查。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Iterable, Mapping, Optional, Sequence

from fleetdock.devices import BaseDevice, DeviceState
from fleetdock.events import CoordinateFrame
from fleetdock.models import DeviceCategory


class SafetySeverity(str, Enum):
    """安全检查结果严重程度，用于区分提示、阻断命令和必须立即处置的风险。"""

    # 仅用于状态说明，不阻断后续命令。
    INFO = "info"
    # 需要上层关注的风险提示，默认不阻断当前命令。
    WARNING = "warning"
    # 违反安全边界，应阻断当前运动命令并执行配置的保护动作。
    BLOCKING = "blocking"
    # 已进入高风险状态，应立即执行返航、悬停或急停等处置动作。
    CRITICAL = "critical"


class SafetyAction(str, Enum):
    """安全违规后的推荐处置动作。

    FleetDock 的安全监督器只返回动作建议；具体由 Follow、Plan 或上层应用决定何时调用
    ``hover``、``stop``、``return_to_launch`` 或 ``emergency_stop``。
    """

    # 不执行额外动作，通常用于告警但允许继续运行的场景。
    NONE = "none"
    # 阻断当前命令但不主动发送设备控制命令。
    BLOCK_COMMAND = "block_command"
    # 请求设备悬停；不支持悬停的设备可退化为零速度或停止。
    HOVER = "hover"
    # 请求设备停止当前运动，适合地面设备或无悬停能力的设备。
    STOP = "stop"
    # 请求设备返回起点或 Home 点，适合低电量、失联等可恢复场景。
    RETURN_TO_LAUNCH = "return_to_launch"
    # 请求设备急停，适合严重越界、极低电量或不可恢复风险。
    EMERGENCY_STOP = "emergency_stop"


@dataclass(frozen=True)
class KeepOutZone:
    """本地坐标禁入区。

    ``center_x_m``、``center_y_m`` 和 ``radius_m`` 单位均为米，坐标系由 ``frame`` 声明。
    第一版使用圆形区域，适合在 SDK 侧做轻量级禁入检查；复杂多边形、地理围栏或动态障碍物
    后续应通过专门的 ObstacleProvider 扩展。
    """

    name: str
    center_x_m: float
    center_y_m: float
    radius_m: float
    frame: CoordinateFrame = CoordinateFrame.LOCAL_NED
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def contains(self, x_m: float, y_m: float, frame: CoordinateFrame) -> bool:
        """判断本地坐标点是否落入禁入区。

        ``x_m``、``y_m`` 是待检查点的本地坐标，单位为米；``frame`` 是该点的坐标系。
        坐标系不一致时返回 ``False``，避免把 NED、ENU 或未知坐标混用。
        """
        if frame is not self.frame:
            return False
        return math.hypot(x_m - self.center_x_m, y_m - self.center_y_m) <= self.radius_m


@dataclass(frozen=True)
class NoFlyZone(KeepOutZone):
    """无人机禁飞区。

    该类型与 ``KeepOutZone`` 使用相同字段，名称上强调空中设备场景；地面设备也可以复用
    ``KeepOutZone`` 表达禁入区域。
    """


@dataclass(frozen=True)
class SafetyPolicy:
    """FleetDock 通用安全策略。

    所有距离、高度单位均为米，时间单位为秒，电量阈值使用 0 到 1 的剩余比例。字段保持可空，
    ``None`` 表示不启用对应规则。策略不绑定 PX4、DJI 或 Unitree 等厂商 SDK；只依赖设备对象的
    标准状态快照和标准命令能力。
    """

    max_relative_altitude_m: Optional[float] = None
    min_relative_altitude_m: Optional[float] = None
    max_distance_from_home_m: Optional[float] = None
    max_distance_from_leader_m: Optional[float] = None
    min_separation_m: Optional[float] = None
    telemetry_timeout_s: float = 2.0
    battery_warn_fraction: Optional[float] = 0.25
    battery_return_fraction: Optional[float] = 0.18
    battery_emergency_fraction: Optional[float] = 0.10
    keep_out_zones: tuple[KeepOutZone, ...] = ()
    no_fly_zones: tuple[NoFlyZone, ...] = ()
    action_on_violation: SafetyAction = SafetyAction.HOVER
    action_on_telemetry_timeout: SafetyAction = SafetyAction.HOVER
    action_on_failsafe: SafetyAction = SafetyAction.BLOCK_COMMAND

    def __post_init__(self) -> None:
        object.__setattr__(self, "action_on_violation", _coerce_action(self.action_on_violation))
        object.__setattr__(self, "action_on_telemetry_timeout", _coerce_action(self.action_on_telemetry_timeout))
        object.__setattr__(self, "action_on_failsafe", _coerce_action(self.action_on_failsafe))
        object.__setattr__(self, "keep_out_zones", tuple(self.keep_out_zones))
        object.__setattr__(self, "no_fly_zones", tuple(self.no_fly_zones))

    @classmethod
    def from_follow_options(
        cls,
        *,
        telemetry_timeout_s: float,
        max_follow_distance_m: float,
        min_separation_m: float,
        base_policy: Optional["SafetyPolicy"] = None,
    ) -> "SafetyPolicy":
        """从旧版跟随执行参数构造安全策略。

        ``telemetry_timeout_s`` 是本地位置遥测最长允许未更新秒数；``max_follow_distance_m`` 是
        follower 与 leader 的最大平面距离，单位为米；``min_separation_m`` 是多 follower 之间的
        最小平面间距，单位为米。未传 ``base_policy`` 时使用旧版参数生成默认策略，保持旧
        ``FollowExecutionOptions`` 行为；传入 ``base_policy`` 时严格保留调用方策略，其中 ``None``
        表示关闭对应安全规则，避免旧默认值覆盖显式配置。
        """
        if base_policy is None:
            return cls(
                telemetry_timeout_s=telemetry_timeout_s,
                max_distance_from_leader_m=max_follow_distance_m,
                min_separation_m=min_separation_m,
            )
        return base_policy


@dataclass(frozen=True)
class SafetyViolation:
    """一次安全规则违规。

    ``code`` 是稳定机器可读编码；``message`` 面向日志和 UI；``metric_value`` 与
    ``limit_value`` 在适用时使用米、秒或电量比例，具体语义由 ``code`` 决定。``action``
    是安全监督器建议执行的保护动作。
    """

    code: str
    message: str
    severity: SafetySeverity
    device_id: str
    action: SafetyAction
    metric_value: Optional[float] = None
    limit_value: Optional[float] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def blocks_command(self) -> bool:
        """返回该违规是否应阻断当前运动命令。"""
        return self.severity in (SafetySeverity.BLOCKING, SafetySeverity.CRITICAL)

    def to_event_payload(self) -> dict[str, Any]:
        """转换为标准安全事件可接收的可序列化载荷。

        返回字典可直接作为 ``EventType.SAFETY_VIOLATION_RAISED`` 的 payload，枚举字段会转换为
        稳定字符串。
        """
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
            "device_id": self.device_id,
            "action": self.action.value,
            "metric_value": self.metric_value,
            "limit_value": self.limit_value,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class SafetyDecision:
    """安全监督器输出的决策。

    ``allowed`` 表示当前命令是否可以继续发送；``action`` 是全部违规中优先级最高的推荐动作；
    ``violations`` 保留所有命中的规则，供事件发布、日志或上层 UI 展示。
    """

    allowed: bool
    action: SafetyAction = SafetyAction.NONE
    violations: tuple[SafetyViolation, ...] = ()

    @classmethod
    def allow(cls, violations: Iterable[SafetyViolation] = ()) -> "SafetyDecision":
        """创建允许继续执行的决策。

        ``violations`` 可携带不阻断命令的 warning 违规；返回 ``allowed=True`` 的决策对象。
        """
        warnings = tuple(violations)
        return cls(True, _select_action(warnings), warnings)

    @classmethod
    def block(cls, violations: Iterable[SafetyViolation]) -> "SafetyDecision":
        """创建阻断当前命令的决策。

        ``violations`` 是导致阻断的安全违规集合；返回对象会自动选择优先级最高的保护动作。
        """
        blocked = tuple(violations)
        return cls(False, _select_action(blocked), blocked)


class SafetySupervisor:
    """通用安全监督器。

    监督器只读取 ``BaseDevice.state.snapshot()`` 和标准能力声明，不直接调用设备 I/O。调用方可在
    Follow、Plan 或应用层根据 ``SafetyDecision.action`` 执行悬停、停止、返航或急停。
    """

    def __init__(self, policy: Optional[SafetyPolicy] = None) -> None:
        self.policy = policy or SafetyPolicy()

    def evaluate_device(self, device: BaseDevice, *, now_s: float) -> SafetyDecision:
        """检查单台设备当前状态并返回安全决策。

        ``device`` 是要检查的标准设备对象；``now_s`` 使用 ``time.monotonic()`` 秒值，用于判断
        遥测新鲜度。无人机启用高度规则；机器狗等地面设备会跳过高度规则，但仍检查遥测、电量、
        failsafe 和禁入区。返回 ``SafetyDecision``，调用方据此决定是否发送命令或执行保护动作。
        """
        state = device.state.snapshot()
        device_id = _device_public_id(device)
        violations: list[SafetyViolation] = []
        violations.extend(self._check_position_telemetry(device_id, state, now_s))
        violations.extend(self._check_failsafe(device_id, state))
        violations.extend(self._check_battery(device_id, state))
        violations.extend(self._check_altitude(device, device_id, state))
        violations.extend(self._check_home_distance(device_id, state))
        violations.extend(self._check_keep_out_zones(device, device_id, state))
        return _decision_from_violations(violations)

    def evaluate_leader_distance(
        self,
        follower: BaseDevice,
        leader: BaseDevice,
    ) -> SafetyDecision:
        """检查 follower 与 leader 的本地平面距离是否超过策略上限。

        距离单位为米；缺少任一设备本地位置时不在该方法内判定失败，调用方应先使用
        ``evaluate_device(...)`` 检查遥测可用性。返回 ``SafetyDecision``，违规编码为
        ``leader_distance_exceeded``。
        """
        max_distance_m = self.policy.max_distance_from_leader_m
        if max_distance_m is None:
            return SafetyDecision.allow()
        follower_xy = _local_xy(follower.state.snapshot())
        leader_xy = _local_xy(leader.state.snapshot())
        if follower_xy is None or leader_xy is None:
            return SafetyDecision.allow()
        distance_m = math.hypot(leader_xy[0] - follower_xy[0], leader_xy[1] - follower_xy[1])
        if distance_m <= max_distance_m:
            return SafetyDecision.allow()
        violation = SafetyViolation(
            code="leader_distance_exceeded",
            message=f"{_device_public_id(follower)} 与 leader 距离 {distance_m:.1f} m 超过限制 {max_distance_m:.1f} m。",
            severity=SafetySeverity.BLOCKING,
            device_id=_device_public_id(follower),
            action=self.policy.action_on_violation,
            metric_value=distance_m,
            limit_value=max_distance_m,
            metadata={"leader_id": _device_public_id(leader)},
        )
        return SafetyDecision.block((violation,))

    def evaluate_fleet_spacing(self, devices: Sequence[BaseDevice], *, now_s: float) -> SafetyDecision:
        """检查多设备之间的最小本地平面间距。

        ``devices`` 可以包含 leader 和 followers；``now_s`` 保留给后续新鲜度检查扩展，当前只用于
        保持接口一致。只比较已有本地位置的设备。距离低于 ``SafetyPolicy.min_separation_m`` 时
        返回阻断决策。
        """
        del now_s
        min_separation_m = self.policy.min_separation_m
        if min_separation_m is None:
            return SafetyDecision.allow()
        samples: list[tuple[BaseDevice, tuple[float, float]]] = []
        for device in devices:
            xy = _local_xy(device.state.snapshot())
            if xy is not None:
                samples.append((device, xy))
        violations: list[SafetyViolation] = []
        for index, (left, left_xy) in enumerate(samples):
            for right, right_xy in samples[index + 1:]:
                distance_m = math.hypot(left_xy[0] - right_xy[0], left_xy[1] - right_xy[1])
                if distance_m < min_separation_m:
                    violations.append(
                        SafetyViolation(
                            code="min_separation_breached",
                            message=(
                                f"{_device_public_id(left)} 与 {_device_public_id(right)} 间距 "
                                f"{distance_m:.1f} m 低于限制 {min_separation_m:.1f} m。"
                            ),
                            severity=SafetySeverity.BLOCKING,
                            device_id=_device_public_id(left),
                            action=self.policy.action_on_violation,
                            metric_value=distance_m,
                            limit_value=min_separation_m,
                            metadata={"other_device_id": _device_public_id(right)},
                        )
                    )
        return _decision_from_violations(violations)

    def evaluate_command(
        self,
        device: BaseDevice,
        command: Any,
        *,
        now_s: float,
    ) -> SafetyDecision:
        """检查即将发出的控制命令是否会明显扩大已知安全风险。

        ``device`` 是命令目标设备；``command`` 是 FollowExecutor 即将发送的控制命令或兼容对象；
        ``now_s`` 使用 ``time.monotonic()`` 秒值。第一版只基于当前状态和命令的垂直方向做轻量前置
        检查；复杂碰撞预测、局部路径重规划和动态障碍物应由后续 ObstacleProvider 接入。返回
        ``SafetyDecision``。
        """
        state_decision = self.evaluate_device(device, now_s=now_s)
        violations = list(state_decision.violations)
        z = getattr(command, "z", None)
        if z is not None:
            violations.extend(self._check_command_altitude(device, _device_public_id(device), device.state.snapshot(), float(z)))
        return _decision_from_violations(violations)

    def _check_position_telemetry(
        self,
        device_id: str,
        state: DeviceState,
        now_s: float,
    ) -> tuple[SafetyViolation, ...]:
        if state.local_x_m is None or state.local_y_m is None:
            return (
                SafetyViolation(
                    code="local_position_unavailable",
                    message=f"{device_id} 缺少本地位置遥测。",
                    severity=SafetySeverity.BLOCKING,
                    device_id=device_id,
                    action=self.policy.action_on_telemetry_timeout,
                ),
            )
        updated_at_s = state.local_position_updated_at_s
        if updated_at_s is None:
            return ()
        age_s = now_s - updated_at_s
        if age_s <= self.policy.telemetry_timeout_s:
            return ()
        return (
            SafetyViolation(
                code="local_position_timeout",
                message=f"{device_id} 本地位置遥测 {age_s:.1f} s 未更新。",
                severity=SafetySeverity.BLOCKING,
                device_id=device_id,
                action=self.policy.action_on_telemetry_timeout,
                metric_value=age_s,
                limit_value=self.policy.telemetry_timeout_s,
            ),
        )

    def _check_failsafe(self, device_id: str, state: DeviceState) -> tuple[SafetyViolation, ...]:
        if not state.failsafe:
            return ()
        return (
            SafetyViolation(
                code="failsafe_active",
                message=f"{device_id} 已进入 failsafe，阻断新的运动命令。",
                severity=SafetySeverity.CRITICAL,
                device_id=device_id,
                action=self.policy.action_on_failsafe,
            ),
        )

    def _check_battery(self, device_id: str, state: DeviceState) -> tuple[SafetyViolation, ...]:
        remaining = state.battery_remaining_fraction
        if remaining is None:
            return ()
        emergency = self.policy.battery_emergency_fraction
        if emergency is not None and remaining <= emergency:
            return (
                SafetyViolation(
                    code="battery_emergency",
                    message=f"{device_id} 电量 {remaining:.2f} 低于急停阈值 {emergency:.2f}。",
                    severity=SafetySeverity.CRITICAL,
                    device_id=device_id,
                    action=SafetyAction.EMERGENCY_STOP,
                    metric_value=remaining,
                    limit_value=emergency,
                ),
            )
        return_threshold = self.policy.battery_return_fraction
        if return_threshold is not None and remaining <= return_threshold:
            return (
                SafetyViolation(
                    code="battery_return_required",
                    message=f"{device_id} 电量 {remaining:.2f} 低于返航阈值 {return_threshold:.2f}。",
                    severity=SafetySeverity.CRITICAL,
                    device_id=device_id,
                    action=SafetyAction.RETURN_TO_LAUNCH,
                    metric_value=remaining,
                    limit_value=return_threshold,
                ),
            )
        warn = self.policy.battery_warn_fraction
        if warn is not None and remaining <= warn:
            return (
                SafetyViolation(
                    code="battery_low",
                    message=f"{device_id} 电量 {remaining:.2f} 低于告警阈值 {warn:.2f}。",
                    severity=SafetySeverity.WARNING,
                    device_id=device_id,
                    action=SafetyAction.NONE,
                    metric_value=remaining,
                    limit_value=warn,
                ),
            )
        return ()

    def _check_altitude(
        self,
        device: BaseDevice,
        device_id: str,
        state: DeviceState,
    ) -> tuple[SafetyViolation, ...]:
        if device.profile.category is not DeviceCategory.DRONE:
            return ()
        altitude = state.relative_altitude_m
        if altitude is None:
            local_z = state.local_z_m
            if local_z is not None and state.local_position_frame is CoordinateFrame.LOCAL_NED:
                altitude = -local_z
            elif local_z is not None:
                altitude = local_z
        if altitude is None:
            return ()
        violations: list[SafetyViolation] = []
        max_altitude = self.policy.max_relative_altitude_m
        if max_altitude is not None and altitude > max_altitude:
            violations.append(
                SafetyViolation(
                    code="altitude_too_high",
                    message=f"{device_id} 相对高度 {altitude:.1f} m 超过限制 {max_altitude:.1f} m。",
                    severity=SafetySeverity.BLOCKING,
                    device_id=device_id,
                    action=self.policy.action_on_violation,
                    metric_value=altitude,
                    limit_value=max_altitude,
                )
            )
        min_altitude = self.policy.min_relative_altitude_m
        if min_altitude is not None and altitude < min_altitude:
            violations.append(
                SafetyViolation(
                    code="altitude_too_low",
                    message=f"{device_id} 相对高度 {altitude:.1f} m 低于限制 {min_altitude:.1f} m。",
                    severity=SafetySeverity.BLOCKING,
                    device_id=device_id,
                    action=self.policy.action_on_violation,
                    metric_value=altitude,
                    limit_value=min_altitude,
                )
            )
        return tuple(violations)

    def _check_home_distance(self, device_id: str, state: DeviceState) -> tuple[SafetyViolation, ...]:
        max_distance = self.policy.max_distance_from_home_m
        if max_distance is None:
            return ()
        xy = _local_xy(state)
        if xy is None:
            return ()
        distance_m = math.hypot(xy[0], xy[1])
        if distance_m <= max_distance:
            return ()
        return (
            SafetyViolation(
                code="home_distance_exceeded",
                message=f"{device_id} 距本地原点 {distance_m:.1f} m 超过限制 {max_distance:.1f} m。",
                severity=SafetySeverity.BLOCKING,
                device_id=device_id,
                action=self.policy.action_on_violation,
                metric_value=distance_m,
                limit_value=max_distance,
            ),
        )

    def _check_keep_out_zones(
        self,
        device: BaseDevice,
        device_id: str,
        state: DeviceState,
    ) -> tuple[SafetyViolation, ...]:
        xy = _local_xy(state)
        if xy is None:
            return ()
        zones: tuple[KeepOutZone, ...] = self.policy.keep_out_zones
        if device.profile.category is DeviceCategory.DRONE:
            zones = (*zones, *self.policy.no_fly_zones)
        violations: list[SafetyViolation] = []
        for zone in zones:
            if zone.contains(xy[0], xy[1], state.local_position_frame):
                violations.append(
                    SafetyViolation(
                        code="keep_out_zone_entered",
                        message=f"{device_id} 已进入禁入区 {zone.name}。",
                        severity=SafetySeverity.CRITICAL,
                        device_id=device_id,
                        action=self.policy.action_on_violation,
                        metadata={"zone": zone.name},
                    )
                )
        return tuple(violations)

    def _check_command_altitude(
        self,
        device: BaseDevice,
        device_id: str,
        state: DeviceState,
        z: float,
    ) -> tuple[SafetyViolation, ...]:
        if device.profile.category is not DeviceCategory.DRONE:
            return ()
        if state.relative_altitude_m is None:
            return ()
        if self.policy.max_relative_altitude_m is not None and z > 0.0:
            if state.relative_altitude_m >= self.policy.max_relative_altitude_m:
                return (
                    SafetyViolation(
                        code="altitude_command_blocked",
                        message=f"{device_id} 已达到限高，阻断继续上升命令。",
                        severity=SafetySeverity.BLOCKING,
                        device_id=device_id,
                        action=self.policy.action_on_violation,
                        metric_value=state.relative_altitude_m,
                        limit_value=self.policy.max_relative_altitude_m,
                    ),
                )
        if self.policy.min_relative_altitude_m is not None and z < 0.0:
            if state.relative_altitude_m <= self.policy.min_relative_altitude_m:
                return (
                    SafetyViolation(
                        code="descent_command_blocked",
                        message=f"{device_id} 已达到最低高度，阻断继续下降命令。",
                        severity=SafetySeverity.BLOCKING,
                        device_id=device_id,
                        action=self.policy.action_on_violation,
                        metric_value=state.relative_altitude_m,
                        limit_value=self.policy.min_relative_altitude_m,
                    ),
                )
        return ()


def _decision_from_violations(violations: Iterable[SafetyViolation]) -> SafetyDecision:
    items = tuple(violations)
    if any(item.blocks_command for item in items):
        return SafetyDecision.block(items)
    return SafetyDecision.allow(items)


def _select_action(violations: Iterable[SafetyViolation]) -> SafetyAction:
    selected = SafetyAction.NONE
    for violation in violations:
        if _ACTION_PRIORITY[violation.action] > _ACTION_PRIORITY[selected]:
            selected = violation.action
    return selected


def _local_xy(state: DeviceState) -> Optional[tuple[float, float]]:
    if state.local_x_m is None or state.local_y_m is None:
        return None
    return float(state.local_x_m), float(state.local_y_m)


def _device_public_id(device: BaseDevice) -> str:
    return device.metadata.device_id or device.instance_id


def _coerce_action(action: SafetyAction | str) -> SafetyAction:
    if isinstance(action, SafetyAction):
        return action
    return SafetyAction(str(action))


_ACTION_PRIORITY = {
    SafetyAction.NONE: 0,
    SafetyAction.BLOCK_COMMAND: 1,
    SafetyAction.STOP: 2,
    SafetyAction.HOVER: 3,
    SafetyAction.RETURN_TO_LAUNCH: 4,
    SafetyAction.EMERGENCY_STOP: 5,
}
