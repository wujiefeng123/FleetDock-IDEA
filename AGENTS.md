# AGENTS.md

## 1. 目的与适用范围

本文件是 `D:\Git\jupyterlab-desktop` 仓库的 Agent 协作规范，适用于在本仓库中工作的 AI Agent、自动化助手和人工协作者。

本仓库以官方 JupyterLab Desktop 为底座，承载面向无人机与低空智能应用开发的 **FleetDock IDEA**。所有工作必须同时满足两个目标：

1. 正确实现 FleetDock IDEA 产品能力。
2. 尽量保持官方 JupyterLab Desktop 的可升级性。

本文件适用于整个仓库。进入包含更具体规范的子目录时，还必须遵守子目录规范。例如：

- `dist-resources/skylab-runtime/fleetdock-src/agents.md`：FleetDock Python SDK 子树规范。

若规则发生冲突，优先级如下：

1. 用户在当前任务中的明确要求。
2. 距离目标文件最近的 `AGENTS.md` / `agents.md`。
3. 本文件。
4. 仓库通用文档和默认工具行为。

## 2. 项目实际情况

### 2.1 仓库关系

- 旧 FleetDock IDEA 项目：
  `D:\Git\ControlPlatform\application\jupyterlab-desktop`
- 新迁移目标与当前开发仓库：
  `D:\Git\jupyterlab-desktop`
- 官方上游远程：
  `origin = https://github.com/jupyterlab/jupyterlab-desktop.git`
- 当前 FleetDock 迁移分支：
  `codex/fleetdock-upstream-migration`

旧项目是迁移参考源，默认只读。除非用户明确指定，否则禁止在旧项目中创建、修改、删除、格式化或提交任何文件。

固定迁移基线和迁移记录见：

- `FLEETDOCK_MIGRATION.md`

基线发生变化时，必须同步更新该文档，不要只在提交消息或对话中记录。

### 2.2 技术栈

- JupyterLab Desktop
- Electron
- TypeScript / JavaScript
- HTML / CSS
- Node.js / Yarn
- Python / Conda
- Windows + WSL2
- PX4 SITL
- Project AirSim
- GStreamer / MediaMTX
- FleetDock Python SDK

当前包身份：

- 内部包名：`skylab-ide`
- 用户可见名称：`FleetDock IDEA`
- 当前基础版本：以 `package.json` 为准

仓库中保留 JupyterLab、JupyterLab Desktop、`skylab-*` 等上游或历史兼容命名是正常情况。除非任务明确要求，不进行全仓品牌替换。

### 2.3 当前架构边界

通用扩展边界：

- `src/main/extensions/session-extension.ts`

FleetDock 桌面集成层：

- `src/main/fleetdock/*`

主要业务区域：

- 应用与工作区：`src/main/application/*`
- 设备管理：`src/main/devices/*`
- UAV 仿真：`src/main/uavsimulation/*`
- 仿真兼容入口：`src/main/simulation/*`
- FleetDock UI 资源：`src/assets/fleetdock-*`
- 历史兼容 UI 资源：`src/assets/skylab-*`
- FleetDock 运行时资源：`dist-resources/skylab-runtime/*`
- 仿真模板与客户端：`dist-resources/skylab-simulation/*`

当前 UI 注入层包括：

- JupyterLab 工作区：`src/main/fleetdock/lab-injector.ts`
- 欢迎页：`src/main/fleetdock/welcome-injector.ts`
- Electron 标题栏：`src/main/fleetdock/titlebar-injector.ts`
- 对应资源：`src/assets/fleetdock-*`、`src/assets/skylab-*`

FleetDock 业务逻辑必须优先留在上述隔离区域。上游核心文件只保留小型、通用、稳定的生命周期钩子。

## 3. 产品目标

FleetDock IDEA 的目标不是简单修改 JupyterLab 品牌，而是提供面向低空智能和无人机应用开发的一体化桌面环境。

