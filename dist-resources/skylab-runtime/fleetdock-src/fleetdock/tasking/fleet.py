"""Fleet 批量任务执行器。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import time
from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence, cast

from fleetdock.tasking.core import DevicePlan, _TaskStep
from fleetdock.tasking.models import (
    FleetTaskResult, HeadingMode, MotionExecutionMode, RouteMotion, TaskCancellationToken, TaskCancelledError, TaskContext, TaskOptions, TaskResult, TaskStatus, TaskStepHookAction, UnlockStrategy,
    TaskStepResult,
)
from fleetdock.tasking.routes import Route
from fleetdock.models import CapabilityName

if TYPE_CHECKING:
    from fleetdock.fleet import Fleet

logger = logging.getLogger(__name__)

class FleetPlan(DevicePlan):
    """Fleet 通用批量计划编排器。

    FleetPlan 复用单设备 `Plan(device)` 的步骤语义：步骤之间串行，同一步内对目标成员并发
    执行；全部目标设备完成后才进入下一步。混合 Fleet 可通过 ``for_drones()`` 和
    ``for_robot_dogs()`` 切换设备类型作用域，子计划与根计划共享同一个步骤队列。
    """

    def __init__(
        self,
        fleet: "Fleet",
        interceptors: Sequence[Any] | None = None,
        *,
        root_plan: "FleetPlan | None" = None,
    ) -> None:
        """保存 Fleet、根计划和步骤 Hook。

        ``root_plan`` 仅供类型作用域子计划内部使用；子计划共享根计划的步骤队列、守卫和失败
        处理器，不会创建独立执行器。Hook 会同时用于 Fleet 级步骤和成员设备步骤。
        """
        super().__init__(fleet, interceptors=interceptors)
        self._fleet: Fleet = root_plan._fleet if root_plan is not None else fleet
        self._scope_fleet = fleet
        self._root_plan = root_plan or self
        if root_plan is not None:
            self._steps = root_plan._steps
            self._active_guards = root_plan._active_guards
            self._failed_handlers = root_plan._failed_handlers
            self._interceptors = root_plan._interceptors

    def for_drones(self) -> "DroneFleetPlan":
        """切换到无人机子计划并返回共享当前根队列的 ``DroneFleetPlan``。

        后续加入的无人机专属或通用动作只作用于当前 Fleet 中的无人机；该方法只声明作用域，
        不会立即发送命令。调用 ``root()`` 可返回根计划继续编排其它设备类型。
        """
        return DroneFleetPlan(self._fleet.drones(), root_plan=self._root_plan)

    def for_robot_dogs(self) -> "RobotDogFleetPlan":
        """切换到机器狗子计划并返回共享当前根队列的 ``RobotDogFleetPlan``。

        后续加入的机器狗专属或通用动作只作用于当前 Fleet 中的机器狗；该方法只声明作用域，
        不会立即发送命令。调用 ``root()`` 可返回根计划继续编排其它设备类型。
        """
        return RobotDogFleetPlan(self._fleet.robot_dogs(), root_plan=self._root_plan)

    def root(self) -> "FleetPlan":
        """返回当前链使用的根 ``FleetPlan``；根计划调用时返回自身。"""
        return self._root_plan

    def _add(self, name: str, **params: Any):
        target_devices = None if self is self._root_plan else tuple(self._scope_fleet)
        self._steps.append(_TaskStep(name=name, params=params, target_devices=target_devices))
        return self

    def run(
        self,
        step_timeout_s: float = 60.0,
        guard_interval_s: float = 0.2,
        *,
        _cancellation_token: Optional[TaskCancellationToken] = None,
    ) -> FleetTaskResult:
        """执行 Fleet 批量任务并返回每台设备的结果汇总。

        `step_timeout_s` 是每个步骤的默认最长等待时间，单位为秒；`guard_interval_s` 是状态确认
        和守卫检查间隔，单位为秒。步骤之间串行，同一步内对成员设备并发执行。返回值为
        `FleetTaskResult`，其中包含每台设备的单设备 `TaskResult`；``_cancellation_token`` 仅由
        ``TaskSession`` 内部传入并共享给全部成员步骤。
        """
        from fleetdock.fleet import device_public_id

        if self is not self._root_plan:
            return self._root_plan.run(
                step_timeout_s=step_timeout_s,
                guard_interval_s=guard_interval_s,
                _cancellation_token=_cancellation_token,
            )

        ctx = TaskContext(
            device=self._fleet,
            options=TaskOptions(),
            cancellation_token=_cancellation_token,
        )
        ctx.options.step_timeout_s = step_timeout_s
        device_results: dict[str, TaskResult] = {}
        step_index = 0
        if not self._fleet:
            return FleetTaskResult(True, TaskStatus.SUCCESS, "Fleet has no devices, no steps executed.")

        while self._steps:
            if ctx.cancellation_token is not None:
                try:
                    ctx.cancellation_token.raise_if_cancelled()
                except TaskCancelledError as exc:
                    return FleetTaskResult(
                        False,
                        TaskStatus.CANCELLED,
                        str(exc),
                        tuple(ctx.step_results),
                        device_results=device_results,
                        succeeded_devices=tuple(device_id for device_id, item in device_results.items() if item.ok),
                        failed_devices=tuple(device_id for device_id, item in device_results.items() if not item.ok),
                        failed_step=ctx.current_step,
                    )
            guard_result = self._check_guards(ctx)
            if guard_result is not None:
                return self._handle_fleet_failed(ctx, guard_result, device_results)

            step = self._steps.popleft()
            if step.name == "__guard__":
                self._active_guards.append(step.params["guard"])
                guard_result = self._check_guards(ctx)
                if guard_result is not None:
                    return self._handle_fleet_failed(ctx, guard_result, device_results)
                continue
            if step.name == "__options__":
                for name, value in step.params.items():
                    setattr(ctx.options, name, value)
                continue

            ctx.current_step = step.name
            step_index += 1
            started_at_s = time.monotonic()
            hook_decision, step = self._before_step_hook(step, ctx, step_index, scope="fleet")
            if hook_decision is not None:
                result = self._result_from_hook_decision(step, hook_decision, started_at_s)
                ctx.step_results.append(result)
                if hook_decision.action == TaskStepHookAction.SKIP:
                    continue
                task_result = TaskResult(False, TaskStatus.FAILED, result.message, tuple(ctx.step_results), failed_step=step.name)
                return self._handle_fleet_failed(ctx, task_result, device_results)
            target_count = len(step.target_devices) if step.target_devices is not None else len(self._fleet)
            logger.debug("Fleet 计划步骤开始: step=%s device_count=%d", step.name, target_count)
            step_device_results = self._run_step_for_devices(step, ctx, guard_interval_s)
            device_results.update(step_device_results)
            if (
                ctx.cancellation_token is not None and ctx.cancellation_token.cancelled
            ) or any(result.status is TaskStatus.CANCELLED for result in step_device_results.values()):
                return FleetTaskResult(
                    False,
                    TaskStatus.CANCELLED,
                    "Fleet task cancelled by caller.",
                    tuple(ctx.step_results),
                    device_results=device_results,
                    succeeded_devices=tuple(device_id for device_id, item in device_results.items() if item.ok),
                    failed_devices=tuple(device_id for device_id, item in device_results.items() if not item.ok),
                    failed_step=step.name,
                )
            failed = {device_id: result for device_id, result in step_device_results.items() if not result.ok}
            finished_at_s = time.monotonic()
            if failed:
                message = "; ".join(f"{device_id}: {result.message}" for device_id, result in failed.items())
                step_result = self._after_step_hook(
                    step,
                    ctx,
                    step_index,
                    TaskStepResult(step.name, False, message, started_at_s=started_at_s, finished_at_s=finished_at_s),
                    scope="fleet",
                )
                ctx.step_results.append(step_result)
                if step_result.ok:
                    continue
                task_result = TaskResult(
                    False,
                    TaskStatus.FAILED,
                    step_result.message,
                    tuple(ctx.step_results),
                    failed_step=step.name,
                )
                return self._handle_fleet_failed(ctx, task_result, device_results)

            step_result = self._after_step_hook(
                step,
                ctx,
                step_index,
                TaskStepResult(
                    step.name,
                    True,
                    f"Fleet step completed for {len(step_device_results)} devices.",
                    started_at_s=started_at_s,
                    finished_at_s=finished_at_s,
                ),
                scope="fleet",
            )
            ctx.step_results.append(step_result)
            logger.debug("Fleet 计划步骤结束: step=%s ok=True", step.name)
            if not step_result.ok:
                task_result = TaskResult(
                    False,
                    TaskStatus.FAILED,
                    step_result.message,
                    tuple(ctx.step_results),
                    failed_step=step.name,
                )
                return self._handle_fleet_failed(ctx, task_result, device_results)

        device_ids = tuple(device_public_id(device) for device in self._fleet)
        return FleetTaskResult(
            True,
            TaskStatus.SUCCESS,
            "Fleet task completed.",
            tuple(ctx.step_results),
            device_results=device_results,
            succeeded_devices=device_ids,
            failed_devices=(),
        )

    def _run_step_for_devices(
        self,
        step: _TaskStep,
        ctx: TaskContext,
        guard_interval_s: float,
    ) -> dict[str, TaskResult]:
        from fleetdock.fleet import device_public_id

        results: dict[str, TaskResult] = {}
        devices = step.target_devices if step.target_devices is not None else tuple(self._fleet)
        max_workers = max(1, len(devices))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="fleetdock-fleet-task") as executor:
            future_map = {
                executor.submit(
                    self._run_single_device_step,
                    device,
                    step,
                    ctx.options,
                    guard_interval_s,
                    self._interceptors,
                    ctx.cancellation_token,
                ): device
                for device in devices
            }
            for future in as_completed(future_map):
                device = future_map[future]
                device_id = device_public_id(device)
                try:
                    results[device_id] = future.result()
                except Exception as exc:
                    results[device_id] = TaskResult(False, TaskStatus.FAILED, str(exc), failed_step=step.name)
        return results

    @staticmethod
    def _run_single_device_step(
        device: Any,
        step: _TaskStep,
        options: TaskOptions,
        guard_interval_s: float,
        interceptors: Sequence[Any],
        cancellation_token: Optional[TaskCancellationToken],
    ) -> TaskResult:
        from fleetdock.tasking.plans import Plan

        task = Plan(device, interceptors=interceptors)
        task._steps.append(_TaskStep("__options__", dict(vars(options))))
        task._steps.append(_TaskStep(step.name, step.params))
        return cast(TaskResult, task.run(
            step_timeout_s=options.step_timeout_s,
            guard_interval_s=guard_interval_s,
            _cancellation_token=cancellation_token,
        ))

    def _handle_fleet_failed(
        self,
        ctx: TaskContext,
        result: TaskResult,
        device_results: Mapping[str, TaskResult],
    ) -> FleetTaskResult:
        handled = self._handle_failed(ctx, result)
        succeeded = tuple(device_id for device_id, item in device_results.items() if item.ok)
        failed = tuple(device_id for device_id, item in device_results.items() if not item.ok)
        return FleetTaskResult(
            handled.ok,
            handled.status,
            handled.message,
            handled.steps,
            device_results=device_results,
            succeeded_devices=succeeded,
            failed_devices=failed,
            failed_step=handled.failed_step,
            triggered_guard=handled.triggered_guard,
            completion_reason=handled.completion_reason,
            safety_action=handled.safety_action,
            cleanup_result=handled.cleanup_result,
        )


class DroneFleetPlan(FleetPlan):
    """无人机 Fleet 批量计划编排器，方法名和入参与 ``DronePlan`` 保持一致。"""

    def unlock(self, strategy: UnlockStrategy = UnlockStrategy.STANDARD) -> "DroneFleetPlan":
        """按 ``strategy`` 把全部无人机解锁动作加入队列并返回当前计划。

        强制策略会对每台成员显式请求适配器强制解锁，安全边界与 ``DronePlan.unlock`` 相同。
        """
        if not isinstance(strategy, UnlockStrategy):
            strategy = UnlockStrategy(strategy)
        return self._add("unlock", strategy=strategy)

    def lock(self) -> "DroneFleetPlan":
        """把全部无人机锁定动作加入计划队列，返回当前 DroneFleetPlan。"""
        return self._add("lock")

    def takeoff(self, altitude: float = 5.0) -> "DroneFleetPlan":
        """把全部无人机自动适配起飞动作加入计划队列。

        ``altitude`` 是相对各自起飞地面的目标高度（米）；每个成员由其适配器独立选择底层链路，
        业务代码无需按品牌分支。建议此前调用 ``wait_until_ready()`` 形成全成员就绪屏障。
        返回当前计划。
        """
        if altitude <= 0:
            raise ValueError("takeoff altitude must be greater than 0.")
        return self._add("takeoff", altitude=altitude)

    def wait_until_ready(
        self,
        timeout_s: float = 60.0,
        *,
        require_global_position: bool = False,
        require_armable: bool = False,
        capability: CapabilityName = CapabilityName.TAKEOFF,
        progress_callback: Optional[Any] = None,
    ) -> "DroneFleetPlan":
        """把全部无人机起飞前就绪等待加入计划队列。

        ``timeout_s`` 是最长等待时间，单位为秒。默认让每个成员按自身适配器选择健康的本地或
        全球定位链路；``require_global_position=True`` 可为室外任务强制全部成员具备全球定位，
        ``require_armable=True`` 可额外等待可解锁标志。``capability`` 选择目标标准能力；``progress_callback`` 分别收到成员设备
        的 ``ReadinessReport``。返回当前 DroneFleetPlan。
        """
        return self._add(
            "wait_until_ready",
            timeout_s=timeout_s,
            require_global_position=require_global_position,
            require_armable=require_armable,
            capability=capability,
            progress_callback=progress_callback,
        )

    def land(self) -> "DroneFleetPlan":
        """把全部无人机降落动作加入计划队列，返回当前 DroneFleetPlan。"""
        return self._add("land")

    def ensure_land(self, *, timeout_s: float = 120.0) -> "DroneFleetPlan":
        """把全部成员的必执行降落加入计划并返回当前计划。

        正常路径并发执行并逐机确认降落；此前步骤出现部分或全部失败时，会对当前无人机作用域启动
        独立降落计划，并把 Fleet 级结果写入 ``cleanup_result``。``timeout_s`` 是每台成员的最长
        降落确认秒数且必须大于零。该入口不要求 GPS 或 Home，适用于室内外统一收尾。
        """
        if timeout_s <= 0:
            raise ValueError("ensure_land timeout must be greater than 0.")

        def land_after_failure(_context: Any, _result: TaskResult) -> TaskResult:
            return DroneFleetPlan(self._scope_fleet).land().run(step_timeout_s=timeout_s)

        self.on_failed(land_after_failure)
        return self.land()

    def return_to_launch(self) -> "DroneFleetPlan":
        """把全部无人机返航动作加入计划队列，返回当前 DroneFleetPlan。"""
        return self._add("return_to_launch")

    def ensure_return_to_launch(self, *, timeout_s: float = 240.0) -> "DroneFleetPlan":
        """把全部成员的必执行返航加入计划并返回当前计划。

        正常路径并发触发 RTL 并逐机等待落地或上锁；此前步骤失败时，会对当前无人机作用域启动
        独立返航计划并把 Fleet 结果写入 ``cleanup_result``。``timeout_s`` 是每台成员的最长确认
        秒数且必须大于零；该入口要求各成员具备有效全球位置和 Home，室内无 GPS 场景应使用
        ``ensure_land()``。
        """
        if timeout_s <= 0:
            raise ValueError("ensure_return_to_launch timeout must be greater than 0.")

        def return_after_failure(_context: Any, _result: TaskResult) -> TaskResult:
            return DroneFleetPlan(self._scope_fleet).return_to_launch().run(step_timeout_s=timeout_s)

        self.on_failed(return_after_failure)
        return self.return_to_launch()

    def hover(self) -> "DroneFleetPlan":
        """把全部无人机悬停动作加入计划队列，返回当前 DroneFleetPlan。"""
        return self._add("hover")

    def route(
        self,
        route: Route,
        *,
        altitude: Optional[float] = None,
        speed: Optional[float] = None,
        heading: Optional[HeadingMode] = None,
        motion: RouteMotion | MotionExecutionMode | str | None = None,
        max_duration_s: Optional[float] = None,
        timeout_s: Optional[float] = None,
    ) -> "DroneFleetPlan":
        """把同一条航线加入全部无人机计划队列。

        `route` 是待执行的标准航线；`altitude`、`speed` 和 `heading` 可覆盖任务默认高度、速度
        和朝向策略，单位分别为米、米/秒和 `HeadingMode`；``motion`` 只影响当前航线的
        原生/SDK 执行策略和平滑参数；``max_duration_s`` 是每台无人机
        达到后正常停止的最长执行秒数，不等同于失败超时；``timeout_s`` 可覆盖这一步的异常
        超时秒数。返回当前 DroneFleetPlan。
        """
        if max_duration_s is not None and max_duration_s <= 0:
            raise ValueError("max_duration_s must be greater than 0.")
        if timeout_s is not None and timeout_s <= 0:
            raise ValueError("timeout_s must be greater than 0.")
        return self._add(
            "route",
            route=route,
            altitude=altitude,
            speed=speed,
            heading=heading,
            motion=self._normalize_route_motion(motion),
            max_duration_s=max_duration_s,
            timeout_s=timeout_s,
        )

    def routes(
        self,
        routes: Sequence[Route],
        *,
        altitude: Optional[float] = None,
        speed: Optional[float] = None,
        heading: Optional[HeadingMode] = None,
        motion: RouteMotion | MotionExecutionMode | str | None = None,
        max_duration_s: Optional[float] = None,
        timeout_s: Optional[float] = None,
    ) -> "DroneFleetPlan":
        """把多条航线按顺序加入全部无人机计划队列。

        `routes` 中每条航线都会按传入顺序追加；`altitude`、`speed` 和 `heading` 为可选默认
        覆盖参数；``motion`` 会应用到本次批量加入的每条航线；``max_duration_s`` 是每条航线各自的最长正常执行秒数，``timeout_s`` 是每条
        航线各自的异常超时秒数。返回当前
        DroneFleetPlan。
        """
        if max_duration_s is not None and max_duration_s <= 0:
            raise ValueError("max_duration_s must be greater than 0.")
        if timeout_s is not None and timeout_s <= 0:
            raise ValueError("timeout_s must be greater than 0.")
        route_motion = self._normalize_route_motion(motion)
        for route in routes:
            self._add(
                "route",
                route=route,
                altitude=altitude,
                speed=speed,
                heading=heading,
                motion=route_motion,
                max_duration_s=max_duration_s,
                timeout_s=timeout_s,
            )
        return self


class RobotDogFleetPlan(FleetPlan):
    """机器狗 Fleet 批量计划编排器，方法名和入参与 ``RobotDogPlan`` 保持一致。"""

    def stand(self) -> "RobotDogFleetPlan":
        """把全部机器狗站立动作加入计划队列，返回当前 RobotDogFleetPlan。"""
        return self._add("stand")

    def sit(self) -> "RobotDogFleetPlan":
        """把全部机器狗坐下动作加入计划队列，返回当前 RobotDogFleetPlan。"""
        return self._add("sit")

    def route(
        self,
        route: Route,
        *,
        speed: Optional[float] = None,
        heading: Optional[HeadingMode] = None,
        motion: RouteMotion | MotionExecutionMode | str | None = None,
        max_duration_s: Optional[float] = None,
        timeout_s: Optional[float] = None,
    ) -> "RobotDogFleetPlan":
        """把同一条二维本地路线加入全部机器狗计划队列并返回当前计划。

        ``route`` 的航点必须是本地二维 x/y 米制坐标；``speed`` 为可选默认速度，单位为米/秒；
        ``heading`` 控制朝向策略；``motion`` 只影响当前路线的执行策略和平滑参数；``max_duration_s`` 是正常执行上限秒数，``timeout_s`` 是异常
        超时秒数。参数语义与 ``RobotDogPlan.route`` 一致；含高度的航线会在声明阶段被拒绝。
        """
        if any(waypoint.z_m is not None or waypoint.altitude_m is not None for waypoint in route.waypoints):
            raise ValueError("RobotDog routes must be two-dimensional and cannot contain altitude.")
        if max_duration_s is not None and max_duration_s <= 0:
            raise ValueError("max_duration_s must be greater than 0.")
        if timeout_s is not None and timeout_s <= 0:
            raise ValueError("timeout_s must be greater than 0.")
        return self._add(
            "route",
            route=route,
            altitude=None,
            speed=speed,
            heading=heading,
            motion=self._normalize_route_motion(motion),
            max_duration_s=max_duration_s,
            timeout_s=timeout_s,
        )


