"""FleetDock 公共异常类型。"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from fleetdock.models import ErrorCode


class FleetDockError(Exception):
    """FleetDock 公共异常基类。

    ``code`` 与 ``StandardResponse.code`` 使用同一套 ``ErrorCode``，``message`` 是面向
    调用方的可读说明，``details`` 保存不含敏感信息的结构化诊断数据。
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.code = code
        self.message = message
        self.details = dict(details or {})
        super().__init__(f"{code.value}: {message}")


class FleetDockValidationError(FleetDockError, ValueError):
    """公共参数、产品配置或初始化模式校验失败。

    该类型继续继承 ``ValueError``，既便于新代码按 FleetDock 异常层级捕获，也兼容
    已有调用方对 ``ValueError`` 的处理。
    """

    def __init__(self, message: str, *, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(ErrorCode.VALIDATION_ERROR, message, details=details)


class ProductNotRegisteredError(FleetDockError, KeyError):
    """请求的产品键尚未注册到 ``ProductRegistry``。"""

    def __init__(self, product: str) -> None:
        super().__init__(
            ErrorCode.PRODUCT_NOT_REGISTERED,
            f"product is not registered: {product}",
            details={"product": product},
        )


class AdapterNotFoundError(FleetDockError, KeyError):
    """产品与固件组合没有可用适配器实现。"""

    def __init__(self, product: str, firmware: Optional[str]) -> None:
        super().__init__(
            ErrorCode.ADAPTER_NOT_FOUND,
            f"adapter is not registered: product={product}, firmware={firmware}",
            details={"product": product, "firmware": firmware},
        )


class CapabilityContractError(FleetDockError, RuntimeError):
    """适配器声明的能力与其实际实现不一致。"""

    def __init__(self, message: str, *, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(ErrorCode.VALIDATION_ERROR, message, details=details)
