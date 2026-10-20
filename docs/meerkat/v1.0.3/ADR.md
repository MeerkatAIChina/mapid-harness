# ADR（架构决策记录）— v1.0.3

> 项目守则第 4 条：触及 Octop 内核或架构层面的修改必须记录于此。

## R1（2026-10-19 ~ 2026-10-20）：无条目

R1 全部改动挂载在 Octop 插件系统与 mapid-harness 自有模块（`src/octop_harness/report_qa/`、
`report_tools.py`、`middleware/`）上，**Octop 内核零改动、无架构层面修改**，按守则无需 ADR 条目。

装载期发现的中间件撞名 bug（langchain 按类名去重）修复于 mapid-harness 自有代码
（`PatientModelRetryMiddleware` 子类），追溯记录见 `rounds/round-1/装载问题记录.md`
（仓库 Issues 未启用，以此承担 issue 追溯职能）。
