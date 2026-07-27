"""多设备 Fleet 计划编排入口。

本模块暴露作用于设备编队的计划链类型。``FleetPlan`` 处理通用批量
动作，``DroneFleetPlan`` 和 ``RobotDogFleetPlan`` 提供同类型设备编队
的专用动作。
"""

from fleetdock.tasking.fleet import DroneFleetPlan, FleetPlan, RobotDogFleetPlan
from fleetdock.tasking.models import FleetTaskResult

__all__ = [
    "DroneFleetPlan",
    "FleetPlan",
    "FleetTaskResult",
    "RobotDogFleetPlan",
]
