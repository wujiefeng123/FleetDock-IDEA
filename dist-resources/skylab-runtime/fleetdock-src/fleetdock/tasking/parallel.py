"""并行计划组合与结果汇总。"""

from __future__ import annotations

from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from typing import Optional, Sequence, cast

from fleetdock.tasking.core import DevicePlan
from fleetdock.tasking.fleet import FleetPlan
from fleetdock.tasking.models import (
    FleetTaskResult,
    ParallelTaskResult,
    TaskCancellationToken,
    TaskCancelledError,
    TaskResult,
    TaskStatus,
)


class _ParallelCancellationToken:
    """并行计划内部取消令牌；同时感知外部会话取消和 fail_fast 本地取消。"""

    def __init__(self, upstream: Optional[TaskCancellationToken]) -> None:
        self._upstream = upstream
        self._local = TaskCancellationToken()

    @property
    def cancelled(self) -> bool:
        """返回外部会话或本地 fail_fast 是否已经请求取消。"""
        return self._local.cancelled or bool(self._upstream is not None and self._upstream.cancelled)

    def cancel(self) -> None:
        """请求本地 fail_fast 取消；不会修改外部会话令牌。"""
        self._local.cancel()

    def raise_if_cancelled(self) -> None:
        """若外部会话或本地 fail_fast 已取消，则抛出标准取消异常。"""
        if self._upstream is not None:
            self._upstream.raise_if_cancelled()
        if self._local.cancelled:
            raise TaskCancelledError("Parallel plan cancelled by fail_fast.")


class ParallelPlan:
    """并行计划组合器。

    通过 `Plan.parallel(plan1, plan2, ...)` 创建；每个子计划独立执行。默认 ``wait_all`` 会等待
    所有子计划结束后汇总结果；``fail_fast`` 会在首个失败后请求其它子计划协作取消并执行安全停止。
    """

    def __init__(self, plans: Sequence[DevicePlan | FleetPlan | "ParallelPlan"], failure_policy: str = "wait_all") -> None:
        if not plans:
            raise ValueError("Plan.parallel requires at least one plan.")
        if failure_policy not in {"wait_all", "fail_fast"}:
            raise ValueError("failure_policy must be 'wait_all' or 'fail_fast'.")
        self._plans = tuple(plans)
        self._failure_policy = failure_policy

    def start(self, step_timeout_s: float = 60.0, guard_interval_s: float = 0.2):
        """在后台启动全部子计划并返回统一 ``TaskSession``。

        ``step_timeout_s`` 和 ``guard_interval_s`` 会传给每条子计划；取消会共享到全部子计划并
        对每个目标执行通用安全停止。
        """
        from fleetdock.tasking.session import TaskSession

        return TaskSession(
            lambda token: self.run(
                step_timeout_s=step_timeout_s,
                guard_interval_s=guard_interval_s,
                _cancellation_token=token,
            ),
            safe_stop=self._safe_stop,
            name="fleetdock-parallel-task",
        )

    def run(
        self,
        step_timeout_s: float = 60.0,
        guard_interval_s: float = 0.2,
        *,
        _cancellation_token: Optional[TaskCancellationToken] = None,
    ) -> ParallelTaskResult:
        """并行执行所有子计划，并返回按传入顺序排列的结果。

        `step_timeout_s` 和 `guard_interval_s` 会传给每个子计划的 `run()`；返回值为
        `ParallelTaskResult`，其中 `plan_results` 按 `Plan.parallel(...)` 传入顺序保存子结果；
        ``failure_policy="fail_fast"`` 时首个失败会触发其它子计划协作取消和通用安全停止；
        ``_cancellation_token`` 仅由异步任务会话内部传入。
        """
        indexed_results: dict[int, TaskResult | FleetTaskResult | ParallelTaskResult] = {}
        fail_fast_triggered = False
        fail_fast_failure_triggered = False
        cancellation_token: Optional[TaskCancellationToken | _ParallelCancellationToken] = _cancellation_token
        if self._failure_policy == "fail_fast":
            cancellation_token = _ParallelCancellationToken(_cancellation_token)

        def run_child(plan: DevicePlan | FleetPlan | ParallelPlan) -> TaskResult | ParallelTaskResult:
            return plan.run(
                step_timeout_s=step_timeout_s,
                guard_interval_s=guard_interval_s,
                _cancellation_token=cast(Optional[TaskCancellationToken], cancellation_token),
            )

        with ThreadPoolExecutor(max_workers=len(self._plans), thread_name_prefix="fleetdock-parallel-plan") as executor:
            future_map = {
                executor.submit(run_child, plan): index
                for index, plan in enumerate(self._plans)
            }
            for future in as_completed(future_map):
                index = future_map[future]
                try:
                    result = future.result()
                except CancelledError:
                    result = TaskResult(False, TaskStatus.CANCELLED, "Parallel child plan cancelled.")
                except Exception as exc:
                    result = TaskResult(False, TaskStatus.FAILED, str(exc))
                indexed_results[index] = result
                if self._failure_policy == "fail_fast" and not result.ok and not fail_fast_triggered:
                    fail_fast_triggered = True
                    fail_fast_failure_triggered = result.status is not TaskStatus.CANCELLED
                    if isinstance(cancellation_token, _ParallelCancellationToken):
                        cancellation_token.cancel()
                    self._safe_stop()
                    for pending_future in future_map:
                        if pending_future is not future:
                            pending_future.cancel()
        plan_results = tuple(indexed_results[index] for index in sorted(indexed_results))
        externally_cancelled = _cancellation_token is not None and _cancellation_token.cancelled
        cancelled = externally_cancelled or (
            not fail_fast_failure_triggered
            and any(result.status is TaskStatus.CANCELLED for result in plan_results)
        )
        ok = not cancelled and len(plan_results) == len(self._plans) and all(result.ok for result in plan_results)
        status = TaskStatus.CANCELLED if cancelled else TaskStatus.SUCCESS if ok else TaskStatus.FAILED
        message = (
            "Parallel plan cancelled by caller."
            if cancelled
            else "Parallel plan completed."
            if ok
            else "Parallel plan failed."
        )
        return ParallelTaskResult(ok, status, message, plan_results)

    def _safe_stop(self) -> None:
        for plan in self._plans:
            safe_stop = getattr(plan, "_safe_stop", None)
            if callable(safe_stop):
                safe_stop()

