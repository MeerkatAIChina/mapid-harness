"""当前日期注入 middleware（Meerkat T5 实验卫生）。

Provisional：内部模块，API 可能变，不进公共 ``__all__``。

基线缺陷：harness 系统提示不含运行时日期，模型把报告落款写成 2025-07-09
（训练数据里的日期）。本 middleware 在每次模型调用前把当前日期追加进
system message（幂等，已注入则跳过），经 hook 插件挂载，内核零改动。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

logger = logging.getLogger(__name__)

# --- Meerkat 约定 --------------------------------------------------------------
DATE_MARK = "当前日期："  # 幂等判定标记
_WEEKDAY_ZH: tuple[str, ...] = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def date_note(now: datetime | None = None) -> str:
    """注入文本：日期 + 星期（星期不让模型自己推算，直接给权威值）。"""
    now = now or datetime.now().astimezone()
    return (
        f"[系统注入] {DATE_MARK}{now:%Y-%m-%d} {_WEEKDAY_ZH[now.weekday()]}。"
        "报告中所有落款、台账、时效性判断均以该日期为准。"
    )


def inject_current_date(request: Any, *, now: datetime | None = None) -> Any:
    """把日期注入 ``ModelRequest`` 的 system message（幂等）。返回新 request。"""
    note = date_note(now)
    msg = request.system_message
    if msg is not None:
        content = msg.content
        if isinstance(content, str):
            if DATE_MARK in content:
                return request
            return request.override(system_message=SystemMessage(content=f"{content}\n\n{note}"))
        blocks = list(content)
        if any(DATE_MARK in str(block) for block in blocks):
            return request
        return request.override(
            system_message=SystemMessage(content=[*blocks, {"type": "text", "text": note}])
        )
    prompt = request.system_prompt or ""
    if DATE_MARK in prompt:
        return request
    return request.override(system_prompt=f"{prompt}\n\n{note}")


class DateInjectionMiddleware(AgentMiddleware):
    """每次模型调用前注入当前日期（含星期）到 system message。"""

    @staticmethod
    def _inject_logged(request: Any) -> Any:
        """注入并记录日志（实证用：确认 middleware 级注入真实发生，见 R1 遗留项②）。"""
        new_request = inject_current_date(request)
        if new_request is not request:
            logger.info("date_injection: injected current date into system message")
        return new_request

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(self._inject_logged(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(self._inject_logged(request))
