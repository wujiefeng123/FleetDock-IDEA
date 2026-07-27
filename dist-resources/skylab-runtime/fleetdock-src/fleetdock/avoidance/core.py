"""FleetDock SDK 侧避障与局部绕行能力。"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from threading import RLock
from typing import Any, Callable, Iterable, Mapping, Protocol

from fleetdock.events import CoordinateFrame, EventCategory, EventType, Vector3
from fleetdock.models import DeviceCategory, StandardResponse
from fleetdock.obstacles import Obstacle, ObstacleKind, ObstacleProvider, ObstacleShape, ObstacleSnapshot

logger = logging.getLogger(__name__)


class _AvoidanceAction(str, Enum):
    """避障决策动作；用于内部评估结果、事件载荷和设备动作映射。"""

    # 风险可接受，继续发送原运动命令。
    ALLOW = "allow"
    # 阻断当前运动命令但不额外发送设备动作。
    BLOCK = "block"
    # 请求空中设备悬停或保持当前位置。
    HOVER = "hover"
    # 请求设备停止当前运动，适合地面设备和不支持悬停的设备。
    STOP = "stop"
    # 请求空中设备返航到 Home 点。
    RETURN_TO_LAUNCH = "return_to_launch"
    # 请求设备急停；仅用于最高风险或调用方明确配置的场景。
    EMERGENCY_STOP = "emergency_stop"
    # 请求 SDK 路线执行器插入临时绕行点并继续执行原路线。
    REPLAN = "replan"


class AvoidanceDimensions(str, Enum):
    """避障几何维度；无人机通常使用 3D，机械狗和地面设备通常使用 2D。"""

    # 由 SDK 按设备类别自动选择；无人机使用 3D，机械狗、人形机器人和其他地面设备使用 2D。
    AUTO = "auto"
    # 只在水平 x/y 平面判断风险，忽略高度差。
    TWO_D = "2d"
    # 同时考虑水平距离和 z 轴高度差。
    THREE_D = "3d"


class AvoidanceStrategy(str, Enum):
    """避障策略；开发者只需选择风险命中后的业务意图。"""

    # 阻断当前路线但不主动发送设备动作，适合上层系统自行接管。
    BLOCK = "block"
    # 保持当前位置；空中设备通常映射为 hover，地面设备会降级为 stop。
    HOLD_POSITION = "hold_position"
    # 停止当前运动，适合机械狗、车辆等地面设备。
    STOP = "stop"
    # 请求设备返航到 Home 点；设备不支持返航时会降级为停止。
    RETURN_TO_LAUNCH = "return_to_launch"
    # 请求设备急停，仅用于最高风险或调用方明确配置的场景。
    EMERGENCY_STOP = "emergency_stop"
    # 先由 SDK route 执行器插入临时绕行点，绕行不可用时执行 fallback_strategy。
    LOCAL_REPLAN = "local_replan"


class AvoidancePlanner(str, Enum):
    """局部绕行规划器后端；用于声明 ``LOCAL_REPLAN`` 采用的算法或外部规划服务。"""

    # 纯 Python 轨迹采样规划器；参考 DWA/DWB 思路生成候选轨迹、碰撞验证并按代价选择绕行点。
    TRAJECTORY_ROLLOUT = "trajectory_rollout"
    # ROS2/Nav2 后端预留，适合机械狗、轮式机器人等二维地面导航场景。
    ROS2_NAV2 = "ros2.nav2"
    # ROS2 无人机外部 planner 后端预留，适合接入 EGO/Fast-Planner 或自研 3D planner。
    ROS2_EXTERNAL = "ros2.external"
    # 通用外部进程后端预留，适合通过 HTTP、gRPC、WebSocket 或本地桥接进程调用商业算法。
    EXTERNAL_PROCESS = "external.process"


@dataclass(frozen=True)
class AvoidancePolicy:
    """设备避障策略。

    ``dimensions`` 使用枚举指定自动、二维或三维几何；默认 ``AUTO`` 会让无人机使用 3D，让机械狗、
    人形机器人和其他地面设备使用 2D；``strategy`` 使用枚举声明风险命中后的业务策略，
    例如保持位置、返航、急停或局部绕行；
    ``planner`` 指定局部绕行采用的规划器后端，默认的 ``TRAJECTORY_ROLLOUT`` 是纯 Python 轨迹采样
    实现；ROS2 或外部进程后端需要通过管理器注册后才会被调用。``fallback_strategy``
    仅在 ``LOCAL_REPLAN`` 无法生成绕行点、后端不可用或超过重规划次数时生效。``lookahead_s``
    是预测窗口秒数；``min_clearance_m`` 是障碍物外缘到设备外缘的最小安全距离，单位为米；
    ``device_radius_m`` 是设备保守包围半径，单位为米。``replan_lateral_clearance_m`` 和
    ``replan_vertical_clearance_m`` 控制候选绕行轨迹相对原路线的水平、垂直偏移，单位为米；为空时由
    SDK 根据设备安全半径和障碍物尺寸自动估算。``replan_retry_timeout_s`` 是规划器暂时无安全候选时，
    route 悬停并等待新障碍快照的最长秒数；``replan_retry_interval_s`` 是重试间隔秒数。重试窗口耗尽或
    重规划次数达到上限后才执行 ``fallback_strategy``。新代码应优先传入 ``AvoidanceStrategy``；
    动态启停使用 ``device.avoidance.enable()`` 和 ``device.avoidance.disable()``；单次 route 显式关闭
    使用 ``AvoidancePolicy.disabled()``。
    """

    dimensions: AvoidanceDimensions = AvoidanceDimensions.AUTO
    strategy: AvoidanceStrategy = AvoidanceStrategy.HOLD_POSITION
    planner: AvoidancePlanner = AvoidancePlanner.TRAJECTORY_ROLLOUT
    fallback_strategy: AvoidanceStrategy = AvoidanceStrategy.HOLD_POSITION
    lookahead_s: float = 3.0
    min_clearance_m: float = 2.0
    device_radius_m: float = 0.5
    stale_obstacle_timeout_s: float = 1.0
    replan_lateral_clearance_m: float | None = None
    replan_vertical_clearance_m: float | None = None
    max_replans_per_route: int = 8
    replan_retry_timeout_s: float = 3.0
    replan_retry_interval_s: float = 0.5
    _disabled: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.dimensions, AvoidanceDimensions):
            raise TypeError("dimensions must be an AvoidanceDimensions value.")
        if not isinstance(self.strategy, AvoidanceStrategy):
            raise TypeError("strategy must be an AvoidanceStrategy value.")
        if not isinstance(self.planner, AvoidancePlanner):
            raise TypeError("planner must be an AvoidancePlanner value.")
        if not isinstance(self.fallback_strategy, AvoidanceStrategy):
            raise TypeError("fallback_strategy must be an AvoidanceStrategy value.")
        if self.fallback_strategy is AvoidanceStrategy.LOCAL_REPLAN:
            raise ValueError("fallback_strategy cannot be LOCAL_REPLAN.")
        if self.lookahead_s <= 0:
            raise ValueError("lookahead_s must be greater than 0.")
        if self.min_clearance_m < 0 or self.device_radius_m < 0:
            raise ValueError("min_clearance_m and device_radius_m cannot be negative.")
        if self.replan_lateral_clearance_m is not None and self.replan_lateral_clearance_m < 0:
            raise ValueError("replan_lateral_clearance_m cannot be negative.")
        if self.replan_vertical_clearance_m is not None and self.replan_vertical_clearance_m < 0:
            raise ValueError("replan_vertical_clearance_m cannot be negative.")
        if self.stale_obstacle_timeout_s < 0:
            raise ValueError("stale_obstacle_timeout_s cannot be negative.")
        if self.max_replans_per_route < 0:
            raise ValueError("max_replans_per_route cannot be negative.")
        if self.replan_retry_timeout_s < 0:
            raise ValueError("replan_retry_timeout_s cannot be negative.")
        if self.replan_retry_interval_s <= 0:
            raise ValueError("replan_retry_interval_s must be greater than 0.")

    @property
    def disabled_for_route(self) -> bool:
        """返回该策略是否表示单次 route 显式关闭 SDK 避障。"""
        return self._disabled

    @classmethod
    def disabled(cls) -> "AvoidancePolicy":
        """创建显式关闭避障的策略占位。

        该对象主要用于 ``route(..., avoidance=AvoidancePolicy.disabled())`` 这种单次路线覆盖场景。
        设备级运行期开关应使用 ``device.avoidance.disable()`` 和 ``device.avoidance.enable()``。返回的
        策略对象不会参与风险评估，传给单次 route 时表示本次路线跳过 SDK 避障。
        """
        return cls(_disabled=True)

    @property
    def requests_local_replan(self) -> bool:
        """返回策略是否要求 SDK route 执行器先尝试局部绕行。"""
        return self.strategy is AvoidanceStrategy.LOCAL_REPLAN

    @property
    def fallback_action(self) -> "_AvoidanceAction":
        """返回风险无法通过局部绕行解除时应执行的内部保护动作。"""
        strategy = self.fallback_strategy if self.requests_local_replan else self.strategy
        return _action_from_strategy(strategy)

    @property
    def safety_radius_m(self) -> float:
        """返回设备半径加安全距离后的基础保护半径，单位为米。"""
        return self.device_radius_m + self.min_clearance_m


@dataclass(frozen=True)
class ObstacleCapacityPolicy:
    """障碍物聚合容量保护策略。

    ``max_obstacles`` 是一次聚合后允许进入避障计算的总障碍物上限；``max_obstacles_per_provider`` 是单个
    provider 每次快照可贡献的上限，避免单个雷达、视觉或外部地图源异常输出拖垮控制循环；``default_ttl_s`` 用于
    provider 未给单个障碍物 ``ttl_s`` 时的新鲜度兜底，单位为秒；``drop_stale_before_snapshot`` 为 True 时会在
    快照聚合阶段直接丢弃过期障碍物，而不是等到单设备风险评估阶段再过滤。该策略只限制标准 ``Obstacle`` 数量，
    原始点云、图像帧或厂商缓存仍应由具体 provider 自己限流和释放。
    """

    max_obstacles: int = 512
    max_obstacles_per_provider: int = 256
    default_ttl_s: float = 1.0
    drop_stale_before_snapshot: bool = True

    def __post_init__(self) -> None:
        if self.max_obstacles <= 0:
            raise ValueError("max_obstacles must be greater than 0.")
        if self.max_obstacles_per_provider <= 0:
            raise ValueError("max_obstacles_per_provider must be greater than 0.")
        if self.default_ttl_s < 0:
            raise ValueError("default_ttl_s cannot be negative.")


@dataclass(frozen=True)
class ObstacleAggregationStats:
    """障碍物聚合过程的诊断信息。

    ``provider_count`` 是本次参与聚合的 provider 数量；``raw_obstacle_count`` 是 provider 原始返回数量；
    ``deduplicated_count`` 是按 ``obstacle_id`` 去重后的数量；``dropped_stale_count``、``dropped_duplicate_count``、
    ``dropped_capacity_count`` 分别记录因为过期、重复和容量上限被丢弃的数量，便于上层日志、UI 或测试判断避障输入是否异常。
    """

    provider_count: int = 0
    raw_obstacle_count: int = 0
    deduplicated_count: int = 0
    dropped_stale_count: int = 0
    dropped_duplicate_count: int = 0
    dropped_capacity_count: int = 0


def _aggregate_obstacle_providers(
    providers: Iterable[ObstacleProvider],
    *,
    capacity: ObstacleCapacityPolicy,
    now_s: float,
    on_provider_error: Callable[[ObstacleProvider, Exception], None] | None = None,
) -> tuple[tuple[Obstacle, ...], set[CoordinateFrame], float | None, ObstacleAggregationStats]:
    provider_count = 0
    raw_count = 0
    dropped_stale = 0
    dropped_duplicate = 0
    dropped_capacity = 0
    frames: set[CoordinateFrame] = set()
    anonymous: list[Obstacle] = []
    by_id: dict[str, Obstacle] = {}
    last_observed_at_s: float | None = None
    for provider in tuple(providers):
        provider_count += 1
        try:
            snapshot = provider.snapshot()
        except Exception as exc:
            if on_provider_error is not None:
                on_provider_error(provider, exc)
            continue
        last_observed_at_s = snapshot.observed_at_s
        frames.add(snapshot.frame)
        provider_obstacles = tuple(snapshot.obstacles)
        raw_count += len(provider_obstacles)
        if len(provider_obstacles) > capacity.max_obstacles_per_provider:
            dropped_capacity += len(provider_obstacles) - capacity.max_obstacles_per_provider
            provider_obstacles = provider_obstacles[: capacity.max_obstacles_per_provider]
        for obstacle in provider_obstacles:
            frames.add(obstacle.frame)
            if capacity.drop_stale_before_snapshot and not obstacle.is_fresh(
                now_s,
                default_ttl_s=capacity.default_ttl_s,
            ):
                dropped_stale += 1
                continue
            if obstacle.obstacle_id is None:
                anonymous.append(obstacle)
                continue
            obstacle_id = str(obstacle.obstacle_id)
            previous = by_id.get(obstacle_id)
            if previous is None:
                by_id[obstacle_id] = obstacle
                continue
            dropped_duplicate += 1
            if _prefer_obstacle(obstacle, previous) is obstacle:
                by_id[obstacle_id] = obstacle

    obstacles = tuple(sorted((*by_id.values(), *anonymous), key=_obstacle_retention_key))
    if len(obstacles) > capacity.max_obstacles:
        dropped_capacity += len(obstacles) - capacity.max_obstacles
        obstacles = obstacles[: capacity.max_obstacles]
    stats = ObstacleAggregationStats(
        provider_count=provider_count,
        raw_obstacle_count=raw_count,
        deduplicated_count=len(by_id) + len(anonymous),
        dropped_stale_count=dropped_stale,
        dropped_duplicate_count=dropped_duplicate,
        dropped_capacity_count=dropped_capacity,
    )
    return obstacles, frames, last_observed_at_s, stats


def _prefer_obstacle(candidate: Obstacle, previous: Obstacle) -> Obstacle:
    candidate_confidence = -1.0 if candidate.confidence is None else float(candidate.confidence)
    previous_confidence = -1.0 if previous.confidence is None else float(previous.confidence)
    if candidate_confidence != previous_confidence:
        return candidate if candidate_confidence > previous_confidence else previous
    return candidate if candidate.observed_at_s >= previous.observed_at_s else previous


def _obstacle_retention_key(obstacle: Obstacle) -> tuple[int, float, float]:
    try:
        kind = obstacle.kind if isinstance(obstacle.kind, ObstacleKind) else ObstacleKind(str(obstacle.kind))
    except ValueError:
        kind = ObstacleKind.UNKNOWN
    dynamic_priority = 0 if kind is ObstacleKind.DYNAMIC else 1
    confidence_priority = -(float(obstacle.confidence) if obstacle.confidence is not None else 0.0)
    freshness_priority = -float(obstacle.observed_at_s)
    return (dynamic_priority, confidence_priority, freshness_priority)


@dataclass(frozen=True)
class CollisionRisk:
    """碰撞风险评估结果。

    ``has_risk`` 为 True 表示当前运动段侵入障碍物外形加设备安全半径后的保护区；``distance_m``
    是预测到的设备参考点到障碍物外形表面的最近距离，单位为米；``limit_m`` 是设备半径和最小安全
    距离合成后的风险阈值；``time_to_closest_s`` 是从
    当前时刻到最近点的秒数。``metadata`` 可携带路线、provider 或调试信息。
    """

    has_risk: bool
    obstacle: Obstacle | None = None
    distance_m: float | None = None
    limit_m: float | None = None
    time_to_closest_s: float | None = None
    reason: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AvoidanceDecision:
    """避障管理器对当前运动请求给出的决策。

    ``action`` 是调用方应执行的动作；``risk`` 保存触发动作的碰撞风险；``detour_points_m`` 在
    ``REPLAN`` 时给出一个或多个本地临时绕行点，坐标单位为米且坐标系见 ``frame``。
    """

    action: _AvoidanceAction
    risk: CollisionRisk | None = None
    message: str = ""
    detour_points_m: tuple[Vector3, ...] = ()
    frame: CoordinateFrame = CoordinateFrame.LOCAL_NED

    @property
    def allowed(self) -> bool:
        """返回是否允许继续原始运动命令。"""
        return self.action is _AvoidanceAction.ALLOW


@dataclass(frozen=True)
class AvoidanceStatus:
    """单设备避障状态快照。

    ``enabled`` 表示当前策略是否启用；``provider_count`` 是已接入的障碍物来源数量；
    ``last_snapshot_at_s`` 使用单调时钟秒值记录最近一次快照时间；``last_decision`` 保存最近一次
    风险评估结果，便于 UI 或日志系统展示避障状态。
    """

    enabled: bool
    provider_count: int
    last_snapshot_at_s: float | None = None
    last_decision: AvoidanceDecision | None = None
    last_obstacle_count: int = 0
    last_aggregation_stats: ObstacleAggregationStats | None = None


@dataclass(frozen=True)
class FleetAvoidanceStatus:
    """Fleet 级共享避障状态快照。

    ``enabled`` 表示共享世界模型是否启用；``provider_count`` 是注册在 ``fleet.avoidance`` 上的共享 provider 数量；
    ``device_count`` 是当前 Fleet 视图内的设备数量；``shared_obstacle_count`` 是最近一次共享世界模型输出的障碍物数量；
    ``capacity`` 是正在使用的容量保护策略；``aggregation_stats`` 记录最近一次聚合的裁剪、去重和过期过滤统计。
    """

    enabled: bool
    provider_count: int
    device_count: int
    shared_obstacle_count: int = 0
    last_snapshot_at_s: float | None = None
    capacity: ObstacleCapacityPolicy = field(default_factory=ObstacleCapacityPolicy)
    aggregation_stats: ObstacleAggregationStats | None = None


class PlannerStatus(str, Enum):
    """局部规划后端返回状态；用于区分可继续、已重规划、无路可绕和后端不可用。"""

    # 当前前视段可继续执行，后端没有要求插入绕行点。
    CLEAR = "clear"
    # 后端已生成一组本地临时绕行点，SDK route 执行器应插入这些点。
    REPLAN = "replan"
    # 后端完成计算但未找到满足安全余量的候选轨迹，应执行策略 fallback。
    NO_SAFE_PATH = "no_safe_path"
    # 后端未注册、未连接或当前设备/坐标系不适用，应执行策略 fallback。
    UNAVAILABLE = "unavailable"
    # 后端计算过程中出现异常，应记录诊断信息并执行策略 fallback。
    ERROR = "error"


@dataclass(frozen=True)
class AvoidancePlanRequest:
    """传给局部避障 planner 后端的标准规划请求。

    ``device_category`` 表示无人机、机械狗等设备类型；``frame`` 声明所有本地坐标的坐标系；
    ``start_m`` 和 ``target_m`` 是当前前视运动段起点与目标点，单位为米；``current_velocity_m_s``
    可为空，非空时表示设备当前速度向量，单位为米/秒；``speed_m_s`` 是 route 执行器计划使用的标量速度；
    ``obstacles`` 是已经归一到同一坐标系且未过期的障碍物；``risk`` 是触发本次规划的风险对象；
    ``policy`` 是本次路线实际使用的避障策略；``route_context`` 可携带路线名、航点索引等调试信息。
    """

    device_category: DeviceCategory | None
    frame: CoordinateFrame
    start_m: Vector3
    target_m: Vector3
    current_velocity_m_s: Vector3 | None
    speed_m_s: float
    obstacles: tuple[Obstacle, ...]
    risk: CollisionRisk
    policy: AvoidancePolicy
    route_context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AvoidancePlanResult:
    """局部避障 planner 后端的标准返回结果。

    ``status`` 声明规划结果；``detour_points_m`` 仅在 ``REPLAN`` 时包含本地临时绕行点，单位为米且坐标系
    与请求 ``frame`` 一致；``message`` 面向日志和事件诊断；``diagnostics`` 可放入后端名称、评分、耗时、
    被拒绝候选数等可序列化调试信息，不应包含密钥、令牌或完整敏感连接参数。
    """

    status: PlannerStatus
    detour_points_m: tuple[Vector3, ...] = ()
    message: str = ""
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


class AvoidancePlannerBackend(Protocol):
    """局部避障 planner 后端协议；内置、ROS2 或外部进程后端都实现该接口。"""

    def plan(self, request: AvoidancePlanRequest) -> AvoidancePlanResult:
        """根据标准请求计算局部绕行结果。

        ``request`` 包含同一坐标系下的当前点、前视目标点、障碍物、触发风险、设备类别和策略参数；
        坐标单位均为米，速度单位为米/秒。实现方不得直接控制设备，也不得阻塞过久；需要失败时返回
        ``NO_SAFE_PATH``、``UNAVAILABLE`` 或 ``ERROR``，由 ``AvoidanceManager`` 统一执行 fallback 策略
        和事件发布。
        """


class AvoidanceBlockedError(RuntimeError):
    """避障策略阻断当前路线时抛出的内部异常，任务层会把它转换为失败结果。"""

    def __init__(self, decision: AvoidanceDecision) -> None:
        super().__init__(decision.message or f"avoidance action triggered: {decision.action.value}")
        self.decision = decision


class AvoidanceManager:
    """挂载在 ``BaseDevice`` 上的避障管理器。

    管理器负责维护 provider、聚合障碍物、进行保守碰撞预测、调用局部 planner backend、发布标准安全事件，
    并把无人机、DJI 类空中设备和机械狗等不同设备的保护动作映射到公共设备方法。
    """

    def __init__(self, device: Any) -> None:
        self._device = device
        self._policy = AvoidancePolicy()
        self._enabled = False
        self._providers: list[ObstacleProvider] = []
        self._provider_close_on_close: dict[int, bool] = {}
        self._capacity = ObstacleCapacityPolicy()
        self._planner_backends: dict[AvoidancePlanner, AvoidancePlannerBackend] = {}
        self._last_snapshot_at_s: float | None = None
        self._last_decision: AvoidanceDecision | None = None
        self._last_obstacle_count = 0
        self._last_aggregation_stats: ObstacleAggregationStats | None = None
        self._install_default_planner_backends()

    def configure(self, policy: AvoidancePolicy | None) -> "AvoidanceManager":
        """配置设备避障策略并返回当前管理器。

        ``policy`` 为 ``None`` 时调用 ``disable()`` 暂停避障；传入 ``AvoidancePolicy`` 后会保存策略并
        立即启用运行期风险检查。该方法不立即控制设备，返回值是当前 ``AvoidanceManager``，便于链式
        添加 provider 或继续配置。
        """
        if policy is None:
            return self.disable()
        self._policy = policy
        self._enabled = not policy.disabled_for_route
        logger.debug(
            "避障策略已配置: device_id=%s enabled=%s strategy=%s planner=%s fallback_action=%s",
            getattr(self._device, "instance_id", None),
            self.enabled,
            self._policy.strategy.value,
            self._policy.planner.value,
            self._policy.fallback_action.value,
        )
        return self

    def enable(self, policy: AvoidancePolicy | None = None) -> "AvoidanceManager":
        """启用当前设备的避障检查并返回当前管理器。

        可选传入 ``policy`` 同时更新策略；未传时沿用最近一次 ``configure(...)`` 保存的策略。启用只影响
        后续风险评估和 route 执行，不会立即发送设备控制命令。
        """
        if policy is not None:
            self._policy = policy
        if self._policy.disabled_for_route:
            self._policy = AvoidancePolicy()
        self._enabled = True
        logger.debug(
            "避障已启用: device_id=%s strategy=%s",
            getattr(self._device, "instance_id", None),
            self._policy.strategy.value,
        )
        return self

    def disable(self) -> "AvoidanceManager":
        """暂停当前设备的避障检查并返回当前管理器。

        该方法保留已配置策略和 provider，后续可调用 ``enable()`` 快速恢复。暂停不会关闭 provider
        资源，也不会向设备发送停止、悬停或返航命令。
        """
        self._enabled = False
        logger.debug("避障已暂停: device_id=%s", getattr(self._device, "instance_id", None))
        return self

    @property
    def policy(self) -> AvoidancePolicy:
        """返回当前避障策略；调用方可读取几何、预测窗口、策略和局部绕行配置。"""
        return self._policy

    @property
    def enabled(self) -> bool:
        """返回当前设备避障是否处于运行期启用状态。"""
        return self._enabled

    def add_provider(self, provider: ObstacleProvider, *, close_on_close: bool = True) -> "AvoidanceManager":
        """添加障碍物 provider 并返回当前管理器。

        同一个 provider 可以被无人机和机械狗复用；provider 应保证 ``snapshot()`` 快速返回。重复添加同一
        provider 对象是幂等操作，不会造成重复障碍物、重复调用 ``snapshot()`` 或重复释放资源。``close_on_close``
        表示当前设备关闭时是否负责调用该 provider 的 ``close()``；Fleet 共享 provider 会以 False 挂载，避免
        单设备关闭误释放共享感知源。本方法不发现或订阅传感器；传感器驱动的 Provider 必须由调用方通过
        ``device.sensors.subscribe(..., provider.observe)`` 显式送入样本。
        """
        if not callable(getattr(provider, "snapshot", None)):
            raise TypeError("provider must provide a snapshot() method.")
        provider_key = id(provider)
        if provider_key in self._provider_close_on_close:
            self._provider_close_on_close[provider_key] = self._provider_close_on_close[provider_key] and close_on_close
            return self
        self._providers.append(provider)
        self._provider_close_on_close[provider_key] = close_on_close
        logger.debug(
            "避障 provider 已添加: device_id=%s provider=%s count=%d",
            getattr(self._device, "instance_id", None),
            provider.__class__.__name__,
            len(self._providers),
        )
        return self

    def remove_provider(self, provider: ObstacleProvider, *, close: bool = True) -> "AvoidanceManager":
        """移除已注册的障碍物 Provider，并按需释放其资源。

        ``provider`` 按对象身份匹配；未注册时视为幂等成功，不会调用 ``close()``。本方法不取消
        ``device.sensors`` 订阅，调用方必须先执行订阅返回的取消函数，避免关闭后继续送入样本。
        ``close=True`` 时会调用 Provider 的 ``close()``（若实现该方法），适合独占
        Provider；共享 Provider 可传 ``False``，由其原拥有者负责释放。返回当前管理器。
        """
        provider_key = id(provider)
        for index, registered in enumerate(self._providers):
            if registered is not provider:
                continue
            self._providers.pop(index)
            self._provider_close_on_close.pop(provider_key, None)
            if close:
                close_method = getattr(provider, "close", None)
                if callable(close_method):
                    close_method()
            logger.debug(
                "避障 provider 已移除: device_id=%s provider=%s count=%d",
                getattr(self._device, "instance_id", None),
                provider.__class__.__name__,
                len(self._providers),
            )
            break
        return self

    def configure_capacity(self, capacity: ObstacleCapacityPolicy | None = None) -> "AvoidanceManager":
        """配置单设备避障聚合容量保护并返回当前管理器。

        ``capacity`` 为 ``None`` 时恢复默认容量策略；传入 ``ObstacleCapacityPolicy`` 后，后续 ``snapshot()``
        会先按 provider 上限、TTL、新鲜度、``obstacle_id`` 去重和总量上限裁剪障碍物，再进入风险评估。该方法不修改
        provider 自身缓存，因此点云、图像帧等原始大对象仍应在 provider 内部自行限流。
        """
        self._capacity = capacity or ObstacleCapacityPolicy()
        logger.debug(
            "避障容量策略已配置: device_id=%s max_obstacles=%d max_per_provider=%d",
            getattr(self._device, "instance_id", None),
            self._capacity.max_obstacles,
            self._capacity.max_obstacles_per_provider,
        )
        return self

    def status(self) -> AvoidanceStatus:
        """返回避障启用状态、provider 数量和最近一次决策。"""
        return AvoidanceStatus(
            enabled=self.enabled,
            provider_count=len(self._providers),
            last_snapshot_at_s=self._last_snapshot_at_s,
            last_decision=self._last_decision,
            last_obstacle_count=self._last_obstacle_count,
            last_aggregation_stats=self._last_aggregation_stats,
        )

    def register_planner_backend(
        self,
        planner: AvoidancePlanner,
        backend: AvoidancePlannerBackend | None,
    ) -> "AvoidanceManager":
        """注册或移除局部避障 planner 后端并返回当前管理器。

        ``planner`` 是 ``AvoidancePolicy.planner`` 中选择的后端枚举；``backend`` 传入实现
        ``AvoidancePlannerBackend`` 协议的对象后，后续 ``LOCAL_REPLAN`` 会把标准规划请求交给它。
        传入 ``None`` 会移除该后端。该方法只修改 SDK 侧路由规划能力，不会立即连接 ROS2、启动外部进程
        或控制设备；外部资源生命周期由具体 backend 自己管理。
        """
        if not isinstance(planner, AvoidancePlanner):
            raise TypeError("planner must be an AvoidancePlanner value.")
        if backend is None:
            self._planner_backends.pop(planner, None)
            logger.debug(
                "避障 planner 后端已移除: device_id=%s planner=%s",
                getattr(self._device, "instance_id", None),
                planner.value,
            )
            return self
        if not callable(getattr(backend, "plan", None)):
            raise TypeError("backend must provide a plan(request) method.")
        self._planner_backends[planner] = backend
        logger.debug(
            "避障 planner 后端已注册: device_id=%s planner=%s backend=%s",
            getattr(self._device, "instance_id", None),
            planner.value,
            backend.__class__.__name__,
        )
        return self

    def snapshot(self) -> ObstacleSnapshot:
        """聚合所有 provider 的当前障碍物快照。

        聚合时会应用 ``configure_capacity(...)`` 配置的单 provider 上限、TTL 过滤、同 ID 去重和总容量限制。
        返回的 ``ObstacleSnapshot`` 只包含允许进入后续风险评估的标准障碍物，不包含原始点云或图像帧。
        """
        now_s = time.monotonic()
        obstacles, frames, observed_at_s, stats = _aggregate_obstacle_providers(
            self._providers,
            capacity=self._capacity,
            now_s=now_s,
            on_provider_error=self._on_provider_error,
        )
        self._last_snapshot_at_s = observed_at_s
        self._last_obstacle_count = len(obstacles)
        self._last_aggregation_stats = stats
        result = ObstacleSnapshot(
            provider_name="fleetdock.aggregate",
            frame=next(iter(frames)) if len(frames) == 1 else CoordinateFrame.UNKNOWN,
            observed_at_s=now_s,
            obstacles=obstacles,
        )
        if obstacles:
            self._device.events.publish(
                EventType.OBSTACLE_SNAPSHOT_UPDATED,
                EventCategory.TELEMETRY,
                {
                    "provider_name": result.provider_name,
                    "obstacle_count": len(obstacles),
                    "observed_at_s": result.observed_at_s,
                },
            )
        if stats.dropped_capacity_count or stats.dropped_stale_count or stats.dropped_duplicate_count:
            logger.debug(
                "避障障碍物聚合已裁剪: device_id=%s raw=%d kept=%d stale=%d duplicate=%d capacity=%d",
                getattr(self._device, "instance_id", None),
                stats.raw_obstacle_count,
                len(obstacles),
                stats.dropped_stale_count,
                stats.dropped_duplicate_count,
                stats.dropped_capacity_count,
            )
        return result

    def evaluate_segment(
        self,
        *,
        start_m: Vector3,
        target_m: Vector3,
        frame: CoordinateFrame = CoordinateFrame.LOCAL_NED,
        speed_m_s: float,
        policy: AvoidancePolicy | None = None,
        allow_replan: bool | None = None,
        replan_count: int = 0,
        ignored_obstacle_ids: Iterable[str] = (),
    ) -> AvoidanceDecision:
        """评估从 ``start_m`` 到 ``target_m`` 的运动段。

        ``start_m`` 与 ``target_m`` 坐标单位为米，坐标系由 ``frame`` 明确声明；``LOCAL_NED`` 的
        z 轴向下为正，``LOCAL_ENU`` 的 z 轴向上为正。``speed_m_s`` 是设备沿该运动段的预计速度，
        单位为米/秒；``policy`` 可临时覆盖设备默认策略；``allow_replan``
        是 route 执行器内部用于单次评估的局部绕行覆盖值；``replan_count`` 是当前 route 已插入绕行点的次数，
        用于限制反复重规划；``ignored_obstacle_ids`` 是当前路线已生成绕行走廊的障碍物 ID，后续评估
        会跳过这些障碍，避免同一静态障碍重复触发重规划事件。返回 ``ALLOW``、阻断动作或 ``REPLAN``
        决策；该方法只计算和发布风险事件，不直接发送设备命令。
        """
        active_policy = self._policy_for_device(policy or self._policy)
        segment_frame = self._normalize_frame(frame)
        if policy is None and not self.enabled:
            return self._remember(AvoidanceDecision(_AvoidanceAction.ALLOW, message="avoidance disabled", frame=segment_frame))
        if active_policy.disabled_for_route or not self._providers:
            return self._remember(AvoidanceDecision(_AvoidanceAction.ALLOW, message="avoidance disabled", frame=segment_frame))
        snapshot = self.snapshot()
        obstacles = self._active_obstacles(
            snapshot.obstacles,
            frame=segment_frame,
            policy=active_policy,
            ignored_obstacle_ids=frozenset(str(item) for item in ignored_obstacle_ids),
        )
        risk = self._first_segment_risk(
            obstacles,
            start_m=start_m,
            target_m=target_m,
            frame=segment_frame,
            speed_m_s=speed_m_s,
            policy=active_policy,
        )
        if not risk.has_risk:
            return self._remember(AvoidanceDecision(_AvoidanceAction.ALLOW, risk=risk, message="no collision risk", frame=segment_frame))

        self._publish_collision_risk(risk, active_policy)
        should_replan = active_policy.requests_local_replan if allow_replan is None else allow_replan
        if should_replan and replan_count < active_policy.max_replans_per_route:
            detours = self._build_detour_points(
                start_m,
                target_m,
                risk,
                active_policy,
                frame=segment_frame,
                obstacles=obstacles,
                speed_m_s=speed_m_s,
            )
            if detours:
                decision = AvoidanceDecision(
                    _AvoidanceAction.REPLAN,
                    risk=risk,
                    message="local avoidance replan requested",
                    detour_points_m=detours,
                    frame=segment_frame,
                )
                self._publish_avoidance_action(decision, None)
                return self._remember(decision)

        decision = AvoidanceDecision(
            active_policy.fallback_action,
            risk=risk,
            message=f"collision risk triggers {active_policy.fallback_action.value}",
            frame=segment_frame,
        )
        return self._remember(decision)

    def execute_action(self, decision: AvoidanceDecision) -> StandardResponse | None:
        """按 ``decision`` 对当前设备执行安全动作。

        空中设备优先映射 ``HOVER`` 和 ``RETURN_TO_LAUNCH``；机械狗等地面设备会把 ``HOVER`` 稳定降级为
        ``STOP``。返回底层标准响应；``ALLOW`` 和 ``REPLAN`` 不会发送动作，返回 ``None``。
        """
        action = self._resolve_device_action(decision.action)
        response: StandardResponse | None = None
        if action is _AvoidanceAction.ALLOW or action is _AvoidanceAction.REPLAN:
            return None
        if action is _AvoidanceAction.BLOCK:
            self._publish_avoidance_action(decision, None)
            return None
        method_name = action.value
        method = getattr(self._device, method_name, None)
        if callable(method):
            logger.debug(
                "执行避障安全动作: device_id=%s action=%s",
                getattr(self._device, "instance_id", None),
                action.value,
            )
            response = method()
        else:
            stop = getattr(self._device, "stop", None)
            response = stop() if callable(stop) else None
        self._publish_avoidance_action(decision, response)
        return response

    def close(self) -> None:
        """释放所有 provider 资源。

        provider 若实现 ``close()`` 会被逐个调用；异常只记录 DEBUG，不阻断设备关闭流程。该方法无
        返回值，调用后已注册 provider 会被清空。
        """
        for provider in tuple(self._providers):
            try:
                self.remove_provider(
                    provider,
                    close=self._provider_close_on_close.get(id(provider), True),
                )
            except Exception:
                logger.debug("避障 provider 关闭失败: provider=%s", provider.__class__.__name__, exc_info=True)

    def _install_default_planner_backends(self) -> None:
        from fleetdock.avoidance.backends import BuiltinTrajectoryRolloutPlanner

        self._planner_backends[AvoidancePlanner.TRAJECTORY_ROLLOUT] = BuiltinTrajectoryRolloutPlanner()

    def _current_velocity_m_s(self) -> Vector3 | None:
        state = getattr(self._device, "state", None)
        if state is None:
            return None
        x = getattr(state, "velocity_x_m_s", None)
        y = getattr(state, "velocity_y_m_s", None)
        z = getattr(state, "velocity_z_m_s", None)
        if x is None and y is None and z is None:
            return None
        return Vector3(float(x or 0.0), float(y or 0.0), float(z or 0.0))

    def _first_segment_risk(
        self,
        obstacles: Iterable[Obstacle],
        *,
        start_m: Vector3,
        target_m: Vector3,
        frame: CoordinateFrame,
        speed_m_s: float,
        policy: AvoidancePolicy,
    ) -> CollisionRisk:
        best_risk = CollisionRisk(False, reason="no obstacle")
        best_margin = float("inf")
        for obstacle in obstacles:
            distance_m, time_to_closest_s = self._closest_clearance_to_moving_obstacle(
                start_m,
                target_m,
                obstacle,
                speed_m_s=max(speed_m_s, 0.01),
                policy=policy,
            )
            limit_m = policy.safety_radius_m
            if (
                distance_m < limit_m
                and time_to_closest_s <= 1e-9
                and self._is_moving_out_of_initial_clearance(
                    start_m,
                    target_m,
                    obstacle,
                    speed_m_s=speed_m_s,
                    policy=policy,
                )
            ):
                # 设备已经处于安全半径内时，允许严格增加净空的脱离运动；route 会持续重评估，
                # 一旦运动不再远离障碍就会恢复阻断，避免起飞点附近的侧后方障碍造成原地返航。
                continue
            margin = distance_m - limit_m
            if margin < best_margin:
                best_margin = margin
                best_risk = CollisionRisk(
                    has_risk=margin <= 0.0,
                    obstacle=obstacle,
                    distance_m=distance_m,
                    limit_m=limit_m,
                    time_to_closest_s=time_to_closest_s,
                    reason="predicted route violates obstacle clearance" if margin <= 0.0 else "nearest obstacle is clear",
                )
        return best_risk

    def _is_moving_out_of_initial_clearance(
        self,
        start_m: Vector3,
        target_m: Vector3,
        obstacle: Obstacle,
        *,
        speed_m_s: float,
        policy: AvoidancePolicy,
    ) -> bool:
        """判断运动段是否正在严格增大与初始近距离障碍的表面净空。"""

        length_m = self._segment_length(start_m, target_m, policy)
        if length_m <= 1e-6:
            return False
        probe_distance_m = min(length_m, max(0.5, min(speed_m_s, 2.0) * 0.5))
        ratio = probe_distance_m / length_m
        probe_time_s = probe_distance_m / max(speed_m_s, 0.01)
        probe = Vector3(
            start_m.x + (target_m.x - start_m.x) * ratio,
            start_m.y + (target_m.y - start_m.y) * ratio,
            start_m.z + (target_m.z - start_m.z) * ratio,
        )
        initial_m = self._distance_to_obstacle_surface(start_m, obstacle, policy)
        probe_m = self._distance_to_obstacle_surface(probe, self._obstacle_at(obstacle, probe_time_s), policy)
        return probe_m > initial_m + 0.05

    def _active_obstacles(
        self,
        obstacles: Iterable[Obstacle],
        *,
        frame: CoordinateFrame,
        policy: AvoidancePolicy,
        ignored_obstacle_ids: frozenset[str],
    ) -> tuple[Obstacle, ...]:
        now_s = time.monotonic()
        active: list[Obstacle] = []
        for obstacle in obstacles:
            if obstacle.obstacle_id is not None and str(obstacle.obstacle_id) in ignored_obstacle_ids:
                continue
            if not obstacle.is_fresh(now_s, default_ttl_s=policy.stale_obstacle_timeout_s):
                self._publish_provider_stale(obstacle.obstacle_id or "unknown", "obstacle expired")
                continue
            obstacle_frame = self._normalize_frame(obstacle.frame)
            if obstacle_frame not in (CoordinateFrame.LOCAL_NED, CoordinateFrame.LOCAL_ENU, CoordinateFrame.UNKNOWN):
                continue
            normalized = obstacle if obstacle_frame is obstacle.frame else replace(obstacle, frame=obstacle_frame)
            active.append(self._obstacle_in_frame(normalized, frame))
        return tuple(active)

    def _closest_clearance_to_moving_obstacle(
        self,
        start_m: Vector3,
        target_m: Vector3,
        obstacle: Obstacle,
        *,
        speed_m_s: float,
        policy: AvoidancePolicy,
    ) -> tuple[float, float]:
        dx = target_m.x - start_m.x
        dy = target_m.y - start_m.y
        dz = target_m.z - start_m.z
        length_m = self._segment_length(start_m, target_m, policy)
        if length_m <= 1e-6:
            return self._distance_to_obstacle_surface(start_m, obstacle, policy), 0.0
        duration_s = min(policy.lookahead_s, length_m / speed_m_s)
        samples = max(3, min(12, int(duration_s / 0.25) + 1))
        best_distance = float("inf")
        best_t = 0.0
        for index in range(samples):
            ratio = index / max(1, samples - 1)
            t_s = duration_s * ratio
            travel_ratio = min(1.0, (speed_m_s * t_s) / length_m)
            device = Vector3(
                start_m.x + dx * travel_ratio,
                start_m.y + dy * travel_ratio,
                start_m.z + dz * travel_ratio,
            )
            distance = self._distance_to_obstacle_surface(device, self._obstacle_at(obstacle, t_s), policy)
            if distance < best_distance:
                best_distance = distance
                best_t = t_s
        return best_distance, best_t

    def _build_detour_points(
        self,
        start_m: Vector3,
        target_m: Vector3,
        risk: CollisionRisk,
        policy: AvoidancePolicy,
        *,
        frame: CoordinateFrame,
        obstacles: tuple[Obstacle, ...],
        speed_m_s: float,
    ) -> tuple[Vector3, ...]:
        backend = self._planner_backends.get(policy.planner)
        if backend is None:
            logger.debug(
                "避障 planner 后端未注册: device_id=%s planner=%s",
                getattr(self._device, "instance_id", None),
                policy.planner.value,
            )
            return ()
        request = AvoidancePlanRequest(
            device_category=getattr(getattr(self._device, "profile", None), "category", None),
            frame=frame,
            start_m=start_m,
            target_m=target_m,
            current_velocity_m_s=self._current_velocity_m_s(),
            speed_m_s=speed_m_s,
            obstacles=obstacles,
            risk=risk,
            policy=policy,
        )
        try:
            result = backend.plan(request)
        except Exception:
            logger.debug(
                "避障 planner 后端计算失败: device_id=%s planner=%s backend=%s",
                getattr(self._device, "instance_id", None),
                policy.planner.value,
                backend.__class__.__name__,
                exc_info=True,
            )
            return ()
        if result.status is PlannerStatus.REPLAN and result.detour_points_m:
            logger.debug(
                "避障 planner 已生成绕行点: device_id=%s planner=%s detour_count=%d message=%s",
                getattr(self._device, "instance_id", None),
                policy.planner.value,
                len(result.detour_points_m),
                result.message,
            )
            return result.detour_points_m
        logger.debug(
            "避障 planner 未返回可用绕行: device_id=%s planner=%s status=%s message=%s",
            getattr(self._device, "instance_id", None),
            policy.planner.value,
            result.status.value,
            result.message,
        )
        return ()

    def _resolve_device_action(self, action: _AvoidanceAction) -> _AvoidanceAction:
        category = getattr(getattr(self._device, "profile", None), "category", None)
        category_value = category.value if isinstance(category, DeviceCategory) else str(category)
        if action is _AvoidanceAction.HOVER and category_value in (
            DeviceCategory.ROBOT_DOG.value,
            DeviceCategory.HUMANOID.value,
        ):
            return _AvoidanceAction.STOP
        if action is _AvoidanceAction.RETURN_TO_LAUNCH and not hasattr(self._device, "return_to_launch"):
            return _AvoidanceAction.STOP
        return action

    def _publish_collision_risk(self, risk: CollisionRisk, policy: AvoidancePolicy) -> None:
        obstacle = risk.obstacle
        self._device.events.publish(
            EventType.COLLISION_RISK_RAISED,
            EventCategory.ALERT,
            {
                "code": "collision_risk",
                "message": risk.reason,
                "severity": "critical",
                "device_id": getattr(self._device.metadata, "device_id", None) or getattr(self._device, "instance_id", ""),
                "action": policy.fallback_action.value,
                "metric_value": risk.distance_m,
                "limit_value": risk.limit_m,
                "metadata": {
                    "obstacle_id": obstacle.obstacle_id if obstacle is not None else None,
                    "time_to_closest_s": risk.time_to_closest_s,
                    "strategy": policy.strategy.value,
                    "planner": policy.planner.value,
                    "fallback_action": policy.fallback_action.value,
                    "obstacle_shape": self._obstacle_shape(obstacle).value if obstacle is not None else None,
                },
            },
        )

    def _publish_avoidance_action(self, decision: AvoidanceDecision, response: StandardResponse | None) -> None:
        risk = decision.risk
        obstacle = risk.obstacle if risk is not None else None
        self._device.events.publish(
            EventType.AVOIDANCE_ACTION_TRIGGERED,
            EventCategory.ALERT,
            {
                "action": decision.action.value,
                "device_id": getattr(self._device.metadata, "device_id", None) or getattr(self._device, "instance_id", ""),
                "violation_codes": ("collision_risk",) if risk is not None and risk.has_risk else (),
                "ok": response.ok if response is not None else None,
                "message": response.message if response is not None else decision.message,
                "metadata": {
                    "obstacle_id": obstacle.obstacle_id if obstacle is not None else None,
                    "detour_count": len(decision.detour_points_m),
                },
            },
        )

    def _publish_provider_stale(self, provider_name: str, reason: str) -> None:
        try:
            self._device.events.publish(
                EventType.OBSTACLE_PROVIDER_STALE,
                EventCategory.ALERT,
                {"provider_name": provider_name, "reason": reason},
            )
        except Exception:
            logger.debug("发布 provider stale 事件失败", exc_info=True)

    def _on_provider_error(self, provider: ObstacleProvider, exc: Exception) -> None:
        logger.debug("避障 provider 快照失败: provider=%s", provider.__class__.__name__, exc_info=True)
        self._publish_provider_stale(provider.__class__.__name__, str(exc))

    def _policy_for_device(self, policy: AvoidancePolicy) -> AvoidancePolicy:
        if policy.dimensions is not AvoidanceDimensions.AUTO:
            return policy
        category = getattr(getattr(self._device, "profile", None), "category", None)
        category_value = category.value if isinstance(category, DeviceCategory) else str(category)
        dimensions = (
            AvoidanceDimensions.THREE_D
            if category_value == DeviceCategory.DRONE.value
            else AvoidanceDimensions.TWO_D
        )
        return replace(policy, dimensions=dimensions)

    def _remember(self, decision: AvoidanceDecision) -> AvoidanceDecision:
        self._last_decision = decision
        return decision

    def _segment_length(self, first: Vector3, second: Vector3, policy: AvoidancePolicy) -> float:
        return self._distance(first, second, policy)

    def _distance_to_obstacle_surface(self, point: Vector3, obstacle: Obstacle, policy: AvoidancePolicy) -> float:
        shape = self._obstacle_shape(obstacle)
        if shape is ObstacleShape.BOX:
            half_x, half_y, half_z = self._obstacle_box_half_extents_m(obstacle)
            dx = max(abs(point.x - obstacle.position_m.x) - half_x, 0.0)
            dy = max(abs(point.y - obstacle.position_m.y) - half_y, 0.0)
            if policy.dimensions is AvoidanceDimensions.TWO_D:
                return math.hypot(dx, dy)
            dz = max(abs(point.z - obstacle.position_m.z) - half_z, 0.0)
            return math.sqrt(dx * dx + dy * dy + dz * dz)
        if shape is ObstacleShape.CYLINDER:
            radius_m = max(float(obstacle.radius_m or 0.0), 0.0)
            horizontal_m = max(math.hypot(point.x - obstacle.position_m.x, point.y - obstacle.position_m.y) - radius_m, 0.0)
            if policy.dimensions is AvoidanceDimensions.TWO_D:
                return horizontal_m
            half_height_m = self._obstacle_vertical_extent_m(obstacle)
            vertical_m = max(abs(point.z - obstacle.position_m.z) - half_height_m, 0.0)
            return math.sqrt(horizontal_m * horizontal_m + vertical_m * vertical_m)
        radius_m = max(float(obstacle.radius_m or 0.0), 0.0)
        return max(self._distance(point, obstacle.position_m, policy) - radius_m, 0.0)

    @staticmethod
    def _obstacle_shape(obstacle: Obstacle) -> ObstacleShape:
        if isinstance(obstacle.shape, ObstacleShape):
            return obstacle.shape
        try:
            return ObstacleShape(str(obstacle.shape))
        except ValueError:
            return ObstacleShape.SPHERE

    def _obstacle_horizontal_extent_m(self, obstacle: Obstacle) -> float:
        shape = self._obstacle_shape(obstacle)
        if shape is ObstacleShape.BOX:
            half_x, half_y, _ = self._obstacle_box_half_extents_m(obstacle)
            return math.hypot(half_x, half_y)
        return max(float(obstacle.radius_m or 0.0), 0.0)

    def _obstacle_vertical_extent_m(self, obstacle: Obstacle) -> float:
        shape = self._obstacle_shape(obstacle)
        if shape is ObstacleShape.BOX:
            _, _, half_z = self._obstacle_box_half_extents_m(obstacle)
            return half_z
        if shape is ObstacleShape.CYLINDER and obstacle.size_m is not None:
            return max(abs(obstacle.size_m.z) * 0.5, 0.0)
        return max(float(obstacle.radius_m or 0.0), 0.0)

    @staticmethod
    def _obstacle_box_half_extents_m(obstacle: Obstacle) -> tuple[float, float, float]:
        if obstacle.size_m is not None:
            return (max(abs(obstacle.size_m.x) * 0.5, 0.0), max(abs(obstacle.size_m.y) * 0.5, 0.0), max(abs(obstacle.size_m.z) * 0.5, 0.0))
        radius_m = max(float(obstacle.radius_m or 0.0), 0.0)
        return (radius_m, radius_m, radius_m)

    @staticmethod
    def _obstacle_at(obstacle: Obstacle, elapsed_s: float) -> Obstacle:
        if obstacle.velocity_m_s is None or elapsed_s <= 0.0:
            return obstacle
        velocity = obstacle.velocity_m_s
        return replace(
            obstacle,
            position_m=Vector3(
                obstacle.position_m.x + velocity.x * elapsed_s,
                obstacle.position_m.y + velocity.y * elapsed_s,
                obstacle.position_m.z + velocity.z * elapsed_s,
            ),
        )

    @staticmethod
    def _distance(first: Vector3, second: Vector3, policy: AvoidancePolicy) -> float:
        dx = first.x - second.x
        dy = first.y - second.y
        if policy.dimensions is AvoidanceDimensions.TWO_D:
            return math.hypot(dx, dy)
        dz = first.z - second.z
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    @staticmethod
    def _normalize_frame(frame: CoordinateFrame | str) -> CoordinateFrame:
        return frame if isinstance(frame, CoordinateFrame) else CoordinateFrame(str(frame))

    @classmethod
    def _obstacle_in_frame(cls, obstacle: Obstacle, target_frame: CoordinateFrame) -> Obstacle:
        source_frame = cls._normalize_frame(obstacle.frame)
        if source_frame is target_frame or CoordinateFrame.UNKNOWN in (source_frame, target_frame):
            return obstacle
        return replace(
            obstacle,
            frame=target_frame,
            position_m=cls._convert_vector_frame(obstacle.position_m, source_frame, target_frame),
            velocity_m_s=(
                cls._convert_vector_frame(obstacle.velocity_m_s, source_frame, target_frame)
                if obstacle.velocity_m_s is not None
                else None
            ),
            size_m=(
                cls._convert_size_frame(obstacle.size_m, source_frame, target_frame)
                if obstacle.size_m is not None
                else None
            ),
        )

    @staticmethod
    def _convert_vector_frame(vector: Vector3, source_frame: CoordinateFrame, target_frame: CoordinateFrame) -> Vector3:
        if source_frame is target_frame or CoordinateFrame.UNKNOWN in (source_frame, target_frame):
            return vector
        if {source_frame, target_frame} == {CoordinateFrame.LOCAL_NED, CoordinateFrame.LOCAL_ENU}:
            return Vector3(vector.y, vector.x, -vector.z)
        return vector

    @staticmethod
    def _convert_size_frame(vector: Vector3, source_frame: CoordinateFrame, target_frame: CoordinateFrame) -> Vector3:
        if source_frame is target_frame or CoordinateFrame.UNKNOWN in (source_frame, target_frame):
            return vector
        if {source_frame, target_frame} == {CoordinateFrame.LOCAL_NED, CoordinateFrame.LOCAL_ENU}:
            return Vector3(abs(vector.y), abs(vector.x), abs(vector.z))
        return vector


class FleetObstacleTransformer(Protocol):
    """Fleet 共享世界模型的坐标转换协议。

    实现方负责把 ``obstacle`` 从其原坐标系转换到 ``target_frame``；``source_device`` 在障碍物来自 Fleet
    成员遥测时指向对应设备，来自共享 provider 时为 ``None``。无法安全转换时应返回 ``None``，不得伪造坐标。
    """

    def transform(
        self,
        obstacle: Obstacle,
        *,
        target_frame: CoordinateFrame,
        source_device: Any | None = None,
    ) -> Obstacle | None:
        """把 ``obstacle`` 从来源设备坐标转换到 ``target_frame``。

        ``source_device`` 是可选来源设备，用于处理不同本地原点；返回转换后的新障碍物，无法安全转换时返回 ``None``。
        """


class _FleetDeviceObstacleProvider:
    def __init__(self, manager: "FleetAvoidanceManager", device: Any) -> None:
        self._manager = manager
        self._device = device

    def snapshot(self) -> ObstacleSnapshot:
        return self._manager._snapshot_for_device(self._device)

    def close(self) -> None:
        return None


class FleetAvoidanceManager:
    """挂载在 ``Fleet`` 上的共享避障与世界模型管理器。

    管理器集中维护跨厂商共享 provider，按 TTL、障碍物 ID 和容量策略聚合世界模型，并通过轻量只读 provider
    把同一份结果提供给 Fleet 内各设备的 ``device.avoidance``。真实 provider 只注册和释放一次，不会复制到每台设备。
    默认还会把队内其它成员的位置和速度转换为动态障碍物，使无人机、机械狗和人形机器人能够互相避让。
    """

    def __init__(
        self,
        fleet: Any,
        *,
        capacity: ObstacleCapacityPolicy | None = None,
        world_frame: CoordinateFrame = CoordinateFrame.LOCAL_NED,
    ) -> None:
        self._fleet = fleet
        self._lock = RLock()
        self._providers: list[ObstacleProvider] = []
        self._provider_ids: set[int] = set()
        self._capacity = capacity or ObstacleCapacityPolicy()
        self._world_frame = world_frame
        self._transformer: FleetObstacleTransformer | None = None
        self._enabled = False
        self._include_fleet_devices = True
        self._assume_shared_local_origin = False
        self._device_obstacle_ttl_s = 0.5
        self._device_providers: dict[int, _FleetDeviceObstacleProvider] = {}
        self._last_snapshot: ObstacleSnapshot | None = None
        self._last_stats: ObstacleAggregationStats | None = None
        self._snapshot_cache_ttl_s = 0.05
        self._shared_cache_at_s = 0.0
        self._shared_cache_obstacles: tuple[Obstacle, ...] = ()
        self._shared_cache_stats: ObstacleAggregationStats | None = None

    @property
    def enabled(self) -> bool:
        """返回共享避障世界模型是否已启用；关闭时不影响设备自己的本地 provider。"""
        return self._enabled

    def add_provider(self, provider: ObstacleProvider) -> "FleetAvoidanceManager":
        """注册一个 Fleet 共享障碍物 provider 并返回当前管理器。

        provider 可以来自激光雷达、视觉、地图、仿真器或厂商 SDK。重复注册同一对象是幂等操作；真实 provider
        不会被逐设备复制，``Fleet.close()`` 时仅释放一次。调用后仍需通过 ``configure(...)`` 或 ``enable()``
        启用共享世界模型和设备侧风险评估。
        """
        if not callable(getattr(provider, "snapshot", None)):
            raise TypeError("provider must provide a snapshot() method.")
        provider_id = id(provider)
        with self._lock:
            if provider_id in self._provider_ids:
                return self
            self._provider_ids.add(provider_id)
            self._providers.append(provider)
            self._invalidate_cache()
        self._attach_to_devices()
        logger.debug(
            "Fleet 避障共享 provider 已添加: fleet=%s provider=%s count=%d",
            getattr(self._fleet, "name", None),
            provider.__class__.__name__,
            len(self._providers),
        )
        return self

    def remove_provider(self, provider: ObstacleProvider, *, close: bool = True) -> "FleetAvoidanceManager":
        """移除一个共享 provider 并返回当前管理器。

        ``provider`` 必须是此前注册的同一对象；未注册时保持幂等。``close`` 为 True 时会立即调用其 ``close()``，
        为 False 时资源生命周期继续由调用方负责。
        """
        provider_id = id(provider)
        with self._lock:
            if provider_id not in self._provider_ids:
                return self
            self._provider_ids.remove(provider_id)
            self._providers = [item for item in self._providers if item is not provider]
            self._invalidate_cache()
        if close:
            self._close_provider(provider)
        return self

    def configure(
        self,
        default_policy: AvoidancePolicy | None = None,
        *,
        policies: Mapping[DeviceCategory | str, AvoidancePolicy] | None = None,
        include_fleet_devices: bool = True,
        assume_shared_local_origin: bool = False,
        device_obstacle_ttl_s: float = 0.5,
        snapshot_cache_ttl_s: float = 0.05,
    ) -> "FleetAvoidanceManager":
        """配置并启用 Fleet 共享避障，完成后返回当前管理器以便链式调用。

        ``default_policy`` 是未单独匹配设备类别时使用的策略，省略时使用 ``AvoidancePolicy()``；其默认
        ``dimensions=AUTO`` 会让无人机采用 3D、机械狗和人形机器人采用 2D。``policies`` 可按
        ``DeviceCategory`` 或其字符串值覆盖不同设备类型。``include_fleet_devices`` 控制是否把队内其它设备作为
        动态障碍物；只有已经配置 ``FleetObstacleTransformer``，或调用方明确设置
        ``assume_shared_local_origin=True``，才会直接合并成员本地坐标，避免错误假设不同飞控和机器人共享同一原点。
        ``device_obstacle_ttl_s`` 是成员遥测障碍物有效期，单位为秒。方法会启用各成员设备的
        ``device.avoidance``，但不会覆盖设备已有本地 provider。``snapshot_cache_ttl_s`` 是共享 provider
        快照复用窗口，单位为秒；默认 0.05 秒用于让同一控制周期内的多设备复用同一份感知结果，设为 0 可关闭缓存。
        """
        if device_obstacle_ttl_s < 0:
            raise ValueError("device_obstacle_ttl_s cannot be negative.")
        if snapshot_cache_ttl_s < 0:
            raise ValueError("snapshot_cache_ttl_s cannot be negative.")
        self._include_fleet_devices = include_fleet_devices
        self._assume_shared_local_origin = assume_shared_local_origin
        self._device_obstacle_ttl_s = device_obstacle_ttl_s
        self._snapshot_cache_ttl_s = snapshot_cache_ttl_s
        normalized_policies = {
            item.value if isinstance(item, DeviceCategory) else str(item): policy
            for item, policy in (policies or {}).items()
        }
        fallback = default_policy or AvoidancePolicy()
        self._attach_to_devices()
        for device in tuple(self._fleet):
            category = getattr(getattr(device, "profile", None), "category", DeviceCategory.OTHER)
            category_value = category.value if isinstance(category, DeviceCategory) else str(category)
            device.avoidance.configure(normalized_policies.get(category_value, fallback))
        self._enabled = True
        logger.debug(
            "Fleet 避障已配置并启用: fleet=%s device_count=%d provider_count=%d include_devices=%s shared_origin=%s",
            getattr(self._fleet, "name", None),
            len(tuple(self._fleet)),
            len(self._providers),
            include_fleet_devices,
            assume_shared_local_origin,
        )
        return self

    def enable(self) -> "FleetAvoidanceManager":
        """启用共享世界模型并恢复各成员设备当前避障策略，不立即发送设备动作；返回当前管理器。"""
        self._attach_to_devices()
        self._enabled = True
        for device in tuple(self._fleet):
            device.avoidance.enable()
        return self

    def disable(self) -> "FleetAvoidanceManager":
        """暂停 Fleet 共享世界模型，不关闭共享 provider，也不关闭设备自己的本地避障能力；返回当前管理器。"""
        self._enabled = False
        return self

    def configure_capacity(self, capacity: ObstacleCapacityPolicy | None = None) -> "FleetAvoidanceManager":
        """使用 ``capacity`` 配置 Fleet 世界模型容量保护并返回当前管理器；传入 ``None`` 恢复默认策略。"""
        self._capacity = capacity or ObstacleCapacityPolicy()
        self._invalidate_cache()
        return self

    def configure_world(
        self,
        *,
        frame: CoordinateFrame = CoordinateFrame.LOCAL_NED,
        transformer: FleetObstacleTransformer | None = None,
    ) -> "FleetAvoidanceManager":
        """使用 ``frame`` 配置共享世界坐标系和可选转换器，并返回当前管理器。

        内置能力仅可靠处理同一原点下的 ``LOCAL_NED`` 与 ``LOCAL_ENU`` 轴转换。多设备本地原点不同、机体系
        ``BODY`` 或大地坐标 ``GEODETIC`` 场景必须传入 ``transformer``；转换器返回 ``None`` 的障碍物会被安全丢弃。
        """
        self._world_frame = frame
        self._transformer = transformer
        self._invalidate_cache()
        return self

    def snapshot(self) -> ObstacleSnapshot:
        """返回当前 Fleet 共享世界模型快照。

        快照包含共享 provider 和可选队内动态障碍物，已完成 TTL、去重、容量保护和坐标转换；坐标单位为米，
        坐标系由 ``configure_world(...)`` 决定。
        """
        return self._build_snapshot(exclude_device=None)

    def status(self) -> FleetAvoidanceStatus:
        """返回共享 provider、设备数量、最近障碍物数量、容量策略和聚合统计。"""
        return FleetAvoidanceStatus(
            enabled=self._enabled,
            provider_count=len(self._providers),
            device_count=len(tuple(self._fleet)),
            shared_obstacle_count=len(self._last_snapshot.obstacles) if self._last_snapshot is not None else 0,
            last_snapshot_at_s=self._last_snapshot.observed_at_s if self._last_snapshot is not None else None,
            capacity=self._capacity,
            aggregation_stats=self._last_stats,
        )

    def close(self) -> None:
        """关闭并释放 Fleet 持有的全部共享 provider；设备侧只读包装不会重复释放真实资源，无返回值。"""
        self._enabled = False
        with self._lock:
            providers = tuple(self._providers)
            self._providers.clear()
            self._provider_ids.clear()
            self._invalidate_cache()
        for provider in providers:
            self._close_provider(provider)

    def _attach_to_devices(self) -> None:
        for device in tuple(self._fleet):
            provider = self._device_providers.get(id(device))
            if provider is None:
                provider = _FleetDeviceObstacleProvider(self, device)
                self._device_providers[id(device)] = provider
            device.avoidance.add_provider(provider, close_on_close=False)

    def _snapshot_for_device(self, device: Any) -> ObstacleSnapshot:
        if not self._enabled:
            return ObstacleSnapshot(
                provider_name="fleetdock.fleet.disabled",
                frame=self._world_frame,
                observed_at_s=time.monotonic(),
                obstacles=(),
            )
        return self._build_snapshot(exclude_device=device)

    def _build_snapshot(self, *, exclude_device: Any | None) -> ObstacleSnapshot:
        with self._lock:
            return self._build_snapshot_locked(exclude_device=exclude_device)

    def _build_snapshot_locked(self, *, exclude_device: Any | None) -> ObstacleSnapshot:
        now_s = time.monotonic()
        cache_is_fresh = (
            self._shared_cache_stats is not None
            and now_s - self._shared_cache_at_s <= self._snapshot_cache_ttl_s
        )
        if cache_is_fresh:
            shared_obstacles = self._shared_cache_obstacles
            stats = self._shared_cache_stats
        else:
            obstacles, _, _, stats = _aggregate_obstacle_providers(
                self._providers,
                capacity=self._capacity,
                now_s=now_s,
                on_provider_error=self._on_provider_error,
            )
            shared: list[Obstacle] = []
            for obstacle in obstacles:
                normalized = self._transform(obstacle, source_device=None)
                if normalized is not None:
                    shared.append(normalized)
            shared_obstacles = tuple(shared)
            self._shared_cache_at_s = now_s
            self._shared_cache_obstacles = shared_obstacles
            self._shared_cache_stats = stats
        transformed = list(shared_obstacles)
        if self._include_fleet_devices:
            for device in tuple(self._fleet):
                if device is exclude_device:
                    continue
                device_obstacle = self._device_obstacle(device, now_s)
                if device_obstacle is not None:
                    transformed.append(device_obstacle)
        bounded, duplicate_drops = self._deduplicate_and_bound(transformed)
        if stats is None:
            stats = ObstacleAggregationStats()
        stats = replace(
            stats,
            deduplicated_count=len(bounded),
            dropped_duplicate_count=stats.dropped_duplicate_count + duplicate_drops,
            dropped_capacity_count=stats.dropped_capacity_count
            + max(0, len(transformed) - duplicate_drops - len(bounded)),
        )
        snapshot = ObstacleSnapshot(
            provider_name="fleetdock.fleet.shared",
            frame=self._world_frame,
            observed_at_s=now_s,
            obstacles=bounded,
        )
        self._last_snapshot = snapshot
        self._last_stats = stats
        return snapshot

    def _invalidate_cache(self) -> None:
        self._shared_cache_at_s = 0.0
        self._shared_cache_obstacles = ()
        self._shared_cache_stats = None

    def _device_obstacle(self, device: Any, now_s: float) -> Obstacle | None:
        state_view = getattr(device, "state", None)
        if state_view is None:
            return None
        state = state_view.snapshot()
        x = getattr(state, "local_x_m", None)
        y = getattr(state, "local_y_m", None)
        if x is None or y is None:
            return None
        z = getattr(state, "local_z_m", None)
        if z is None:
            z = getattr(state, "relative_altitude_m", None)
        frame = getattr(state, "local_position_frame", CoordinateFrame.LOCAL_NED)
        velocity = None
        velocity_values = (
            getattr(state, "velocity_x_m_s", None),
            getattr(state, "velocity_y_m_s", None),
            getattr(state, "velocity_z_m_s", None),
        )
        if any(value is not None for value in velocity_values):
            velocity = Vector3(*(float(value or 0.0) for value in velocity_values))
        category = getattr(getattr(device, "profile", None), "category", DeviceCategory.OTHER)
        category_value = category.value if isinstance(category, DeviceCategory) else str(category)
        default_radius_m = 0.7 if category_value == DeviceCategory.DRONE.value else 0.5
        radius_m = float(getattr(getattr(device, "avoidance", None), "policy", AvoidancePolicy()).device_radius_m)
        if radius_m <= 0:
            radius_m = default_radius_m
        device_id = getattr(getattr(device, "metadata", None), "device_id", None) or getattr(device, "instance_id", "")
        obstacle = Obstacle(
            obstacle_id=f"fleet.device.{device_id}",
            kind=ObstacleKind.DYNAMIC,
            shape=ObstacleShape.SPHERE,
            frame=frame,
            position_m=Vector3(float(x), float(y), float(z or 0.0)),
            velocity_m_s=velocity,
            radius_m=radius_m,
            confidence=1.0,
            observed_at_s=now_s,
            ttl_s=self._device_obstacle_ttl_s,
        )
        return self._transform(obstacle, source_device=device)

    def _transform(self, obstacle: Obstacle, *, source_device: Any | None) -> Obstacle | None:
        if self._transformer is not None:
            return self._transformer.transform(
                obstacle,
                target_frame=self._world_frame,
                source_device=source_device,
            )
        if source_device is not None and not self._assume_shared_local_origin:
            return None
        source_frame = AvoidanceManager._normalize_frame(obstacle.frame)
        if source_frame not in (CoordinateFrame.LOCAL_NED, CoordinateFrame.LOCAL_ENU, CoordinateFrame.UNKNOWN):
            return None
        return AvoidanceManager._obstacle_in_frame(obstacle, self._world_frame)

    def _deduplicate_and_bound(self, obstacles: Iterable[Obstacle]) -> tuple[tuple[Obstacle, ...], int]:
        by_id: dict[str, Obstacle] = {}
        anonymous: list[Obstacle] = []
        duplicate_drops = 0
        for obstacle in obstacles:
            if obstacle.obstacle_id is None:
                anonymous.append(obstacle)
                continue
            obstacle_id = str(obstacle.obstacle_id)
            previous = by_id.get(obstacle_id)
            if previous is None:
                by_id[obstacle_id] = obstacle
            else:
                duplicate_drops += 1
                by_id[obstacle_id] = _prefer_obstacle(obstacle, previous)
        ordered = tuple(sorted((*by_id.values(), *anonymous), key=_obstacle_retention_key))
        return ordered[: self._capacity.max_obstacles], duplicate_drops

    def _on_provider_error(self, provider: ObstacleProvider, exc: Exception) -> None:
        logger.debug(
            "Fleet 避障 provider 快照失败: fleet=%s provider=%s error=%s",
            getattr(self._fleet, "name", None),
            provider.__class__.__name__,
            exc,
            exc_info=True,
        )

    @staticmethod
    def _close_provider(provider: ObstacleProvider) -> None:
        close = getattr(provider, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger.debug("Fleet 避障 provider 关闭失败: provider=%s", provider.__class__.__name__, exc_info=True)


def _action_from_strategy(strategy: AvoidanceStrategy) -> _AvoidanceAction:
    if strategy is AvoidanceStrategy.BLOCK:
        return _AvoidanceAction.BLOCK
    if strategy is AvoidanceStrategy.HOLD_POSITION:
        return _AvoidanceAction.HOVER
    if strategy is AvoidanceStrategy.STOP:
        return _AvoidanceAction.STOP
    if strategy is AvoidanceStrategy.RETURN_TO_LAUNCH:
        return _AvoidanceAction.RETURN_TO_LAUNCH
    if strategy is AvoidanceStrategy.EMERGENCY_STOP:
        return _AvoidanceAction.EMERGENCY_STOP
    if strategy is AvoidanceStrategy.LOCAL_REPLAN:
        return _AvoidanceAction.REPLAN
    raise ValueError(f"Unsupported avoidance strategy: {strategy!r}")
