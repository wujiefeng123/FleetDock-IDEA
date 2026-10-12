"""Product profile registry."""

from __future__ import annotations

import logging
from typing import Dict, Iterable, Mapping, Type, TypeVar

from fleetdock.adapters import DeviceAdapter, MockDroneAdapter, MockRobotDogAdapter, Px4MavlinkAdapter
from fleetdock.events import EventType
from fleetdock.errors import AdapterNotFoundError, FleetDockValidationError, ProductNotRegisteredError
from fleetdock.models import (
    CapabilityName,
    CommandName,
    DeviceCategory,
    FirmwareKey,
    FirmwareVersion,
    InitMode,
    IntegrationMaturity,
    ProductKey,
    ProductProfile,
    ProductType,
    VendorName,
    normalize_firmware,
    normalize_product,
)
from fleetdock.movement import MoveTargetKind

FirmwareT = TypeVar("FirmwareT", bound=str)
AdapterT = TypeVar("AdapterT", bound=DeviceAdapter)
logger = logging.getLogger(__name__)


class ProductRegistry:
    """产品注册表。

    注册表保存 `ProductProfile` 与具体适配器实现的映射。设备创建时会先根据产品枚举取得
    profile，再按固件/SDK 版本查找适配器类型，用于完成能力声明、参数校验和厂商集成分流。
    """

    def __init__(self) -> None:
        self._profiles: Dict[str, ProductProfile] = {}
        self._adapter_types: Dict[tuple[str, str], Type[DeviceAdapter]] = {}

    def register(
        self,
        profile: ProductProfile,
        adapters: Mapping[FirmwareT, Type[AdapterT]] | None = None,
    ) -> None:
        """注册产品 profile 和可选适配器映射。

        `profile` 描述产品能力、固件版本和初始化参数；`adapters` 的键是固件/SDK 版本，
        值是适配器类。重复注册同一产品会替换旧 profile 和旧适配器映射；无返回值。
        """
        product_key = profile.product.value
        adapter_types: Dict[tuple[str, str], Type[DeviceAdapter]] = {}
        for firmware, adapter_type in (adapters or {}).items():
            if not profile.supports_firmware(firmware):
                raise FleetDockValidationError(
                    f"unsupported firmware for product {product_key}",
                    details={"product": product_key, "firmware": normalize_firmware(firmware)},
                )
            firmware_key = normalize_firmware(firmware)
            if firmware_key is None:
                raise FleetDockValidationError("firmware is required", details={"product": product_key})
            adapter_types[(product_key, firmware_key)] = adapter_type

        self._profiles[product_key] = profile
        self._adapter_types = {
            key: value for key, value in self._adapter_types.items() if key[0] != product_key
        }
        self._adapter_types.update(adapter_types)
        logger.debug(
            "产品注册完成: product=%s firmware_count=%d adapter_count=%d",
            product_key,
            len(profile.supported_firmware),
            len(adapter_types),
        )

    def get(self, product: ProductKey) -> ProductProfile:
        """按 ``product`` 枚举或键返回 profile；未注册时抛出 ``ProductNotRegisteredError``。"""
        product_key = normalize_product(product)
        try:
            profile = self._profiles[product_key]
            logger.debug("产品查询成功: product=%s", product_key)
            return profile
        except KeyError as exc:
            logger.debug("产品查询失败: product=%s", product_key)
            raise ProductNotRegisteredError(product_key) from exc

    def has(self, product: ProductKey) -> bool:
        """判断 ``product`` 是否已注册并返回布尔结果。"""
        return normalize_product(product) in self._profiles

    def get_adapter(self, product: ProductKey, firmware: FirmwareKey) -> Type[DeviceAdapter]:
        """按 ``product`` 和 ``firmware`` 返回适配器类；未找到时抛出 ``AdapterNotFoundError``。"""
        product_key = normalize_product(product)
        firmware_key = normalize_firmware(firmware)
        try:
            adapter_type = self._adapter_types[(product_key, firmware_key or "")]
            logger.debug(
                "适配器查询成功: product=%s firmware=%s adapter=%s",
                product_key,
                firmware_key,
                adapter_type.__name__,
            )
            return adapter_type
        except KeyError as exc:
            logger.debug("适配器查询失败: product=%s firmware=%s", product_key, firmware_key)
            raise AdapterNotFoundError(product_key, firmware_key) from exc

    def list(self) -> Iterable[ProductProfile]:
        """返回当前已注册的所有产品 profile 快照，供文档、配置界面或调试使用。"""
        return tuple(self._profiles.values())


