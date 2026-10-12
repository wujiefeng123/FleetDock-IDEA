"""使用 FleetDock 标准 SDK 控制 PX4 真机起飞一米、保持五秒并降落。"""

import os
import time

from fleetdock import DeviceFactory, Plan


TAKEOFF_ALTITUDE_M = 1.0
CLIMB_SPEED_M_S = 0.25
ALTITUDE_TOLERANCE_M = 0.15
HOLD_DURATION_S = 5.0


def main() -> None:
    """确认真机环境后执行自动适配起飞，并在落地确认后释放设备资源。"""
    endpoint = os.getenv("FLEETDOCK_PX4_ENDPOINT", "serial://COM3:57600")
    if input("确认场地净空且遥控器可接管后输入 FLY：").strip() != "FLY":
        print("已取消真机飞行。")
        return

    drone = DeviceFactory.create().px4_generic().params(endpoint=endpoint, timeout_s=30.0).build()
    try:
        connect_result = drone.connect()
        print("连接：", connect_result)
        if not connect_result.ok:
            return

        takeoff_result = (
            Plan(drone)
            .climb_speed(CLIMB_SPEED_M_S)
            .altitude_tolerance(ALTITUDE_TOLERANCE_M)
            .wait_until_ready(timeout_s=60.0)
            .takeoff(altitude=TAKEOFF_ALTITUDE_M)
            .run(step_timeout_s=60.0)
        )
        print("起飞：", takeoff_result)
        if not takeoff_result.ok:
            return

        print(f"保持 {HOLD_DURATION_S:.0f} 秒。")
        time.sleep(HOLD_DURATION_S)

        land_result = Plan(drone).land().run(step_timeout_s=60.0)
        print("降落：", land_result)
    finally:
        try:
            if drone.state.snapshot().in_air is True:
                print("异常清理降落：", Plan(drone).land().run(step_timeout_s=60.0))
        finally:
            drone.close()


if __name__ == "__main__":
    main()
