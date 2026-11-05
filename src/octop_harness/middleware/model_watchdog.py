"""模型调用看门狗 middleware（A5：线上端点静默挂起超时判失败）。

设计文档_R4 §4：挂起不抛异常 → 既有重试层全部抓不到。本模块用
``asyncio.wait_for`` 包住下游调用，超时抛内置 ``TimeoutError``——
Task 5 拔测已证 langchain ``default_retry_on`` 对其判 True，patient_retry
会接走重试（本中间件必须挂在 patient_retry **内侧**，每次重试独立计时）。

挂载形态照抄 patient_retry：实现在此，shim 插件 ``meerkat-watchdog``
（``.octop-dev/plugins/`` 环境目录）注入，priority 排 meerkat-resilience 之后。

仅实现异步路径（agent 运行时为 async）；同步 ``wrap_model_call`` 不包超时。
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 300.0


@dataclass(frozen=True)
class ModelWatchdogSettings:
    """看门狗参数；插件 ``config.json`` 或 env 覆盖。"""

    timeout_s: float = DEFAULT_TIMEOUT_S
    enabled: bool = False

    @classmethod
    def from_env(cls) -> ModelWatchdogSettings:
        enabled = os.environ.get("OCTOP_MODEL_WATCHDOG_ENABLED", "") == "1"
        timeout = float(os.environ.get("OCTOP_MODEL_WATCHDOG_TIMEOUT_S", DEFAULT_TIMEOUT_S))
        return cls(timeout_s=timeout, enabled=enabled)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelWatchdogSettings:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


class ModelWatchdogMiddleware(AgentMiddleware[Any, Any]):
    """超时即抛 TimeoutError，交内层重试链（patient_retry）处理。"""

    def __init__(self, settings: ModelWatchdogSettings | None = None):
        super().__init__()
        self.settings = settings or ModelWatchdogSettings.from_env()

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        if not self.settings.enabled:
            return await handler(request)
        try:
            return await asyncio.wait_for(handler(request), timeout=self.settings.timeout_s)
        except TimeoutError:
            logger.warning("model_watchdog: call exceeded %ss", self.settings.timeout_s)
            raise

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(request)
