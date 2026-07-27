"""兼容旧版避障后端导入路径；新代码请使用 :mod:`fleetdock.avoidance.backends`。"""

from fleetdock.avoidance.backends import BuiltinTrajectoryRolloutPlanner

__all__ = ["BuiltinTrajectoryRolloutPlanner"]
