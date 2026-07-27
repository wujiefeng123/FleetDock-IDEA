"""PX4 GPS 螺旋航线示例；演示 AMSL 高度、Home 高度和局部偏移转换。"""

from __future__ import annotations

from fleetdock import (
    CapabilityName,
    DeviceFactory,
    DeviceMetadata,
    Drone,
    GpsPoint,
    HeadingMode,
    MotionExecutionMode,
    Plan,
    Route,
    RouteMotion,
    StandardResponse,
    TaskResult,
    UnlockStrategy,
)
from drone_plan import print_route_parameters


MAVSDK_ENDPOINT = "udpin://0.0.0.0:14555"
CONNECT_TIMEOUT_S = 30.0
TAKEOFF_ALTITUDE_M = 20.0
STEP_TIMEOUT_S = 240.0

# 螺旋中心点：重庆轨道交通大学城站北侧约 12 米，WGS84 坐标。
SPIRAL_CENTER_LATITUDE_DEG = 29.6101273
SPIRAL_CENTER_LONGITUDE_DEG = 106.3051375
# 航点绝对海拔（AMSL，米），当前仿真 Home 约 294.2 米，对应相对高度约 20 米。
SPIRAL_ALTITUDE_AMSL_M = 314.2

SPIRAL_MAX_RADIUS_M = 12.0
SPIRAL_SPACING_M = 4.0
SPIRAL_TURNS = 2.0
SPIRAL_SPEED_M_S = 3.0
SPIRAL_ACCEPTANCE_RADIUS_M = 1.5
SPIRAL_HEADING_MODE = HeadingMode.TANGENT


def run_or_raise(response: StandardResponse) -> None:
    """打印标准设备响应；命令失败时抛出异常并停止演示。"""

    print(response, flush=True)
    if not response.ok:
        raise RuntimeError(response.message)


def run_plan_or_raise(result: TaskResult) -> None:
    """打印计划结果；任一步骤失败时抛出异常并进入返航兜底。"""

    print(result, flush=True)
    if not result.ok:
        raise RuntimeError(result.message)


def build_drone() -> Drone:
    """创建连接默认 PX4 MAVLink 端口的标准无人机对象。"""

    return (
        DeviceFactory.create()
        .px4_generic()
        .params(endpoint=MAVSDK_ENDPOINT, timeout_s=CONNECT_TIMEOUT_S)
        .metadata(DeviceMetadata(device_id="gps-spiral-demo", name="GPS 螺旋航线 Plan Demo 无人机"))
        .build()
    )


def build_spiral_route() -> tuple[GpsPoint, Route]:
    """用固定 WGS84 中心点和 AMSL 海拔生成 GPS 螺旋航线。"""

    center = GpsPoint(
        latitude_deg=SPIRAL_CENTER_LATITUDE_DEG,
        longitude_deg=SPIRAL_CENTER_LONGITUDE_DEG,
        altitude_m=SPIRAL_ALTITUDE_AMSL_M,
    )
    route = Route.spiral(
        max_radius_m=SPIRAL_MAX_RADIUS_M,
        spacing_m=SPIRAL_SPACING_M,
        turns=SPIRAL_TURNS,
        gps_origin=center,
        altitude_m=SPIRAL_ALTITUDE_AMSL_M,
        acceptance_radius_m=SPIRAL_ACCEPTANCE_RADIUS_M,
        name="university-town-gps-spiral",
    )
    return center, route


def print_route(center: GpsPoint, route: Route) -> None:
    """打印螺旋中心和全部 GPS 航点，便于运行前核对坐标与绝对海拔。"""

    print_route_parameters(
        route,
        label="GPS 螺旋",
        altitude_m=SPIRAL_ALTITUDE_AMSL_M,
        speed_m_s=SPIRAL_SPEED_M_S,
        motion_mode=MotionExecutionMode.SDK,
        heading=SPIRAL_HEADING_MODE,
    )
    print(
        "[GPS 螺旋中心] "
        f"lat={center.latitude_deg:.7f}, lon={center.longitude_deg:.7f}, "
        f"altitude_amsl={center.altitude_m:.1f} m",
        flush=True,
    )
    print(f"[GPS 螺旋航点] 共 {len(route.waypoints)} 个：", flush=True)
    for index, waypoint in enumerate(route.waypoints, start=1):
        print(
            f"  {index:02d}: lat={waypoint.latitude_deg:.7f}, "
            f"lon={waypoint.longitude_deg:.7f}, altitude_amsl={waypoint.altitude_m:.1f} m",
            flush=True,
        )


def main() -> None:
    """连接 PX4，执行固定 GPS 中心的螺旋航线并最终返航。"""
    center, spiral_route = build_spiral_route()
    print_route(center, spiral_route)

    drone = build_drone()
    should_rtl = False
    try:
        print("[1/3] 连接 PX4...", flush=True)
        run_or_raise(drone.connect())

        print("[2/3] 等待定位和 Home，随后解锁、起飞并执行 GPS 螺旋航线...", flush=True)
        should_rtl = True
        result = (
            Plan(drone)
            .timeout(STEP_TIMEOUT_S)
            .wait_until_ready(
                timeout_s=60.0,
                require_global_position=True,
                require_armable=False,
                capability=CapabilityName.TAKEOFF,
            )
            .unlock(strategy=UnlockStrategy.STANDARD_THEN_FORCE)
            .takeoff(altitude=TAKEOFF_ALTITUDE_M)
            .route(
                spiral_route,
                speed=SPIRAL_SPEED_M_S,
                heading=SPIRAL_HEADING_MODE,
                motion=RouteMotion(mode=MotionExecutionMode.SDK),
                timeout_s=STEP_TIMEOUT_S,
            )
            .run(step_timeout_s=STEP_TIMEOUT_S)
        )
        run_plan_or_raise(result)

        print("[3/3] 螺旋航线完成，返航...", flush=True)
        rtl_result = (
            Plan(drone)
            .timeout(STEP_TIMEOUT_S)
            .return_to_launch()
            .run(step_timeout_s=STEP_TIMEOUT_S)
        )
        run_plan_or_raise(rtl_result)
        should_rtl = False
        print("GPS 螺旋航线 Plan Demo 执行完成。", flush=True)
    except Exception as exc:
        print(f"GPS 螺旋航线 Plan Demo 失败：{exc}", flush=True)
        if should_rtl:
            rtl_response = drone.return_to_launch()
            print(rtl_response, flush=True)
            if not rtl_response.ok:
                print(drone.land(), flush=True)
        raise
    finally:
        drone.close()


if __name__ == "__main__":
    main()
