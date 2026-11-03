# Task 12：round-4 产物三格式端到端渲染验收记录

日期：2026-11-03 ｜ 执行：native（执行计划_R4 Task 12）

## 输入

- `docs/meerkat/v1.0.5/rounds/round-4/product/report_merged.md`（255 KB，round-4 真实产物）

## 命令

```powershell
uv run python -m octop_harness.pipeline.build_outputs \
  docs/meerkat/v1.0.5/rounds/round-4/product/report_merged.md \
  docs/meerkat/v1.0.5/rounds/round-4/product/rendered --base-name report_merged
```

## 产物

| 格式 | 大小 | 耗时 |
|---|---|---|
| docx | 123,324 B | 4.1s |
| html | 292,073 B | 0.1s |
| pdf | 1,348,594 B | 8.2s |

`validate_report` 返回空列表（全过）。

## 机器抽查（HTML）

- 标题（h1/h2）84 个、表格 17 张：章节骨架与表格渲染完整。
- 图表 0 张：round-4 产物不含 chart DSL 代码块（图表 DSL 是 R4 才引入的契约），非渲染缺陷。
- 无「本章参考资料」节：round-4 是 v5 骨架，顺序编码制（章末参考资料节）是 v6 提示词的契约，非渲染缺陷。

## 意外发现（真实占位符残留）

round-4 产物正文含两处占位符（GD-21 型在真实产物中的实例）：

- L805：「累计帮助客户节约用电量约 **XX 亿** 千瓦时」
- L811：「员工培训时长累计超过 **XX 万** 小时」

这正是 R4 新增 `placeholder_scan` 检查器（Task 8，GD-21）要拦的缺陷。round-4 早于该检查器上线，属旧轮次的已知质量债，非本次渲染引入。该发现反向验证了 GD-21 的现实性。

## 遗留

- 计划 Step 2「人读 HTML/PDF 抽查 3 章（排版、表格、目录）」留待整分支评审时由用户执行，产物快照已存 `rendered/` 备查。
