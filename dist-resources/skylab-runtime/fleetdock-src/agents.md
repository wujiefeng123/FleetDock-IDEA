# Agents 协作规范

## SDK 注释规范

- 所有面向使用者的公共 SDK 方法必须提供中文 docstring，说明方法用途、关键参数语义、单位、执行时机和返回值；新增或修改公共方法时必须同步补齐。
- 所有面向使用者的公共类、数据对象、枚举类及枚举成员必须提供中文说明；枚举成员应说明业务语义、单位、坐标系或适用场景，确保开发者无需查阅实现即可理解。
- 任务编排类、设备对象类、适配器公共入口、事件订阅入口和示例中暴露给开发者调用的方法，都应优先让 IDE 能直接展示中文说明。
- 内部私有方法可以只在逻辑复杂或存在安全边界时补充简短中文注释；不得用无意义注释替代清晰命名。

本文档用于约束 FleetDock 仓库中的自动化代理和协作者行为，确保源码、测试、示例和文档保持一致。

## 项目实际情况

FleetDock 当前是一个 Python SDK 骨架，核心目标是用统一设备对象控制不同类型的协同设备。现有公共入口在 `fleetdock/__init__.py`，主要模块包括：

- `fleetdock/models.py`：标准命令、连接信息、产品配置、响应和错误码。
- `fleetdock/devices.py`：`BaseDevice`、`Drone`、`RobotDog` 等面向用户的标准设备对象。
- `fleetdock/registry.py`：内置产品注册表，当前包含 `mock.drone`、`mock.robot_dog`、`px4.mavlink`。
- `fleetdock/factory.py`：根据产品配置创建对应适配器。
- `fleetdock/adapters/`：适配器层，包含基类、Mock 适配器和 PX4 MAVSDK 适配器。
- `fleetdock/events.py`：基础事件和消息总线能力。
- `examples/`：可运行示例，用于展示标准 SDK 用法。
- `tests/`：基于标准库 `unittest` 的测试。

## 产品级目标

- FleetDock 的长期目标是产品级 SDK，而不是一次性 demo 或脚本集合；后续所有设计建议、代码实现和架构取舍都应默认面向可复用、可维护、可观察、可测试和可扩展的 SDK 能力。
- 面向公共 SDK 的能力应优先沉入 `fleetdock/` 核心模块，形成稳定 API、状态模型、错误处理、安全边界、事件输出和测试覆盖；示例脚本只负责展示这些 API 的用法，不应承载核心业务逻辑。
- 提出新功能方案时，应优先回答“这个能力如何产品化”：公共入口是什么、生命周期如何管理、状态如何查询、异常如何暴露、如何测试、如何跨适配器扩展、README 如何同步。
- 不应为了快速演示而把关键能力长期留在 `examples/` 中；如果 demo 中出现可复用的控制闭环、策略计算、编排状态或安全逻辑，应优先抽象进 SDK 核心，再让 demo 调用 SDK。
- 用户明确要求 demo、教学示例或演示脚本时，demo 必须简单、浅显、易读，尽量少封装，优先直接展示标准 SDK 调用流程；复杂的产品级封装应留在 SDK 内部，而不是堆在 demo 里增加学习成本。
- demo 的目标是鼓励开发者理解和使用 FleetDock：代码应短、路径清楚、日志可读、角色关系明确，避免让用户误以为必须复制内部执行器或复杂控制循环才能使用 SDK。

## 本地开发环境

- 当前仓库优先使用 conda 环境 `fleetdock`，环境路径为 `C:\ProgramData\anaconda3\envs\fleetdock`。
- `fleetdock` 环境优先使用 Python 3.12。只有显式试装尚未接入真机适配器的 `unitree` extra 时，才需要处理 `unitree-sdk2` 固定的 `cyclonedds==0.10.2`；该组合当前不适合在 Python 3.13 下构建。
- 显式安装 `unitree` extra 前，应先在 conda 环境中安装 CycloneDDS C 库：`conda install -n fleetdock -c conda-forge cyclonedds=0.10.5`。
- 安装 `unitree-sdk2` 时，应设置 `CYCLONEDDS_HOME` 和 `CMAKE_PREFIX_PATH` 为当前 conda 环境的 `Library` 目录，并确保其 `Library\bin` 在 `PATH` 中。
- 自动化代理在本仓库执行 Python、pip、测试或示例时，应优先调用 `C:\ProgramData\anaconda3\envs\fleetdock\python.exe`，避免误用 WindowsApps 的 `python.exe` 占位符。
- 安装基础 SDK 使用：`C:\ProgramData\anaconda3\envs\fleetdock\python.exe -m pip install -e .`
- 新电脑安装完整生产运行依赖使用：`C:\ProgramData\anaconda3\envs\fleetdock\python.exe -m pip install ".[full-prod]"`；安装完整开发环境使用：`C:\ProgramData\anaconda3\envs\fleetdock\python.exe -m pip install -e ".[full-dev]"`。
- `px4`、`dji-cloud`、`unitree`、`projectairsim-rtsp`、`vision`、`dev` 等碎片化 extra 继续按各自能力维护；已实现的运行集成依赖必须同步维护到 `full-prod`，并确保 `full-dev` 始终完整包含 `full-prod` 与 `dev`，同时更新 README 和依赖聚合测试。`unitree` 在真实适配器和协议测试落地前保持显式 opt-in，不进入聚合安装组。
- `integrations` 和 `integrations-lite` 作为兼容、轻量入口保留，不得替代新电脑完整安装所使用的 `full-prod` 和 `full-dev`。
- 运行测试使用：`C:\ProgramData\anaconda3\envs\fleetdock\python.exe -m unittest discover -s tests`。
- 如果用户终端已经显示 `(fleetdock)`，交互式命令可以直接使用 `python -m pip ...` 和 `python -m unittest ...`。