核心产品目标：

- 保持 JupyterLab 主工作区作为可靠的代码、Notebook 和数据开发环境。
- 提供 FleetDock 应用创建、工作区管理和运行时生成能力。
- 提供无人机、传感器及其他设备的登记、绑定、连接和调试能力。
- 提供 Windows + WSL2 + PX4 + Project AirSim 的本地仿真工作流。
- 提供仿真视频、传感器数据、MAVLink 和 FleetDock SDK 的统一集成。
- 让产品专属能力易发现、易使用，同时不破坏上游工作流。
- 让后续官方上游更新可以通过小范围合并完成，而不是重新二开整个仓库。

非目标：

- 不把整个 JupyterLab Desktop 复制为不可升级的私有分叉。
- 不为了品牌统一而无差别替换所有上游文本和文件。
- 不静默安装、修复或删除用户的 WSL、PX4、Project AirSim、Conda 环境。
- 不默认把大型仿真二进制、缓存或生成环境提交到 Git。

## 4. 工作原则

### 4.1 上游优先

- 上游依赖版本、构建工具、Electron API、锁文件和安全策略具有权威性。
- FleetDock 只增加产品确实需要的配置、模块和资源。
- 遇到上游新实现与旧 FleetDock 实现冲突时，保留新上游实现，再通过适配器补回 FleetDock 行为。
- 禁止用旧项目整文件覆盖新上游文件。

### 4.2 外挂优先

实现顺序：

1. 独立 FleetDock 模块或资源。
2. adapter、controller、service、injector。
3. 通用扩展接口或生命周期钩子。
4. 只有前三种方式无法完成时，才最小修改上游核心逻辑。

禁止把设备、仿真、发布、FleetDock UI 等业务逻辑直接堆入：

- `SessionWindow`
- `App`
- `LabView`
- Jupyter server 启动器
- 上游 preload 主文件

### 4.3 最小、明确、可逆

- 只修改完成当前任务所必需的文件。
- 避免无关重排、重命名、格式化和清理。
- 一个提交只表达一个清晰意图。
- 大任务按架构层、业务域或风险边界拆分。
- 行为变化必须能从代码、日志、类型、文档或提交中看出来。
- 不删除兼容入口和回退路径，除非任务明确要求并完成影响评估。

### 4.4 证据优先

- 诊断问题时先读取日志、状态、配置和实际运行路径。
- 不以截图中的截断文案代替完整错误。
- 不把“可能”描述为“已经确认”。
- 修复前明确根因；修复后使用与故障路径相同或等价的方式验证。

### 4.5 保护用户工作

- 未提交内容默认属于用户或其他 Agent。
- 禁止回滚、覆盖、暂存或提交不属于当前任务的改动。
- 禁止使用 `git reset --hard`、`git checkout -- <file>` 等破坏性恢复命令。
- 发现工作树非干净时，先判断改动归属和重叠范围；无法安全绕开时再请求用户确认。

## 5. Agents 协作规范

### 5.1 开始任务前

每个 Agent 开始工作前必须：

1. 阅读本文件及目标目录下更具体的规范。
2. 阅读与任务直接相关的文档，如 `dev.md`、`FLEETDOCK_MIGRATION.md`。
3. 检查当前仓库、分支、`git status` 和最近提交。
4. 确认操作目标是新仓库，而不是旧项目。
5. 确认任务是诊断、修改、验证还是发布，不擅自扩大权限。

### 5.2 任务拆分

- 只有边界清晰且文件范围基本不重叠的工作才适合并行。
- 每个子任务应声明目标、允许修改的目录和预期产物。
- 不让多个 Agent 同时修改同一个核心文件。
- 若无法避免共享文件，由一个 Agent 负责最终整合，其他 Agent 只提供分析或补丁建议。

### 5.3 共用工作区

