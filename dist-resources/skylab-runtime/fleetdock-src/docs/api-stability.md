# API 稳定性与弃用策略

- `fleetdock.api` 是精简稳定入口，控制在少量常用设备、工厂、计划、响应与诊断类型内。
- `fleetdock` 顶层现有导出继续作为兼容入口，不立即删除，避免破坏已有项目。
- 领域能力优先从 `fleetdock.events`、`fleetdock.routes`、`fleetdock.safety`、`fleetdock.avoidance` 导入。
- `stable` 能力遵循语义化版本；破坏性调整先提供至少一个次版本的弃用期。
- `beta` 可能增加参数或状态，但尽量保持既有调用兼容。
- `experimental` 不承诺跨次版本兼容；`simulated` 不代表真机支持；`planned` 不能用于控制。
- 私有名称和 `fleetdock.tasking` 内部实现不属于稳定契约。
