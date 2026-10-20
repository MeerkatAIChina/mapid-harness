# R1 改动包（changes.md）

> 轮次：round-1 · 日期：2026-10-19 ~ 2026-10-20
> 归因层打包：工具层 / 机制层 / 提示词层，各自可独立验证。

## 工具层（T1/T2）

| 改动 | 位置 | 验证 |
|---|---|---|
| report_qa 检查器模块（8 类检查） | `src/octop_harness/report_qa/` | 33 单测 + 金缺陷回归 TP 9/9 |
| `merge_chapters` / `report_stats` 插件工具 | `src/octop_harness/report_tools.py` + 插件 shim `meerkat-report` | 18 单测；快速轮实际调用 ✅ |
| 回归脚本 | `docs/meerkat/v1.0.3/golden_defects/run_checker_regression.py` | 五轮迭代后 TP 100% / FP 0 |

### 检查器修复记录（回归 + 快速轮驱动）

| 来源 | 问题 | 修复 |
|---|---|---|
| 回归 R2→3 | ASCII 图行被判表格行、分隔行打断表块 | `_is_table_line` 排除结构行；块扫描补分隔行条件 |
| 回归 R2→3 | 「根据年报…」句式、署名行误报 | `SOURCE_RE` / `ATTRIBUTION_RE` / `RULE_LINE_RE` |
| 回归 R3→4 | 模板爆破对正常数据表 FP（8 处） | 「模板且多变」列 ≥2 才判定 |
| 回归 R4→5 | 装饰性前后缀（~/%/20/次）被当模子 | 模子须为实质前缀（含汉字或 ≥3 字符） |
| 回归 R3 | fence 内 ASCII 图例行被判逐字重复 | fence 块内行豁免 |
| 快速轮 | 台账「口径/年份」复合列名误报 schema（级联 freshness 全章告警） | `_header_covers` 按主列名匹配 |
| 快速轮 | 「约 7,000 字」字数声明被判精确数值 | `_is_word_count_decl` 过滤（unsourced + freshness 触发线） |
| 快速轮 | 附录待核实清单触发 freshness（结构性误报） | 附录章豁免 freshness（`APPENDIX_MARK`） |

## 机制层（T4/T5）

| 改动 | 位置 | 验证 |
|---|---|---|
| 耐心重试 middleware（内层 5 次指数退避） | `src/octop_harness/middleware/patient_retry.py` + shim `meerkat-resilience` | 7 单测；本轮无断流未触发（遗留：模拟拔测） |
| **撞名修复**：`PatientModelRetryMiddleware` 子类 | 同上 | langchain 按类名去重；防回归测试已加 |
| 日期注入 middleware | `src/octop_harness/middleware/date_injection.py` + shim `meerkat-date` | 6 单测；快速轮强信号（全产物日期=今天） |
| 记忆归档脚本 | `scripts/reset_agent_memory.py` | 本轮两次归档（基线 536MB + 中止轮），可回滚 |

## 提示词层（T3）

| 改动 | 位置 |
|---|---|
| 提示词 v3（软门禁三问/结构化台账/禁自报字数/内建合并/先搜后写/判据一致） | `prompts/竞品分析_欧普照明_v3.md` |
| 快速轮缩量版（5 章约 3 万字，机制逐字保留） | `prompts/竞品分析_欧普照明_v3_快速轮.md` |

## 实验基建（本轮新增）

- 基线 workspace 冻结：`baseline/baseline_workspace/`（金缺陷宿主从活目录改为冻结工件）
- 检查单增补「§3 workspace 隔离」（模板 + 本轮副本）
- `plan/Octop插件装载与重启步骤.md`（热装载 `POST /api/plugins/reload` 或重启）
- `rounds/round-1/装载问题记录.md`（仓库 Issues 未启用，追溯链以此承担）

## Harness commit

待用户批准后提交（feature/triz 分支）。涉及的 Octop 内核改动：**零**
（全部挂在插件系统与 mapid-harness 自有模块上，无需 ADR）。
