# ADR（架构决策记录）— v1.0.6

> 项目守则：触及 octop-harness 内核（`protocols/`、`agent.py`、`manager.py` 等通用层）或架构层面的修改必须记录于此；版本 CHANGELOG 中内核缝改动单独标注并指回本文件。

## ADR-001（2026-11-05）：内部工序模型调用经 lc_source 打标，协议层统一过滤（内核缝）

**背景**

R4 长报告机制的核验 / 审校 / 出口核实工序（`middleware/verification.py`、`review_chain.py`、`chapter_gate.py`）在工具中间件内部发起模型调用。这些调用的流式 token 会进入 LangGraph messages 流，被协议层当作「用户可见回复」透传到聊天界面（round-6 后端 bug ⑤：用户在会话里看到内部核验 JSON 与病态重复内容，并与前端断连重补叠加成「ws 重放旧缓存」观感 bug ③）。

**决策**

1. `middleware/verification.py` 定义并导出常量 `INTERNAL_STREAM_CONFIG = {"metadata": {"lc_source": "verification"}}`；全部 4 个内部工序调用点（断言抽取、断言判定、`run_review`、`chapter_gate._assess_exit`）统一携带该 config。
2. **内核缝**：`protocols/langgraph.py` 的 `_INTERNAL_LC_SOURCES` 由 `{"summarization"}` 扩为 `{"summarization", "verification"}`——协议层按 `lc_source` 元数据过滤内部模型流，与既有 summarization（对话压缩）过滤同一机制、同一语义。

**被否决方案**

- 在 octop（宿主平台）侧过滤：违反「fork 随主仓升级、源码不动」边界，且泄漏源在 harness 层，应在源头打标。
- 按模型名 / 调用方身份过滤：judge 模型由 `OCTOP_VERIFY_MODEL` 配置，与写作模型可能同源，不可靠。
- 协议层按输出内容特征（JSON 形态等）过滤：脆弱且误伤面大。
- 为内部工序单独开非流式调用通道：改动面远大于一行枚举扩展，且丧失统一的流式观测口径。

**后果**

- 内核面改动收敛为一行枚举值扩展，语义向后兼容（仅新增过滤来源，不改变既有过滤行为）。
- 后续任何新增「中间件内部模型调用」工序，必须复用 `INTERNAL_STREAM_CONFIG`（或向 `_INTERNAL_LC_SOURCES` 登记新来源并更新本 ADR），否则会重新泄漏。
- 对用户可见行为的影响：聊天界面不再出现内部核验/审校输出；bug ③ 的主因随之消除。

**验证**

- 新增回归测试 `tests/test_protocols/test_langgraph.py::test_stream_skips_internal_verification_model_output`（先红后绿）。
- 相关 94 个测试全绿（protocols + middleware 核验链路）。

**状态**：代码已完成未提交（工作区），提交时 CHANGELOG 需标注「内核缝 → ADR-001」。

## ADR-002（2026-11-06）：台账来源编号作为「查无实证」的结案凭证（机制行为变更，用户批准）

**背景**

round-8 实战首次抵达 merge 硬闸：95 条「断言查无实证未处理」卡死合并（`chapter_gate.py` merge 硬失败）。逐条复核 101 条 `verify_notes`（含后续滚动）发现其构成：

- 主体（约半数）是「正文带来源编号 `[n]`、台账 `data_ledger.md` 已登记该编号，但 judge 外部检索未命中」的法定披露数据——本轮 unverifiable 率 46-56%，主因是检索工具能力（空页 / PDF 乱码），而非断言无据；
- 约 15-20 条正文无编号，真正需要降级 / 标注；
- 8 条是审校回流疑点（「请核对 / 需核实」式元评论），judge 对元评论只能判 unverifiable，落入「降级表述或标注」桶语义不通。

写作 agent 拒绝按闸门要求批量打 `[待核实]`（= 把已核实年报数据虚假降级，违反提示词数据红线），交回人工裁决。**用户裁决**：批准「有源未中」以台账凭证结案，「无源」维持原规则。

**决策**

1. `verification.is_acknowledged()` 增加第三层判定（可选参数 `ledger_text`）：断言自带、或正文同事实句（共享 ≥2 位数字串 / 承重关键词）带有的来源编号 `[n]`，若 `n` 在 `data_ledger.md` 引用编号登记表（首列数字行）中已登记，视为已结案，不进 `verify_notes`。未登记编号（如 `[42]` 查无此号）不算凭证——编号造假 / 漏登记仍被拦。judge 的 unverifiable 裁决不改写，日志留痕。
2. `chapter_gate._verify_chapter()` 中，审校回流疑点被判 unverifiable 时不再进 `verify_notes`（不占 merge 待处理名额），改为 `verification_log.jsonl` 记 `doubt_unresolved` 留痕；refuted 疑点仍进 `verify_findings`（证伪语义不变）。
3. **merge 闸对存量 `verify_notes` 重新分类**（同日补刀）：凭证判定只在产出 notes 时生效，而旧口径落盘的存量 notes 因「已过章不重复核」（round-7 F1）永远不会重算——round-8 恢复后 12 章 78 条存量仍会卡闸。merge 硬闸在计数前按现行 `is_acknowledged`（降级标注 / 台账凭证）对存量 notes 重分类并落盘，有源未中出队、无源条目继续拦。

**被否决方案**

- merge 闸按「断言是否仍出现在当前正文」过滤 `verify_notes`（原拟修复方向）：实证否决——抽取断言是 LLM 改写（插入删改、全角减号差异），子串匹配会误杀真断言，fail-open 削弱闸门。
- 放宽闸门仅 `refuted` 阻断（agent 建议①）：无条件信任检索假阴性，削弱闸门，用户未选。
- 人工逐条复核 95 条后放行（agent 建议③）：本轮可由机制消纳的主体不该转嫁给人工。

**后果**

- 「查无实证」从单桶拆为「无源」（维持原规则：降级 / 打标）与「有源未中」（凭证结案）两类；merge 闸口径随之变化，属机制行为变更。
- 残余风险：编号指向的来源本身不含该数字（台账登记造假 / 错配）时此路径防不住；对冲 = merge 环节既有台账反查 + 事后抽检。R5 应评估「台账登记真实性抽检」工序。
- `doubt_unresolved` 目前仅留痕不驱动修复，R5 需设计疑点未决的正式处置路径。

**验证**

- 新增测试先红后绿：`tests/test_verification.py` 4 个（自带编号 / 未登记编号 / 正文同事实句编号 / 无登记表维持原行为 + 疑点元评论分流）、`tests/test_chapter_gate_middleware.py` 2 个（带登记编号的查无实证 merge 放行；存量 notes merge 重分类：有源出队、无源继续拦）。
- 全量套件：2144 passed + 53 failed（与基线一致，全为 Windows 平台类预存在失败，无新增）。
- ruff check / ruff format / mypy --strict（改动文件）全绿。

**状态**：代码已完成未提交（工作区）；本次不触及内核，无需内核缝标注，但属机制行为变更，CHANGELOG 需指回本 ADR。
