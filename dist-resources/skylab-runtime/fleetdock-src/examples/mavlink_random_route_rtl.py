"""MAVSDK-Python 随机航线返航教学 Demo。

流程：
1. 连接 MAVSDK。
2. 解锁、起飞，并确认本地高度超过 10 米。
3. 以当前位置为中心，用 Offboard 随机飞一个几何图形。
4. 发送 RTL(return to launch) 返航。

运行前请先启动：
- Project AirSim Neighborhood，并加载 PX4 SITL 场景和 Drone1
- WSL Ubuntu-24.04 中的 PX4 SITL: make px4_sitl none_iris
"""

from __future__ import annotations

import asyncio
import math
import os
import random
import sys
import time
from dataclasses import dataclass
from typing import Any, Optional


# ===== 教学配置区：一般只需要改这里 =====

MAVSDK_ENDPOINT = "udpin://0.0.0.0:14555"

MIN_ROUTE_ALTITUDE_M = 14
TAKEOFF_ALTITUDE_M = 14.5
SHAPE_MIN_RADIUS_M = 20.0
SHAPE_MAX_RADIUS_M = 35.0
CIRCLE_POINT_COUNT = 20
WAYPOINT_TOLERANCE_M = 1.5
WAYPOINT_TIMEOUT_S = 45.0
WAYPOINT_HOLD_S = 1.0
RANDOM_SEED: Optional[int] = None

MAVSDK_CONNECT_TIMEOUT_S = 30.0
HEALTH_OBSERVE_SECONDS = 30.0
TAKEOFF_ALTITUDE_TIMEOUT_S = 60.0

CONDA_NAME = os.environ.get("CONDA_NAME", "fleetdock")


@dataclass(frozen=True)
class RoutePoint:
    """教学航线的单个本地 NED 目标点，位置单位为米、偏航角单位为度。"""
    north_offset_m: float
    east_offset_m: float


def log(message: str) -> None:
    """只打印到控制台，避免教学 demo 额外生成日志文件。"""

    encoding = sys.stdout.encoding or "utf-8"
    safe_message = message.encode(encoding, errors="replace").decode(encoding, errors="replace")
    safe_message = safe_message.replace("\x00", "")
    print(safe_message, flush=True)


