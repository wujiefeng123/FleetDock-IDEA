"""内部异常到标准响应的统一转换；不属于公共 SDK API。"""

from __future__ import annotations

from fleetdock.models import ErrorCode, ProductKey, StandardCommand, StandardResponse


def error_code_from_exception(exc: Exception) -> ErrorCode:
    """读取 ``exc`` 携带的错误码并返回标准枚举；未知或非法错误码降级为 UNKNOWN_ERROR。"""
    if isinstance(exc, ValueError):
        return ErrorCode.VALIDATION_ERROR
    if isinstance(exc, TimeoutError):
        return ErrorCode.COMMAND_TIMEOUT
    code = getattr(exc, "code", ErrorCode.UNKNOWN_ERROR)
    return code if isinstance(code, ErrorCode) else ErrorCode.UNKNOWN_ERROR


def safe_exception_message(exc: Exception) -> str:
    """返回 ``exc`` 的非空文本；不序列化异常属性，避免意外暴露连接对象或敏感参数。"""
    return str(exc) or type(exc).__name__


def command_failure(command: StandardCommand, exc: Exception) -> StandardResponse:
    """把 ``exc`` 转成与 ``command`` 关联的失败响应，并返回统一 ``StandardResponse``。"""
    return StandardResponse.failure(command, error_code_from_exception(exc), safe_exception_message(exc))


def operation_failure(*, device_id: str, product: ProductKey, exc: Exception) -> StandardResponse:
    """把 ``exc`` 转成 ``device_id``、``product`` 对应的操作失败响应并返回。"""
    return StandardResponse.operation_failure(
        device_id=device_id,
        product=product,
        code=error_code_from_exception(exc),
        message=safe_exception_message(exc),
        data={"exception_type": type(exc).__name__},
    )
