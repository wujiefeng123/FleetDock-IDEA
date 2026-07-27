"""Adapter factory."""

from __future__ import annotations

import logging

from fleetdock.adapters import DeviceAdapter
from fleetdock.events import DeviceEventStream, EventHub
from fleetdock.errors import CapabilityContractError, FleetDockValidationError
from fleetdock.models import (
    CapabilityName,
    ConnectionInfo,
    FirmwareKey,
    ProductKey,
    normalize_firmware,
)
from fleetdock.registry import ProductRegistry, default_registry

logger = logging.getLogger(__name__)


class AdapterFactory:
    """适配器工厂。

    工厂根据产品 profile、固件/SDK 版本和初始化模式创建具体 `DeviceAdapter`。它会在创建前
    校验产品是否支持当前模式、SDK 创建模式是否缺少必填 `params`、托管模式是否提供
    `managed_object`。
    """

    def __init__(self, registry: ProductRegistry = default_registry) -> None:
        self.registry = registry

    def create(
        self,
        instance_id: str,
        product: ProductKey,
        firmware_version: FirmwareKey,
        connection: ConnectionInfo,
        event_hub: EventHub,
        device_events: DeviceEventStream,
    ) -> DeviceAdapter:
        """创建并返回产品对应的适配器实例。

        `instance_id` 是 FleetDock 内部设备实例 ID；`product` 和 `firmware_version` 用于查找
        profile 与适配器类；`connection` 携带 `params` 或 `managed_object`；`event_hub`
        和 `device_events` 用于发布进程内消息和单设备标准事件。
        """
        logger.debug(
            "开始创建适配器: instance_id=%s product=%s firmware=%s mode=%s",
            instance_id,
            product,
            firmware_version,
            connection.mode.value,
        )
        profile = self.registry.get(product)
        if not profile.supports_init_mode(connection.mode):
            logger.debug("适配器创建失败: 产品不支持初始化模式 %s", connection.mode.value)
            raise FleetDockValidationError(
                f"product does not support initialization mode {connection.mode.value}",
                details={"product": str(product), "mode": connection.mode.value},
            )

        if connection.mode.value == "managed_object" and connection.managed_object is None:
            logger.debug("适配器创建失败: 托管模式缺少 managed_object")
            raise FleetDockValidationError("managed_object is required")

        if connection.mode.value == "sdk_create":
            missing_params = profile.missing_required_params(connection.params)
            if missing_params:
                names = ", ".join(missing_params)
                logger.debug("适配器创建失败: 缺少连接参数 %s", names)
                raise FleetDockValidationError(
                    f"missing connection params: {names}",
                    details={"missing_params": missing_params},
                )

        firmware_key = normalize_firmware(profile.resolve_firmware(firmware_version))
        if firmware_key is None:
            logger.debug("适配器创建失败: 产品没有默认固件")
            raise FleetDockValidationError("product has no default firmware", details={"product": str(product)})
        adapter_type = self.registry.get_adapter(product, firmware_key)
        adapter = adapter_type(instance_id, product, connection, profile, event_hub, device_events)
        self._validate_capability_contracts(adapter)
        logger.debug("适配器创建完成: instance_id=%s adapter=%s", instance_id, adapter_type.__name__)
        return adapter

    @staticmethod
    def _validate_capability_contracts(adapter: DeviceAdapter) -> None:
        missing_handlers = tuple(
            command.value
            for command in adapter.profile.supported_commands
            if getattr(adapter, f"_handle_{command.value}", None) is None
        )
        if missing_handlers:
            logger.debug(
                "适配器命令契约不一致: adapter=%s missing=%s",
                type(adapter).__name__,
                missing_handlers,
            )
            raise CapabilityContractError(
                f"Adapter '{type(adapter).__name__}' declares commands without handlers: {', '.join(missing_handlers)}.",
                details={"adapter": type(adapter).__name__, "missing_commands": missing_handlers},
            )
        if (
            adapter.profile.has_capability(CapabilityName.MISSION)
            and type(adapter).supports_native_route is DeviceAdapter.supports_native_route
        ):
            logger.debug(
                "适配器能力契约不一致: adapter=%s capability=%s",
                type(adapter).__name__,
                CapabilityName.MISSION.value,
            )
            raise CapabilityContractError(
                f"Adapter '{type(adapter).__name__}' declares mission capability but does not implement native routes.",
                details={"adapter": type(adapter).__name__, "capability": CapabilityName.MISSION.value},
            )


default_factory = AdapterFactory()
