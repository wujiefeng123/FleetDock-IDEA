"""跨设备能力就绪状态模型与标准评估规则。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Iterable, Optional

from fleetdock.models import CapabilityName, IntegrationMaturity

if TYPE_CHECKING:
    from fleetdock.devices import BaseDevice, DeviceState


class ReadinessRequirement(str, Enum):
    """设备执行某项标准能力前可被检查的跨厂商条件。"""

    CAPABILITY_SUPPORTED = "capability_supported"  # 产品 profile 声明支持目标能力。
    CONNECTED = "connected"  # 设备适配器已经建立可执行命令的连接。
    TELEMETRY_AVAILABLE = "telemetry_available"  # 至少收到过一条标准设备事件。
    ALTITUDE = "altitude"  # 空中设备相对 Home 高度遥测可用，单位为米。
    LOCAL_POSITION = "local_position"  # 本地坐标位置可用，坐标系由 DeviceState 声明。
    GLOBAL_POSITION = "global_position"  # 全球经纬度位置可用。
    HOME_POSITION = "home_position"  # 空中设备 Home 点或等价安全参考点可用。
    ARMABLE = "armable"  # 空中设备健康状态未禁止解锁。
    ARMED = "armed"  # 空中设备已经解锁，允许进入持续手动控制。
    IN_AIR = "in_air"  # 空中设备已经离地，允许发送空中机体系速度目标。


@dataclass(frozen=True)
class ReadinessIssue:
    """单项未就绪条件；包含标准条件枚举和面向开发者的可读原因。"""

    requirement: ReadinessRequirement
    message: str


@dataclass(frozen=True)
class ReadinessReport:
    """设备针对某项能力的不可变就绪报告。

    ``capability`` 是待执行的标准能力；``issues`` 为空表示当前可执行。报告是调用时快照，
    不会随后台遥测自动变化，调用方可重复查询或交给 ``wait_until_ready`` 等待。
    """

    device_id: str
    product: str
    capability: CapabilityName
    issues: tuple[ReadinessIssue, ...] = ()
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def ready(self) -> bool:
        """返回报告中是否不存在任何未就绪条件。"""
        return not self.issues

    @property
    def missing_requirements(self) -> tuple[ReadinessRequirement, ...]:
        """按评估顺序返回未满足的标准条件枚举。"""
        return tuple(issue.requirement for issue in self.issues)


@dataclass(frozen=True)
class DeviceDiagnostic:
    """设备连接和遥测的强类型诊断快照。

    ``lifecycle_state`` 描述 SDK 资源状态，``connected`` 表示适配器控制链路状态，
    ``maturity`` 表示产品集成成熟度；``telemetry_age_s`` 是最近标准事件距当前的单调时钟秒数，
    尚未收到事件时为 ``None``。该对象不包含连接密码、令牌或厂商原生对象。
    """

    device_id: str
    product: str
    lifecycle_state: str
    connected: bool
    maturity: IntegrationMaturity
    telemetry_age_s: Optional[float]
    armed: Optional[bool]
    in_air: Optional[bool]
    flight_mode: Optional[str]
    failsafe: Optional[bool]


def evaluate_readiness(
    device: "BaseDevice",
    capability: CapabilityName,
    *,
    extra_requirements: Iterable[ReadinessRequirement] = (),
    ignored_requirements: Iterable[ReadinessRequirement] = (),
) -> ReadinessReport:
    """按产品能力、标准状态和调用方附加条件生成就绪报告。

    ``device`` 是要评估的标准设备对象；``capability`` 是计划执行的标准能力；
    ``extra_requirements`` 可追加本次调用场景必须满足的条件，``ignored_requirements`` 可显式忽略
    默认条件。返回值是调用瞬间的 ``ReadinessReport`` 快照，不会等待遥测变化。
    """
    if not isinstance(capability, CapabilityName):
        capability = CapabilityName(capability)
    required = _requirements_for_capability(capability)
    required.update(extra_requirements)
    required.difference_update(ignored_requirements)
    issues: list[ReadinessIssue] = []
    state = device.state.snapshot()

    if not device.profile.has_capability(capability):
        issues.append(ReadinessIssue(
            ReadinessRequirement.CAPABILITY_SUPPORTED,
            f"Product '{device.product_key}' does not declare capability '{capability.value}'.",
        ))
    for requirement in ReadinessRequirement:
        if requirement is ReadinessRequirement.CAPABILITY_SUPPORTED or requirement not in required:
            continue
        message = _missing_message(requirement, device, state)
        if message is not None:
            issues.append(ReadinessIssue(requirement, message))

    adapter_check = getattr(device.adapter, "additional_readiness_issues", None)
    if callable(adapter_check):
        issues.extend(adapter_check(capability, state) or ())
    return ReadinessReport(
        device_id=device.metadata.device_id or device.instance_id,
        product=device.product_key,
        capability=capability,
        issues=tuple(issues),
        checked_at=datetime.now(timezone.utc),
    )


def _requirements_for_capability(capability: CapabilityName) -> set[ReadinessRequirement]:
    requirements = {ReadinessRequirement.CONNECTED}
    if capability is CapabilityName.TELEMETRY:
        requirements.add(ReadinessRequirement.TELEMETRY_AVAILABLE)
    if capability in {CapabilityName.OFFBOARD, CapabilityName.MISSION, CapabilityName.ROUTE}:
        requirements.add(ReadinessRequirement.LOCAL_POSITION)
    if capability is CapabilityName.MANUAL_CONTROL:
        requirements.update({
            ReadinessRequirement.TELEMETRY_AVAILABLE,
            ReadinessRequirement.LOCAL_POSITION,
            ReadinessRequirement.ARMED,
            ReadinessRequirement.IN_AIR,
        })
    if capability is CapabilityName.TAKEOFF:
        requirements.update({
            ReadinessRequirement.ALTITUDE,
            ReadinessRequirement.LOCAL_POSITION,
            ReadinessRequirement.HOME_POSITION,
            ReadinessRequirement.ARMABLE,
        })
    return requirements


def _missing_message(
    requirement: ReadinessRequirement,
    device: "BaseDevice",
    state: "DeviceState",
) -> Optional[str]:
    if requirement is ReadinessRequirement.CONNECTED:
        if bool(getattr(device.adapter, "connected", False)) or state.connected is True:
            return None
        return "Device is not connected."
    if requirement is ReadinessRequirement.TELEMETRY_AVAILABLE:
        return None if state.last_event_updated_at_s is not None else "No standard telemetry event has been received."
    if requirement is ReadinessRequirement.ALTITUDE:
        return None if state.relative_altitude_m is not None else "Relative altitude telemetry is unavailable."
    if requirement is ReadinessRequirement.LOCAL_POSITION:
        if state.health_local_position_ok is not False and state.local_x_m is not None and state.local_y_m is not None:
            return None
        return "Local position is unavailable or reported unhealthy."
    if requirement is ReadinessRequirement.GLOBAL_POSITION:
        if state.health_global_position_ok is not False and state.latitude_deg is not None and state.longitude_deg is not None:
            return None
        return "Global position is unavailable or reported unhealthy."
    if requirement is ReadinessRequirement.HOME_POSITION:
        if state.health_home_position_ok is not False and (
            state.home_absolute_altitude_m is not None
            or (state.home_latitude_deg is not None and state.home_longitude_deg is not None)
        ):
            return None
        return "Home position or equivalent safety reference is unavailable."
    if requirement is ReadinessRequirement.ARMABLE:
        return None if state.health_armable is not False else "Device health reports that arming is not allowed."
    if requirement is ReadinessRequirement.ARMED:
        return None if state.armed is True else "Device is not armed."
    if requirement is ReadinessRequirement.IN_AIR:
        return None if state.in_air is True else "Device is not in air."
    return None
