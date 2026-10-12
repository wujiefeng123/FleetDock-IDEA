"""通用单设备任务执行器。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import logging
import time
from typing import TYPE_CHECKING, Any, Callable, Deque, List, Mapping, Optional, Self, Sequence

from fleetdock.models import CapabilityName, CommandName, ErrorCode, StandardCommand, StandardResponse
from fleetdock.avoidance import AvoidanceBlockedError
from fleetdock.movement import MoveTarget
from fleetdock.readiness import ReadinessRequirement
from fleetdock.tasking.models import (
    HeadingMode, MotionExecutionMode, RouteMotion, TaskCancellationToken, TaskCancelledError, TaskContext, TaskGuardAction, TaskOptions,
    TaskCompletionReason, TaskResult, TaskStatus, TaskStepHookAction, TaskStepHookDecision, TaskStepHookEvent,
    TaskStepHookPhase, TaskStepResult, UnlockStrategy,
)
from fleetdock.tasking.route_execution import RouteExecutionMixin
from fleetdock.takeoff import TakeoffAdapter, TakeoffOptions
from fleetdock.event_models import serialize_event_data

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

class _GuardTriggered(RuntimeError):
    def __init__(self, result: "TaskResult") -> None:
        super().__init__(result.message)
        self.result = result


@dataclass(frozen=True)
class _TaskStep:
    name: str
    params: Mapping[str, Any]
    target_devices: tuple[Any, ...] | None = None


@dataclass(frozen=True)
class _TaskGuard:
    condition: Callable[[TaskContext], bool]
    then: Callable[[TaskContext], Any] | TaskGuardAction | None
    reason: str


class DevicePlan(RouteExecutionMixin):
    """通用计划编排基类，负责按队列顺序执行跨设备通用动作。"""

    def __init__(self, device: Any, interceptors: Sequence[Callable[[TaskStepHookEvent], TaskStepHookDecision | None]] | None = None) -> None:
        """保存任务目标设备和步骤 Hook。

        `interceptors` 会在每个真实任务步骤执行前、执行后或异常时按注册顺序调用。Hook 默认只监听；
        只有返回 `TaskStepHookDecision` 时才会替换入参、替换结果、跳过步骤或中断任务。
        """
        self._device = device
        self._steps: Deque[_TaskStep] = deque()
        self._active_guards: List[_TaskGuard] = []
        self._failed_handlers: List[Callable[[TaskContext, TaskResult], Any]] = []
        self._interceptors = list(interceptors or ())

    def speed(self, speed_m_s: float) -> Self:
        """设置 ``speed_m_s`` 为后续航线默认水平速度，单位为米/秒；返回当前计划链。"""
        return self._set_option(speed_m_s=speed_m_s)

    def climb_speed(self, climb_speed_m_s: float) -> Self:
        """设置 ``climb_speed_m_s`` 为后续爬升速度，单位为米/秒；返回当前计划链。"""
        return self._set_option(climb_speed_m_s=climb_speed_m_s)

    def altitude(self, altitude_m: float) -> Self:
        """设置 ``altitude_m`` 为后续航线相对 Home 高度，单位为米；返回当前计划链。"""
        return self._set_option(altitude_m=altitude_m)

    def altitude_tolerance(self, tolerance_m: float) -> Self:
        """设置高度容差 ``tolerance_m``，单位为米；达到目标高度减容差即完成，返回当前计划链。"""
        return self._set_option(altitude_tolerance_m=tolerance_m)

    def position_tolerance(self, tolerance_m: float) -> Self:
        """设置位置到达容差 ``tolerance_m``，单位为米；返回当前计划链。"""
        return self._set_option(position_tolerance_m=tolerance_m)

    def tolerance(self, tolerance_m: float) -> Self:
        """把 ``tolerance_m`` 同时设为高度和位置容差，单位为米；返回当前计划链。"""
        return self._set_option(altitude_tolerance_m=tolerance_m, position_tolerance_m=tolerance_m)

    def retry(self, retry_count: int, *, backoff_s: float = 0.5) -> Self:
        """设置后续安全步骤的额外重试次数和退避秒数并返回当前计划链。

        仅状态查询、停止/悬停、解锁/上锁、站立/坐下和就绪等待会自动重试；起飞、移动、
        航线、降落、返航及其它结果不幂等步骤始终只执行一次。``retry_count`` 和 ``backoff_s``
        不得为负数。
        """
        if retry_count < 0 or backoff_s < 0:
            raise ValueError("retry_count and backoff_s cannot be negative.")
        return self._set_option(retry_count=retry_count, retry_backoff_s=backoff_s)

    def timeout(self, timeout_s: float) -> Self:
        """设置步骤超时 ``timeout_s``，单位为秒；返回当前计划链。"""
        return self._set_option(step_timeout_s=timeout_s)

    def heading(self, heading: HeadingMode) -> Self:
        """设置 ``heading`` 为后续航线机头策略；校验枚举后返回当前计划链。"""
        return self._set_option(heading=self._normalize_heading(heading))

    def require_confirmation(self, required: bool = True) -> Self:
        """设置 ``required`` 是否要求遥测确认步骤完成；返回当前计划链。"""
        return self._set_option(require_confirmation=required)

    def on_failed(self, handler: Callable[[TaskContext, TaskResult], Any]) -> Self:
        """注册失败 ``handler``；任务失败、超时或守卫触发时调用，并返回当前计划链。"""
        self._failed_handlers.append(handler)
        return self

    def move(
        self,
        target: MoveTarget,
    ) -> Self:
        """把强类型移动目标加入计划队列并返回当前计划链。

        ``target`` 与 ``device.move(target)`` 使用同一套 GPS 点、局部点或相对位移模型；
        声明阶段不会发送命令，执行时由设备 profile 校验目标类型。
        """
        return self._add("move", target=target)

    def set_velocity(
        self,
        *,
        x: float = 0.0,
        y: float = 0.0,
        z: float = 0.0,
        speed: float = 0.0,
        yaw_deg: Optional[float] = None,
    ) -> Self:
        """把高级速度控制步骤加入队列并返回当前计划链。

        ``x``、``y``、``z`` 是局部运动方向分量，``speed`` 单位为米/秒，``yaw_deg`` 是可选
        偏航角（度）；该步骤在 ``run()`` 执行到此处时发送。
        """
        params: dict[str, Any] = {"x": x, "y": y, "z": z, "speed": speed}
        if yaw_deg is not None:
            params["yaw_deg"] = yaw_deg
        return self._add("set_velocity", **params)

    def stop(self) -> Self:
        """把停止运动步骤加入队列并返回当前计划链。"""
        return self._add("stop")

    def emergency_stop(self) -> Self:
        """把紧急停止步骤加入队列并返回当前计划链。"""
        return self._add("emergency_stop")

    def get_status(self) -> Self:
        """把状态查询步骤加入队列并返回当前计划链。"""
        return self._add("get_status")

    def _set_option(self, **options: Any) -> Self:
        self._steps.append(_TaskStep(name="__options__", params=options))
        return self

    def when(
        self,
        condition: Callable[[TaskContext], bool],
        *,
        then: Callable[[TaskContext], Any] | TaskGuardAction | str | None = None,
        reason: str = "Task guard triggered.",
    ) -> Self:
        """注册运行期守卫并返回当前计划链。

        ``condition`` 满足时执行 ``then``；``reason`` 写入失败结果并中断后续步骤。
        """
        action = self._normalize_guard_action(then)
        self._steps.append(
            _TaskStep(
                name="__guard__",
                params={"guard": _TaskGuard(condition=condition, then=action, reason=reason)},
            )
        )
        return self

    def start(self, step_timeout_s: float = 60.0, guard_interval_s: float = 0.2):
        """在后台启动当前计划并返回可查询、等待和取消的 ``TaskSession``。

        ``step_timeout_s`` 是默认步骤异常超时秒数，``guard_interval_s`` 是守卫和取消检查间隔秒数。
        同一计划对象只能消费一次计划队列；启动后调用方应通过返回会话管理生命周期。
        """
        from fleetdock.tasking.session import TaskSession

        return TaskSession(
            lambda token: self.run(
                step_timeout_s=step_timeout_s,
                guard_interval_s=guard_interval_s,
                _cancellation_token=token,
            ),
            safe_stop=self._safe_stop,
            name="fleetdock-device-task",
        )

    def run(
        self,
        step_timeout_s: float = 60.0,
        guard_interval_s: float = 0.2,
        *,
        _cancellation_token: Optional[TaskCancellationToken] = None,
    ) -> TaskResult:
        """同步执行计划队列并返回 ``TaskResult``。

        ``step_timeout_s`` 是默认步骤超时秒数，``guard_interval_s`` 是守卫检查间隔秒数；
        ``_cancellation_token`` 由 ``TaskSession`` 内部传入，普通调用方不应自行构造。
        """
        ctx = TaskContext(
            device=self._device,
            options=TaskOptions(),
            state_source=self._device.state,
            cancellation_token=_cancellation_token,
        )
        ctx.options.step_timeout_s = step_timeout_s
        step_index = 0
        while self._steps:
            if ctx.cancellation_token is not None:
                try:
                    ctx.cancellation_token.raise_if_cancelled()
                except TaskCancelledError as exc:
                    return TaskResult(
                        False,
                        TaskStatus.CANCELLED,
                        str(exc),
                        tuple(ctx.step_results),
                        failed_step=ctx.current_step,
                        completion_reason=TaskCompletionReason.CANCELLED,
                    )
            guard_result = self._check_guards(ctx)
            if guard_result is not None:
                return self._handle_failed(ctx, guard_result)

            step = self._steps.popleft()
            if step.name == "__guard__":
                self._active_guards.append(step.params["guard"])
                guard_result = self._check_guards(ctx)
                if guard_result is not None:
                    return self._handle_failed(ctx, guard_result)
                continue
            if step.name == "__options__":
                for name, value in step.params.items():
                    setattr(ctx.options, name, value)
                continue
            step_index += 1
            started_at_s = time.monotonic()
            ctx.current_step = step.name
            hook_decision, step = self._before_step_hook(step, ctx, step_index)
            if hook_decision is not None:
                result = self._result_from_hook_decision(step, hook_decision, started_at_s)
                ctx.step_results.append(result)
                if hook_decision.action == TaskStepHookAction.SKIP:
                    continue
                task_result = TaskResult(
                    False,
                    TaskStatus.FAILED,
                    result.message,
                    tuple(ctx.step_results),
                    failed_step=step.name,
                    completion_reason=TaskCompletionReason.STEP_FAILED,
                )
                return self._handle_failed(ctx, task_result)
            ctx.current_step = step.name
            metadata = getattr(self._device, "metadata", None)
            device_id = getattr(metadata, "device_id", None) or getattr(self._device, "instance_id", None)
            logger.debug(
                "任务步骤开始: device_id=%s step=%s params=%s",
                device_id,
                step.name,
                dict(step.params),
            )
            try:
                response = self._execute_step_with_retry(
                    step,
                    ctx,
                    ctx.options.step_timeout_s,
                    guard_interval_s,
                )
                if ctx.cancellation_token is not None:
                    ctx.cancellation_token.raise_if_cancelled()
            except _GuardTriggered as exc:
                return self._handle_failed(ctx, exc.result)
            except TaskCancelledError as exc:
                return TaskResult(
                    False,
                    TaskStatus.CANCELLED,
                    str(exc),
                    tuple(ctx.step_results),
                    failed_step=step.name,
                    completion_reason=TaskCompletionReason.CANCELLED,
                )
            except AvoidanceBlockedError as exc:
                result = TaskStepResult(
                    step.name,
                    False,
                    str(exc),
                    started_at_s=started_at_s,
                    finished_at_s=time.monotonic(),
                )
                result = self._on_step_error_hook(step, ctx, step_index, result, exc)
                ctx.step_results.append(result)
                task_result = TaskResult(
                    False,
                    TaskStatus.FAILED,
                    str(exc),
                    tuple(ctx.step_results),
                    failed_step=step.name,
                    completion_reason=TaskCompletionReason.AVOIDANCE_FALLBACK,
                    safety_action=exc.decision.action.value,
                )
                return self._handle_failed(ctx, task_result)
            except TimeoutError as exc:
                result = TaskStepResult(step.name, False, str(exc), started_at_s=started_at_s, finished_at_s=time.monotonic())
                result = self._on_step_error_hook(step, ctx, step_index, result, exc)
                ctx.step_results.append(result)
                task_result = TaskResult(
                    False,
                    TaskStatus.TIMEOUT,
                    str(exc),
                    tuple(ctx.step_results),
                    failed_step=step.name,
                    completion_reason=TaskCompletionReason.TIMEOUT,
                )
                return self._handle_failed(ctx, task_result)
            except Exception as exc:
                result = TaskStepResult(step.name, False, str(exc), started_at_s=started_at_s, finished_at_s=time.monotonic())
                result = self._on_step_error_hook(step, ctx, step_index, result, exc)
                ctx.step_results.append(result)
                task_result = TaskResult(
                    False,
                    TaskStatus.FAILED,
                    str(exc),
                    tuple(ctx.step_results),
                    failed_step=step.name,
                    completion_reason=TaskCompletionReason.STEP_FAILED,
                )
                return self._handle_failed(ctx, task_result)

            finished_at_s = time.monotonic()
            step_result = TaskStepResult(
                step.name,
                response.ok,
                response.message,
                response=response,
                started_at_s=started_at_s,
                finished_at_s=finished_at_s,
            )
            step_result = self._after_step_hook(step, ctx, step_index, step_result)
            ctx.step_results.append(step_result)
            logger.debug(
                "任务步骤结束: device_id=%s step=%s ok=%s message=%s",
                device_id,
                step.name,
                step_result.ok,
                step_result.message,
            )
            if not step_result.ok:
                task_result = TaskResult(
                    False,
                    TaskStatus.FAILED,
                    step_result.message,
                    tuple(ctx.step_results),
                    failed_step=step.name,
                    completion_reason=TaskCompletionReason.STEP_FAILED,
                )
                return self._handle_failed(ctx, task_result)

            guard_result = self._check_guards(ctx)
            if guard_result is not None:
                return self._handle_failed(ctx, guard_result)

        return TaskResult(
            True,
            TaskStatus.SUCCESS,
            "Task completed.",
            tuple(ctx.step_results),
            completion_reason=TaskCompletionReason.COMPLETED,
        )

    def _add(self, name: str, **params: Any) -> Self:
        self._steps.append(_TaskStep(name=name, params=params))
        return self

    def _before_step_hook(
        self,
        step: _TaskStep,
        ctx: TaskContext,
        step_index: int,
        *,
        scope: str = "device",
        device_id: Optional[str] = None,
    ) -> tuple[TaskStepHookDecision | None, _TaskStep]:
        metadata = getattr(ctx.device, "metadata", None)
        resolved_device_id = device_id or getattr(metadata, "device_id", None) or getattr(ctx.device, "instance_id", None)
        params = dict(step.params)
        for interceptor in self._interceptors:
            event = TaskStepHookEvent(
                TaskStepHookPhase.BEFORE_STEP,
                step_index,
                step.name,
                params,
                ctx.device,
                scope=scope,
                device_id=resolved_device_id,
            )
            try:
                decision = interceptor(event)
            except Exception as exc:
                return TaskStepHookDecision.fail(f"interceptor error before step '{step.name}': {exc}"), step
            if decision is None or decision.action == TaskStepHookAction.CONTINUE:
                continue
            if decision.action == TaskStepHookAction.REPLACE_PARAMS:
                params = dict(decision.params or {})
                continue
            if decision.action in (TaskStepHookAction.SKIP, TaskStepHookAction.FAIL):
                return decision, _TaskStep(step.name, params, step.target_devices)
        return None, _TaskStep(step.name, params, step.target_devices)

    def _after_step_hook(
        self,
        step: _TaskStep,
        ctx: TaskContext,
        step_index: int,
        result: TaskStepResult,
        *,
        scope: str = "device",
        device_id: Optional[str] = None,
    ) -> TaskStepResult:
        metadata = getattr(ctx.device, "metadata", None)
        resolved_device_id = device_id or getattr(metadata, "device_id", None) or getattr(ctx.device, "instance_id", None)
        current = result
        for interceptor in self._interceptors:
            event = TaskStepHookEvent(
                TaskStepHookPhase.AFTER_STEP,
                step_index,
                step.name,
                dict(step.params),
                ctx.device,
                scope=scope,
                device_id=resolved_device_id,
                result=current,
                message=current.message,
            )
            try:
                decision = interceptor(event)
            except Exception as exc:
                return TaskStepResult(
                    step.name,
                    False,
                    f"interceptor error after step '{step.name}': {exc}",
                    response=current.response,
                    started_at_s=current.started_at_s,
                    finished_at_s=time.monotonic(),
                )
            if decision is None or decision.action == TaskStepHookAction.CONTINUE:
                continue
            if decision.action == TaskStepHookAction.REPLACE_RESULT and decision.result is not None:
                current = decision.result
            elif decision.action == TaskStepHookAction.FAIL:
                current = TaskStepResult(
                    step.name,
                    False,
                    decision.message,
                    response=current.response,
                    started_at_s=current.started_at_s,
                    finished_at_s=time.monotonic(),
                )
        return current

    def _on_step_error_hook(
        self,
        step: _TaskStep,
        ctx: TaskContext,
        step_index: int,
        result: TaskStepResult,
        exception: BaseException,
        *,
        scope: str = "device",
        device_id: Optional[str] = None,
    ) -> TaskStepResult:
        metadata = getattr(ctx.device, "metadata", None)
        resolved_device_id = device_id or getattr(metadata, "device_id", None) or getattr(ctx.device, "instance_id", None)
        current = result
        for interceptor in self._interceptors:
            event = TaskStepHookEvent(
                TaskStepHookPhase.ON_STEP_ERROR,
                step_index,
                step.name,
                dict(step.params),
                ctx.device,
                scope=scope,
                device_id=resolved_device_id,
                result=current,
                exception=exception,
                message=current.message,
            )
            try:
                decision = interceptor(event)
            except Exception as exc:
                return TaskStepResult(
                    step.name,
                    False,
                    f"{current.message}; interceptor error on step '{step.name}': {exc}",
                    response=current.response,
                    started_at_s=current.started_at_s,
                    finished_at_s=time.monotonic(),
                )
            if decision is None or decision.action == TaskStepHookAction.CONTINUE:
                continue
            if decision.action == TaskStepHookAction.REPLACE_RESULT and decision.result is not None:
                current = decision.result
            elif decision.action == TaskStepHookAction.FAIL:
                current = TaskStepResult(
                    step.name,
                    False,
                    decision.message,
                    response=current.response,
                    started_at_s=current.started_at_s,
                    finished_at_s=time.monotonic(),
                )
        return current

    @staticmethod
    def _result_from_hook_decision(
        step: _TaskStep,
        decision: TaskStepHookDecision,
        started_at_s: float,
    ) -> TaskStepResult:
        ok = decision.action == TaskStepHookAction.SKIP
        message = decision.message or ("Step skipped by interceptor." if ok else "Step failed by interceptor.")
        return TaskStepResult(step.name, ok, message, started_at_s=started_at_s, finished_at_s=time.monotonic())

    def _handle_failed(self, ctx: TaskContext, result: TaskResult) -> TaskResult:
        messages: List[str] = []
        cleanup_result: TaskResult | None = result.cleanup_result
        for handler in self._failed_handlers:
            try:
                handler_result = handler(ctx, result)
                if isinstance(handler_result, TaskResult):
                    cleanup_result = handler_result
            except Exception as exc:
                messages.append(f"failure handler error: {exc}")
        if not messages and cleanup_result is result.cleanup_result:
            return result
        return TaskResult(
            ok=result.ok,
            status=result.status,
            message=f"{result.message}; {'; '.join(messages)}",
            steps=result.steps,
            failed_step=result.failed_step,
            triggered_guard=result.triggered_guard,
            completion_reason=result.completion_reason,
            safety_action=result.safety_action,
            cleanup_result=cleanup_result,
        )

    @staticmethod
    def _normalize_guard_action(
        then: Callable[[TaskContext], Any] | TaskGuardAction | str | None,
    ) -> Callable[[TaskContext], Any] | TaskGuardAction | None:
        if then is None or callable(then) or isinstance(then, TaskGuardAction):
            return then
        return TaskGuardAction(str(then))

    @staticmethod
    def _normalize_heading(heading: HeadingMode | str | None) -> HeadingMode:
        if heading is None:
            return HeadingMode.NONE
        if isinstance(heading, HeadingMode):
            return heading
        return HeadingMode(str(heading))

    @staticmethod
    def _normalize_motion_mode(mode: MotionExecutionMode | str | None) -> MotionExecutionMode:
        if mode is None:
            return MotionExecutionMode.AUTO
        if isinstance(mode, MotionExecutionMode):
            return mode
        return MotionExecutionMode(str(mode))

    @staticmethod
    def _normalize_route_motion(motion: RouteMotion | MotionExecutionMode | str | None) -> RouteMotion | None:
        if motion is None:
            return None
        if isinstance(motion, RouteMotion):
            return motion
        if isinstance(motion, (MotionExecutionMode, str)):
            return RouteMotion(mode=motion)
        raise TypeError("motion must be RouteMotion, MotionExecutionMode, str, or None.")

    def _execute_step(
        self,
        step: _TaskStep,
        ctx: TaskContext,
        step_timeout_s: float,
        guard_interval_s: float,
    ) -> StandardResponse:
        if step.name == "wait_until_ready":
            return self._wait_until_ready(step, ctx, guard_interval_s)
        if step.name == "route":
            return self._route(step, ctx, guard_interval_s)
        if step.name == "takeoff":
            adapter = getattr(self._device, "adapter", None)
            if isinstance(adapter, TakeoffAdapter) and adapter.supports_confirmed_takeoff():
                options = TakeoffOptions(
                    target_altitude_m=float(step.params["altitude"]),
                    altitude_tolerance_m=ctx.options.altitude_tolerance_m,
                    timeout_s=step_timeout_s,
                    climb_speed_m_s=ctx.options.climb_speed_m_s,
                )

                def cancellation_check() -> None:
                    self._raise_if_guard_triggered(ctx)

                command = StandardCommand(
                    command=CommandName.TAKEOFF,
                    device_id=self._device.instance_id,
                    product=self._device.product,
                    params={"altitude": options.target_altitude_m},
                )
                try:
                    status = adapter.execute_takeoff(
                        options,
                        cancellation_check=cancellation_check,
                    )
                except (TaskCancelledError, _GuardTriggered):
                    raise
                except Exception as exc:
                    prefix = str(exc).split(":", 1)[0]
                    code = ErrorCode.__members__.get(prefix, ErrorCode.UNKNOWN_ERROR)
                    return StandardResponse.failure(command, code, str(exc) or type(exc).__name__)
                return StandardResponse.success(
                    command,
                    "Takeoff target physically confirmed.",
                    {"takeoff": serialize_event_data(status)},
                )
            if ctx.state.armed is not True and hasattr(self._device, "unlock"):
                unlock_response = self._device.unlock()
                if not unlock_response.ok:
                    return unlock_response

        if step.name == "unlock" and "strategy" in step.params:
            response = self._execute_unlock(step)
        else:
            method = getattr(self._device, step.name)
            response = method(**dict(step.params))
        if not response.ok:
            return response
        self._wait_for_standard_confirmation(step, ctx, step_timeout_s, guard_interval_s)
        return response

    def _execute_step_with_retry(
        self,
        step: _TaskStep,
        ctx: TaskContext,
        step_timeout_s: float,
        guard_interval_s: float,
    ) -> StandardResponse:
        safe_steps = {
            "get_status",
            "stop",
            "hover",
            "unlock",
            "lock",
            "stand",
            "sit",
            "wait_until_ready",
        }
        attempts = 1 + ctx.options.retry_count if step.name in safe_steps else 1
        last_response: Optional[StandardResponse] = None
        for attempt in range(1, attempts + 1):
            self._raise_if_guard_triggered(ctx)
            try:
                last_response = self._execute_step(step, ctx, step_timeout_s, guard_interval_s)
            except Exception:
                if attempt >= attempts:
                    raise
                logger.debug(
                    "安全任务步骤异常后重试: step=%s attempt=%d/%d",
                    step.name,
                    attempt,
                    attempts,
                    exc_info=True,
                )
            else:
                if last_response.ok or attempt >= attempts:
                    return last_response
                logger.debug(
                    "安全任务步骤失败后重试: step=%s attempt=%d/%d code=%s message=%s",
                    step.name,
                    attempt,
                    attempts,
                    last_response.code.value,
                    last_response.message,
                )
            deadline_s = time.monotonic() + ctx.options.retry_backoff_s
            while time.monotonic() < deadline_s:
                self._raise_if_guard_triggered(ctx)
                time.sleep(min(guard_interval_s, max(0.0, deadline_s - time.monotonic())))
        if last_response is None:
            raise RuntimeError(f"Step '{step.name}' retry loop finished without a response.")
        return last_response

    def _execute_unlock(self, step: _TaskStep) -> StandardResponse:
        """执行显式空中设备解锁策略，强制回退只在调用方声明时发生。"""
        strategy = step.params.get("strategy", UnlockStrategy.STANDARD)
        if not isinstance(strategy, UnlockStrategy):
            strategy = UnlockStrategy(strategy)
        if strategy is UnlockStrategy.FORCE:
            logger.debug("任务请求强制解锁: device_id=%s", getattr(self._device, "instance_id", None))
            return self._device.unlock(force=True)
        response = self._device.unlock()
        if response.ok or strategy is UnlockStrategy.STANDARD:
            return response
        logger.debug(
            "标准解锁失败后按显式策略请求强制解锁: device_id=%s message=%s",
            getattr(self._device, "instance_id", None),
            response.message,
        )
        return self._device.unlock(force=True)

    def _wait_for_standard_confirmation(
        self,
        step: _TaskStep,
        ctx: TaskContext,
        timeout_s: float,
        guard_interval_s: float,
    ) -> None:
        if step.name == "takeoff" and ctx.options.require_confirmation:
            target = float(step.params["altitude"]) - ctx.options.altitude_tolerance_m
            takeoff_grace_s = min(10.0, max(guard_interval_s, timeout_s * 0.1))
            self._climb_until_altitude(target, ctx, timeout_s, guard_interval_s, takeoff_grace_s=takeoff_grace_s)
        elif step.name == "land" and ctx.options.require_confirmation:
            self._wait_until(lambda: self._land_is_confirmed(ctx.state), ctx, timeout_s, guard_interval_s)
        elif step.name == "lock" and ctx.options.require_confirmation:
            self._wait_until(lambda: ctx.state.armed is False, ctx, timeout_s, guard_interval_s)
        elif step.name == "unlock" and ctx.options.require_confirmation:
            self._wait_until(lambda: ctx.state.armed is True, ctx, timeout_s, guard_interval_s)
        elif step.name == "return_to_launch" and ctx.options.require_confirmation and ctx.state.in_air is True:
            self._wait_until(
                lambda: ctx.state.in_air is False or ctx.state.armed is False,
                ctx,
                timeout_s,
                guard_interval_s,
            )

    @staticmethod
    def _land_is_confirmed(state: Any) -> bool:
        if state is None:
            return False
        if state.in_air is False or state.armed is False:
            return True
        landed_state = str(state.landed_state or "").strip().lower()
        return landed_state in {"on_ground", "landed", "landed_state_on_ground"}

    def _wait_until_ready(self, step: _TaskStep, ctx: TaskContext, guard_interval_s: float) -> StandardResponse:
        capability = step.params.get("capability", CapabilityName.TAKEOFF)
        if not isinstance(capability, CapabilityName):
            capability = CapabilityName(capability)
        acquire = getattr(self._device, "_acquire_capability_telemetry", None)
        release = acquire(capability) if callable(acquire) else lambda: None
        try:
            return self._wait_until_ready_impl(step, ctx, guard_interval_s)
        finally:
            release()

    def _wait_until_ready_impl(self, step: _TaskStep, ctx: TaskContext, guard_interval_s: float) -> StandardResponse:
        timeout_s = float(step.params["timeout_s"])
        require_global_position = bool(step.params["require_global_position"])
        require_armable = bool(step.params.get("require_armable", False))
        capability = step.params.get("capability", CapabilityName.TAKEOFF)
        if not isinstance(capability, CapabilityName):
            capability = CapabilityName(capability)
        progress_callback = step.params.get("progress_callback")
        extra_requirements = tuple(
            requirement
            for requirement, enabled in (
                (ReadinessRequirement.GLOBAL_POSITION, require_global_position),
                (ReadinessRequirement.ARMABLE, require_armable),
            )
            if enabled
        )
        deadline_s = time.monotonic() + timeout_s
        last_report = None
        while time.monotonic() < deadline_s:
            self._raise_if_guard_triggered(ctx)
            last_report = self._device.readiness_for(
                capability,
                extra_requirements=extra_requirements,
            )
            if callable(progress_callback):
                progress_callback(last_report)
            if last_report.ready:
                logger.debug(
                    "设备能力已就绪: device_id=%s capability=%s",
                    getattr(self._device, "instance_id", None),
                    capability.value,
                )
                return self._device.get_status()
            time.sleep(guard_interval_s)
        missing = ", ".join(
            issue.requirement.value for issue in (last_report.issues if last_report is not None else ())
        )
        raise TimeoutError(
            f"Capability '{capability.value}' did not become ready before timeout; missing: {missing or 'unknown'}."
        )

    def _climb_until_altitude(
        self,
        target_altitude_m: float,
        ctx: TaskContext,
        timeout_s: float,
        guard_interval_s: float,
        climb_speed: Optional[float] = None,
        takeoff_grace_s: float = 0.0,
    ) -> None:
        climb_speed_m_s = float(climb_speed if climb_speed is not None else ctx.options.climb_speed_m_s)
        started_at = time.monotonic()
        deadline = time.monotonic() + timeout_s
        last_compensation_at = 0.0
        wait_interval_s = min(0.2, max(0.001, guard_interval_s))
        compensation_interval_s = min(0.2, max(0.05, guard_interval_s))
        while time.monotonic() < deadline:
            self._raise_if_guard_triggered(ctx)
            altitude = ctx.state.relative_altitude_m
            if altitude is None:
                logger.debug("等待高度遥测: target_altitude_m=%.2f", target_altitude_m)
                time.sleep(wait_interval_s)
                continue
            if altitude is not None and altitude >= target_altitude_m:
                logger.debug("高度目标已达成: current_altitude_m=%.2f target_altitude_m=%.2f", altitude, target_altitude_m)
                return

            now = time.monotonic()
            if now - started_at < takeoff_grace_s and ctx.state.in_air is not True:
                logger.debug(
                    "等待原生起飞状态: current_altitude_m=%.2f target_altitude_m=%.2f in_air=%s",
                    altitude,
                    target_altitude_m,
                    ctx.state.in_air,
                )
                time.sleep(wait_interval_s)
                continue
            if now - last_compensation_at >= compensation_interval_s:
                logger.debug(
                    "发送高度补偿速度: current_altitude_m=%.2f target_altitude_m=%.2f climb_speed_m_s=%.2f",
                    altitude,
                    target_altitude_m,
                    climb_speed_m_s,
                )
                response = self._device.set_velocity(z=1.0, speed=climb_speed_m_s)
                last_compensation_at = now
                if not response.ok:
                    logger.debug("高度补偿命令未成功: message=%s", response.message)
            time.sleep(wait_interval_s)
        raise TimeoutError(f"Altitude did not reach {target_altitude_m:.1f} m before timeout.")

    def _wait_until(
        self,
        predicate: Callable[[], bool],
        ctx: TaskContext,
        timeout_s: float,
        guard_interval_s: float,
    ) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._raise_if_guard_triggered(ctx)
            if predicate():
                return
            time.sleep(guard_interval_s)
        raise TimeoutError(f"Step '{ctx.current_step}' did not finish before timeout.")

    def _check_guards(self, ctx: TaskContext) -> Optional[TaskResult]:
        for guard in self._active_guards:
            if guard.condition(ctx):
                self._run_guard_action(guard, ctx)
                return TaskResult(
                    False,
                    TaskStatus.GUARD_TRIGGERED,
                    guard.reason,
                    tuple(ctx.step_results),
                    failed_step=ctx.current_step,
                    triggered_guard=guard.reason,
                    completion_reason=TaskCompletionReason.GUARD_TRIGGERED,
                )
        return None

    def _raise_if_guard_triggered(self, ctx: TaskContext) -> None:
        if ctx.cancellation_token is not None:
            ctx.cancellation_token.raise_if_cancelled()
        guard_result = self._check_guards(ctx)
        if guard_result is not None:
            raise _GuardTriggered(guard_result)

    def _safe_stop(self) -> None:
        """为 TaskSession 提供跨设备标准安全停止，不包含返航或降落等业务动作。"""
        adapter = getattr(self._device, "adapter", None)
        cancel_route = getattr(adapter, "cancel_route", None)
        if callable(cancel_route):
            cancel_route()
            return
        stop = getattr(self._device, "stop", None)
        if callable(stop):
            stop()
            return
        try:
            targets = tuple(self._device)
        except TypeError:
            targets = ()
        for target in targets:
            target_stop = getattr(target, "stop", None)
            if callable(target_stop):
                target_stop()

    def _run_guard_action(self, guard: _TaskGuard, ctx: TaskContext) -> None:
        if callable(guard.then):
            guard.then(ctx)
        elif guard.then is None and hasattr(self._device, "stop"):
            self._device.stop()
        elif guard.then == TaskGuardAction.STOP and hasattr(self._device, "stop"):
            self._device.stop()
        elif guard.then == TaskGuardAction.LAND and hasattr(self._device, "land"):
            self._device.land()
        elif guard.then == TaskGuardAction.RETURN_TO_LAUNCH and hasattr(self._device, "return_to_launch"):
            self._device.return_to_launch()


