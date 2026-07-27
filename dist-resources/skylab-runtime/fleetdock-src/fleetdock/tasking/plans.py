"""根据设备类型创建计划编排器。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Sequence, overload

if TYPE_CHECKING:
    from fleetdock.devices import BaseDevice, Drone, RobotDog
    from fleetdock.fleet import Fleet
    from fleetdock.tasking.core import DevicePlan
    from fleetdock.tasking.device import DronePlan, RobotDogPlan
    from fleetdock.tasking.fleet import FleetPlan
    from fleetdock.tasking.models import TaskStepHookDecision, TaskStepHookEvent
    from fleetdock.tasking.parallel import ParallelPlan

class _PlanFactory:
    """计划编排器入口；可像函数一样调用，也提供并行计划组合入口。"""

    @overload
    def __call__(
        self,
        device: "Drone",
        *,
        interceptors: Sequence[Callable[["TaskStepHookEvent"], "TaskStepHookDecision | None"]] | None = None,
    ) -> "DronePlan":
        """为单架无人机创建计划编排器；调用 run() 前不会发送命令。"""
        ...

    @overload
    def __call__(
        self,
        device: "RobotDog",
        *,
        interceptors: Sequence[Callable[["TaskStepHookEvent"], "TaskStepHookDecision | None"]] | None = None,
    ) -> "RobotDogPlan":
        """为单台机器狗创建计划编排器；调用 run() 前不会发送命令。"""
        ...

    @overload
    def __call__(
        self,
        device: "BaseDevice",
        *,
        interceptors: Sequence[Callable[["TaskStepHookEvent"], "TaskStepHookDecision | None"]] | None = None,
    ) -> "DevicePlan":
        """为单个通用设备创建计划编排器；返回支持跨设备通用动作的 DevicePlan。"""
        ...

    @overload
    def __call__(
        self,
        device: "Fleet",
        *,
        interceptors: Sequence[Callable[["TaskStepHookEvent"], "TaskStepHookDecision | None"]] | None = None,
    ) -> "FleetPlan":
        """为 Fleet 创建批量计划编排器；每个步骤内会并发作用于 Fleet 成员。"""
        ...

    def __call__(
        self,
        device: Any,
        *,
        interceptors: Sequence[Callable[["TaskStepHookEvent"], "TaskStepHookDecision | None"]] | None = None,
    ) -> "DevicePlan | FleetPlan":
        """根据设备或 Fleet 创建对应的计划编排器。

        `device` 可传入单个 `Drone`、`RobotDog`、其它 `BaseDevice`，也可传入 `Fleet` 或筛选后的
        Fleet。该入口只声明计划对象，不会立即发送命令；返回值需要继续链式追加动作并调用
        `run()` 才会执行。`interceptors` 会在每个真实步骤执行前后触发；回调返回 `None` 表示只监听，
        返回 `TaskStepHookDecision` 可替换入参、替换结果、跳过步骤或中断任务。
        """
        from fleetdock.devices import Drone, RobotDog
        from fleetdock.fleet import Fleet
        from fleetdock.tasking.core import DevicePlan
        from fleetdock.tasking.device import DronePlan, RobotDogPlan
        from fleetdock.tasking.fleet import DroneFleetPlan, FleetPlan, RobotDogFleetPlan

        if isinstance(device, Fleet):
            devices = tuple(device)
            if devices and all(isinstance(item, Drone) for item in devices):
                return DroneFleetPlan(device, interceptors=interceptors)
            if devices and all(isinstance(item, RobotDog) for item in devices):
                return RobotDogFleetPlan(device, interceptors=interceptors)
            return FleetPlan(device, interceptors=interceptors)
        if isinstance(device, Drone):
            return DronePlan(device, interceptors=interceptors)
        if isinstance(device, RobotDog):
            return RobotDogPlan(device, interceptors=interceptors)
        return DevicePlan(device, interceptors=interceptors)

    @staticmethod
    def parallel(
        *plans: "DevicePlan | FleetPlan | ParallelPlan",
        failure_policy: str = "wait_all",
    ) -> "ParallelPlan":
        """创建并行计划组合器。

        ``plans`` 是要并发运行的子计划；``failure_policy="wait_all"`` 会等待全部子计划结束后汇总，
        ``"fail_fast"`` 会在首个失败后请求其它子计划协作取消并执行安全停止。返回 ``ParallelPlan``。
        """
        from fleetdock.tasking.parallel import ParallelPlan

        return ParallelPlan(plans, failure_policy=failure_policy)


Plan = _PlanFactory()


