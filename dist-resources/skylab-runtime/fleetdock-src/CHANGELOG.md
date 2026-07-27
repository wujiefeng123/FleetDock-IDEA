# 变更日志

本项目遵循语义化版本。`0.x` 阶段的稳定公共入口会保持兼容；试验能力的调整会在此记录。

## 0.3.0

- 标准运动控制统一使用 `CommandName.MOVE` 和强类型 `MoveTarget`，产品通过
  `supported_move_targets` 声明支持的坐标目标，不再维护重复的 `GOTO_GLOBAL/GOTO_LOCAL` 命令与能力。
- `Drone` 和 `DronePlan` 的 `goto_global()`、`goto_local()` 保留为 `move(...)` 的便捷入口；
  删除未生效的 `goto_local(..., speed=...)` 参数，避免调用方误以为速度已进入执行链。

## 0.2.0

- 增加产品集成成熟度、类型包标记和版本入口。
- 增加质量门禁、发布治理、支持矩阵、故障测试和渐进式 Quick Start。
- 明确即时设备命令与任务完成确认的语义边界。
- 全仓 mypy 和 85% 覆盖率作为发布门禁，重点保护任务、事件、设备生命周期和 PX4 安全路径。
