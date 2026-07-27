"""FleetDock 后台工作线程的统一生命周期。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
from threading import RLock, Thread, current_thread
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class WorkerState(str, Enum):
    """后台工作线程状态，用于诊断启动、运行、停止和异常退出。"""

    CREATED = "created"  # 已创建但尚未启动。
    RUNNING = "running"  # 工作函数正在后台线程中运行。
    STOPPING = "stopping"  # 已请求停止，正在等待工作函数退出。
    STOPPED = "stopped"  # 工作函数已正常退出。
    FAILED = "failed"  # 工作函数因未处理异常退出。


@dataclass(frozen=True)
class WorkerStatus:
    """后台工作线程的不可变状态快照。

    时间字段使用 ``time.monotonic()`` 的秒值，只适合计算进程内耗时；``last_error``
    保存异常对象供诊断，不保证可序列化。
    """

    name: str
    state: WorkerState
    is_alive: bool
    started_at_s: Optional[float] = None
    stopped_at_s: Optional[float] = None
    last_error: Optional[BaseException] = None


class ManagedWorker:
    """统一管理一个后台线程，并记录其生命周期和未处理异常。

    ``target`` 在线程启动后执行；``request_stop`` 应唤醒目标函数并请求其退出。线程默认不是
    daemon，资源拥有者必须在 ``close()`` 中调用 ``stop()`` 或 ``join()``。
    """

    def __init__(
        self,
        name: str,
        target: Callable[[], None],
        *,
        request_stop: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[BaseException], None]] = None,
        daemon: bool = False,
    ) -> None:
        self.name = name
        self._target = target
        self._request_stop = request_stop
        self._on_error = on_error
        self._daemon = daemon
        self._lock = RLock()
        self._thread: Optional[Thread] = None
        self._state = WorkerState.CREATED
        self._started_at_s: Optional[float] = None
        self._stopped_at_s: Optional[float] = None
        self._last_error: Optional[BaseException] = None

    def start(self) -> "ManagedWorker":
        """启动后台线程；重复调用不会创建第二个线程，返回当前工作对象。"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self
            if self._state not in (WorkerState.CREATED, WorkerState.STOPPED):
                raise RuntimeError(f"Worker '{self.name}' cannot start from state {self._state.value}.")
            self._state = WorkerState.RUNNING
            self._started_at_s = time.monotonic()
            self._stopped_at_s = None
            self._last_error = None
            self._thread = Thread(target=self._run, name=self.name, daemon=self._daemon)
            self._thread.start()
        logger.debug("后台工作线程已启动: name=%s daemon=%s", self.name, self._daemon)
        return self

    def request_stop(self) -> None:
        """请求后台线程停止但不等待；具体唤醒动作由构造时的回调完成，无返回值。"""
        callback: Optional[Callable[[], None]]
        with self._lock:
            if self._state in (WorkerState.STOPPED, WorkerState.FAILED):
                return
            if self._state is WorkerState.RUNNING:
                self._state = WorkerState.STOPPING
            callback = self._request_stop
        if callback is not None:
            callback()

    def join(self, timeout_s: Optional[float] = None) -> bool:
        """等待线程退出；``timeout_s`` 单位为秒，返回是否已在期限内退出。"""
        thread = self._thread
        if thread is None or thread is current_thread():
            return True
        thread.join(timeout=timeout_s)
        return not thread.is_alive()

    def stop(self, timeout_s: Optional[float] = 2.0) -> bool:
        """请求停止并等待退出；``timeout_s`` 单位为秒，返回线程是否已经结束。"""
        self.request_stop()
        stopped = self.join(timeout_s)
        if not stopped:
            logger.debug("等待后台工作线程退出超时: name=%s timeout_s=%s", self.name, timeout_s)
        return stopped

    def status(self) -> WorkerStatus:
        """返回线程安全的不可变状态快照。"""
        with self._lock:
            thread = self._thread
            return WorkerStatus(
                name=self.name,
                state=self._state,
                is_alive=bool(thread is not None and thread.is_alive()),
                started_at_s=self._started_at_s,
                stopped_at_s=self._stopped_at_s,
                last_error=self._last_error,
            )

    def _run(self) -> None:
        try:
            self._target()
        except BaseException as exc:
            with self._lock:
                self._state = WorkerState.FAILED
                self._last_error = exc
                self._stopped_at_s = time.monotonic()
            logger.debug("后台工作线程异常退出: name=%s error=%s", self.name, exc, exc_info=True)
            if self._on_error is not None:
                try:
                    self._on_error(exc)
                except Exception:
                    logger.debug("后台工作线程错误回调异常: name=%s", self.name, exc_info=True)
            return
        with self._lock:
            self._state = WorkerState.STOPPED
            self._stopped_at_s = time.monotonic()
        logger.debug("后台工作线程已退出: name=%s", self.name)