def _commands(*commands: CommandName) -> frozenset[CommandName]:
    return frozenset(commands)


def _capabilities(*capabilities: CapabilityName) -> frozenset[CapabilityName]:
    return frozenset(capabilities)


def _modes(*modes: InitMode) -> frozenset[InitMode]:
    return frozenset(modes)


def _firmware_map(*firmware: tuple[FirmwareVersion, str]) -> dict[FirmwareVersion, str]:
    return dict(firmware)


DRONE_MOVE_TARGETS = frozenset(
    {
        MoveTargetKind.GPS_POINT,
        MoveTargetKind.RELATIVE_POINT_3D,
        MoveTargetKind.RELATIVE_MOVE_3D,
    }
)
ROBOT_MOVE_TARGETS = frozenset(
    {
        MoveTargetKind.RELATIVE_POINT_2D,
        MoveTargetKind.RELATIVE_MOVE_2D,
    }
)


DRONE_COMMANDS = _commands(
    CommandName.CONNECT,
    CommandName.UNLOCK,
    CommandName.LOCK,
    CommandName.TAKEOFF,
    CommandName.LAND,
    CommandName.RETURN_TO_LAUNCH,
    CommandName.HOVER,
    CommandName.MOVE,
    CommandName.STOP,
    CommandName.EMERGENCY_STOP,
    CommandName.GET_STATUS,
)

DRONE_CAPABILITIES = _capabilities(
    CapabilityName.CONNECT,
    CapabilityName.ARM,
    CapabilityName.LOCK,
    CapabilityName.TAKEOFF,
    CapabilityName.LAND,
    CapabilityName.RETURN_TO_LAUNCH,
    CapabilityName.HOVER,
    CapabilityName.MOVE,
    CapabilityName.STOP,
    CapabilityName.EMERGENCY_STOP,
    CapabilityName.STATUS,
    CapabilityName.TELEMETRY,
    CapabilityName.VELOCITY_CONTROL,
    CapabilityName.POSITION_CONTROL,
    CapabilityName.SAFETY_SUPERVISION,
    CapabilityName.OBSTACLE_SENSING,
    CapabilityName.COLLISION_PREDICTION,
    CapabilityName.OBSTACLE_AVOIDANCE,
    CapabilityName.LOCAL_REPLANNING,
    CapabilityName.FOLLOW_EXECUTION,
    CapabilityName.FORMATION_LAYOUT,
    CapabilityName.ROUTE,
)

PX4_CAPABILITIES = frozenset({*DRONE_CAPABILITIES, CapabilityName.OFFBOARD, CapabilityName.MISSION})
MOCK_DRONE_CAPABILITIES = frozenset({*DRONE_CAPABILITIES, CapabilityName.MANUAL_CONTROL})
PX4_CAPABILITIES = frozenset({*PX4_CAPABILITIES, CapabilityName.MANUAL_CONTROL})
PX4_EVENTS = frozenset(
    event.value
    for event in (
        EventType.CONNECTION_CHANGED,
        EventType.COMMAND_STARTED,
        EventType.COMMAND_COMPLETED,
        EventType.SENSOR_DISCOVERED,
        EventType.SENSOR_LOST,
        EventType.SENSOR_STREAM_STARTED,
        EventType.SENSOR_STREAM_STOPPED,
        EventType.SENSOR_STREAM_ERROR,
        EventType.ERROR_OCCURRED,
        EventType.VELOCITY_UPDATED,
        EventType.ATTITUDE_UPDATED,
        EventType.GLOBAL_POSITION_UPDATED,
        EventType.LOCAL_POSITION_UPDATED,
        EventType.HOME_POSITION_UPDATED,
        EventType.ROUTE_STARTED,
        EventType.ROUTE_COMPLETED,
        EventType.ROUTE_REPLANNED,
        EventType.WAYPOINT_STARTED,
        EventType.WAYPOINT_REACHED,
        EventType.WAYPOINT_SETTLED,
        EventType.WAYPOINT_ACTION_REQUESTED,
        EventType.BATTERY_UPDATED,
        EventType.HEALTH_UPDATED,
        EventType.SAFETY_VIOLATION_RAISED,
        EventType.SAFETY_ACTION_TRIGGERED,
        EventType.COLLISION_RISK_RAISED,
        EventType.AVOIDANCE_ACTION_TRIGGERED,
        EventType.OBSTACLE_SNAPSHOT_UPDATED,
        EventType.OBSTACLE_PROVIDER_STALE,
        EventType.ARMED_CHANGED,
        EventType.IN_AIR_CHANGED,
        EventType.LANDED_STATE_CHANGED,
        EventType.FLIGHT_MODE_CHANGED,
        EventType.TAKEOFF_STARTED,
        EventType.TAKEOFF_STRATEGY_SELECTED,
        EventType.TAKEOFF_PROGRESS_UPDATED,
        EventType.TAKEOFF_COMPLETED,
        EventType.TAKEOFF_ABORTED,
    )
)
MOCK_DRONE_EVENTS = frozenset(
    event
    for event in PX4_EVENTS
    if not event.startswith("aerial.takeoff.")
)