## 版本控制与迁移忽略规则

- 迁移到其他开发环境或提交代码时，只带走源码、测试、示例、项目配置和文档，不带走本地安装、缓存和构建产物。
- `__pycache__/`、`*.pyc`、`.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/`、覆盖率输出等运行或检查缓存应保持在 `.gitignore` 中。
- `.venv/`、`venv/`、`env/`、`.conda/`、`.env`、`.env.*` 等本地环境和私有配置不应提交；如需提供环境变量示例，应使用 `.env.example`。
- `build/`、`dist/`、`*.egg-info/`、`pip-wheel-metadata/` 等打包或 editable install 生成物不应提交，也不需要随项目迁移。
- `.idea/`、`.vscode/`、`*.code-workspace`、`Thumbs.db`、`Desktop.ini` 等 IDE 或系统本地状态默认不提交，除非用户明确要求共享某个团队配置文件。
- 新增工具链时，如果会产生新的本地缓存、日志、下载物或构建目录，应同步更新 `.gitignore` 和本节说明。

## 工作原则

- 优先保持公共 SDK 的稳定性，用户侧应继续通过 `Drone`、`RobotDog`、`ConnectionInfo` 和标准命令使用系统。
- 新增设备或厂商能力时，应优先通过 `ProductProfile`、`ProductRegistry`、`AdapterFactory` 和新的 `DeviceAdapter` 实现扩展。
- 适配器内部可以处理厂商协议细节，但不要把原始厂商协议对象暴露成公共 SDK 的必需用法。
- 新增标准命令时，需要同步更新 `CommandName`、相关设备对象、适配器支持列表、Mock 行为、示例和测试。
- 新增产品时，需要在注册表中声明产品名、设备类别、厂商、控制系统、适配器键和支持命令。
- 涉及 PX4 的改动使用 `mavsdk` 可选依赖，不应破坏未安装 PX4 依赖时的 Mock 流程。
- 示例应保持短小、可运行，并优先展示面向用户的标准 SDK 用法。
- 测试优先覆盖标准命令流程、产品注册、适配器选择、错误响应和 PX4 客户端注入路径。
- 所有面向用户的说明文字优先使用中文；代码标识符、协议名、产品键和命令名保持英文。
- 所有模块变更都需要用中文同步到 `README.md`。
- SDK 内部日志统一使用 Python 标准库 `logging` 和模块级 `logging.getLogger(__name__)`，运行轨迹统一记录为 `DEBUG`，不占用留给上层应用的 `INFO` 等级。
- 新增或修改设备创建、产品/适配器选择、连接、命令执行、状态变化、事件发布及异常处理等关键路径时，必须同步补充必要的 `DEBUG` 日志；不得记录密码、令牌、密钥或完整敏感连接参数。
- SDK 不得调用 `logging.basicConfig()`、不得擅自添加输出 Handler 或设置全局日志等级；日志是否显示及输出位置由使用者配置。

## 设备事件标准

- 设备事件是 FleetDock 公共 SDK 标准的一部分，必须按跨品牌、跨设备能力设计，不得只围绕 PX4、无人机或某一厂商协议建模。
- 标准事件按通用、运动、导航、电源、安全、空中设备和足式设备等能力领域划分；无人机、机器狗及后续设备应复用位置、速度、姿态、电池、健康等通用事件和字段模型。
- 标准事件类型必须使用公开枚举，标准事件实例必须使用对应的强类型事件类，标准字段必须使用公开的不可变数据对象；面向开发人员的标准事件不得要求通过 `payload.get("字段名")` 读取无类型字典。
- 标准字段必须明确语义、单位和坐标系。默认使用 SI 单位，单位应体现在字段名中；位置、速度等空间数据必须声明 `CoordinateFrame`，厂商缺失的字段使用可空类型，不得伪造为 `0`。
- 厂商适配器负责把 MAVSDK、DJI SDK、Unitree SDK 等原生事件转换为 FleetDock 标准事件，不得要求业务代码直接处理厂商事件才能完成通用功能。
- 成功转换的标准事件应通过 `OriginalEvent` 保留来源厂商、原事件名、完整可序列化原数据和可选 `native_data`。`native_data` 只用于单进程高级接入，不保证可序列化，也不得成为公共功能的必需依赖。
- 无法匹配标准的厂商事件必须包装为 `VendorEvent` 透传，事件名使用 `vendor.<厂商>.<事件>`；开发人员必须能够取得 `event_name`、`raw_data` 和可选 `native_data`，不得静默丢弃未知厂商字段。
- 应用自定义事件使用 `CustomEvent` 和 `custom.<应用>.<事件>` 命名空间；厂商专有事件、自定义事件和标准事件必须能够通过同一个单设备事件订阅接口接收。
- 每个设备通过 `device.events.subscribe(handler)` 提供完整单设备事件流，由使用者进行二次过滤；取消订阅函数和 `device.close()` 必须放在 `finally` 中，确保异常、返航失败或程序退出时仍能释放订阅和后台遥测资源。
- `ProductProfile` 必须通过 `supported_events` 和 `extension_namespaces` 声明产品事件能力。新增或修改事件时，需要同步更新公共导出、适配器转换、产品声明、Mock 行为、示例、测试和中文 `README.md`。
