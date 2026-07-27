"""通用任务异步执行会话。"""

from __future__ import annotations

import logging
from threading import Condition, RLock, Thread
from typing import Callable, Optional

from fleetdock.tasking.models import (
    FleetTaskResult,
    ParallelTaskResult,
    TaskCancellationToken,
    TaskCancelMode,
    TaskResult,
    TaskSessionState,
    TaskStatus,
)

logger = logging.getLogger(__name__)

TaskExecutionResult = TaskResult | FleetTaskResult | ParallelTaskResult


class TaskSession:
    """在后台执行一条设备、Fleet 或并行计划，并提供查询、等待和取消能力。

    会话由 ``Plan(...).start()`` 或 ``Plan.parallel(...).start()`` 创建。状态读取和取消操作线程安全；
    ``cancel(SAFE_STOP)`` 会先发出协作取消信号，再调用计划提供的跨设备安全停止动作。取消不会
    自动返航、降落或执行应用业务补偿，这些动作应由调用方在 ``wait()`` 后显式决定。
    """

    def __init__(
        self,
        runner: Callable[[TaskCancellationToken], TaskExecutionResult],
        *,
        safe_stop: Optional[Callable[[], None]] = None,
        name: str = "fleetdock-task",
    ) -> None:
        """保存后台执行入口并立即启动任务。

        ``runner`` 接收会话独占的取消令牌并返回标准任务结果；``safe_stop`` 在调用方选择
        ``SAFE_STOP`` 时同步调用；``name`` 用作诊断线程名。普通开发者应通过 Plan 创建会话。
        """
        self._runner = runner
        self._safe_stop = safe_stop
        self._token = TaskCancellationToken()
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._state = TaskSessionState.CREATED
        self._result: Optional[TaskExecutionResult] = None
        self._exception: Optional[BaseException] = None
        self._thread = Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    @property
    def state(self) -> TaskSessionState:
        """返回当前生命周期状态；后台线程推进时该值会安全更新。"""
        with self._lock:
            return self._state

    @property
    def result(self) -> Optional[TaskExecutionResult]:
        """返回已经生成的任务结果；仍在执行或发生未转换异常时返回 ``None``。"""
        with self._lock:
            return self._result

    @property
    def exception(self) -> Optional[BaseException]:
        """返回后台执行未能转换为任务结果的异常；正常完成、失败或取消时为 ``None``。"""
        with self._lock:
            return self._exception

    @property
    def done(self) -> bool:
        """返回会话是否已经进入成功、失败或取消终态。"""
        return self.state in {
            TaskSessionState.CANCELLED,
            TaskSessionState.SUCCEEDED,
            TaskSessionState.FAILED,
        }

    def cancel(self, mode: TaskCancelMode = TaskCancelMode.SAFE_STOP) -> bool:
        """请求取消任务并返回本次调用是否首次发出请求。

        ``mode`` 决定取消后是否执行通用安全停止动作：``SAFE_STOP`` 会调用当前计划提供的停止回调，
        ``NONE`` 只阻止后续步骤。取消是协作式的，正在阻塞的厂商调用可能要等到下一个可中断点才结束。
        已结束或已取消时返回 ``False``。
        """
        if not isinstance(mode, TaskCancelMode):
            mode = TaskCancelMode(mode)
        with self._condition:
            if self._state in {
                TaskSessionState.CANCELLING,
                TaskSessionState.CANCELLED,
                TaskSessionState.SUCCEEDED,
                TaskSessionState.FAILED,
            }:
                return False
            self._state = TaskSessionState.CANCELLING
            self._token.cancel()
            self._condition.notify_all()
        logger.debug("任务会话收到取消请求: thread=%s mode=%s", self._thread.name, mode.value)
        if mode is TaskCancelMode.SAFE_STOP and self._safe_stop is not None:
            try:
                self._safe_stop()
            except Exception:
                logger.debug("任务会话安全停止动作失败: thread=%s", self._thread.name, exc_info=True)
        return True

    def wait(self, timeout_s: Optional[float] = None) -> TaskExecutionResult:
        """等待后台任务结束并返回标准任务结果。

        ``timeout_s`` 为最长等待秒数，``None`` 表示无限等待；等待超时抛出 ``TimeoutError``，但
        不会自动取消任务。若后台出现未转换异常，则原异常会在调用线程中重新抛出。
        """
        if timeout_s is not None and timeout_s < 0:
            raise ValueError("timeout_s cannot be negative.")
        with self._condition:
            finished = self._condition.wait_for(lambda: self.done, timeout=timeout_s)
            if not finished:
                raise TimeoutError("Task session did not finish before wait timeout.")
            if self._exception is not None:
                raise self._exception
            if self._result is None:
                raise RuntimeError("Task session finished without a result.")
            return self._result

    def _run(self) -> None:
        with self._condition:
            if self._state is TaskSessionState.CREATED:
                self._state = TaskSessionState.RUNNING
            self._condition.notify_all()
        try:
            result = self._runner(self._token)
            with self._condition:
                self._result = result
                if result.status is TaskStatus.CANCELLED or self._token.cancelled:
                    self._state = TaskSessionState.CANCELLED
                elif result.ok:
                    self._state = TaskSessionState.SUCCEEDED
                else:
                    self._state = TaskSessionState.FAILED
                self._condition.notify_all()
        except BaseException as exc:
            logger.debug("任务会话后台执行异常: thread=%s", self._thread.name, exc_info=True)
            with self._condition:
                self._exception = exc
                self._state = TaskSessionState.CANCELLED if self._token.cancelled else TaskSessionState.FAILED
                self._condition.notify_all()