ROBOT_COMMANDS = _commands(
    CommandName.CONNECT,
    CommandName.MOVE,
    CommandName.STOP,
    CommandName.EMERGENCY_STOP,
    CommandName.STAND,
    CommandName.SIT,
    CommandName.GET_STATUS,
)

ROBOT_CAPABILITIES = _capabilities(
    CapabilityName.CONNECT,
    CapabilityName.MOVE,
    CapabilityName.STOP,
    CapabilityName.EMERGENCY_STOP,
    CapabilityName.STATUS,
    CapabilityName.VELOCITY_CONTROL,
    CapabilityName.POSITION_CONTROL,
    CapabilityName.SAFETY_SUPERVISION,
    CapabilityName.OBSTACLE_SENSING,
    CapabilityName.COLLISION_PREDICTION,
    CapabilityName.OBSTACLE_AVOIDANCE,
    CapabilityName.LOCAL_REPLANNING,
    CapabilityName.FOLLOW_EXECUTION,
    CapabilityName.FORMATION_LAYOUT,
    CapabilityName.STAND,
    CapabilityName.SIT,
    CapabilityName.GAIT,
    CapabilityName.POSE,
    CapabilityName.ROUTE,
)
ROBOT_EVENTS = frozenset(
    event.value
    for event in (
        EventType.CONNECTION_CHANGED,
        EventType.COMMAND_STARTED,
        EventType.COMMAND_COMPLETED,
        EventType.SENSOR_DISCOVERED,
        EventType.SENSOR_LOST,
        EventType.SENSOR_STREAM_STARTED,
        EventType.SENSOR_STREAM_STOPPED,
        EventType.SENSOR_STREAM_ERROR,
        EventType.VELOCITY_UPDATED,
        EventType.POSTURE_CHANGED,
        EventType.SAFETY_VIOLATION_RAISED,
        EventType.SAFETY_ACTION_TRIGGERED,
        EventType.COLLISION_RISK_RAISED,
        EventType.AVOIDANCE_ACTION_TRIGGERED,
        EventType.OBSTACLE_SNAPSHOT_UPDATED,
        EventType.OBSTACLE_PROVIDER_STALE,
    )
)


