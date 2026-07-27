"""任务状态、选项和执行结果模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from threading import Event
from typing import TYPE_CHECKING, Any, List, Mapping, Optional

if TYPE_CHECKING:
    from fleetdock.models import StandardResponse

class TaskStatus(str, Enum):
    """计划或任务执行结果状态。"""

    # 所有步骤执行成功。
    SUCCESS = "success"
    # 某一步执行失败。
    FAILED = "failed"
    # 运行期守卫条件触发并中断任务。
    GUARD_TRIGGERED = "guard_triggered"
    # 任务在限定时间内未完成。
    TIMEOUT = "timeout"
    # 调用方通过 TaskSession 请求取消，任务已停止后续步骤。
    CANCELLED = "cancelled"


class TaskCompletionReason(str, Enum):
    """任务结束的结构化原因；业务代码应使用本枚举而不是解析 ``message`` 文本。"""

    COMPLETED = "completed"  # 所有声明步骤均成功完成。
    STEP_FAILED = "step_failed"  # 普通任务步骤返回失败或抛出异常。
    AVOIDANCE_FALLBACK = "avoidance_fallback"  # 路线被避障策略阻断并执行了配置的安全动作。
    GUARD_TRIGGERED = "guard_triggered"  # 运行期守卫触发并中断后续步骤。
    TIMEOUT = "timeout"  # 某一步骤或整条航线超过允许时间。
    CANCELLED = "cancelled"  # 调用方请求取消任务。


class TaskSessionState(str, Enum):
    """异步任务会话生命周期状态。"""

    CREATED = "created"  # 会话已创建，后台任务尚未开始。
    RUNNING = "running"  # 任务正在后台执行。
    CANCELLING = "cancelling"  # 已收到取消请求，正在协作停止。
    CANCELLED = "cancelled"  # 任务已按取消请求结束。
    SUCCEEDED = "succeeded"  # 任务全部步骤成功完成。
    FAILED = "failed"  # 任务失败或异常结束。


class TaskCancelMode(str, Enum):
    """任务取消时对目标设备采取的通用安全动作。"""

    SAFE_STOP = "safe_stop"  # 请求当前执行器停止，并向设备发送标准 stop；无人机保持悬停，足式设备停止移动。
    NONE = "none"  # 只停止后续任务步骤，不额外发送设备运动命令。


class TaskCancelledError(RuntimeError):
    """任务收到协作取消信号时抛出的内部兼容异常；调用方通常通过 TaskResult 查看取消结果。"""


class TaskCancellationToken:
    """线程安全的任务取消信号。

    任务执行器、等待循环和适配器回调共享同一实例。调用 ``cancel()`` 后状态不可恢复；
    ``raise_if_cancelled()`` 会抛出 ``TaskCancelledError``，用于立即退出当前可中断点。
    """

    def __init__(self) -> None:
        self._event = Event()

    @property
    def cancelled(self) -> bool:
        """返回是否已经收到取消请求。"""
        return self._event.is_set()

    def cancel(self) -> None:
        """发出取消请求；可重复调用且不阻塞等待任务结束，该方法无返回值。"""
        self._event.set()

    def raise_if_cancelled(self) -> None:
        """若已取消则抛出 ``TaskCancelledError``，否则直接返回。"""
        if self.cancelled:
            raise TaskCancelledError("Task cancelled by caller.")


class TaskStepHookPhase(str, Enum):
    """任务步骤 Hook 的触发阶段。"""

    # 步骤命令发送前触发，可通过返回决策替换入参、跳过步骤或中断任务。
    BEFORE_STEP = "before_step"
    # 步骤执行完成并生成结果后触发，可通过返回决策替换步骤结果或中断任务。
    AFTER_STEP = "after_step"
    # 步骤执行抛出异常时触发，用于记录异常上下文或转换失败消息。
    ON_STEP_ERROR = "on_step_error"


class TaskStepHookAction(str, Enum):
    """任务步骤 Hook 返回决策的动作类型。"""

    # 不改变任务流程，继续执行。
    CONTINUE = "continue"
    # 替换当前步骤入参，仅在 BEFORE_STEP 阶段生效。
    REPLACE_PARAMS = "replace_params"
    # 替换当前步骤结果，仅在 AFTER_STEP 阶段生效。
    REPLACE_RESULT = "replace_result"
    # 跳过当前步骤并记录为成功。
    SKIP = "skip"
    # 将当前步骤标记为失败并停止任务。
    FAIL = "fail"


class TaskGuardAction(str, Enum):
    """守卫条件触发后的内置安全动作。"""

    # 停止当前运动或任务。
    STOP = "stop"
    # 执行降落，通常用于无人机。
    LAND = "land"
    # 执行返航。
    RETURN_TO_LAUNCH = "return_to_launch"


class HeadingMode(str, Enum):
    """航线执行时的朝向控制模式。"""

    # 不主动控制朝向，保持设备或飞控默认行为。
    NONE = "none"
    # 朝向沿航线切线方向。
    TANGENT = "tangent"
    # 朝向航线中心点，适合环绕观察。
    CENTER = "center"
    # 使用固定 yaw 角。
    FIXED = "fixed"


class MotionExecutionMode(str, Enum):
    """航线运动执行策略，决定优先使用飞控原生能力还是 SDK 侧控制。"""

    # 自动选择，优先飞控原生能力，不可用时回退到 SDK 控制。
    AUTO = "auto"
    # 强制使用设备适配器的原生路线系统，适用于无人机 Mission 或足式设备导航任务。
    NATIVE = "native"
    # 强制使用飞控原生航线/任务能力。
    FLIGHT_CONTROLLER = "flight_controller"
    # 强制使用 FleetDock SDK 侧运动控制。
    SDK = "sdk"


@dataclass(frozen=True)
class RouteMotion:
    """单条航线的执行策略和 SDK 平滑参数。

    ``mode`` 决定优先使用飞控/厂商原生航线还是 FleetDock SDK 侧速度控制；
    ``acceleration_m_s2`` 和 ``deceleration_m_s2`` 是水平加减速度，单位为米/秒平方；
    ``lookahead_time_s`` 与 ``min_lookahead_m`` 控制连续航线跟踪的前视距离；
    ``settle_time_s`` 和 ``settle_velocity_m_s`` 控制航点到达后的稳定判定。该对象只影响传入的
    单个 ``route(...)`` 步骤，不会改变同一计划中的其它航线。
    """

    mode: MotionExecutionMode | str = MotionExecutionMode.AUTO
    acceleration_m_s2: float = 1.0
    deceleration_m_s2: float = 1.2
    lookahead_time_s: float = 1.0
    min_lookahead_m: float = 1.0
    settle_time_s: float = 0.5
    settle_velocity_m_s: float = 0.25

    def __post_init__(self) -> None:
        motion_mode = self.mode if isinstance(self.mode, MotionExecutionMode) else MotionExecutionMode(str(self.mode))
        if self.acceleration_m_s2 <= 0 or self.deceleration_m_s2 <= 0:
            raise ValueError("acceleration_m_s2 and deceleration_m_s2 must be greater than 0.")
        if self.lookahead_time_s <= 0 or self.min_lookahead_m <= 0:
            raise ValueError("lookahead_time_s and min_lookahead_m must be greater than 0.")
        if self.settle_time_s < 0 or self.settle_velocity_m_s < 0:
            raise ValueError("settle_time_s and settle_velocity_m_s cannot be negative.")
        object.__setattr__(self, "mode", motion_mode)


class RouteContinuity(str, Enum):
    """航线连续执行语义，用于告诉 SDK 中间航点是否允许停顿。

    该枚举影响 `MotionExecutionMode.AUTO` 和 `SDK` 航线执行：单位、坐标系仍由 `Route`
    和 `Waypoint` 决定；返回值体现在航线执行事件和 `TaskResult` 中。
    """

    # 允许在每个航点停止、稳定和执行到点动作，适合检查点、拍照点或需要人工确认的路线。
    STOP_ALLOWED = "stop_allowed"
    # 优先平滑飞越普通中间航点；设备或适配器不支持时可以回退到原生航线或点到点执行。
    PREFERRED = "preferred"
    # 必须使用 SDK 连续平滑跟踪普通中间航点；不满足执行条件时失败，不静默降级。
    REQUIRED = "required"


class UnlockStrategy(str, Enum):
    """空中设备计划步骤的解锁策略。"""

    STANDARD = "standard"  # 只执行标准解锁，失败时停止计划。
    FORCE = "force"  # 明确请求适配器强制解锁，不先尝试标准解锁。
    STANDARD_THEN_FORCE = "standard_then_force"  # 标准解锁失败后再显式请求强制解锁。


class WaypointAction(str, Enum):
    """航点到达或稳定后要触发的动作。"""

    # 不执行额外动作。
    NONE = "none"
    # 在航点悬停或保持。
    HOVER = "hover"
    # 在航点触发拍照。
    PHOTO = "photo"
    # 调用方自定义动作。
    CUSTOM = "custom"


class RoutePattern(str, Enum):
    """内置航线图形/生成模式。"""

    # 按给定航点顺序生成折线航线。
    POLYLINE = "polyline"
    # 多边形边界航线。
    POLYGON = "polygon"
    # 圆形航线。
    CIRCLE = "circle"
    # 椭圆航线。
    ELLIPSE = "ellipse"
    # 圆弧航线。
    ARC = "arc"
    # 矩形网格覆盖航线。
    GRID = "grid"
    # 多边形区域覆盖航线。
    SURVEY_AREA = "survey_area"
    # 螺旋搜索航线。
    SPIRAL = "spiral"
    # 沿中心线生成走廊覆盖航线。
    CORRIDOR = "corridor"


@dataclass(frozen=True)
class TaskStepResult:
    """单个任务步骤的执行结果。

    `name` 是步骤名，`ok` 表示该步骤是否成功，`response` 保存底层标准命令响应；
    `started_at_s` 和 `finished_at_s` 使用 `time.monotonic()` 的秒值，仅用于计算耗时。
    """

    name: str
    ok: bool
    message: str = ""
    response: Optional[StandardResponse] = None
    started_at_s: float = 0.0
    finished_at_s: float = 0.0


@dataclass(frozen=True)
class TaskStepHookEvent:
    """任务步骤 Hook 收到的运行期事件。

    `phase` 表示触发阶段；`step_index` 是从 1 开始的真实任务步骤序号，不包含参数配置和守卫注册；
    `step_name` 与 `params` 描述即将执行或已经执行的步骤；`scope` 为 `device` 或 `fleet`，
    `device_id` 在单设备或 Fleet 成员步骤中表示业务设备 ID。`result` 仅在步骤已有结果时提供，
    `exception` 仅在步骤抛出异常时提供。Hook 不应直接修改 `params`，如需调整流程应返回
    `TaskStepHookDecision`。
    """

    phase: TaskStepHookPhase
    step_index: int
    step_name: str
    params: Mapping[str, Any]
    device: Any
    scope: str = "device"
    device_id: Optional[str] = None
    result: Optional[TaskStepResult] = None
    exception: Optional[BaseException] = None
    message: str = ""


@dataclass(frozen=True)
class TaskStepHookDecision:
    """任务步骤 Hook 对当前步骤给出的显式处理决策。

    Hook 返回 `None` 或 `continue_()` 表示只监听、不干预。`replace_params(...)` 只能用于
    `BEFORE_STEP` 阶段；`replace_result(...)` 只能用于 `AFTER_STEP` 阶段；`skip(...)` 会把
    当前步骤记录为成功但不调用设备方法；`fail(...)` 会把当前步骤记录为失败并停止任务。
    """

    action: TaskStepHookAction
    params: Optional[Mapping[str, Any]] = None
    result: Optional[TaskStepResult] = None
    message: str = ""

    @staticmethod
    def continue_() -> "TaskStepHookDecision":
        """声明 Hook 不改变当前步骤，返回继续执行的决策对象。"""
        return TaskStepHookDecision(TaskStepHookAction.CONTINUE)

    @staticmethod
    def replace_params(params: Mapping[str, Any]) -> "TaskStepHookDecision":
        """在步骤执行前替换当前步骤入参。

        `params` 是新的步骤参数映射，键名应与对应任务方法的参数名一致；返回值为可交给 Hook
        框架处理的决策对象，新入参会传给设备方法或航线执行器。
        """
        return TaskStepHookDecision(TaskStepHookAction.REPLACE_PARAMS, params=dict(params))

    @staticmethod
    def replace_result(result: TaskStepResult) -> "TaskStepHookDecision":
        """在步骤执行后替换当前步骤结果。

        `result` 是新的步骤结果对象，通常用于补充业务消息或封装特殊适配器结果；返回值为可交给
        Hook 框架处理的决策对象。
        """
        return TaskStepHookDecision(TaskStepHookAction.REPLACE_RESULT, result=result)

    @staticmethod
    def skip(message: str = "Step skipped by interceptor.") -> "TaskStepHookDecision":
        """跳过当前步骤并按成功记录。

        `message` 会写入 `TaskStepResult.message` 供任务结果汇总展示；返回值为可交给 Hook 框架处理
        的决策对象。
        """
        return TaskStepHookDecision(TaskStepHookAction.SKIP, message=message)

    @staticmethod
    def fail(message: str = "Step failed by interceptor.") -> "TaskStepHookDecision":
        """中断当前步骤并按失败记录。

        `message` 会写入失败步骤和任务失败结果；返回值为可交给 Hook 框架处理的决策对象。
        """
        return TaskStepHookDecision(TaskStepHookAction.FAIL, message=message)


@dataclass(frozen=True)
class TaskResult:
    """任务编排整体执行结果。

    `status` 表示成功、失败、超时或守卫触发；`steps` 按执行顺序保存每个步骤结果；
    `failed_step` 和 `triggered_guard` 分别标记失败步骤和触发的守卫原因；``completion_reason`` 是供
    业务判断的结构化结束原因，``safety_action`` 保存避障触发的安全动作名；``cleanup_result`` 是
    ``ensure_return_to_launch()`` 等必执行清理计划的独立结果，避免把主任务失败与安全返航成功混为一谈。
    """

    ok: bool
    status: TaskStatus
    message: str
    steps: tuple[TaskStepResult, ...] = ()
    failed_step: Optional[str] = None
    triggered_guard: Optional[str] = None
    completion_reason: Optional[TaskCompletionReason] = None
    safety_action: Optional[str] = None
    cleanup_result: Optional["TaskResult"] = None


@dataclass(frozen=True)
class FleetTaskResult(TaskResult):
    """Fleet 批量任务执行结果。

    顶层字段与 `TaskResult` 保持相同语义；`device_results` 按业务设备 ID 或 SDK 实例 ID
    保存每台设备的单设备任务结果，`succeeded_devices` 和 `failed_devices` 便于调用方快速
    判断哪些成员完成或失败。
    """

    device_results: Mapping[str, TaskResult] = field(default_factory=dict)
    succeeded_devices: tuple[str, ...] = ()
    failed_devices: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParallelTaskResult:
    """并行计划执行结果。

    `plan_results` 按传入 `Plan.parallel(...)` 的顺序保存每个子计划结果；任一子计划失败时
    顶层 `ok` 为 False，默认仍等待其它子计划结束后汇总。
    """

    ok: bool
    status: TaskStatus
    message: str
    plan_results: tuple[TaskResult | FleetTaskResult | "ParallelTaskResult", ...]


@dataclass
class TaskContext:
    """任务运行期上下文。

    该对象会传给守卫条件和失败处理器；``device`` 是正在执行任务的设备或 Fleet，``state`` 属性
    在每次访问时从 ``state_source.snapshot()`` 取得最新不可变快照，避免等待和控制循环读取过期状态；
    Fleet 上下文没有单设备状态时返回 ``None``。``options`` 是当前任务参数，``step_results`` 会随执行推进更新。
    """

    device: Any
    options: "TaskOptions"
    state_source: Any = None
    current_step: Optional[str] = None
    step_results: List[TaskStepResult] = field(default_factory=list)
    cancellation_token: Optional[TaskCancellationToken] = None

    @property
    def state(self) -> Any:
        """返回最新设备状态快照；Fleet 上下文没有单设备状态时返回 ``None``。"""
        if self.state_source is None:
            return None
        snapshot = getattr(self.state_source, "snapshot", None)
        return snapshot() if callable(snapshot) else self.state_source


@dataclass
class TaskOptions:
    """任务编排默认参数。

    速度单位为米/秒，加速度单位为米/秒平方，时间单位为秒，距离和高度单位为米。
    这些参数会被链式配置方法覆盖，并影响后续加入队列的任务步骤。
    """

    motion_mode: MotionExecutionMode = MotionExecutionMode.AUTO
    speed_m_s: float = 3.0
    climb_speed_m_s: float = 1.0
    acceleration_m_s2: float = 1.0
    deceleration_m_s2: float = 1.2
    lookahead_time_s: float = 1.0
    min_lookahead_m: float = 1.0
    settle_time_s: float = 0.5
    settle_velocity_m_s: float = 0.25
    altitude_m: Optional[float] = None
    altitude_tolerance_m: float = 0.5
    position_tolerance_m: float = 0.5
    retry_count: int = 0
    retry_backoff_s: float = 0.5
    step_timeout_s: float = 60.0
    heading: HeadingMode = HeadingMode.NONE
    require_confirmation: bool = True


