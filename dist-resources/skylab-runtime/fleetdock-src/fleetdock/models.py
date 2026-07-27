"""Standard FleetDock data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Union
from uuid import uuid4

from fleetdock._frozen import freeze_mapping
from fleetdock.movement import MoveTargetKind


class ErrorCode(str, Enum):
    """标准响应错误码；所有适配器应尽量转换为这些跨设备、跨厂商的统一错误。"""

    # 操作成功。
    OK = "OK"
    # 未知错误，通常表示适配器没有更精确的错误分类。
    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    # 命令名称或参数非法。
    INVALID_COMMAND = "INVALID_COMMAND"
    # 当前设备或产品不支持该命令。
    UNSUPPORTED_COMMAND = "UNSUPPORTED_COMMAND"
    # 产品未注册到 ProductRegistry。
    PRODUCT_NOT_REGISTERED = "PRODUCT_NOT_REGISTERED"
    # 找不到产品对应的适配器实现。
    ADAPTER_NOT_FOUND = "ADAPTER_NOT_FOUND"
    # 连接设备或底层 SDK 失败。
    CONNECTION_FAILED = "CONNECTION_FAILED"
    # 设备尚未连接，无法执行当前操作。
    NOT_CONNECTED = "NOT_CONNECTED"
    # 当前设备状态不允许执行该操作。
    DEVICE_STATE_ERROR = "DEVICE_STATE_ERROR"
    # 参数校验失败。
    VALIDATION_ERROR = "VALIDATION_ERROR"


class CommandName(str, Enum):
    """FleetDock 标准命令名；设备对象和适配器通过这些命令保持统一控制语义。"""

    # 连接设备或底层控制系统。
    CONNECT = "connect"
    # 解锁/使能设备，例如无人机 arm。
    UNLOCK = "unlock"
    # 上锁/禁用设备，例如无人机 disarm。
    LOCK = "lock"
    # 起飞到指定高度，通常用于空中设备。
    TAKEOFF = "takeoff"
    # 降落。
    LAND = "land"
    # 返回起飞点或 Home 点。
    RETURN_TO_LAUNCH = "return_to_launch"
    # 悬停或保持当前位置。
    HOVER = "hover"
    # 按强类型目标、速度或位移移动。
    MOVE = "move"
    # 停止当前运动。
    STOP = "stop"
    # 紧急停止，语义比 STOP 更强。
    EMERGENCY_STOP = "emergency_stop"
    # 足式设备站立。
    STAND = "stand"
    # 足式设备坐下。
    SIT = "sit"
    # 获取设备状态快照。
    GET_STATUS = "get_status"


class CapabilityName(str, Enum):
    """产品能力名；用于声明和查询设备是否支持某类控制、遥测或任务能力。"""

    # 支持连接。
    CONNECT = "connect"
    # 支持解锁/使能。
    ARM = "arm"
    # 支持上锁/禁用。
    LOCK = "lock"
    # 支持起飞。
    TAKEOFF = "takeoff"
    # 支持降落。
    LAND = "land"
    # 支持返航。
    RETURN_TO_LAUNCH = "return_to_launch"
    # 支持悬停。
    HOVER = "hover"
    # 支持运动控制；具体目标类型由 ProductProfile.supported_move_targets 声明。
    MOVE = "move"
    # 支持停止运动。
    STOP = "stop"
    # 支持紧急停止。
    EMERGENCY_STOP = "emergency_stop"
    # 支持状态查询。
    STATUS = "status"
    # 支持遥测订阅。
    TELEMETRY = "telemetry"
    # 支持飞控外部控制模式，例如 PX4 Offboard。
    OFFBOARD = "offboard"
    # 支持速度控制，可作为跟随执行器的通用底层能力。
    VELOCITY_CONTROL = "velocity_control"
    # 支持带输入超时保护的机体系手动速度控制，适合调试控制器或上层遥控界面。
    MANUAL_CONTROL = "manual_control"
    # 支持位置目标控制，可作为低频跟随或队形重排的底层能力。
    POSITION_CONTROL = "position_control"
    # 支持 FleetDock SDK 侧安全前置监督，可统一处理限高、间距、电量、遥测超时和 failsafe。
    SAFETY_SUPERVISION = "safety_supervision"
    # 支持接入障碍物 provider 或厂商感知数据。
    OBSTACLE_SENSING = "obstacle_sensing"
    # 支持 SDK 侧碰撞风险预测。
    COLLISION_PREDICTION = "collision_prediction"
    # 支持 SDK 侧避障动作映射，例如悬停、停止、返航或急停。
    OBSTACLE_AVOIDANCE = "obstacle_avoidance"
    # 支持 SDK 侧局部路线重规划和临时绕行点插入。
    LOCAL_REPLANNING = "local_replanning"
    # 支持 FleetDock SDK 侧跟随执行，通过标准遥测和 move/stop 等命令实现。
    FOLLOW_EXECUTION = "follow_execution"
    # 支持 FleetDock SDK 侧队形槽位布局，可由 fleet.follow(..., layout=...) 转换为跟随策略。
    FORMATION_LAYOUT = "formation_layout"
    # 支持飞控或厂商 SDK 原生 Follow Target / Follow Me 能力。
    FOLLOW_TARGET = "follow_target"
    # 支持厂商或飞控原生多机编队控制能力。
    FORMATION_CONTROL = "formation_control"
    # 支持飞控原生任务/航线能力。
    MISSION = "mission"
    # 支持 FleetDock 标准路线执行，可由厂商原生导航或 SDK 控制实现。
    ROUTE = "route"
    # 支持站立动作。
    STAND = "stand"
    # 支持坐下动作。
    SIT = "sit"
    # 支持步态控制。
    GAIT = "gait"
    # 支持姿态控制。
    POSE = "pose"


class DeviceCategory(str, Enum):
    """设备类别；用于产品注册、适配器选择和上层界面分类。"""

    # 无人机或其他空中设备。
    DRONE = "drone"
    # 机器狗。
    ROBOT_DOG = "robot_dog"
    # 人形机器人。
    HUMANOID = "humanoid"
    # 其他或自定义设备。
    OTHER = "other"


class VendorName(str, Enum):
    """设备厂商名称；用于产品 profile、日志和能力声明。"""

    # FleetDock 内置或模拟设备。
    FLEETDOCK = "FleetDock"
    # PX4 飞控生态。
    PX4 = "PX4"
    # DJI 大疆生态。
    DJI = "DJI"
    # Unitree 宇树生态。
    UNITREE = "Unitree"
    # 其他厂商。
    OTHER = "Other"


class ProductType(str, Enum):
    """内置产品类型；优先用枚举选择产品，避免调用方手写产品字符串。"""

    # FleetDock 内置模拟无人机，用于零厂商依赖学习和业务流程测试。
    MOCK_DRONE = "mock.drone"
    # FleetDock 内置模拟机器狗，用于零厂商依赖学习和业务流程测试。
    MOCK_ROBOT_DOG = "mock.robot_dog"
    # 通用 PX4 无人机。
    PX4_GENERIC = "px4.generic"
    # DJI Matrice 350 RTK 行业无人机。
    DJI_MATRICE_350_RTK = "dji.matrice_350_rtk"
    # DJI Mavic 3 行业系列无人机。
    DJI_MAVIC_3_ENTERPRISE = "dji.mavic_3_enterprise"
    # Unitree Go2 机器狗。
    UNITREE_GO2 = "unitree.go2"
    # Unitree G1 人形机器人。
    UNITREE_G1 = "unitree.g1"
    # 其他或自定义产品。
    OTHER = "other"

    @property
    def display_name(self) -> str:
        """返回产品的中文显示名，适合用于 IDE 提示、配置界面、日志和错误信息。"""
        return _PRODUCT_TYPE_TEXT[self][0]

    @property
    def description(self) -> str:
        """返回产品的中文说明，描述设备对象、典型接入 SDK 和适配场景。"""
        return _PRODUCT_TYPE_TEXT[self][1]


class IntegrationMaturity(str, Enum):
    """产品集成成熟度；用于区分真实可交付能力、试验能力、模拟能力和规划占位。"""

    STABLE = "stable"  # 已通过持续测试，可作为当前版本的稳定公共能力使用。
    BETA = "beta"  # 已有真实实现，但接口或边界仍可能在兼容策略内调整。
    EXPERIMENTAL = "experimental"  # 仅供受控验证，不承诺生产环境稳定性。
    SIMULATED = "simulated"  # 仅由 Mock/仿真实现支撑，不代表对应厂商真机已接入。
    PLANNED = "planned"  # 只有产品画像或规划信息，当前不能创建设备执行控制。


_PRODUCT_TYPE_TEXT = {
    ProductType.MOCK_DRONE: (
        "FleetDock 模拟无人机",
        "无需厂商 SDK 或仿真器即可学习标准 Drone、事件和 Plan 流程。",
    ),
    ProductType.MOCK_ROBOT_DOG: (
        "FleetDock 模拟机器狗",
        "无需厂商 SDK 或真机即可学习标准 RobotDog、移动和任务流程。",
    ),
    ProductType.PX4_GENERIC: (
        "通用 PX4 无人机",
        "面向 PX4 飞控的通用无人机产品，优先覆盖 MAVLink/MAVSDK 链路。",
    ),
    ProductType.DJI_MATRICE_350_RTK: (
        "大疆 Matrice 350 RTK",
        "面向 DJI Matrice 350 RTK 行业无人机的产品 profile，优先考虑 MSDK、PSDK 和 Cloud API 集成入参。",
    ),
    ProductType.DJI_MAVIC_3_ENTERPRISE: (
        "大疆 Mavic 3 行业系列",
        "面向 DJI Mavic 3E/Mavic 3T 行业无人机的产品 profile，优先考虑 MSDK 和 Cloud API 集成入参。",
    ),
    ProductType.UNITREE_GO2: (
        "宇树 Go2 机器狗",
        "面向 Unitree Go2 机器狗的产品 profile，优先考虑 SDK2 网络接口和托管 client 入参。",
    ),
    ProductType.UNITREE_G1: (
        "宇树 G1 人形机器人",
        "面向 Unitree G1 人形机器人的产品 profile，优先考虑 SDK2 网络接口和托管 client 入参。",
    ),
    ProductType.OTHER: (
        "其他/自研设备",
        "用于第三方或自研设备登记产品信息，正式控制前需要注册对应 adapter。",
    ),
}


class FirmwareVersion(str, Enum):
    """固件或厂商 SDK 版本键；用于在同一产品下选择不同适配器实现。"""

    # FleetDock 内置 Mock 适配器协议版本。
    MOCK_V1 = "mock.v1"
    # FleetDock 早期 PX4 适配器版本。
    PX4_V0_1 = "px4.v0.1"
    # DJI Mobile SDK v5。
    DJI_MSDK_V5 = "dji.msdk.v5"  # DJI 移动端控制应用使用的 Mobile SDK v5。
    # DJI Payload SDK v3。
    DJI_PSDK_V3 = "dji.psdk.v3"  # DJI 机载负载设备使用的 Payload SDK v3。
    # DJI Cloud API v1。
    DJI_CLOUD_API_V1 = "dji.cloud_api.v1"  # DJI 云端 MQTT/HTTP 接入使用的 Cloud API v1。
    # Unitree SDK2。
    UNITREE_SDK2 = "unitree.sdk2"  # Unitree Go2、G1 等设备的 DDS 网络控制 SDK2。
    # 自定义固件或外部适配器版本。
    CUSTOM = "custom"


class InitMode(str, Enum):
    """适配器初始化方式；声明 SDK 创建底层对象还是接管外部已创建对象。"""

    # FleetDock 根据 params 创建底层 SDK client 或连接对象。
    SDK_CREATE = "sdk_create"
    # 调用方传入已经创建好的底层对象，由 FleetDock 托管使用。
    MANAGED_OBJECT = "managed_object"


ProductKey = Union[ProductType, str]
FirmwareKey = Union[FirmwareVersion, str]


def normalize_product(product: ProductKey) -> str:
    """把 ``product`` 产品枚举或自定义键规范化并返回字符串，用于注册表查询。"""
    return product.value if isinstance(product, ProductType) else str(product)


def normalize_firmware(firmware: Optional[FirmwareKey]) -> Optional[str]:
    """把 ``firmware`` 枚举或自定义版本键规范化并返回字符串；未指定时返回 ``None``。"""
    if firmware is None:
        return None
    return firmware.value if isinstance(firmware, FirmwareVersion) else str(firmware)


@dataclass(frozen=True)
class DeviceMetadata:
    """调用方维护的设备扩展信息。

    该对象不参与产品、固件或适配器选择；`device_id`、`name`、`tags` 和 `extra`
    主要用于业务资产编号、界面展示、分组过滤和可序列化扩展信息。`user_context`
    可挂载调用方自己的对象，不保证可序列化，也不应作为 FleetDock 标准能力的必需依赖。
    """

    device_id: Optional[str] = None
    name: Optional[str] = None
    tags: tuple[str, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)
    user_context: Any = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "extra", freeze_mapping(self.extra))


@dataclass(frozen=True)
class ConnectionInfo:
    """设备初始化输入的内部兼容模型。

    推荐用户入口是设备构造函数上的 `params` 和 `managed_object`；本对象用于在
    适配器工厂和旧代码之间传递统一初始化信息。`params` 保存 SDK 创建模式需要的连接
    参数，`managed_object` 保存调用方已创建的原生 SDK 对象，`mode` 标记初始化方式。
    ``close_managed_object`` 仅用于外部托管模式：默认 ``False`` 表示对象仍归调用方所有，设备关闭或
    连接失败时 FleetDock 不会关闭它；显式设为 ``True`` 才把释放责任交给 FleetDock。
    """

    params: Mapping[str, Any] = field(default_factory=dict)
    managed_object: Any = None
    mode: InitMode = InitMode.SDK_CREATE
    close_managed_object: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", freeze_mapping(self.params))

    @classmethod
    def from_params(cls, **params: Any) -> "ConnectionInfo":
        """创建 FleetDock 管理模式连接信息。

        `params` 是产品 profile 声明的初始化参数，例如 PX4 的 `endpoint`、
        DJI 的 `sdk_mode` 或 Unitree 的 `network_interface`；返回值可交给工厂或适配器使用。
        """
        return cls(params=params, mode=InitMode.SDK_CREATE)

    @classmethod
    def from_managed_object(
        cls,
        managed_object: Any,
        *,
        close_on_close: bool = False,
        **params: Any,
    ) -> "ConnectionInfo":
        """创建外部托管模式连接信息。

        `managed_object` 是调用方已经创建并希望 FleetDock 复用的底层 SDK client；`params` 仅补充
        产品识别、端点或调试信息。``close_on_close=False`` 表示调用方继续负责关闭对象；设为 ``True``
        表示所有权转交 FleetDock，设备关闭或连接失败时 SDK 可以关闭该对象。返回可交给适配器工厂的
        外部托管模式 ``ConnectionInfo``。
        """
        return cls(
            params=params,
            managed_object=managed_object,
            mode=InitMode.MANAGED_OBJECT,
            close_managed_object=close_on_close,
        )

    def get_param(self, name: str, default: Any = None) -> Any:
        """读取初始化参数；当 `name` 不存在时返回 `default`。"""
        return self.params.get(name, default)


@dataclass(frozen=True)
class ProductProfile:
    """产品能力画像。

    一个 profile 描述某个 `ProductType` 的设备类别、厂商、控制体系、标准命令、
    capabilities、支持事件、初始化方式、固件/SDK 版本和必填入参。注册表和工厂依赖
    该对象完成参数校验、适配器选择和能力查询。``maturity`` 明确当前集成是否适合
    生产、试验或仅模拟使用，调用方可在构建设备前据此建立部署准入规则。
    """

    product: ProductType
    category: DeviceCategory
    vendor: VendorName
    control_system: str
    supported_commands: frozenset[CommandName]
    maturity: IntegrationMaturity = IntegrationMaturity.EXPERIMENTAL
    display_name: str = ""
    description: str = ""
    capabilities: frozenset[CapabilityName] = field(default_factory=frozenset)
    supported_init_modes: frozenset[InitMode] = field(default_factory=lambda: frozenset(InitMode))
    supported_firmware: Mapping[FirmwareVersion, str] = field(default_factory=dict)
    default_firmware: Optional[FirmwareVersion] = None
    required_params: tuple[str, ...] = ()
    supported_events: frozenset[str] = field(default_factory=frozenset)
    extension_namespaces: frozenset[str] = field(default_factory=frozenset)
    supported_move_targets: frozenset[MoveTargetKind] = field(default_factory=frozenset)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "supported_firmware", freeze_mapping(self.supported_firmware))
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))
        if not self.display_name:
            object.__setattr__(self, "display_name", self.product.display_name)
        if not self.description:
            object.__setattr__(self, "description", self.product.description)
        if self.default_firmware is None and self.supported_firmware:
            object.__setattr__(self, "default_firmware", next(iter(self.supported_firmware)))
        if self.default_firmware is not None and self.supported_firmware:
            if self.default_firmware not in self.supported_firmware:
                from fleetdock.errors import FleetDockValidationError

                raise FleetDockValidationError(
                    "default_firmware must be included in supported_firmware",
                    details={"product": self.product.value, "firmware": self.default_firmware.value},
                )

    def supports(self, command: CommandName) -> bool:
        """判断 ``command`` 标准命令是否受支持；返回值表示适配器是否应实现该命令。"""
        return command in self.supported_commands

    def has_capability(self, capability: CapabilityName) -> bool:
        """判断产品是否声明 ``capability`` 标准能力并返回布尔值。"""
        return capability in self.capabilities

    def supports_event(self, event_type: Any) -> bool:
        """判断 ``event_type`` 枚举或事件名是否在产品声明中并返回布尔值。"""
        value = getattr(event_type, "value", event_type)
        return str(value) in self.supported_events

    def supports_move_target(self, target_kind: MoveTargetKind) -> bool:
        """判断产品是否声明支持 ``target_kind`` 移动目标，并返回布尔结果。"""
        return target_kind in self.supported_move_targets

    def supports_init_mode(self, mode: InitMode) -> bool:
        """判断产品是否支持 ``mode`` 初始化方式并返回布尔值。"""
        return mode in self.supported_init_modes

    def supports_firmware(self, firmware: FirmwareKey) -> bool:
        """判断 ``firmware`` 是否可用于该产品并返回布尔值；自定义版本可由 ``CUSTOM`` 兜底。"""
        if not self.supported_firmware:
            return True
        if isinstance(firmware, FirmwareVersion):
            return firmware in self.supported_firmware
        try:
            return FirmwareVersion(firmware) in self.supported_firmware
        except ValueError:
            return FirmwareVersion.CUSTOM in self.supported_firmware

    def resolve_firmware(self, firmware: Optional[FirmwareKey] = None) -> Optional[FirmwareKey]:
        """解析最终使用的固件/SDK 版本。

        ``firmware`` 未传入时返回产品默认版本；传入但不受支持时抛出 ``ValueError``，避免创建到不兼容
        的适配器实例。
        """
        if firmware is None:
            return self.default_firmware
        if not self.supports_firmware(firmware):
            from fleetdock.errors import FleetDockValidationError

            raise FleetDockValidationError(
                f"unsupported firmware for product {self.product.value}",
                details={"product": self.product.value, "firmware": normalize_firmware(firmware)},
            )
        return firmware

    def missing_required_params(self, params: Mapping[str, Any]) -> tuple[str, ...]:
        """检查 ``params`` 并返回其中缺失的必填字段名元组。"""
        return tuple(name for name in self.required_params if name not in params)


@dataclass(frozen=True)
class StandardCommand:
    """FleetDock 内部标准命令对象。

    设备方法会把用户调用转换为该对象，再交给适配器执行。`device_id` 是 SDK 内部实例
    标识，`params` 保存命令参数，`timeout_s` 单位为秒，`metadata` 用于追踪调用上下文。
    """

    command: CommandName
    device_id: str
    product: ProductKey
    params: Mapping[str, Any] = field(default_factory=dict)
    command_id: str = field(default_factory=lambda: str(uuid4()))
    timeout_s: Optional[float] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", freeze_mapping(self.params))
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))


@dataclass(frozen=True)
class StandardResponse:
    """标准命令响应。

    适配器执行命令后统一返回该对象。`ok` 表示命令是否成功，`code` 使用标准错误码，
    `message` 面向调用方说明结果，`data` 保存可序列化的补充数据。
    """

    ok: bool
    command_id: str
    device_id: str
    product: str
    code: ErrorCode = ErrorCode.OK
    message: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", freeze_mapping(self.data))

    @classmethod
    def success(
        cls,
        command: StandardCommand,
        message: str = "ok",
        data: Optional[Mapping[str, Any]] = None,
    ) -> "StandardResponse":
        """根据 ``command`` 构造并返回成功响应；``message`` 是结果说明，``data`` 携带补充数据。"""
        return cls(
            ok=True,
            command_id=command.command_id,
            device_id=command.device_id,
            product=normalize_product(command.product),
            code=ErrorCode.OK,
            message=message,
            data=data or {},
        )

    @classmethod
    def failure(
        cls,
        command: StandardCommand,
        code: ErrorCode,
        message: str,
        data: Optional[Mapping[str, Any]] = None,
    ) -> "StandardResponse":
        """根据 ``command`` 构造并返回失败响应；``code``、``message`` 和 ``data`` 说明失败详情。"""
        return cls(
            ok=False,
            command_id=command.command_id,
            device_id=command.device_id,
            product=normalize_product(command.product),
            code=code,
            message=message,
            data=data or {},
        )

    @classmethod
    def operation_failure(
        cls,
        *,
        device_id: str,
        product: ProductKey,
        code: ErrorCode,
        message: str,
        data: Optional[Mapping[str, Any]] = None,
    ) -> "StandardResponse":
        """为未生成标准命令的批量操作异常构造失败响应。

        ``device_id`` 和 ``product`` 标识失败设备；``code`` 与公共异常使用相同错误码，
        ``message`` 说明失败原因。由于异常发生在标准命令边界之外，返回值的
        ``command_id`` 为空字符串；``data`` 可记录非敏感诊断信息，调用方仍可统一处理失败。
        """
        return cls(
            ok=False,
            command_id="",
            device_id=device_id,
            product=normalize_product(product),
            code=code,
            message=message,
            data=data or {},
        )
