"""单设备计划编排入口。

本模块暴露作用于单台设备的计划链类型。``DevicePlan`` 提供通用设备
动作，``DronePlan`` 和 ``RobotDogPlan`` 在此基础上增加对应设备类型的
专用动作。
"""

from fleetdock.tasking.core import DevicePlan
from fleetdock.tasking.device import DronePlan, RobotDogPlan

__all__ = [
    "DevicePlan",
    "DronePlan",
    "RobotDogPlan",
]
