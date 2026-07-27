"""PX4 SDK 平滑螺旋航线示例；展示速度、前视和机头切向参数。"""

from __future__ import annotations

from fleetdock import DeviceFactory, HeadingMode, MotionExecutionMode, Plan, Route, RouteMotion, UnlockStrategy
from drone_plan import print_route_parameters


MAVSDK_ENDPOINT = "udpin://0.0.0.0:14555"
TAKEOFF_ALTITUDE_M = 20.0
ROUTE_SPEED_M_S = 2.0
LOOKAHEAD_TIME_S = 1.2
MIN_LOOKAHEAD_M = 2.0
SPIRAL_ACCEPTANCE_RADIUS_M = 5.0


def main() -> None:
    """运行一个连续平滑螺旋航线示例。"""

    drone = (
        DeviceFactory.create()
        .px4_generic()
        .params(endpoint=MAVSDK_ENDPOINT, timeout_s=30.0)
        .build()
    )
    route = Route.spiral(
        max_radius_m=10.0,
        spacing_m=3.0,
        turns=4.5,
        acceptance_radius_m=SPIRAL_ACCEPTANCE_RADIUS_M,
        name="smooth-spiral",
    )
    print_route_parameters(
        route,
        label="平滑螺旋",
        altitude_m=TAKEOFF_ALTITUDE_M,
        speed_m_s=ROUTE_SPEED_M_S,
        motion_mode=MotionExecutionMode.AUTO,
        heading=HeadingMode.TANGENT,
        lookahead_time_s=LOOKAHEAD_TIME_S,
        min_lookahead_m=MIN_LOOKAHEAD_M,
    )

    try:
        response = drone.connect()
        print(response, flush=True)
        if not response.ok:
            raise RuntimeError(response.message)
        result = (
            Plan(drone)
            .wait_until_ready(timeout_s=60.0)
            .unlock(strategy=UnlockStrategy.STANDARD_THEN_FORCE)
            .takeoff(altitude=TAKEOFF_ALTITUDE_M)
            .route(
                route,
                altitude=TAKEOFF_ALTITUDE_M,
                speed=ROUTE_SPEED_M_S,
                heading=HeadingMode.TANGENT,
                motion=RouteMotion(
                    mode=MotionExecutionMode.AUTO,
                    lookahead_time_s=LOOKAHEAD_TIME_S,
                    min_lookahead_m=MIN_LOOKAHEAD_M,
                ),
                timeout_s=180.0,
            )
            .return_to_launch()
            .run(step_timeout_s=180.0, guard_interval_s=0.05)
        )
        print(result, flush=True)
        if not result.ok:
            raise RuntimeError(result.message)
    finally:
        drone.close()


if __name__ == "__main__":
    main()
