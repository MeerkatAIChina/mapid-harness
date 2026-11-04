"""耐心重试 middleware（Meerkat T4 断流续跑）。

Provisional：内部模块，API 可能变，不进公共 ``__all__``。

链路背景（调研结论）：模型调用失败后，Octop 外层的 ``ModelRetryMiddleware``
重试 2 次，耗尽即把错误转成一条无工具调用的 AIMessage，turn 优雅终止、等人工
「继续」。本模块作为**内层**重试 middleware（经 hook 插件注入），用更大的预算
把短暂断流吸收在 turn 内部——turn 不结束，也就不需要人工干预。

约束实现：
- 参数可配置：默认值见模块常量，插件目录 ``config.json`` 可覆盖（不改代码即可校准）；
- 重试可见：每次失败经 ``retry_on`` 钩子写 warning 日志（harness 日志带 agent 标签），
  观察链路与「0 人工干预」判定据此区分机器恢复与人工干预；
- 循环保护：次数硬上限，耗尽后 ``on_failure="error"`` 抛给外层走既有报错路径。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import ModelRetryMiddleware
from langchain.agents.middleware._retry import default_retry_on

from octop_harness.middleware.empty_guard import EmptyCompletionError

logger = logging.getLogger(__name__)

# --- 默认重试预算（与外层 Octop ModelRetryMiddleware 协调：内层单周期约 4 分钟，
#     外层最多再叠加 2 个周期，总耐心约 12 分钟封顶，指数退避不构成重试风暴） ----
DEFAULT_MAX_RETRIES = 5
DEFAULT_INITIAL_DELAY = 15.0
DEFAULT_BACKOFF_FACTOR = 2.0
DEFAULT_MAX_DELAY = 120.0


@dataclass(frozen=True)
class PatientRetrySettings:
    """耐心重试参数；插件 ``config.json`` 的键与之同名。"""

    max_retries: int = DEFAULT_MAX_RETRIES
    initial_delay: float = DEFAULT_INITIAL_DELAY
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR
    max_delay: float = DEFAULT_MAX_DELAY

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PatientRetrySettings:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def _logging_retry_on(exc: Exception) -> bool:
    """每次模型调用失败的记录钩子：写日志后按默认规则判定是否重试。

    空完成（empty_guard 抛出的 ``EmptyCompletionError``）不在 langchain
    默认可重试集合内，显式并入——瞬时空响应由本层吸收，耗尽仍抛给外层。
    """
    retryable = default_retry_on(exc) or isinstance(exc, EmptyCompletionError)
    logger.warning(
        "patient_retry: model call failed (%s: %s), retryable=%s",
        type(exc).__name__,
        exc,
        retryable,
    )
    return retryable


class PatientModelRetryMiddleware(ModelRetryMiddleware):
    """内层耐心重试；与 Octop 外层 ``ModelRetryMiddleware`` 区分类名。

    langchain ``create_agent`` 按 ``middleware.name``（= 类名）去重，两个同名
    ``ModelRetryMiddleware`` 实例会被 ``AssertionError`` 拒掉（R1 插件装载时发现，
    专家无法启动）。子类化使 ``name`` 变为 ``PatientModelRetryMiddleware``。
    """


def build_patient_model_retry(
    settings: PatientRetrySettings | None = None,
) -> PatientModelRetryMiddleware:
    """返回配置好的内层耐心重试 middleware。"""
    cfg = settings or PatientRetrySettings()
    return PatientModelRetryMiddleware(
        max_retries=cfg.max_retries,
        retry_on=_logging_retry_on,
        on_failure="error",  # 耗尽后抛给外层（Octop 的 ModelRetryMiddleware）
        backoff_factor=cfg.backoff_factor,
        initial_delay=cfg.initial_delay,
        max_delay=cfg.max_delay,
        jitter=True,
    )