def import_mavsdk() -> Any:
    """导入并返回 MAVSDK ``System`` 类型；缺少依赖时给出中文安装提示。"""
    try:
        from mavsdk import System
    except ImportError:
        print(
            f"未安装 MAVSDK-Python。请在 {CONDA_NAME} conda 环境中运行：python -m pip install mavsdk",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return System


async def wait_mavsdk_connected(drone: Any) -> None:
    """等待 MAVSDK 收到连接状态；超过配置时间时抛出超时异常。"""
    log("等待 MAVSDK 车辆连接...")

    async def wait_loop() -> None:
        """消费 MAVSDK 连接状态流，首次连接成功后返回。"""
        async for state in drone.core.connection_state():
            if state.is_connected:
                log("MAVSDK 已连接车辆。")
                return

    await asyncio.wait_for(wait_loop(), timeout=MAVSDK_CONNECT_TIMEOUT_S)


async def observe_health(drone: Any) -> None:
    """观察 health，但不把 global position 作为本地 Offboard 飞行的硬条件。"""

    log(f"观察 MAVSDK health，最多 {HEALTH_OBSERVE_SECONDS:.1f} 秒...")
    deadline = time.monotonic() + HEALTH_OBSERVE_SECONDS
    health_stream = drone.telemetry.health()
    last_print_s = 0.0

    while time.monotonic() < deadline:
        try:
            health = await asyncio.wait_for(health_stream.__anext__(), timeout=1.0)
        except (asyncio.TimeoutError, StopAsyncIteration):
            return

        now_s = time.monotonic()
        if now_s - last_print_s >= 1.0:
            log(
                "MAVSDK health："
                f"local={health.is_local_position_ok}, "
                f"global={health.is_global_position_ok}, "
                f"home={health.is_home_position_ok}"
            )
            last_print_s = now_s

        if health.is_local_position_ok and health.is_home_position_ok:
            log("本地定位和 home position 已就绪。")
            return

    log("health 未完全 ready，继续执行本地 Offboard 飞行。")


async def set_takeoff_altitude_if_possible(drone: Any) -> None:
    """设置略高于最低航线高度的起飞目标。"""

    try:
        await drone.action.set_takeoff_altitude(TAKEOFF_ALTITUDE_M)
        log(f"起飞高度设置为 {TAKEOFF_ALTITUDE_M:.1f} m。")
    except Exception as err:
        raise RuntimeError(f"无法设置起飞高度，已停止飞行：{err}") from err


async def arm(drone: Any) -> None:
    """等待飞控可解锁后发送标准 arm；失败时保留原始 MAVSDK 异常。"""
    log("解锁无人机...")
    try:
        await drone.action.arm()
    except Exception as err:
        log(f"MAVSDK 普通解锁失败，尝试 SDK 强制解锁：{err}")
        force_arm = getattr(drone.action, "arm_force", None)
        if not callable(force_arm):
            raise RuntimeError("当前 MAVSDK 客户端不支持 arm_force，无法继续强制解锁。") from err
        await force_arm()


async def takeoff(drone: Any) -> None:
    """发送起飞命令并等待相对高度达到教学配置目标。"""
    from mavsdk.offboard import PositionNedYaw, VelocityNedYaw

    async def read_local_position() -> tuple[float, float, float]:
        """读取一次本地 NED 位置并返回 north/east/down 米数。"""
        async for position_velocity in drone.telemetry.position_velocity_ned():
            position = position_velocity.position
            return position.north_m, position.east_m, position.down_m
        raise RuntimeError("未收到本地 NED 位置。")

    try:
        start_north_m, start_east_m, start_down_m = await asyncio.wait_for(
            read_local_position(), timeout=5.0
        )
    except TimeoutError as err:
        raise RuntimeError("起飞前未收到本地高度，已取消起飞。") from err

    log("起飞...")
    await drone.action.takeoff()

    # 位置目标会把上升限制在指定高度，即使后续遥测中断也不会无限爬升。
    target_down_m = start_down_m - TAKEOFF_ALTITUDE_M
    await drone.offboard.set_position_ned(
        PositionNedYaw(start_north_m, start_east_m, target_down_m, 0.0)
    )
    await drone.offboard.start()

    async def wait_until_high_enough() -> None:
        """等待本地高度达到目标容差范围。"""
        last_log_time = 0.0
        async for position_velocity in drone.telemetry.position_velocity_ned():
            altitude_m = start_down_m - position_velocity.position.down_m
            now = time.monotonic()
            if now - last_log_time >= 1.0:
                log(f"当前相对起飞高度 {altitude_m:.2f} m，目标 {TAKEOFF_ALTITUDE_M:.1f} m。")
                last_log_time = now
            if altitude_m > MIN_ROUTE_ALTITUDE_M:
                log(f"相对起飞高度已达 {altitude_m:.2f} m，开始执行后续航线。")
                return

    log(f"使用有界位置目标上升到 {TAKEOFF_ALTITUDE_M:.1f} m...")
    try:
        await asyncio.wait_for(wait_until_high_enough(), timeout=TAKEOFF_ALTITUDE_TIMEOUT_S)
    except Exception as err:
        log(f"起飞高度检测失败，立即停止上升并降落：{err}")
        try:
            await drone.offboard.set_velocity_ned(VelocityNedYaw(0.0, 0.0, 0.0, 0.0))
            await drone.offboard.stop()
        finally:
            await drone.action.land()
        raise RuntimeError("起飞未完成，已取消后续航线。") from err


def build_random_shape_route() -> tuple[str, list[RoutePoint]]:
    """随机生成一种教学几何航线，并返回名称和本地 NED 目标点。"""
    rng = random.Random(RANDOM_SEED)
    shape_name = rng.choice(("圆形", "三角形", "正方形", "矩形", "梯形"))
    radius_m = rng.uniform(SHAPE_MIN_RADIUS_M, SHAPE_MAX_RADIUS_M)

    if shape_name == "圆形":
        raw_points = [
            (radius_m * math.cos(math.tau * index / CIRCLE_POINT_COUNT),
             radius_m * math.sin(math.tau * index / CIRCLE_POINT_COUNT))
            for index in range(CIRCLE_POINT_COUNT)
        ]
    elif shape_name == "三角形":
        raw_points = [
            (radius_m * math.cos(math.tau * index / 3), radius_m * math.sin(math.tau * index / 3))
            for index in range(3)
        ]
    elif shape_name == "正方形":
        half_side_m = radius_m / math.sqrt(2.0)
        raw_points = [
            (half_side_m, half_side_m),
            (-half_side_m, half_side_m),
            (-half_side_m, -half_side_m),
            (half_side_m, -half_side_m),
        ]
    elif shape_name == "矩形":
        half_height_m = radius_m * rng.uniform(0.5, 0.75)
        raw_points = [
            (radius_m, half_height_m),
            (-radius_m, half_height_m),
            (-radius_m, -half_height_m),
            (radius_m, -half_height_m),
        ]
    else:
        half_height_m = radius_m * 0.7
        half_top_m = radius_m * rng.uniform(0.45, 0.7)
        raw_points = [
            (half_top_m, half_height_m),
            (-half_top_m, half_height_m),
            (-radius_m, -half_height_m),
            (radius_m, -half_height_m),
        ]

    rotation_rad = rng.uniform(0.0, math.tau)
    cos_rotation = math.cos(rotation_rad)
    sin_rotation = math.sin(rotation_rad)
    points = [
        RoutePoint(
            north_offset_m=north_m * cos_rotation - east_m * sin_rotation,
            east_offset_m=north_m * sin_rotation + east_m * cos_rotation,
        )
        for north_m, east_m in raw_points
    ]
    if rng.choice((False, True)):
        points.reverse()
    start_index = rng.randrange(len(points))
    points = points[start_index:] + points[:start_index]
    points.append(points[0])

    direction = "顺时针" if _signed_area(points) < 0.0 else "逆时针"
    description = (
        f"{shape_name}，半径/半尺寸 {radius_m:.1f} m，"
        f"旋转 {math.degrees(rotation_rad):.1f}°，{direction}"
    )
    return description, points


def _signed_area(points: list[RoutePoint]) -> float:
    return sum(
        current.east_offset_m * following.north_offset_m
        - following.east_offset_m * current.north_offset_m
        for current, following in zip(points, points[1:])
    )


async def fly_random_shape_route(drone: Any) -> None:
    """以当前位置为中心，飞行随机几何图形。"""

    from mavsdk.offboard import PositionNedYaw, VelocityNedYaw

    await drone.offboard.set_velocity_ned(VelocityNedYaw(0.0, 0.0, 0.0, 0.0))

    async def read_local_position() -> tuple[float, float, float]:
        """读取一次本地 NED 位置，供航点到达判断使用。"""
        async for position_velocity in drone.telemetry.position_velocity_ned():
            position = position_velocity.position
            return position.north_m, position.east_m, position.down_m
        raise RuntimeError("未收到本地 NED 位置。")

    center_north_m, center_east_m, route_down_m = await asyncio.wait_for(
        read_local_position(), timeout=5.0
    )
    description, route_points = build_random_shape_route()
    route_points.append(RoutePoint(0.0, 0.0))
    log(
        f"随机航线：{description}；中心 NED="
        f"({center_north_m:.1f}, {center_east_m:.1f}, {route_down_m:.1f})"
    )

    for index, point in enumerate(route_points, start=1):
        target_north_m = center_north_m + point.north_offset_m
        target_east_m = center_east_m + point.east_offset_m
        current_north_m, current_east_m, _ = await asyncio.wait_for(read_local_position(), timeout=5.0)
        yaw_deg = math.degrees(
            math.atan2(target_east_m - current_east_m, target_north_m - current_north_m)
        ) % 360.0
        await drone.offboard.set_position_ned(
            PositionNedYaw(target_north_m, target_east_m, route_down_m, yaw_deg)
        )
        log(
            f"航点 {index}/{len(route_points)}: "
            f"north={target_north_m:.1f} m, east={target_east_m:.1f} m, yaw={yaw_deg:.1f}°"
        )

        async def wait_until_reached() -> None:
            """持续检查当前位置，进入航点水平和垂直容差后返回。"""
            async for position_velocity in drone.telemetry.position_velocity_ned():
                position = position_velocity.position
                distance_m = math.hypot(
                    target_north_m - position.north_m,
                    target_east_m - position.east_m,
                )
                if distance_m <= WAYPOINT_TOLERANCE_M:
                    return

        try:
            await asyncio.wait_for(wait_until_reached(), timeout=WAYPOINT_TIMEOUT_S)
        except TimeoutError as err:
            raise RuntimeError(f"航点 {index} 在 {WAYPOINT_TIMEOUT_S:.0f} 秒内未到达。") from err
        await asyncio.sleep(WAYPOINT_HOLD_S)

    log("几何图形已闭合并回到航线中心，停止 Offboard，准备返航。")
    await drone.offboard.stop()


async def fly_with_mavsdk() -> None:
    """使用原生 MAVSDK 执行教学航线并在 finally 中请求返航。"""
    System = import_mavsdk()
    drone = System()
    log(f"连接 MAVSDK: {MAVSDK_ENDPOINT}")
    await drone.connect(system_address=MAVSDK_ENDPOINT)

    await wait_mavsdk_connected(drone)
    await observe_health(drone)
    await set_takeoff_altitude_if_possible(drone)
    await arm(drone)
    await takeoff(drone)
    try:
        await fly_random_shape_route(drone)
    finally:
        log("发送 RTL 返航命令。")
        try:
            await drone.action.return_to_launch()
        except Exception as err:
            log(f"RTL 不可用，改为在当前位置降落：{err}")
            await drone.action.land()


async def main_async() -> None:
    """运行异步教学入口并输出场景配置。"""
    await fly_with_mavsdk()
    log("Demo 完成。")


def main() -> int:
    """启动异步示例；成功返回 0，失败打印中文错误并返回非零状态。"""
    asyncio.run(main_async())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