- 所有 Agent 都应假定文件系统和 Git 工作树是共享的。
- 修改前后都要重新检查 `git status`，不能依赖任务开始时的旧状态。
- 不使用全仓格式化来“顺便整理”其他 Agent 的文件。
- 不移动、删除或重写来源不明的临时文件和未跟踪文件。
- 发现其他 Agent 新增改动时，先判断是否与当前任务重叠，不得擅自撤销。

### 5.4 沟通与交接

进行中的说明应包含：

- 当前判断或已确认根因。
- 正在修改的边界。
- 是否会影响运行环境、打包产物或用户数据。
- 仍未验证的风险。

交接至少应包含：

- 修改目的。
- 修改文件。
- 关键设计决定。
- 已运行的验证。
- 未运行的验证及原因。
- Git 分支和提交号。
- 仍需用户执行的步骤。

不得仅用“已完成”“应该可以”作为交接结论。

### 5.5 审查责任

- 接手 Agent 必须检查实际 diff，不能只依据前一个 Agent 的文字结论。
- 对安全敏感 IPC、进程清理、路径解析和打包资源，应做独立复核。
- 如果发现既有实现不符合本规范，应在当前任务范围内修正或明确记录，不静默忽略。

## 6. 注释规范

### 6.1 注释写什么

注释应解释：

- 为什么需要偏离或扩展上游行为。
- 该逻辑保护的兼容性、安全性或生命周期约束。
- 不直观的业务规则。
- 以后升级上游时必须保留的原因。

注释不应：

- 逐行复述代码。
- 记录聊天过程。
- 写无上下文的“临时修复”。
- 使用过期的任务编号或无法访问的外部说明作为唯一依据。

### 6.2 FleetDock 修改标记

修改上游核心文件或在通用文件中增加 FleetDock 所需钩子时，必须就近添加：

TypeScript / JavaScript / Java / C：

```ts
// [UAV-IDE MODIFY] Keep product extensions behind the generic session lifecycle.
```

Python：

```python
# [UAV-IDE MODIFY] Restrict cleanup to simulation processes owned by the IDE.
```

CSS：

```css
/* [UAV-IDE MODIFY] Match the desktop chrome to the FleetDock workspace. */
```

Shell：

```sh
# [UAV-IDE MODIFY] Package the FleetDock runtime after the upstream environment is created.
```

要求：

- 标记后的说明使用简洁英文，说明原因而非动作本身。
- 新建的纯 FleetDock 隔离文件，通常在模块、类或关键边界处标记一次即可，不要每行重复。
- 上游核心文件中的每个独立 FleetDock 钩子都应有标记。
- 不修改或删除上游版权头。

### 6.3 TODO 和 FIXME

新增 TODO/FIXME 必须包含可执行条件：

```ts
// TODO(UAV-IDE): Remove this adapter after the upstream API exposes WebContents lifecycle hooks.
```

禁止：

```ts
// TODO: fix later
```

### 6.4 日志与错误

- 错误消息应说明失败对象和下一步建议。
- 不在日志中输出 token、密码、密钥、完整认证 URL 或敏感设备凭据。
- 进程启动和清理日志应带运行 ID、资源类型或受控路径。
- 面向用户的错误优先使用清晰简体中文；底层技术细节可使用英文。

## 7. 代码与架构规则

### 7.1 推荐修改区域

FleetDock 新能力优先放在：

- `src/main/fleetdock/*`
- `src/main/application/*`
- `src/main/devices/*`
- `src/main/uavsimulation/*`
- `src/main/simulation/*`
- `src/assets/fleetdock-*`
- `src/assets/skylab-*`
- `dist-resources/skylab-runtime/*`
- `dist-resources/skylab-simulation/*`

### 7.2 谨慎修改区域

以下属于上游核心或高冲突区域：

- `src/main/app.ts`
- `src/main/main.ts`
- `src/main/sessionwindow/*`
- `src/main/labview/*`
- `src/main/titlebarview/*`
- `src/main/eventmanager.ts`
- `src/main/eventtypes.ts`
- `src/main/server.ts`
- `src/main/config/*`
- 上游 preload 入口
- `webpack.preload.js`
- `package.json`
- `yarn.lock`
- `scripts/*`

