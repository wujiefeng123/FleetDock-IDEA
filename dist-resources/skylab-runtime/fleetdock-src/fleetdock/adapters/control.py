"""适配器内部互斥控制权管理。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from typing import Callable, Iterator, Optional


class ControlConflictError(RuntimeError):
    """当前控制器不能安全抢占已有控制器。"""


@dataclass(frozen=True)
class ControlLease:
    """一次内部控制权快照；``priority`` 越大越允许抢占较低优先级控制。"""

    owner: str
    priority: int


class ControlAuthority:
    """为同一设备串行化起飞、航线、手动控制和安全动作。"""

    def __init__(self) -> None:
        self._lock = RLock()
        self._active: Optional[ControlLease] = None
        self._cancel: Optional[Callable[[], None]] = None

    @contextmanager
    def acquire(
        self,
        owner: str,
        priority: int,
        *,
        preempt: bool = False,
        cancel: Optional[Callable[[], None]] = None,
    ) -> Iterator[ControlLease]:
        """取得控制权并在上下文结束时释放。

        ``owner`` 是控制器诊断名，``priority`` 是越大越优先的内部整数；``preempt`` 允许高优先级
        安全动作抢占，``cancel`` 是被抢占时调用的协作取消函数。返回上下文迭代器，其值为不可变
        ``ControlLease``；存在不可抢占的活动控制器时抛出 ``ControlConflictError``。
        """
        with self._lock:
            active = self._active
            if active is not None and active.owner != owner:
                if not preempt or priority <= active.priority:
                    raise ControlConflictError(
                        f"Control '{owner}' conflicts with active controller '{active.owner}'."
                    )
                if self._cancel is not None:
                    self._cancel()
            lease = ControlLease(owner, priority)
            self._active = lease
            self._cancel = cancel
        try:
            yield lease
        finally:
            with self._lock:
                if self._active is lease:
                    self._active = None
                    self._cancel = None

    def status(self) -> Optional[ControlLease]:
        """返回当前控制权快照；空闲时返回 ``None``。"""
        with self._lock:
            return self._active
