"""无人机和机器狗专用计划声明器。"""

from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from fleetdock.avoidance import AvoidancePolicy
from fleetdock.models import CapabilityName
from fleetdock.movement import GpsPoint, RelativePoint3D
from fleetdock.tasking.core import DevicePlan
from fleetdock.tasking.models import HeadingMode, MotionExecutionMode, RouteMotion, TaskResult, UnlockStrategy
from fleetdock.tasking.routes import Route

class DronePlan(DevicePlan):
    """无人机专用计划编排器，提供起飞、返航、航线等无人机动作。"""

    def unlock(self, strategy: UnlockStrategy = UnlockStrategy.STANDARD) -> "DronePlan":
        """把无人机标准解锁动作加入计划队列。

        ``strategy`` 默认只执行标准解锁；``FORCE`` 和 ``STANDARD_THEN_FORCE`` 都会显式请求
        适配器的强制解锁能力，可能绕过厂商部分预检，仅应在调用方确认风险后使用。返回当前计划链。
        """
        if not isinstance(strategy, UnlockStrategy):
            strategy = UnlockStrategy(strategy)
        return self._add("unlock", strategy=strategy)

    def lock(self) -> "DronePlan":
        """把无人机锁定动作加入计划队列并返回当前计划链。"""
        return self._add("lock")

    def takeoff(self, altitude: float = 5.0) -> "DronePlan":
        """把自动适配的起飞动作加入队列并返回当前计划链。

        ``altitude`` 是相对起飞地面的目标高度（米）。执行时 SDK 自动完成预检、标准解锁、
        控制链路选择和物理高度确认；开发者无需判断 PX4、MAVSDK 或其他厂商实现，也无需先调用
        ``unlock()``。已有显式 ``unlock().takeoff()`` 调用仍保持兼容。
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
        progress_callback: Optional[Callable[[Any], None]] = None,
    ) -> "DronePlan":
        """把起飞前就绪等待加入队列并返回当前计划链。

        ``timeout_s`` 是最长等待秒数。默认起飞检查允许健康的本地定位或全球定位链路择一通过；
        ``require_global_position=True`` 可为室外任务额外强制全球定位，``require_armable=True`` 可
        额外等待飞控可解锁标志；``capability`` 选择目标标准能力，默认检查起飞；
        ``progress_callback`` 在每次检查后收到最新 ``ReadinessReport``。
        """
        return self._add(
            "wait_until_ready",
            timeout_s=timeout_s,
            require_global_position=require_global_position,
            require_armable=require_armable,
            capability=capability,
            progress_callback=progress_callback,
        )

    def land(self) -> "DronePlan":
        """把降落动作加入计划队列并返回当前计划链。"""
        return self._add("land")

    def ensure_land(self, *, timeout_s: float = 120.0) -> "DronePlan":
        """把必执行降落加入计划并返回当前计划链。

        正常路径会执行并确认降落；此前任一步骤失败、超时、取消或被安全策略阻断时，失败处理器会
        启动独立降落计划，并把结果写入 ``TaskResult.cleanup_result``。``timeout_s`` 是降落确认的
        最长秒数且必须大于零。该入口不依赖 GPS 或 Home，适合作为仿真、室内和室外共用 Demo 的
        安全收尾；需要返回起飞点时应显式使用 ``ensure_return_to_launch()``。
        """
        if timeout_s <= 0:
            raise ValueError("ensure_land timeout must be greater than 0.")

        def land_after_failure(_context: Any, _result: TaskResult) -> TaskResult:
            return DronePlan(self._device).land().run(step_timeout_s=timeout_s)

        self.on_failed(land_after_failure)
        return self.land()

    def return_to_launch(self) -> "DronePlan":
        """把返航动作加入计划队列并返回当前计划链。"""
        return self._add("return_to_launch")

    def ensure_return_to_launch(self, *, timeout_s: float = 240.0) -> "DronePlan":
        """把必执行返航加入计划并返回当前计划链。

        正常路径会在此前步骤成功后执行并等待标准返航确认；此前任一步骤失败、超时或被避障阻断时，
        失败处理器会启动独立返航计划并把结果写入 ``TaskResult.cleanup_result``。``timeout_s`` 是返航
        确认的最长秒数且必须大于零。该方法不会把主航线失败改写为成功，调用方可分别检查主结果和清理结果。
        """

        if timeout_s <= 0:
            raise ValueError("ensure_return_to_launch timeout must be greater than 0.")

        def return_after_failure(_context: Any, _result: TaskResult) -> TaskResult:
            return DronePlan(self._device).return_to_launch().run(step_timeout_s=timeout_s)

        self.on_failed(return_after_failure)
        return self.return_to_launch()

    def hover(self) -> "DronePlan":
        """把悬停动作加入计划队列并返回当前计划链。"""
        return self._add("hover")

    def goto_global(
        self,
        latitude_deg: float,
        longitude_deg: float,
        altitude_m: float,
        yaw_deg: float = 0.0,
    ) -> "DronePlan":
        """通过标准 ``MOVE`` 步骤加入全球航点并返回当前计划链。

        ``latitude_deg``、``longitude_deg``、``yaw_deg`` 单位为度，``altitude_m`` 为 AMSL 米；
        声明阶段不会发送命令，执行时等价于 ``move(GpsPoint(...))``。
        """
        return self.move(GpsPoint(latitude_deg, longitude_deg, altitude_m, yaw_deg))

    def goto_local(self, x_m: float, y_m: float, z_m: float) -> "DronePlan":
        """通过标准 ``MOVE`` 步骤加入任务局部航点并返回当前计划链。

        ``x_m/y_m/z_m`` 单位为米，x/y 分别对应任务局部北向和东向，z 轴向上为正；
        声明阶段不会发送命令，执行时等价于
        ``move(RelativePoint3D(...))``。
        """
        return self.move(RelativePoint3D(x_m, y_m, z_m))

    def route(
        self,
        route: Route,
        *,
        altitude: Optional[float] = None,
        speed: Optional[float] = None,
        heading: Optional[HeadingMode] = None,
        motion: RouteMotion | MotionExecutionMode | str | None = None,
        avoidance: AvoidancePolicy | None = None,
        max_duration_s: Optional[float] = None,
        timeout_s: Optional[float] = None,
    ) -> "DronePlan":
        """把 ``route`` 加入队列并返回当前计划链。

        ``altitude``（米）、``speed``（米/秒）、``heading`` 可覆盖任务默认值；``motion`` 可指定
        当前航线的原生/SDK 执行策略、加减速度、前视距离和稳定判定参数，不会影响后续航线；
        ``avoidance`` 可为当前航线指定避障策略，阻断模式会触发悬停/停止等安全动作，局部绕行模式
        会在 SDK route 执行器中插入临时绕行点；
        ``max_duration_s`` 是可选最长正常执行秒数，达到后安全停止并把步骤记为成功，它不同于
        异常超时；``timeout_s`` 可为当前航线单独覆盖 ``run(step_timeout_s=...)``，通常应大于
        ``max_duration_s``。航线提前飞完时仍会立即成功结束。执行期间发布
        标准航线和航点事件。返回当前计划链，参数非法时抛出 ``ValueError``。
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
            avoidance=avoidance,
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
        avoidance: AvoidancePolicy | None = None,
        max_duration_s: Optional[float] = None,
        timeout_s: Optional[float] = None,
    ) -> "DronePlan":
        """把多条航线按传入顺序加入计划队列。

        该方法只在计划声明阶段追加步骤，不会立即控制设备飞行；真正执行发生在
        run() 被调用时。routes 中的每个 Route 会依次按 Plan(drone).route(...)
        的语义执行；altitude、speed 和 heading 为可选覆盖参数，分别表示默认
        飞行高度（米）、速度（m/s）和朝向模式；``motion`` 会应用到本次批量加入的每条航线；
        ``max_duration_s`` 是每条航线各自的最长
        正常执行秒数，``timeout_s`` 可覆盖每条航线的异常超时秒数。返回当前 DronePlan，便于继续链式
        添加返航、降落等后续步骤。``avoidance`` 会应用到本次批量加入的每条航线。
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
                avoidance=avoidance,
                max_duration_s=max_duration_s,
                timeout_s=timeout_s,
            )
        return self

class RobotDogPlan(DevicePlan):
    """机器狗专用计划编排器，提供站立、坐下等足式设备动作。"""

    def stand(self) -> "RobotDogPlan":
        """把机器狗站立动作加入计划队列并返回当前计划链。"""
        return self._add("stand")

    def sit(self) -> "RobotDogPlan":
        """把机器狗坐下动作加入计划队列并返回当前计划链。"""
        return self._add("sit")

    def route(
        self,
        route: Route,
        *,
        speed: Optional[float] = None,
        heading: Optional[HeadingMode] = None,
        motion: RouteMotion | MotionExecutionMode | str | None = None,
        avoidance: AvoidancePolicy | None = None,
        max_duration_s: Optional[float] = None,
        timeout_s: Optional[float] = None,
    ) -> "RobotDogPlan":
        """加入足式设备二维本地路线并返回当前计划链。

        ``route`` 的每个航点必须提供本地 x/y 米制坐标；``speed`` 为米/秒，``heading`` 控制朝向，
        ``motion`` 只影响当前路线的原生/SDK 执行策略和平滑参数；``avoidance`` 可指定二维避障策略，
        风险时通常映射为停止或急停；``max_duration_s`` 是正常执行上限，``timeout_s`` 是异常超时。设备可使用厂商原生导航或
        FleetDock SDK 速度控制，不支持路线能力时执行结果会明确失败。
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
            avoidance=avoidance,
            max_duration_s=max_duration_s,
            timeout_s=timeout_s,
        )
