"""FleetDock 标准设备事件类型、数据对象与载荷转换。"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Union
from uuid import uuid4

from fleetdock._frozen import freeze_mapping, freeze_value

class EventCategory(str, Enum):
    """单设备事件的语义分类，用于帮助调用方按生命周期、命令、遥测等领域过滤事件。"""

    # 生命周期事件，例如连接状态变化。
    LIFECYCLE = "lifecycle"
    # 命令执行事件，例如命令开始、完成。
    COMMAND = "command"
    # 遥测事件，例如位置、电量、姿态。
    TELEMETRY = "telemetry"
    # 状态变化事件，例如解锁状态、飞行模式变化。
    STATE = "state"
    # 告警事件，例如错误、失效保护或安全警告。
    ALERT = "alert"
    # 应用自定义事件分类。
    CUSTOM = "custom"
    # 传感器发现、流生命周期和后端诊断事件。
    SENSOR = "sensor"


class CoordinateFrame(str, Enum):
    """空间数据坐标系；位置、速度等字段必须明确使用哪个坐标框架。"""

    # 本地 NED 坐标系：x 北、y 东、z 下，单位为米或米/秒。
    LOCAL_NED = "local_ned"
    # 本地 ENU 坐标系：x 东、y 北、z 上，单位为米或米/秒。
    LOCAL_ENU = "local_enu"
    # 机体系/设备本体系，随设备姿态变化。
    BODY = "body"
    # 航空器 FRD 机体系：x 前、y 右、z 下，适用于 PX4/MAVLink 惯性与机体运动数据。
    BODY_FRD = "body_frd"
    # 机器人 FLU 机体系：x 前、y 左、z 上，适用于 ROS 和常见足式机器人本体数据。
    BODY_FLU = "body_flu"
    # 大地坐标系，经纬度使用度，高度使用米。
    GEODETIC = "geodetic"
    # 未知或厂商未声明坐标系。
    UNKNOWN = "unknown"


class EventType(str, Enum):
    """FleetDock 标准事件类型。

    Device 与 Fleet 使用同一枚举订阅事件；枚举值按 core、motion、navigation、power、
    safety、aerial 和 legged 命名空间区分领域，调用方无需选择不同的分组枚举。
    """

    CONNECTION_CHANGED = "core.connection.changed"  # 设备连接状态发生变化。
    COMMAND_STARTED = "core.command.started"  # 标准命令开始执行。
    COMMAND_COMPLETED = "core.command.completed"  # 标准命令执行结束。
    ERROR_OCCURRED = "core.error.occurred"  # SDK 或适配器报告错误。
    HANDLER_FAILED = "core.handler.failed"  # 事件订阅回调执行失败。
    SENSOR_DISCOVERED = "sensor.discovered"  # SensorHub 发现一个可订阅传感器。
    SENSOR_LOST = "sensor.lost"  # 刷新发现时一个非活动传感器不再可用。
    SENSOR_STREAM_STARTED = "sensor.stream.started"  # 首个订阅者启动原生传感器流。
    SENSOR_STREAM_STOPPED = "sensor.stream.stopped"  # 最后一个订阅者停止原生传感器流。
    SENSOR_STREAM_ERROR = "sensor.stream.error"  # 原生传感器流启动或订阅失败。
    VELOCITY_UPDATED = "motion.velocity.updated"  # 带坐标系的线速度更新，单位为米/秒。
    ATTITUDE_UPDATED = "motion.attitude.updated"  # 姿态角更新，单位为度。
    GLOBAL_POSITION_UPDATED = "navigation.global_position.updated"  # 全球位置更新。
    LOCAL_POSITION_UPDATED = "navigation.local_position.updated"  # 带坐标系的本地位置更新，单位为米。
    HOME_POSITION_UPDATED = "navigation.home_position.updated"  # Home 点位置更新。
    ROUTE_STARTED = "navigation.route.started"  # 航线开始执行。
    ROUTE_COMPLETED = "navigation.route.completed"  # 航线执行完成。
    ROUTE_REPLANNED = "navigation.route.replanned"  # SDK 因避障插入临时路线点。
    WAYPOINT_STARTED = "navigation.waypoint.started"  # 航点开始执行。
    WAYPOINT_REACHED = "navigation.waypoint.reached"  # 设备进入航点接受范围。
    WAYPOINT_SETTLED = "navigation.waypoint.settled"  # 设备在航点满足稳定条件。
    WAYPOINT_ACTION_REQUESTED = "navigation.waypoint.action_requested"  # 航点动作被请求。
    BATTERY_UPDATED = "power.battery.updated"  # 电池状态更新。
    HEALTH_UPDATED = "safety.health.updated"  # 设备健康状态更新。
    WARNING_RAISED = "safety.warning.raised"  # 设备或适配器产生安全告警。
    FAILSAFE_CHANGED = "safety.failsafe.changed"  # 失效保护状态变化。
    SAFETY_VIOLATION_RAISED = "safety.violation.raised"  # SDK 安全策略发现限高、间距、电量等违规。
    SAFETY_ACTION_TRIGGERED = "safety.action.triggered"  # SDK 根据安全策略触发悬停、停止、返航或急停。
    COLLISION_RISK_RAISED = "safety.collision_risk.raised"  # SDK 避障预测发现碰撞风险。
    AVOIDANCE_ACTION_TRIGGERED = "safety.avoidance.action.triggered"  # SDK 避障策略触发阻断、悬停、停止或绕行。
    OBSTACLE_SNAPSHOT_UPDATED = "safety.obstacle.snapshot.updated"  # 障碍物 provider 快照完成更新。
    OBSTACLE_PROVIDER_STALE = "safety.obstacle.provider.stale"  # 障碍物 provider 或障碍物数据已过期。
    ARMED_CHANGED = "aerial.armed.changed"  # 无人机解锁或上锁状态变化。
    IN_AIR_CHANGED = "aerial.in_air.changed"  # 无人机空中状态变化。
    LANDED_STATE_CHANGED = "aerial.landed_state.changed"  # 无人机着陆状态变化。
    FLIGHT_MODE_CHANGED = "aerial.flight_mode.changed"  # 无人机飞行模式变化。
    POSTURE_CHANGED = "legged.posture.changed"  # 足式设备姿态变化。
    GAIT_CHANGED = "legged.gait.changed"  # 足式设备步态变化。
    FOOT_CONTACT_UPDATED = "legged.foot_contact.updated"  # 足端接触状态更新。


# 兼容原分组名称；所有名称现在都指向同一个标准事件枚举类。
CoreEventType = EventType
MotionEventType = EventType
NavigationEventType = EventType
PowerEventType = EventType
SafetyEventType = EventType
AerialEventType = EventType
LeggedEventType = EventType

StandardEventType = EventType

STANDARD_EVENT_ENUMS = (EventType,)


class RouteCompletionReason(str, Enum):
    """航线正常结束原因，用于区分完整飞完与达到调用方设置的时长上限。"""

    ROUTE_FINISHED = "route_finished"  # 全部航点均已执行完成。
    DURATION_REACHED = "duration_reached"  # 达到 max_duration_s 后按预期停止。


class Events:
    """标准设备事件兼容命名空间。

    新代码应直接使用 `EventType`；原分组枚举暂时保留，便于已有代码平滑迁移。
    """

    EventType = EventType
    CoreEventType = CoreEventType
    MotionEventType = MotionEventType
    NavigationEventType = NavigationEventType
    PowerEventType = PowerEventType
    SafetyEventType = SafetyEventType
    AerialEventType = AerialEventType
    LeggedEventType = LeggedEventType


class ExtensionEventType(str):
    """厂商或应用扩展事件；标准事件必须使用对应枚举。"""

    def __new__(cls, value: str) -> "ExtensionEventType":
        if not value.startswith(("vendor.", "custom.", "extension.")):
            raise ValueError("Extension event type must start with 'vendor.', 'custom.' or 'extension.'.")
        return str.__new__(cls, value)

    @property
    def value(self) -> str:
        """返回扩展事件名字符串，可用于和标准枚举的 `.value` 一样参与订阅匹配。"""
        return str(self)


DeviceEventType = Union[StandardEventType, ExtensionEventType]
DeviceEventName = Union[DeviceEventType, str]


def parse_standard_event_type(value: str) -> StandardEventType:
    """把 `value` 标准事件名解析为 `EventType` 并返回；未知名称会抛出 `ValueError`。"""
    return EventType(value)


@dataclass(frozen=True)
class EventSource:
    """设备事件来源信息。

    `device_id` 是业务设备标识，未配置时回退为 SDK 实例标识；`instance_id` 始终记录 SDK
    实例标识。`product` 是产品键，`device_category` 表示设备类别，`adapter` 记录适配器名称。
    """

    device_id: str
    product: str
    device_category: str
    adapter: str
    instance_id: str = ""

    def __post_init__(self) -> None:
        if not self.instance_id:
            object.__setattr__(self, "instance_id", self.device_id)


@dataclass(frozen=True)
class OriginalEvent:
    """厂商原始事件快照。

    标准事件转换成功后仍可通过该对象保留来源厂商、原事件名、可序列化原始数据和可选
    `native_data`。`native_data` 仅用于同进程高级接入，不保证可序列化。
    """

    provider: str
    event_name: str
    raw_data: Any
    native_data: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw_data", freeze_value(self.raw_data))


def serialize_event_data(value: Any) -> Any:
    """把 ``value`` 递归转换为可序列化结构并返回，用于 ``OriginalEvent.raw_data``。"""
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): serialize_event_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [serialize_event_data(item) for item in value]
    if is_dataclass(value):
        return {item.name: serialize_event_data(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, BaseException):
        return {"type": type(value).__name__, "message": str(value)}
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, dict):
        return {
            key: serialize_event_data(item)
            for key, item in attributes.items()
            if not key.startswith("_")
        }
    return str(value)


@dataclass(frozen=True)
class Vector3:
    """三轴向量；具体单位和坐标系由包含它的字段声明，例如米或米/秒。"""

    x: float
    y: float
    z: float


@dataclass(frozen=True)
class GlobalPosition:
    """全球位置。

    经纬度单位为度，绝对高度和相对高度单位为米；`relative_altitude_m` 可为空，表示厂商
    或飞控未提供相对 Home 点高度。
    """

    latitude_deg: float
    longitude_deg: float
    absolute_altitude_m: float
    relative_altitude_m: Optional[float] = None


@dataclass(frozen=True)
class LocalPosition:
    """本地位置。

    `frame` 明确坐标系，`position_m` 的 x/y/z 单位为米；厂商适配器不得在坐标系未知时
    默默伪装成 NED 或 ENU。
    """

    frame: CoordinateFrame
    position_m: Vector3


@dataclass(frozen=True)
class Velocity:
    """线速度。

    `frame` 明确速度所在坐标系，`linear_velocity_m_s` 的 x/y/z 单位为米/秒。
    """

    frame: CoordinateFrame
    linear_velocity_m_s: Vector3


@dataclass(frozen=True)
class Attitude:
    """欧拉角姿态，roll、pitch、yaw 均使用度。"""

    roll_deg: float
    pitch_deg: float
    yaw_deg: float


@dataclass(frozen=True)
class BatteryState:
    """电池状态。

    `remaining_fraction` 是 0 到 1 的剩余比例；电压单位为伏，电流单位为安，温度单位为
    摄氏度。厂商未提供的字段保持 None。
    """

    remaining_fraction: float
    voltage_v: Optional[float] = None
    current_a: Optional[float] = None
    temperature_c: Optional[float] = None


@dataclass(frozen=True)
class HealthState:
    """设备健康状态。

    所有字段均为可空布尔值；None 表示底层 SDK 未提供或当前无法判断，不应被调用方当成
    False 的故障结论。
    """

    gyrometer_calibration_ok: Optional[bool] = None
    accelerometer_calibration_ok: Optional[bool] = None
    magnetometer_calibration_ok: Optional[bool] = None
    local_position_ok: Optional[bool] = None
    global_position_ok: Optional[bool] = None
    home_position_ok: Optional[bool] = None
    armable: Optional[bool] = None


@dataclass(frozen=True)
class StateChange:
    """通用状态变化载荷；记录变化前后值和可选原因。"""

    previous: Any
    current: Any
    reason: Optional[str] = None


@dataclass(frozen=True)
class CommandStarted:
    """标准命令开始事件载荷；`command_id` 用于和完成事件关联。"""

    command: str
    command_id: str


@dataclass(frozen=True)
class CommandCompleted:
    """标准命令完成事件载荷；`ok` 和 `code` 描述命令结果。"""

    command: str
    command_id: str
    ok: bool
    code: str
    message: str


@dataclass(frozen=True)
class ErrorInfo:
    """错误事件载荷；`stream` 可标记产生错误的遥测流或后台任务。"""

    message: str
    stream: Optional[str] = None


@dataclass(frozen=True)
class SensorStreamInfo:
    """传感器发现和流生命周期事件载荷。

    ``sensor_id`` 是单设备唯一 ID，``sensor_kind`` 是标准或扩展类型完整名称；``message`` 仅在
    错误或需要补充诊断时使用，不包含流地址、密码、令牌等敏感连接信息。
    """

    sensor_id: str
    sensor_kind: str
    message: str = ""


@dataclass(frozen=True)
class EventHandlerFailure:
    """设备事件回调失败的诊断载荷。

    ``event_id`` 和 ``event_type`` 指向触发失败的原事件；``handler_name`` 是可读处理器名，
    ``consecutive_failures`` 表示该订阅连续失败次数。异常仅以类型和消息公开，不携带对象。
    """

    event_id: str
    event_type: str
    handler_name: str
    exception_type: str
    message: str
    consecutive_failures: int


@dataclass(frozen=True)
class WarningInfo:
    """安全告警载荷；`code` 是稳定机器可读编码，`message` 是中文或厂商原文说明。"""

    code: str
    message: str


@dataclass(frozen=True)
class SafetyViolationInfo:
    """安全策略违规载荷。

    ``code`` 是稳定机器可读编码；``severity`` 表示严重程度；``device_id`` 是触发违规的业务设备 ID；
    ``action`` 是 SDK 建议或已经准备执行的保护动作。``metric_value`` 与 ``limit_value`` 在适用时使用
    米、秒或电量比例，具体语义由 ``code`` 决定；``metadata`` 保存关联设备、禁入区等扩展信息。
    """

    code: str
    message: str
    severity: str
    device_id: str
    action: str
    metric_value: Optional[float] = None
    limit_value: Optional[float] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))


@dataclass(frozen=True)
class SafetyActionInfo:
    """安全保护动作载荷。

    ``action`` 表示 SDK 根据安全策略触发的动作，例如 ``hover``、``stop``、``return_to_launch`` 或
    ``emergency_stop``；``device_id`` 是动作目标设备；``violation_codes`` 记录触发动作的违规编码；
    ``ok`` 表示动作命令是否成功发送或执行，``message`` 保存适配器响应或失败原因。
    """

    action: str
    device_id: str
    violation_codes: tuple[str, ...] = ()
    ok: Optional[bool] = None
    message: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))


@dataclass(frozen=True)
class ObstacleSnapshotInfo:
    """障碍物快照更新载荷。

    ``provider_name`` 是聚合或原始 provider 名称；``obstacle_count`` 是本次快照中的障碍物数量；
    ``observed_at_s`` 使用进程内单调时钟秒值，只用于判断数据新鲜度。
    """

    provider_name: str
    obstacle_count: int
    observed_at_s: float


@dataclass(frozen=True)
class ObstacleProviderStaleInfo:
    """障碍物 provider 过期或异常载荷。"""

    provider_name: str
    reason: str = ""


@dataclass(frozen=True)
class RouteReplannedInfo:
    """航线局部重规划载荷。

    ``route_id`` 和 ``route_name`` 标识原路线；``reason`` 说明重规划原因；``detour_count`` 是本次插入
    的临时绕行点数量；``metadata`` 可记录障碍物 ID、距离阈值等可序列化诊断信息。
    """

    route_id: str
    route_name: Optional[str]
    reason: str
    detour_count: int
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))


@dataclass(frozen=True)
class FootContactState:
    """足式设备足端接触状态；键为足端名称，值表示是否接触地面。"""

    contacts: Mapping[str, bool]

    def __post_init__(self) -> None:
        object.__setattr__(self, "contacts", freeze_mapping(self.contacts))


@dataclass(frozen=True)
class RouteProgress:
    """航线级进度载荷；用于航线开始和完成事件。

    ``completion_reason`` 在开始事件中为 ``None``；完成事件中说明航线是完整飞完，还是达到
    调用方设置的最长执行秒数后正常停止。该字段描述正常完成，不用于表达超时或设备错误。
    """

    route_id: str
    route_name: Optional[str]
    waypoint_count: int
    pattern: Optional[str] = None
    completion_reason: Optional[RouteCompletionReason] = None


@dataclass(frozen=True)
class WaypointProgress:
    """航点级进度载荷。

    本地坐标 x/y/z 单位为米；全球坐标经纬度单位为度，高度和距离单位为米；缺失字段保持
    None，避免伪造坐标或距离。
    """

    route_id: str
    route_name: Optional[str]
    waypoint_id: str
    waypoint_index: int
    waypoint_count: int
    x_m: Optional[float] = None
    y_m: Optional[float] = None
    z_m: Optional[float] = None
    latitude_deg: Optional[float] = None
    longitude_deg: Optional[float] = None
    altitude_m: Optional[float] = None
    distance_m: Optional[float] = None
    action: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))


@dataclass(frozen=True)
class DeviceEvent:
    """单设备事件基类。

    标准事件使用枚举类型和强类型 `_data` 载荷；厂商、自定义或扩展事件使用
    `ExtensionEventType`。`sequence` 在单设备事件流内递增，时间字段使用 UTC。
    """

    type: DeviceEventType
    category: EventCategory
    source: EventSource
    sequence: int
    _data: Any = field(default=None, repr=False)
    extensions: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    original: Optional[OriginalEvent] = None
    schema_version: int = 1
    correlation_id: Optional[str] = None
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(self, "extensions", freeze_mapping(self.extensions))

    def to_dict(
        self,
        *,
        include_original: bool = True,
        include_native_data: bool = False,
    ) -> Mapping[str, Any]:
        """返回稳定、可序列化的标准事件字典。

        ``include_original`` 控制是否输出 ``OriginalEvent`` 中的来源厂商、原事件名和可序列化原数据；
        默认包含来源厂商和 ``OriginalEvent.raw_data``，但不包含不保证可序列化的
        ``native_data``；只有单进程诊断明确需要时才应设置 ``include_native_data=True``。
        时间字段输出 UTC ISO 8601 字符串，事件类型、类别和载荷递归转换为基础结构。
        """
        result: dict[str, Any] = {
            "event_id": self.event_id,
            "type": self.type.value,
            "category": self.category.value,
            "source": serialize_event_data(self.source),
            "sequence": self.sequence,
            "data": serialize_event_data(self._data),
            "extensions": serialize_event_data(self.extensions),
            "schema_version": self.schema_version,
            "correlation_id": self.correlation_id,
            "occurred_at": self.occurred_at.isoformat(),
            "received_at": self.received_at.isoformat(),
        }
        if include_original and self.original is not None:
            original = {
                "provider": self.original.provider,
                "event_name": self.original.event_name,
                "raw_data": serialize_event_data(self.original.raw_data),
            }
            if include_native_data:
                original["native_data"] = serialize_event_data(self.original.native_data)
            result["original"] = original
        return result


class ConnectionChangedEvent(DeviceEvent):
    """连接状态变化事件；`change` 返回连接前后状态和变化原因。"""

    @property
    def change(self) -> StateChange:
        """返回连接前后状态及可选变化原因组成的强类型载荷。"""
        return self._data


class CommandStartedEvent(DeviceEvent):
    """标准命令开始事件；`command` 返回命令名和 command_id。"""

    @property
    def command(self) -> CommandStarted:
        """返回正在开始执行的标准命令名称和关联命令 ID。"""
        return self._data


class CommandCompletedEvent(DeviceEvent):
    """标准命令完成事件；`command` 返回执行结果、错误码和消息。"""

    @property
    def command(self) -> CommandCompleted:
        """返回标准命令名称、关联 ID、成功状态、错误码和消息。"""
        return self._data


class ErrorOccurredEvent(DeviceEvent):
    """错误事件；用于适配器、遥测后台任务或命令流程报告异常。"""

    @property
    def error(self) -> ErrorInfo:
        """返回错误消息及产生错误的可选遥测流名称。"""
        return self._data


class SensorStreamEvent(DeviceEvent):
    """传感器发现、丢失、流启动、停止或错误事件。"""

    @property
    def sensor(self) -> SensorStreamInfo:
        """返回传感器 ID、标准或扩展类型名称及可选诊断消息。"""
        return self._data


class EventHandlerFailedEvent(DeviceEvent):
    """事件订阅回调失败事件；``failure`` 返回原事件和处理器诊断信息。"""

    @property
    def failure(self) -> EventHandlerFailure:
        """返回回调失败的强类型诊断载荷。"""
        return self._data


class GlobalPositionUpdatedEvent(DeviceEvent):
    """全球位置更新事件；`position` 返回经纬度和高度，单位见 `GlobalPosition`。"""

    @property
    def position(self) -> GlobalPosition:
        """返回经纬度、AMSL 高度和可选相对 Home 高度的全球位置。"""
        return self._data


class HomePositionUpdatedEvent(GlobalPositionUpdatedEvent):
    """Home 点位置更新事件，载荷格式与全球位置一致。"""

    pass


class LocalPositionUpdatedEvent(DeviceEvent):
    """本地位置更新事件；`position` 返回带坐标系的米级位置。"""

    @property
    def position(self) -> LocalPosition:
        """返回明确坐标系且三轴单位为米的本地位置载荷。"""
        return self._data


class RouteStartedEvent(DeviceEvent):
    """航线开始事件；`progress` 返回航线标识、名称、航点数量和图形模式。"""

    @property
    def progress(self) -> RouteProgress:
        """返回航线 ID、名称、航点数量及可选图形模式。"""
        return self._data


class RouteCompletedEvent(RouteStartedEvent):
    """航线完成事件，载荷格式与航线开始事件一致。"""

    pass


class WaypointStartedEvent(DeviceEvent):
    """航点开始事件；`progress` 返回航点索引、位置和动作信息。"""

    @property
    def progress(self) -> WaypointProgress:
        """返回航点索引、坐标、距离和待执行动作等进度信息。"""
        return self._data


class WaypointReachedEvent(WaypointStartedEvent):
    """航点到达事件，表示设备进入航点接受范围。"""

    pass


class WaypointSettledEvent(WaypointStartedEvent):
    """航点稳定事件，表示设备在航点满足速度或停留判定。"""

    pass


class WaypointActionRequestedEvent(WaypointStartedEvent):
    """航点动作请求事件，例如拍照或调用方自定义动作。"""

    pass


class VelocityUpdatedEvent(DeviceEvent):
    """速度更新事件；`velocity` 返回带坐标系的三轴速度，单位为米/秒。"""

    @property
    def velocity(self) -> Velocity:
        """返回明确坐标系且三轴单位为米/秒的线速度载荷。"""
        return self._data


class AttitudeUpdatedEvent(DeviceEvent):
    """姿态更新事件；`attitude` 返回 roll、pitch、yaw，单位为度。"""

    @property
    def attitude(self) -> Attitude:
        """返回单位为度的滚转角、俯仰角和偏航角姿态载荷。"""
        return self._data


class BatteryUpdatedEvent(DeviceEvent):
    """电池状态更新事件；`battery` 返回剩余比例、电压、电流和温度。"""

    @property
    def battery(self) -> BatteryState:
        """返回剩余比例及厂商可选提供的电压、电流和温度。"""
        return self._data


class HealthUpdatedEvent(DeviceEvent):
    """健康状态更新事件；`health` 返回定位、校准和可解锁等可空状态。"""

    @property
    def health(self) -> HealthState:
        """返回定位、传感器校准和可解锁状态；未知字段保持 ``None``。"""
        return self._data


class StateChangedEvent(DeviceEvent):
    """通用状态变化事件；具体语义由子类事件类型决定。"""

    @property
    def change(self) -> StateChange:
        """返回业务状态变化前后值及适配器可选提供的原因。"""
        return self._data


class ArmedChangedEvent(StateChangedEvent):
    """无人机解锁/上锁状态变化事件。"""

    pass


class InAirChangedEvent(StateChangedEvent):
    """无人机是否处于空中状态的变化事件。"""

    pass


class LandedStateChangedEvent(StateChangedEvent):
    """无人机着陆状态变化事件。"""

    pass


class FlightModeChangedEvent(StateChangedEvent):
    """无人机飞行模式变化事件。"""

    pass


class FailsafeChangedEvent(StateChangedEvent):
    """失效保护状态变化事件；变化值表示保护是否启用及可选触发原因。"""

    pass


class PostureChangedEvent(StateChangedEvent):
    """足式设备姿态或站坐等状态变化事件。"""

    pass


class GaitChangedEvent(StateChangedEvent):
    """足式设备步态模式变化事件。"""

    pass


class WarningRaisedEvent(DeviceEvent):
    """安全告警事件；`warning` 返回告警编码和说明。"""

    @property
    def warning(self) -> WarningInfo:
        """返回稳定机器可读告警码和面向调用方的告警说明。"""
        return self._data


class SafetyViolationRaisedEvent(DeviceEvent):
    """安全策略违规事件；`violation` 返回违规编码、严重程度、设备和建议动作。"""

    @property
    def violation(self) -> SafetyViolationInfo:
        """返回安全策略违规的强类型载荷。"""
        return self._data


class SafetyActionTriggeredEvent(DeviceEvent):
    """安全保护动作事件；`action` 返回动作名称、目标设备和执行结果。"""

    @property
    def action(self) -> SafetyActionInfo:
        """返回安全保护动作的强类型载荷。"""
        return self._data


class CollisionRiskRaisedEvent(SafetyViolationRaisedEvent):
    """碰撞风险事件；``violation`` 返回障碍物、距离阈值和建议动作等风险信息。"""

    pass


class AvoidanceActionTriggeredEvent(SafetyActionTriggeredEvent):
    """避障动作事件；``action`` 返回阻断、悬停、停止、返航或绕行结果。"""

    pass


class ObstacleSnapshotUpdatedEvent(DeviceEvent):
    """障碍物快照更新事件；``snapshot`` 返回 provider 名称、数量和时间戳。"""

    @property
    def snapshot(self) -> ObstacleSnapshotInfo:
        """返回障碍物快照摘要，包含 provider 名称、障碍物数量和单调时钟秒值时间戳。"""
        return self._data


class ObstacleProviderStaleEvent(DeviceEvent):
    """障碍物 provider 过期或异常事件；``stale`` 返回来源和原因。"""

    @property
    def stale(self) -> ObstacleProviderStaleInfo:
        """返回过期或异常的 provider 名称及原因。"""
        return self._data


class RouteReplannedEvent(DeviceEvent):
    """航线局部重规划事件；``replan`` 返回本次插入临时绕行点的摘要。"""

    @property
    def replan(self) -> RouteReplannedInfo:
        """返回重规划原因、临时点数量和可序列化诊断信息。"""
        return self._data


class FootContactUpdatedEvent(DeviceEvent):
    """足式设备足端接触状态更新事件。"""

    @property
    def contact(self) -> FootContactState:
        """返回各足端名称到是否接触地面的布尔状态映射。"""
        return self._data


class VendorEvent(DeviceEvent):
    """厂商事件透传对象。

    当厂商原生事件无法映射为 FleetDock 标准事件时使用该类型；调用方可读取
    `event_name`、可序列化 `raw_data` 和可选 `native_data`。
    """

    @property
    def event_name(self) -> str:
        """返回厂商事件名，例如 `vendor.px4.estimator_status.updated`。"""
        return self.type.value

    @property
    def raw_data(self) -> Any:
        """返回可序列化厂商原始数据；若没有 `OriginalEvent` 则返回事件载荷。"""
        return self.original.raw_data if self.original is not None else self._data

    @property
    def native_data(self) -> Any:
        """返回进程内原生对象；该值可能不可序列化，也可能为 None。"""
        return self.original.native_data if self.original is not None else None


class CustomEvent(DeviceEvent):
    """应用自定义事件对象；事件名应使用 `custom.<应用>.<事件>` 命名空间。"""

    @property
    def event_name(self) -> str:
        """返回 ``custom.<应用>.<事件>`` 命名空间中的完整自定义事件名。"""
        return self.type.value

    @property
    def data(self) -> Any:
        """返回调用方发布的自定义事件数据。"""
        return self._data


STANDARD_EVENT_CLASSES = {
    EventType.CONNECTION_CHANGED: ConnectionChangedEvent,
    EventType.COMMAND_STARTED: CommandStartedEvent,
    EventType.COMMAND_COMPLETED: CommandCompletedEvent,
    EventType.ERROR_OCCURRED: ErrorOccurredEvent,
    EventType.HANDLER_FAILED: EventHandlerFailedEvent,
    EventType.SENSOR_DISCOVERED: SensorStreamEvent,
    EventType.SENSOR_LOST: SensorStreamEvent,
    EventType.SENSOR_STREAM_STARTED: SensorStreamEvent,
    EventType.SENSOR_STREAM_STOPPED: SensorStreamEvent,
    EventType.SENSOR_STREAM_ERROR: SensorStreamEvent,
    EventType.VELOCITY_UPDATED: VelocityUpdatedEvent,
    EventType.ATTITUDE_UPDATED: AttitudeUpdatedEvent,
    EventType.GLOBAL_POSITION_UPDATED: GlobalPositionUpdatedEvent,
    EventType.LOCAL_POSITION_UPDATED: LocalPositionUpdatedEvent,
    EventType.HOME_POSITION_UPDATED: HomePositionUpdatedEvent,
    EventType.ROUTE_STARTED: RouteStartedEvent,
    EventType.ROUTE_COMPLETED: RouteCompletedEvent,
    EventType.ROUTE_REPLANNED: RouteReplannedEvent,
    EventType.WAYPOINT_STARTED: WaypointStartedEvent,
    EventType.WAYPOINT_REACHED: WaypointReachedEvent,
    EventType.WAYPOINT_SETTLED: WaypointSettledEvent,
    EventType.WAYPOINT_ACTION_REQUESTED: WaypointActionRequestedEvent,
    EventType.BATTERY_UPDATED: BatteryUpdatedEvent,
    EventType.HEALTH_UPDATED: HealthUpdatedEvent,
    EventType.WARNING_RAISED: WarningRaisedEvent,
    EventType.FAILSAFE_CHANGED: FailsafeChangedEvent,
    EventType.SAFETY_VIOLATION_RAISED: SafetyViolationRaisedEvent,
    EventType.SAFETY_ACTION_TRIGGERED: SafetyActionTriggeredEvent,
    EventType.COLLISION_RISK_RAISED: CollisionRiskRaisedEvent,
    EventType.AVOIDANCE_ACTION_TRIGGERED: AvoidanceActionTriggeredEvent,
    EventType.OBSTACLE_SNAPSHOT_UPDATED: ObstacleSnapshotUpdatedEvent,
    EventType.OBSTACLE_PROVIDER_STALE: ObstacleProviderStaleEvent,
    EventType.ARMED_CHANGED: ArmedChangedEvent,
    EventType.IN_AIR_CHANGED: InAirChangedEvent,
    EventType.LANDED_STATE_CHANGED: LandedStateChangedEvent,
    EventType.FLIGHT_MODE_CHANGED: FlightModeChangedEvent,
    EventType.POSTURE_CHANGED: PostureChangedEvent,
    EventType.GAIT_CHANGED: GaitChangedEvent,
    EventType.FOOT_CONTACT_UPDATED: FootContactUpdatedEvent,
}


def _coerce_standard_payload(event_type: StandardEventType, payload: Any) -> Any:
    if event_type in (
        EventType.SENSOR_DISCOVERED,
        EventType.SENSOR_LOST,
        EventType.SENSOR_STREAM_STARTED,
        EventType.SENSOR_STREAM_STOPPED,
        EventType.SENSOR_STREAM_ERROR,
    ):
        if isinstance(payload, SensorStreamInfo):
            return payload
        data = dict(payload)
        return SensorStreamInfo(
            sensor_id=str(data["sensor_id"]),
            sensor_kind=str(data["sensor_kind"]),
            message=str(data.get("message", "")),
        )
    if event_type in (EventType.GLOBAL_POSITION_UPDATED, EventType.HOME_POSITION_UPDATED):
        if isinstance(payload, GlobalPosition):
            return payload
        data = dict(payload)
        return GlobalPosition(
            latitude_deg=float(data["latitude_deg"]),
            longitude_deg=float(data["longitude_deg"]),
            absolute_altitude_m=float(data["absolute_altitude_m"]),
            relative_altitude_m=(
                float(data["relative_altitude_m"]) if data.get("relative_altitude_m") is not None else None
            ),
        )
    if event_type == EventType.LOCAL_POSITION_UPDATED:
        if isinstance(payload, LocalPosition):
            return payload
        data = dict(payload)
        return LocalPosition(
            frame=CoordinateFrame(data["frame"]),
            position_m=Vector3(**{key: float(value) for key, value in data["position_m"].items()}),
        )
    if event_type in (EventType.ROUTE_STARTED, EventType.ROUTE_COMPLETED):
        if isinstance(payload, RouteProgress):
            return payload
        data = dict(payload)
        return RouteProgress(
            route_id=str(data["route_id"]),
            route_name=str(data["route_name"]) if data.get("route_name") is not None else None,
            waypoint_count=int(data["waypoint_count"]),
            pattern=str(data["pattern"]) if data.get("pattern") is not None else None,
            completion_reason=(
                RouteCompletionReason(data["completion_reason"])
                if data.get("completion_reason") is not None
                else None
            ),
        )
    if event_type == EventType.ROUTE_REPLANNED:
        if isinstance(payload, RouteReplannedInfo):
            return payload
        data = dict(payload)
        return RouteReplannedInfo(
            route_id=str(data["route_id"]),
            route_name=str(data["route_name"]) if data.get("route_name") is not None else None,
            reason=str(data.get("reason", "")),
            detour_count=int(data.get("detour_count", 0)),
            metadata=dict(data.get("metadata") or {}),
        )
    if event_type in (
        EventType.WAYPOINT_STARTED,
        EventType.WAYPOINT_REACHED,
        EventType.WAYPOINT_SETTLED,
        EventType.WAYPOINT_ACTION_REQUESTED,
    ):
        if isinstance(payload, WaypointProgress):
            return payload
        data = dict(payload)
        return WaypointProgress(
            route_id=str(data["route_id"]),
            route_name=str(data["route_name"]) if data.get("route_name") is not None else None,
            waypoint_id=str(data["waypoint_id"]),
            waypoint_index=int(data["waypoint_index"]),
            waypoint_count=int(data["waypoint_count"]),
            x_m=float(data["x_m"]) if data.get("x_m") is not None else None,
            y_m=float(data["y_m"]) if data.get("y_m") is not None else None,
            z_m=float(data["z_m"]) if data.get("z_m") is not None else None,
            latitude_deg=float(data["latitude_deg"]) if data.get("latitude_deg") is not None else None,
            longitude_deg=float(data["longitude_deg"]) if data.get("longitude_deg") is not None else None,
            altitude_m=float(data["altitude_m"]) if data.get("altitude_m") is not None else None,
            distance_m=float(data["distance_m"]) if data.get("distance_m") is not None else None,
            action=str(data["action"]) if data.get("action") is not None else None,
            metadata=dict(data.get("metadata") or {}),
        )
    if event_type == EventType.VELOCITY_UPDATED:
        if isinstance(payload, Velocity):
            return payload
        data = dict(payload)
        return Velocity(
            frame=CoordinateFrame(data["frame"]),
            linear_velocity_m_s=Vector3(
                **{key: float(value) for key, value in data["linear_velocity_m_s"].items()}
            ),
        )
    if event_type == EventType.ATTITUDE_UPDATED:
        if isinstance(payload, Attitude):
            return payload
        return Attitude(**{key: float(value) for key, value in dict(payload).items()})
    if event_type == EventType.BATTERY_UPDATED:
        if isinstance(payload, BatteryState):
            return payload
        data = dict(payload)
        return BatteryState(
            remaining_fraction=float(data["remaining_fraction"]),
            voltage_v=float(data["voltage_v"]) if data.get("voltage_v") is not None else None,
            current_a=float(data["current_a"]) if data.get("current_a") is not None else None,
            temperature_c=float(data["temperature_c"]) if data.get("temperature_c") is not None else None,
        )
    if event_type == EventType.HEALTH_UPDATED:
        if isinstance(payload, HealthState):
            return payload
        data = dict(payload)
        return HealthState(
            gyrometer_calibration_ok=data.get("gyrometer_calibration_ok", data.get("is_gyrometer_calibration_ok")),
            accelerometer_calibration_ok=data.get(
                "accelerometer_calibration_ok", data.get("is_accelerometer_calibration_ok")
            ),
            magnetometer_calibration_ok=data.get(
                "magnetometer_calibration_ok", data.get("is_magnetometer_calibration_ok")
            ),
            local_position_ok=data.get("local_position_ok", data.get("is_local_position_ok")),
            global_position_ok=data.get("global_position_ok", data.get("is_global_position_ok")),
            home_position_ok=data.get("home_position_ok", data.get("is_home_position_ok")),
            armable=data.get("armable", data.get("is_armable")),
        )
    if event_type == EventType.COMMAND_STARTED:
        if isinstance(payload, CommandStarted):
            return payload
        data = dict(payload)
        return CommandStarted(command=str(data["command"]), command_id=str(data["command_id"]))
    if event_type == EventType.COMMAND_COMPLETED:
        if isinstance(payload, CommandCompleted):
            return payload
        data = dict(payload)
        return CommandCompleted(
            command=str(data["command"]),
            command_id=str(data["command_id"]),
            ok=bool(data["ok"]),
            code=str(data["code"]),
            message=str(data["message"]),
        )
    if event_type == EventType.ERROR_OCCURRED:
        if isinstance(payload, ErrorInfo):
            return payload
        data = dict(payload)
        return ErrorInfo(message=str(data.get("message", data.get("error", ""))), stream=data.get("stream"))
    if event_type == EventType.HANDLER_FAILED:
        if isinstance(payload, EventHandlerFailure):
            return payload
        data = dict(payload)
        return EventHandlerFailure(
            event_id=str(data["event_id"]),
            event_type=str(data["event_type"]),
            handler_name=str(data["handler_name"]),
            exception_type=str(data["exception_type"]),
            message=str(data.get("message", "")),
            consecutive_failures=int(data.get("consecutive_failures", 1)),
        )
    if event_type == EventType.WARNING_RAISED:
        if isinstance(payload, WarningInfo):
            return payload
        data = dict(payload)
        return WarningInfo(code=str(data.get("code", "unknown")), message=str(data.get("message", "")))
    if event_type in (EventType.SAFETY_VIOLATION_RAISED, EventType.COLLISION_RISK_RAISED):
        if isinstance(payload, SafetyViolationInfo):
            return payload
        data = dict(payload)
        return SafetyViolationInfo(
            code=str(data.get("code", "unknown")),
            message=str(data.get("message", "")),
            severity=str(data.get("severity", "warning")),
            device_id=str(data.get("device_id", "")),
            action=str(data.get("action", "none")),
            metric_value=float(data["metric_value"]) if data.get("metric_value") is not None else None,
            limit_value=float(data["limit_value"]) if data.get("limit_value") is not None else None,
            metadata=dict(data.get("metadata") or {}),
        )
    if event_type in (EventType.SAFETY_ACTION_TRIGGERED, EventType.AVOIDANCE_ACTION_TRIGGERED):
        if isinstance(payload, SafetyActionInfo):
            return payload
        data = dict(payload)
        return SafetyActionInfo(
            action=str(data.get("action", "none")),
            device_id=str(data.get("device_id", "")),
            violation_codes=tuple(str(item) for item in data.get("violation_codes", ())),
            ok=bool(data["ok"]) if data.get("ok") is not None else None,
            message=str(data.get("message", "")),
            metadata=dict(data.get("metadata") or {}),
        )
    if event_type == EventType.OBSTACLE_SNAPSHOT_UPDATED:
        if isinstance(payload, ObstacleSnapshotInfo):
            return payload
        data = dict(payload)
        return ObstacleSnapshotInfo(
            provider_name=str(data.get("provider_name", "")),
            obstacle_count=int(data.get("obstacle_count", 0)),
            observed_at_s=float(data.get("observed_at_s", 0.0)),
        )
    if event_type == EventType.OBSTACLE_PROVIDER_STALE:
        if isinstance(payload, ObstacleProviderStaleInfo):
            return payload
        data = dict(payload)
        return ObstacleProviderStaleInfo(
            provider_name=str(data.get("provider_name", "")),
            reason=str(data.get("reason", "")),
        )
    if event_type == EventType.FOOT_CONTACT_UPDATED:
        if isinstance(payload, FootContactState):
            return payload
        return FootContactState(contacts={key: bool(value) for key, value in dict(payload)["contacts"].items()})
    if isinstance(payload, StateChange):
        return payload
    data = dict(payload)
    return StateChange(previous=data.get("previous"), current=data.get("current"), reason=data.get("reason"))