default_registry = ProductRegistry()
default_registry.register(
    ProductProfile(
        product=ProductType.MOCK_DRONE,
        category=DeviceCategory.DRONE,
        vendor=VendorName.FLEETDOCK,
        control_system="FleetDock Mock",
        supported_commands=DRONE_COMMANDS,
        maturity=IntegrationMaturity.SIMULATED,
        capabilities=MOCK_DRONE_CAPABILITIES,
        supported_init_modes=_modes(InitMode.SDK_CREATE),
        supported_firmware=_firmware_map((FirmwareVersion.MOCK_V1, "FleetDock 内置模拟无人机适配器")),
        default_firmware=FirmwareVersion.MOCK_V1,
        supported_events=MOCK_DRONE_EVENTS,
        extension_namespaces=frozenset({"mock"}),
        supported_move_targets=DRONE_MOVE_TARGETS,
    ),
    adapters={FirmwareVersion.MOCK_V1: MockDroneAdapter},
)
default_registry.register(
    ProductProfile(
        product=ProductType.MOCK_ROBOT_DOG,
        category=DeviceCategory.ROBOT_DOG,
        vendor=VendorName.FLEETDOCK,
        control_system="FleetDock Mock",
        supported_commands=ROBOT_COMMANDS,
        maturity=IntegrationMaturity.SIMULATED,
        capabilities=ROBOT_CAPABILITIES,
        supported_init_modes=_modes(InitMode.SDK_CREATE),
        supported_firmware=_firmware_map((FirmwareVersion.MOCK_V1, "FleetDock 内置模拟机器狗适配器")),
        default_firmware=FirmwareVersion.MOCK_V1,
        supported_events=ROBOT_EVENTS,
        extension_namespaces=frozenset({"mock"}),
        supported_move_targets=ROBOT_MOVE_TARGETS,
    ),
    adapters={FirmwareVersion.MOCK_V1: MockRobotDogAdapter},
)
default_registry.register(
    ProductProfile(
        product=ProductType.PX4_GENERIC,
        category=DeviceCategory.DRONE,
        vendor=VendorName.PX4,
        control_system="PX4",
        supported_commands=DRONE_COMMANDS,
        maturity=IntegrationMaturity.BETA,
        capabilities=PX4_CAPABILITIES,
        supported_init_modes=_modes(InitMode.SDK_CREATE, InitMode.MANAGED_OBJECT),
        supported_firmware=_firmware_map((FirmwareVersion.PX4_V0_1, "PX4 通用 MAVSDK 接入路线")),
        default_firmware=FirmwareVersion.PX4_V0_1,
        required_params=("endpoint",),
        supported_events=PX4_EVENTS,
        extension_namespaces=frozenset({"px4"}),
        supported_move_targets=DRONE_MOVE_TARGETS,
        metadata={
            "connection_examples": [
                {"endpoint": "udpin://0.0.0.0:14555"},
            ],
        },
    ),
    adapters={FirmwareVersion.PX4_V0_1: Px4MavlinkAdapter},
)

default_registry.register(
    ProductProfile(
        product=ProductType.DJI_MATRICE_350_RTK,
        category=DeviceCategory.DRONE,
        vendor=VendorName.DJI,
        control_system="DJI MSDK/PSDK/Cloud API",
        supported_commands=DRONE_COMMANDS,
        maturity=IntegrationMaturity.PLANNED,
        capabilities=DRONE_CAPABILITIES,
        supported_init_modes=_modes(InitMode.SDK_CREATE, InitMode.MANAGED_OBJECT),
        supported_firmware=_firmware_map(
            (FirmwareVersion.DJI_MSDK_V5, "DJI Mobile SDK 5 接入路线"),
            (FirmwareVersion.DJI_PSDK_V3, "DJI Payload SDK 3 接入路线"),
            (FirmwareVersion.DJI_CLOUD_API_V1, "DJI Cloud API 1 接入路线"),
        ),
        default_firmware=FirmwareVersion.DJI_MSDK_V5,
        required_params=("sdk_mode",),
        supported_move_targets=DRONE_MOVE_TARGETS,
        metadata={
            "status": "profile-only",
            "sdk_modes": ("msdk", "psdk", "cloud_api"),
            "connection_examples": [
                {"sdk_mode": "msdk", "app_key": "...", "app_secret": "..."},
                {"sdk_mode": "psdk", "device_name": "payload-001"},
                {"sdk_mode": "cloud_api", "mqtt_host": "broker.example.com", "workspace_id": "..."},
            ],
        },
    )
)
default_registry.register(
    ProductProfile(
        product=ProductType.DJI_MAVIC_3_ENTERPRISE,
        category=DeviceCategory.DRONE,
        vendor=VendorName.DJI,
        control_system="DJI MSDK/PSDK/Cloud API",
        supported_commands=DRONE_COMMANDS,
        maturity=IntegrationMaturity.PLANNED,
        capabilities=DRONE_CAPABILITIES,
        supported_init_modes=_modes(InitMode.SDK_CREATE, InitMode.MANAGED_OBJECT),
        supported_firmware=_firmware_map(
            (FirmwareVersion.DJI_MSDK_V5, "DJI Mobile SDK 5 接入路线"),
            (FirmwareVersion.DJI_PSDK_V3, "DJI Payload SDK 3 接入路线"),
            (FirmwareVersion.DJI_CLOUD_API_V1, "DJI Cloud API 1 接入路线"),
        ),
        default_firmware=FirmwareVersion.DJI_MSDK_V5,
        required_params=("sdk_mode",),
        supported_move_targets=DRONE_MOVE_TARGETS,
        metadata={
            "status": "profile-only",
            "sdk_modes": ("msdk", "cloud_api"),
            "connection_examples": [
                {"sdk_mode": "msdk", "app_key": "...", "app_secret": "..."},
                {"sdk_mode": "cloud_api", "mqtt_host": "broker.example.com", "workspace_id": "..."},
            ],
        },
    )
)

