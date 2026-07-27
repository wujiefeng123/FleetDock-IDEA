"""高级计划编排入口。

``Plan`` 是面向开发者的链式任务入口；``ParallelPlan`` 用于并行运行多条
单设备或 Fleet 计划。具体实现位于 ``fleetdock.tasking`` 包，本模块用于
稳定简洁的公共导入路径。
"""

from fleetdock.tasking.parallel import ParallelPlan
from fleetdock.tasking.plans import Plan

__all__ = [
    "ParallelPlan",
    "Plan",
]
