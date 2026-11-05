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