修改这些文件前必须回答：

1. 是否可以通过现有 FleetDock 模块完成？
2. 是否可以扩展通用接口，而不是写入产品业务？
3. 新钩子是否足够小并能被其他扩展复用？
4. 下次上游升级时是否容易识别和重放？

### 7.3 扩展边界

- `SessionExtensionHost` 只暴露稳定、最小的能力。
- 通用扩展接口不得出现 `FleetDock`、`UAV`、`PX4` 等产品业务类型。
- FleetDock 生命周期接入集中在 `src/main/fleetdock/session-extension.ts`。
- UI 外观通过 injector + 独立 asset 注入，不直接重写上游页面。
- injector 必须处理：
  - 页面已经完成 `dom-ready` 的情况。
  - WebContents 被销毁的情况。
  - 重复注入。
  - CSS 注入键和监听器清理。

### 7.4 IPC 与安全

- 新 IPC 必须集中声明并有明确命名空间。
- renderer 暴露仅提供当前视图实际需要的最小 API。
- 所有敏感 IPC handler 必须验证 `event.sender`。
- 不允许通过通用 `send(channel, ...)` 绕过频道白名单。
- 不降低 `contextIsolation`、sandbox、CSP 或 Trusted Types 约束。
- 不为了方便而向页面暴露 Node.js、文件系统或任意命令执行能力。
- preload 变更后必须验证打包入口和 renderer 实际调用契约。

## 8. UI 与产品体验规则

- JupyterLab 主工作区始终优先保证编辑、Notebook、终端和文件浏览可用。
- FleetDock UI 应与产品蓝白设计语言一致，同时支持暗色模式。
- 欢迎页、标题栏和 JupyterLab 主体风格应一致，但实现必须保持隔离。
- 不通过整页复制或覆盖上游 HTML 来实现外观。
- 注入脚本应优先复用上游 DOM、事件和 IPC，不重复实现已有能力。
- MutationObserver 必须限定目标范围并避免循环更新。
- UI 失败时不得阻断 JupyterLab 核心工作区启动。
- 中文文案使用简体中文和 UTF-8。
- 交互控件必须有可理解的标题、标签或 `aria-*` 属性。
- 涉及危险动作时必须保留确认、禁用态和明确结果反馈。

## 9. 仿真与设备规则

### 9.1 仿真

- 仿真主逻辑保留在 `src/main/uavsimulation/*`。
- `src/main/simulation/*` 只承担兼容入口或薄控制层。
- 不把仿真进程树管理放入 `SessionWindow`。
- 不自动安装或静默修复 WSL、PX4、Project AirSim。
- 环境检测只报告状态、完整错误和可执行建议。
- 进程清理只允许作用于当前 IDE 启动并记录的资源。
- 禁止使用宽泛进程名杀死用户自行启动的 Python、PX4、AirSim 或 WSL 进程。
- 生成配置写入当前工作区的受控 `.jupyter/simulation` 路径。
- 启动失败必须清理本次运行已创建的资源，同时保留足够诊断信息。

重点验证：

- 场景登记、更新和删除。
- WSL 发行版与 PX4 路径解析。
- Project AirSim 客户端连接。
- FleetDock sensor service。
- GStreamer / RTSP。
- MediaMTX / WebRTC。
- MAVLink 输出。
- 启动失败和停止清理。

### 9.2 设备

- 设备持久化、连接、探测和传感器能力保持分层。
- 不在 renderer 中直接操作串口、SSH、TCP 或本地进程。
- 设备凭据和连接参数不得写入日志或提交到仓库。
- 设备删除和解绑应区分全局设备库、应用绑定和运行时实例。
- 端口分配与清理必须有所有权记录。

## 10. 依赖与打包规则

