"""单设备计划编排公共入口。"""

from fleetdock.tasking.core import DevicePlan
from fleetdock.tasking.device import DronePlan, RobotDogPlan

__all__ = [
    "DevicePlan",
    "DronePlan",
    "RobotDogPlan",
]
