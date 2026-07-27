# 贡献指南

变更公共 SDK 时必须同步中文 docstring、README/文档和测试。提交前运行：

```powershell
C:\ProgramData\anaconda3\envs\fleetdock\python.exe -m unittest discover -s tests
C:\ProgramData\anaconda3\envs\fleetdock\python.exe -m build
```

稳定公共 API 的破坏性调整必须先弃用，并在 `CHANGELOG.md` 中说明迁移路径。