### 10.1 Node / Electron 依赖

- `package.json` 和 `yarn.lock` 默认以上游为准。
- 不为了 FleetDock 功能随意降级 Electron、JupyterLab 或构建工具。
- 新增依赖前先确认是否已有上游或本地实现可复用。
- 依赖变更必须单独审查并说明运行时、打包体积和安全影响。

### 10.2 Python 运行时

FleetDock 桌面运行时使用项目内置环境，不依赖任意系统 Python。

生成环境：

- `env_installer/jlab_server`

生成安装包：

- `env_installer/jlab_server.tar.gz`

Windows 用户安装后的运行时通常位于：

- `%APPDATA%\skylab-ide\jlab_server`

FleetDock SDK 的依赖聚合以其 `pyproject.toml` 为准。桌面打包应使用已审核的聚合 extra，例如 `full-prod`，不要在桌面脚本中重复维护一份依赖版本清单。

修改内置运行时安装流程时，至少验证：

```powershell
env_installer\jlab_server\python.exe -m pip check
env_installer\jlab_server\python.exe -c "import fleetdock, projectairsim"
```

涉及 Project AirSim RTSP 时还应验证：

```powershell
env_installer\jlab_server\python.exe -c "from fleetdock.simulation._gstreamer_rtsp import _load_gstreamer; _load_gstreamer(); print('OK')"
```

### 10.3 打包资源

- `package.json` 的 `build.extraResources` 必须保持最小和明确。
- 不提交 `node_modules`、`build`、`dist`、Conda 展开环境、缓存或临时预览。
- 大型二进制和生成资源必须先确认来源、许可证、更新方式和是否应由构建阶段生成。
- `dist-resources/skylab-runtime` 和 `dist-resources/skylab-simulation` 是兼容目录，不随意改名。
- 打包内容不得无意包含 SDK 测试、示例缓存、Agent 配置或开发凭据。

## 11. 本地开发环境

### 11.1 默认环境

- 默认开发仓库：`D:\Git\jupyterlab-desktop`
- 默认 Conda 环境：`jupyter-dev`
- Windows 平台脚本后缀：`win-64`
- 开发说明以 `dev.md` 为准。

如果系统未全局安装 Yarn，可通过 Corepack 调用。下文使用 `corepack yarn`；已正确安装 Yarn 时可以使用 `yarn`。

### 11.2 首次准备

```powershell
cd D:\Git\jupyterlab-desktop
conda activate jupyter-dev
conda install -c conda-forge conda-pack conda-lock
corepack yarn install --frozen-lockfile
corepack yarn build
corepack yarn create_env_installer:win-64
corepack yarn start
```

`create_env_installer:win-64` 会：

1. 根据上游 Conda lock 创建内置环境。
2. 复制上游额外资源。
3. 安装 FleetDock SDK 生产依赖。
4. 安装 Project AirSim 客户端。
5. 生成 `jlab_server.tar.gz`。

首次启动后，如果用户目录尚无内置运行时，应在：

`Manage Python environments` → `Bundled environment installation` → `Install`

完成安装。

不要把“仓库生成环境”和“用户安装运行时”混为一谈。诊断时必须明确检查的是哪一个路径。

### 11.3 日常开发

修改 TypeScript、preload 或静态资源后：

```powershell
conda activate jupyter-dev
corepack yarn build
corepack yarn start
```

监听开发：

```powershell
corepack yarn watch
```

注意：标题栏等常驻 WebContents 通常需要重启应用才能加载新资源；欢迎页等可重建视图有时重新打开即可。

### 11.4 发布构建

```powershell
corepack yarn clean
corepack yarn build
corepack yarn create_env_installer:win-64
corepack yarn dist:win-64
```

不得把开发环境中手工补装依赖后的偶然状态当作发布结果。发布包必须能从锁文件和安装脚本可重复生成。

## 12. 验证规范

按改动风险选择验证，不机械运行所有命令，也不跳过关键路径。