default_registry.register(
    ProductProfile(
        product=ProductType.UNITREE_GO2,
        category=DeviceCategory.ROBOT_DOG,
        vendor=VendorName.UNITREE,
        control_system="Unitree SDK2",
        supported_commands=ROBOT_COMMANDS,
        maturity=IntegrationMaturity.SIMULATED,
        capabilities=ROBOT_CAPABILITIES,
        supported_init_modes=_modes(InitMode.SDK_CREATE, InitMode.MANAGED_OBJECT),
        supported_firmware=_firmware_map((FirmwareVersion.UNITREE_SDK2, "Unitree SDK2 网络控制接口")),
        default_firmware=FirmwareVersion.UNITREE_SDK2,
        required_params=("network_interface",),
        supported_events=ROBOT_EVENTS,
        extension_namespaces=frozenset({"unitree"}),
        supported_move_targets=ROBOT_MOVE_TARGETS,
        metadata={
            "status": "profile-only",
            "connection_examples": [
                {"network_interface": "eth0"},
                {"network_interface": "enp3s0", "robot_ip": "192.168.123.161"},
            ],
        },
    ),
    adapters={FirmwareVersion.UNITREE_SDK2: MockRobotDogAdapter},
)
default_registry.register(
    ProductProfile(
        product=ProductType.UNITREE_G1,
        category=DeviceCategory.HUMANOID,
        vendor=VendorName.UNITREE,
        control_system="Unitree SDK2",
        supported_commands=ROBOT_COMMANDS,
        maturity=IntegrationMaturity.SIMULATED,
        capabilities=ROBOT_CAPABILITIES,
        supported_init_modes=_modes(InitMode.SDK_CREATE, InitMode.MANAGED_OBJECT),
        supported_firmware=_firmware_map((FirmwareVersion.UNITREE_SDK2, "Unitree SDK2 网络控制接口")),
        default_firmware=FirmwareVersion.UNITREE_SDK2,
        required_params=("network_interface",),
        supported_events=ROBOT_EVENTS,
        extension_namespaces=frozenset({"unitree"}),
        supported_move_targets=ROBOT_MOVE_TARGETS,
        metadata={
            "status": "profile-only",
            "connection_examples": [
                {"network_interface": "eth0"},
                {"network_interface": "enp3s0", "robot_ip": "192.168.123.161"},
            ],
        },
    ),
    adapters={FirmwareVersion.UNITREE_SDK2: MockRobotDogAdapter},
)

default_registry.register(
    ProductProfile(
        product=ProductType.OTHER,
        category=DeviceCategory.OTHER,
        vendor=VendorName.OTHER,
        control_system="custom",
        supported_commands=_commands(CommandName.CONNECT, CommandName.GET_STATUS),
        maturity=IntegrationMaturity.PLANNED,
        capabilities=_capabilities(CapabilityName.CONNECT, CapabilityName.STATUS),
        supported_init_modes=_modes(InitMode.SDK_CREATE, InitMode.MANAGED_OBJECT),
        supported_firmware=_firmware_map((FirmwareVersion.CUSTOM, "第三方或自研固件/SDK")),
        default_firmware=FirmwareVersion.CUSTOM,
        metadata={
            "usage": "用于第三方或自研设备登记产品信息；正式控制前需要注册对应 adapter。",
        },
    )
)
