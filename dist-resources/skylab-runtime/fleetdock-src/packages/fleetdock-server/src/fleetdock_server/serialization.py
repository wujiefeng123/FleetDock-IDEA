"""FleetDock 公共模型到安全 JSON 数据的转换。"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


_OMITTED_FIELDS = frozenset({"native_data"})


def to_json_value(value: Any) -> Any:
    """把公开模型递归转换为 JSON 可序列化值。

    ``value`` 可以是 FleetDock 数据对象、枚举、集合或标量。返回值只包含 JSON 能表示的数据；
    原生对象始终省略，图像、点云等二进制缓冲区不会放入控制或遥测 JSON，而是返回长度摘要。
    """

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, memoryview):
        return {"binary_omitted": True, "length": value.nbytes}
    if isinstance(value, (bytes, bytearray)):
        return {"binary_omitted": True, "length": len(value)}
    if is_dataclass(value):
        return {
            item.name: to_json_value(getattr(value, item.name))
            for item in fields(value)
            if item.name not in _OMITTED_FIELDS
        }
    if isinstance(value, (Mapping, MappingProxyType)):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [to_json_value(item) for item in value]
    return str(value)