### 12.1 文档

- 检查路径、命令和版本是否与仓库一致。
- 运行 Markdown 格式检查或 Prettier。
- 确认没有把临时环境和个人路径误写为通用要求。

### 12.2 TypeScript / Electron

```powershell
corepack yarn type-check
corepack yarn build
corepack yarn eslint:check
corepack yarn prettier:check
```

### 12.3 JavaScript / CSS 注入资源

```powershell
node --check src\assets\<modified-file>.js
node_modules\.bin\eslint.cmd src\assets\<modified-file>.js
node_modules\.bin\prettier.cmd --check src\assets\<modified-file>.js src\assets\<modified-file>.css
```

并进行实际或等价 DOM 渲染验证：

- 注入只发生一次。
- 深浅色样式正常。
- 上游 DOM 更新后仍能工作。
- 页面销毁后无残留监听。
- 核心操作按钮仍调用原 IPC。

### 12.4 测试

```powershell
corepack yarn test:unit
corepack yarn test:e2e
```

FleetDock 静态与契约检查位于 `scripts/check-*`。修改对应领域时应运行相关检查，不必无差别运行所有脚本。

### 12.5 无法验证时

最终交接必须说明：

- 未运行的检查。
- 未运行原因。
- 可能影响。
- 推荐的人工验证步骤。

## 13. 中文与编码规范

- 所有文本源码保持 UTF-8。
- 修改中文文件优先使用小范围补丁。
- 禁止使用系统默认 ANSI/GBK 编码覆盖源码。
- 不使用未明确指定 UTF-8 的 `Set-Content`、`Out-File`、`>`、`>>` 写入中文源码。
- 不使用 `Get-Content | Set-Content` 整文件重写中文文件。
- 机械替换必须显式使用 UTF-8，例如 Node.js：

```js
const text = fs.readFileSync(filePath, 'utf8');
fs.writeFileSync(filePath, text, 'utf8');
```

内嵌 HTML / JS 模板中的中文若容易受 shell 转码影响，可使用 Unicode 转义。

完成后搜索常见乱码特征：

```powershell
rg -n "�|锟|Ã|Â" <modified-files>
```

发现乱码时只修复任务涉及区域，不进行全仓猜测性替换。

## 14. 版本控制规则

### 14.1 分支

- FleetDock 工作分支使用 `codex/` 前缀，除非用户指定其他命名。
- 不直接在官方上游分支上开发 FleetDock 功能。
- 开始工作前确认当前分支，避免在旧项目或错误 worktree 中提交。

### 14.2 提交

- 提交必须小而完整，可独立审查和回滚。
- 不把功能、重构、格式化、依赖更新混在同一提交。
- 提交消息使用祈使式英文，示例：

```text
Restore FleetDock welcome workspace styling
Bundle FleetDock production dependencies
```

- 提交前运行 `git diff --check`。
- 提交后确认工作树状态和提交内容。
- 不使用 `--amend` 改写用户或其他 Agent 的提交，除非得到明确授权。

### 14.3 暂存

- 只暂存当前任务文件，使用显式路径。
- 禁止默认使用 `git add -A` 或 `git add .` 吸收来源不明的改动。
- 提交前查看 staged diff。

### 14.4 推送

当前 `origin` 是官方 JupyterLab Desktop 仓库，不是 FleetDock 私有远程。

- 未经用户明确授权，不得执行 `git push`。
- 推送前必须再次确认远程 URL、目标分支和权限。
- 禁止向官方 `origin/master` 或其他上游维护分支推送 FleetDock 提交。
- 禁止 force push，除非用户明确要求且目标分支确认无误。

### 14.5 禁止提交

除非仓库策略或用户明确要求，不提交：

- `node_modules/`
- `build/`
- `dist/`
- `env_installer/jlab_server/`
- 临时 Conda 环境
- 本地日志和缓存
- 截图预览
- 密钥、token、证书和设备凭据
- 未审核的大型仿真二进制

