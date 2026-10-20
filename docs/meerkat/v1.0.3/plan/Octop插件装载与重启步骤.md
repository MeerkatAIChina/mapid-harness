# Octop 插件装载与重启步骤（R1 用）

> 日期：2026-10-20　前置：金缺陷回归已达标（见 `golden_defects/回归报告_检查器层.md`）
> 目标：把 R1 的三个插件（merge/stats 工具、耐心重试、日期注入）载进运行中的 Octop，
> 并验证生效。全程不动 Octop 内核代码。

## 0. 前置事实（已验证，无需操作）

- venv 的 `octop_harness` 是 editable 安装，指向 `E:\projects\mapid-harness\src`
  —— T1/T2/T4/T5 的代码改动直接生效，**不需要安装/打包步骤**。
- 三个插件 shim 已就位且验证可加载：
  - `.octop-dev/plugins/meerkat-report/`（kind: tool → `merge_chapters`、`report_stats`）
  - `.octop-dev/plugins/meerkat-resilience/`（kind: hook → 耐心重试；同目录 `config.json` 可调参数）
  - `.octop-dev/plugins/meerkat-date/`（kind: hook → system prompt 日期注入）
- 插件默认全局+agent 级启用；**禁用 = 在插件管理页 disable，或把目录改名/移走后 reload**。

## 1. 配置 workspace_root（merge_chapters / report_stats 需要）

插件工具本身无 workspace 绑定，根目录走 agent 的 `config_json`：

```
config_json.plugins.meerkat-report.tools.<tool>.config.workspace_root
```

对基线 agent（CompetitorAnalysis），在其配置中写入（UI 的 agent 编辑页，或 API 更新 config_json）：

```json
"plugins": {
  "meerkat-report": {
    "tools": {
      "merge_chapters": {"config": {"workspace_root": "E:\\projects\\meerkatai-octop\\.octop-dev\\agents\\CompetitorAnalysis"}},
      "report_stats":   {"config": {"workspace_root": "E:\\projects\\meerkatai-octop\\.octop-dev\\agents\\CompetitorAnalysis"}}
    }
  }
}
```

> workspace_root = agent 目录本身（chapters/、data_ledger.md 所在处）。
> 改动前备份原 config_json。不配的话工具仍可调用，但相对路径会报
> 「Error: 相对路径需要配置 workspace_root…」，绝对路径直通不受影响。

## 2. 装载（二选一）

- **热装载（推荐）**：`POST /api/plugins/reload`（需 admin / plugins 权限）。
  会重扫 `.octop-dev/plugins/` 并 `agent_registry.reload_all()`，无需停服务。
- **冷启动**：按日常方式重启 Octop 后端，启动时自动扫描装载。

## 3. 验证清单（装载后逐项打勾）

- [ ] `GET /api/plugins`：meerkat-report / meerkat-resilience / meerkat-date
      三个 `loaded=true, enabled=true`。
- [ ] **report_stats**：让 CompetitorAnalysis「统计一下 chapters/ 各章字数」，
      应返回各章字数表，而不是 workspace_root 错误。
- [ ] **merge_chapters**：让 agent 合并章节到 `report_merged.md`，
      检查产出文件的章节顺序、`<!-- ===== 文件名 ===== -->` 标记、体量自校验通过。
- [ ] **meerkat-date**：新一轮对话后，日志/trajectory 的 system prompt 含
      `[系统注入] 当前日期：YYYY-MM-DD`（幂等，重复出现一次即正常）。
- [ ] **meerkat-resilience**：无法主动安全触发；首轮观察日志——若发生模型调用失败，
      应出现 `patient_retry: model call failed...` warning 且 turn 内自动恢复
      （重试事件可见性验收，对齐执行计划 T4 约束②）。

## 4. 回滚

任意插件出问题：插件管理页 disable（或移走目录）→ `POST /api/plugins/reload`。
三插件相互独立，可单独回滚；对 Octop 内核零改动，无需回滚代码。

## 5. 注意

- 插件默认对**所有 agent** 启用。R1 只关注 CompetitorAnalysis；其他 agent
  （AD4B6B/RKSX0F）若跑任务也会加载这三个插件，属预期。
- meerkat-resilience 参数调整：改 `.octop-dev/plugins/meerkat-resilience/config.json`
  （键同 `PatientRetrySettings`：max_retries/initial_delay/backoff_factor/max_delay），
  reload 后生效。
