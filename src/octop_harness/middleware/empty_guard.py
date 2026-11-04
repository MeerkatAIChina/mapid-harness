"""空完成护栏 middleware（round-5 模型事故：API 200 + 空内容被静默当成功）。

验收记录_R5 §候选改进①。模型返回 200 但 AIMessage 无正文且无 tool_calls
时，turn 会"优雅"终止——看起来像完成，实际是模型退化（round-5 三连空
响应自行收尾）。本中间件对该响应抛 ``EmptyCompletionError``：瞬时空完成
由内层 patient_retry 吸收重试，耗尽后经外层重试链转为可见错误 turn，
不再静默。

挂载形态照抄 model_watchdog：实现在此，shim 插件 ``meerkat-empty-guard``
（``.octop-dev/plugins/`` 环境目录）注入，priority=120 排在 watchdog
（110）内侧。

仅实现异步路径（agent 运行时为 async）；同步 ``wrap_model_call`` 原样透传。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware

logger = logging.getLogger(__name__)


class EmptyCompletionError(RuntimeError):
    """模型调用成功返回但内容为空（无正文、无 tool_calls）。"""


@dataclass(frozen=True)
class EmptyGuardSettings:
    """护栏参数；插件 ``config.json`` 或 env 覆盖。"""

    enabled: bool = False

    @classmethod
    def from_env(cls) -> EmptyGuardSettings:
        return cls(enabled=os.environ.get("OCTOP_EMPTY_GUARD_ENABLED", "") == "1")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EmptyGuardSettings:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def _text_of(content: Any) -> str:
    """从 str / content block 列表中提取纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return ""


def _is_empty_response(response: Any) -> bool:
    """判定响应为空完成：没有任何非空白正文且没有 tool_calls。

    非预期形态（无 ``result`` 属性）保守放行，不误伤。
    """
    messages = getattr(response, "result", None)
    if messages is None:
        return False
    if len(messages) == 0:
        return True
    for msg in messages:
        if getattr(msg, "tool_calls", None):
            return False
        if _text_of(getattr(msg, "content", "")).strip():
            return False
    return True


class EmptyCompletionGuardMiddleware(AgentMiddleware[Any, Any]):
    """空完成即抛 EmptyCompletionError，交内层重试链（patient_retry）处理。"""

    def __init__(self, settings: EmptyGuardSettings | None = None):
        super().__init__()
        self.settings = settings or EmptyGuardSettings.from_env()

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        if not self.settings.enabled:
            return await handler(request)
        response = await handler(request)
        if _is_empty_response(response):
            logger.warning("empty_guard: model returned empty completion")
            raise EmptyCompletionError("model returned empty completion")
        return response

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(request)