## 15. 上游迁移与升级规则

### 15.1 不变量

- 旧 FleetDock 项目保持只读。
- 新官方仓库是唯一迁移目标。
- 新上游依赖和构建配置优先。
- 只迁移 FleetDock 特有配置、业务和品牌资源。
- FleetDock 功能通过隔离模块和小型通用钩子挂载。

### 15.2 更新前

1. 确认工作树干净，或明确保护已有未提交内容。
2. 记录当前 FleetDock HEAD 和目标上游 revision。
3. 阅读上游 Release、迁移说明和依赖变化。
4. 检查 Electron、JupyterLab、Node 和 Conda 的重大版本变化。
5. 不在旧项目中做“预合并”。

### 15.3 合并上游

标准流程：

```powershell
git fetch origin
git switch codex/fleetdock-upstream-migration
git merge --no-ff origin/master
```

若官方默认分支发生变化，以实际远程为准，并更新 `FLEETDOCK_MIGRATION.md`。

### 15.4 冲突处理

- 不选择“全部采用旧版”。
- 先理解新上游为何修改该文件。
- 保留新上游的数据结构、生命周期和安全修复。
- 只重放冲突位置所需的通用扩展钩子或 FleetDock 配置。
- 如果上游已经提供等价扩展点，删除自建钩子并迁移到上游接口。
- 依赖冲突默认采用新上游版本，FleetDock 特有依赖另行评审。
- 对 `package.json`、preload、IPC、`SessionWindow` 和打包配置进行重点复核。

禁止：

- 用旧文件覆盖新文件。
- 为快速通过编译而绕过 sender 校验。
- 恢复已被上游删除的过时 Electron API。
- 在冲突解决时顺便重构无关 FleetDock 模块。

### 15.5 合并后检查

至少完成：

1. 对比新上游与 FleetDock 分支的 diff。
2. 确认 FleetDock 业务仍位于隔离目录。
3. 确认 `package.json` 和 `yarn.lock` 以新上游为基础。
4. 运行 type-check、build、单元测试和相关 FleetDock 检查。
5. 验证欢迎页、标题栏、JupyterLab 注入和 IPC。
6. 验证应用创建、设备管理和本地仿真启动/停止。
7. 构建 unpacked 应用并检查打包资源。
8. 更新 `FLEETDOCK_MIGRATION.md` 的 revision、冲突和验证记录。

## 16. 安全与破坏性操作

- 删除、覆盖、移动、清理前必须确认绝对路径和边界。
- 不对仓库根目录、用户目录、磁盘根目录执行递归删除。
- 清理进程前确认 PID、启动来源和本次运行所有权。
- 不自动修改 WSL 配置、系统 PATH、注册表、防火墙或系统服务。
- 不自动安装驱动、PX4、Project AirSim 或系统级 GStreamer。
- 外部环境变更必须明确说明影响和恢复方式。
- 对用户数据、工作区和设备配置优先采用可恢复操作。

## 17. 完成与交付标准

任务完成前逐项确认：

- [ ] 修改发生在正确的新仓库和分支。
- [ ] 旧项目未被修改。
- [ ] diff 足够小且无无关格式化。
- [ ] FleetDock 逻辑优先位于隔离模块。
- [ ] 上游核心只包含必要的通用钩子。
- [ ] 新增 FleetDock 钩子有 `[UAV-IDE MODIFY]` 注释。
- [ ] IPC sender 校验和安全边界未削弱。
- [ ] 中文编码正常。
- [ ] 相关构建、检查和定向验证已通过。
- [ ] 生成环境、用户运行时和发布包状态没有混淆。
- [ ] Git 提交边界清晰，工作树状态已复核。
- [ ] 未推送到错误远程。
- [ ] 交接中列明提交、验证、风险和用户下一步。

当无法满足某一项时，不得静默宣称完成；必须明确说明原因、风险和后续处理建议。
