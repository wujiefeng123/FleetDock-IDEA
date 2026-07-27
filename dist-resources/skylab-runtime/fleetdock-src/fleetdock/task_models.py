"""任务编排结果、状态和选项模型入口。

本模块暴露与具体设备无关的任务数据对象，方便开发者区分“任务模型”
和“任务执行器”。具体实现位于 ``fleetdock.tasking.models``。
"""

from fleetdock.tasking.models import (
    HeadingMode,
    MotionExecutionMode,
    ParallelTaskResult,
    RouteContinuity,
    RouteMotion,
    TaskContext,
    TaskCompletionReason,
    TaskCancelMode,
    TaskCancellationToken,
    TaskGuardAction,
    TaskOptions,
    TaskResult,
    TaskStatus,
    TaskSessionState,
    TaskStepHookAction,
    TaskStepHookDecision,
    TaskStepHookEvent,
    TaskStepHookPhase,
    TaskStepResult,
    UnlockStrategy,
)

__all__ = [
    "HeadingMode",
    "MotionExecutionMode",
    "ParallelTaskResult",
    "RouteContinuity",
    "RouteMotion",
    "TaskContext",
    "TaskCompletionReason",
    "TaskCancelMode",
    "TaskCancellationToken",
    "TaskGuardAction",
    "TaskOptions",
    "TaskResult",
    "TaskStatus",
    "TaskSessionState",
    "TaskStepHookAction",
    "TaskStepHookDecision",
    "TaskStepHookEvent",
    "TaskStepHookPhase",
    "TaskStepResult",
    "UnlockStrategy",
]
