"""兼容旧版内置规划器导入路径。"""

from fleetdock.avoidance.backends.builtin_trajectory_rollout import BuiltinTrajectoryRolloutPlanner

__all__ = ["BuiltinTrajectoryRolloutPlanner"]
