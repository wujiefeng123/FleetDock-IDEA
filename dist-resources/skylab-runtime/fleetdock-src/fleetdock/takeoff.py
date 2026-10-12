"""跨厂商无人机起飞任务的公共模型。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import math
from typing import Any, Callable, Optional, Protocol, runtime_checkable


class TakeoffExecutionState(str, Enum):
    """起飞任务状态；描述物理执行进度，不代表单次命令是否已被接受。"""

    PRECHECKING = "prechecking"  # 正在检查定位、健康、电量和控制链路。
    STARTING = "starting"  # 正在建立起飞控制并请求解锁。
    ASCENDING = "ascending"  # 无人机正在向目标相对高度爬升。
    HOLDING = "holding"  # 已到达容差范围，正在建立稳定保持状态。
    COMPLETED = "completed"  # 已确认到达目标高度并安全完成控制交接。
    ABORTED = "aborted"  # 起飞被取消、安全策略中止或执行失败。


class TakeoffMethod(str, Enum):
    """SDK 自动选出的起飞控制方法；仅用于状态和诊断，不要求业务代码分支。"""

    AUTOPILOT = "autopilot"  # 使用飞控原生起飞任务。
    LOCAL_POSITION = "local_position"  # 使用本地位置闭环起飞，坐标转换由适配器负责。


@dataclass(frozen=True)
class TakeoffOptions:
    """起飞任务的跨厂商安全参数。

    ``target_altitude_m`` 是相对起飞地面的目标高度（米）；``altitude_tolerance_m`` 是完成
    判定容差（米）；``timeout_s`` 是整个任务上限（秒）；``climb_speed_m_s`` 是期望爬升速度
    （米/秒）。``telemetry_max_age_s`` 是起飞前及爬升期间位置、姿态等控制遥测的最大年龄
    （秒）；``battery_telemetry_max_age_s`` 是进入爬升后的电池监督遥测最大年龄（秒），起飞前
    电池仍使用更严格的 ``telemetry_max_age_s``。其余字段限制水平漂移、机体倾角和最低剩余
    电量。调用方不选择厂商协议或飞控模式，适配器会依据运行状态自动适配。
    """

    target_altitude_m: float = 5.0
    altitude_tolerance_m: float = 0.15
    timeout_s: float = 60.0
    climb_speed_m_s: float = 0.5
    telemetry_max_age_s: float = 5.0
    battery_telemetry_max_age_s: float = 15.0
    max_horizontal_deviation_m: float = 2.0
    max_tilt_deg: float = 35.0
    min_battery_fraction: float = 0.15
    control_rate_hz: float = 10.0
    native_progress_timeout_s: float = 4.0

    def __post_init__(self) -> None:
        values = (
            self.target_altitude_m,
            self.altitude_tolerance_m,
            self.timeout_s,
            self.climb_speed_m_s,
            self.telemetry_max_age_s,
            self.battery_telemetry_max_age_s,
            self.max_horizontal_deviation_m,
            self.max_tilt_deg,
            self.control_rate_hz,
            self.native_progress_timeout_s,
        )
        if not all(math.isfinite(value) and value > 0.0 for value in values):
            raise ValueError("Takeoff distance, speed, rate, and timeout values must be finite and positive.")
        if self.altitude_tolerance_m >= self.target_altitude_m:
            raise ValueError("altitude_tolerance_m must be lower than target_altitude_m.")
        if not math.isfinite(self.min_battery_fraction) or not 0.0 <= self.min_battery_fraction <= 1.0:
            raise ValueError("min_battery_fraction must be between 0 and 1.")


@dataclass(frozen=True)
class TakeoffStatus:
    """不可变起飞状态快照。

    ``execution_id`` 唯一标识一次执行；``state`` 是物理进度；``method`` 是 SDK 自动选出的
    诊断方法；高度字段单位为米，``progress`` 范围为 0~1；``reason`` 解释选择或失败原因。
    """

    execution_id: str
    state: TakeoffExecutionState
    target_altitude_m: float
    current_altitude_m: Optional[float] = None
    progress: float = 0.0
    method: Optional[TakeoffMethod] = None
    reason: str = ""

    def changed(self, **changes: Any) -> "TakeoffStatus":
        """返回应用 ``changes`` 后的新快照；原对象保持不变。"""
        return replace(self, **changes)


TakeoffProgressCallback = Callable[[TakeoffStatus], None]


@runtime_checkable
class TakeoffAdapter(Protocol):
    """适配器可选的物理起飞任务协议。"""

    def supports_confirmed_takeoff(self) -> bool:
        """返回当前底层客户端是否实现自动适配和物理完成确认。"""

    def execute_takeoff(
        self,
        options: TakeoffOptions,
        *,
        progress_callback: Optional[TakeoffProgressCallback] = None,
        cancellation_check: Optional[Callable[[], None]] = None,
    ) -> TakeoffStatus:
        """执行并确认物理起飞完成。

        ``options`` 定义相对高度（米）、速度（米/秒）、超时（秒）和安全边界；
        ``progress_callback`` 在状态变化时同步接收不可变快照；``cancellation_check`` 由任务层传入，
        可抛出取消或守卫异常中止执行。成功返回最终 ``TakeoffStatus``；预检、控制、安全或超时失败
        时抛出异常，适配器不得在不确定空中状态直接上锁。
        """
