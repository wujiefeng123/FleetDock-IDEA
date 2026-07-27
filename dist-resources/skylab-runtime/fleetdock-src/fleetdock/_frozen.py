"""公共快照使用的轻量只读容器工具。"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any


class FrozenDict(Mapping[Any, Any]):
    """保持 Mapping 读取接口的递归只读字典；用于阻止已发布 SDK 快照被调用方改写。"""

    def __init__(self, values: Mapping[Any, Any] | None = None) -> None:
        self._values = {key: freeze_value(value) for key, value in (values or {}).items()}

    def __getitem__(self, key: Any) -> Any:
        return self._values[key]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return repr(self._values)

    def __deepcopy__(self, memo: dict[int, Any]) -> "FrozenDict":
        return self


def freeze_value(value: Any) -> Any:
    """递归冻结 ``value`` 并返回只读快照；普通标量和厂商原生对象保持原样。"""

    if isinstance(value, FrozenDict):
        return value
    if isinstance(value, Mapping):
        return FrozenDict(value)
    if isinstance(value, (list, tuple)):
        return tuple(freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(freeze_value(item) for item in value)
    return value


def freeze_mapping(value: Mapping[Any, Any]) -> Mapping[Any, Any]:
    """把 ``value`` 递归转换为只读映射并返回；键及非容器值保持原有类型。"""

    return value if isinstance(value, FrozenDict) else FrozenDict(value)
