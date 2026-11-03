"""模型调用看门狗测试（设计文档_R4 §4 / 执行计划 Task 6）。"""

from __future__ import annotations

import asyncio

import pytest

from octop_harness.middleware.model_watchdog import (
    ModelWatchdogMiddleware,
    ModelWatchdogSettings,
)


async def test_watchdog_times_out_hanging_call():
    mw = ModelWatchdogMiddleware(ModelWatchdogSettings(timeout_s=0.05, enabled=True))

    async def handler(request):
        await asyncio.sleep(10)
        return "ok"

    with pytest.raises(TimeoutError):
        await mw.awrap_model_call(object(), handler)


async def test_watchdog_passes_fast_call():
    mw = ModelWatchdogMiddleware(ModelWatchdogSettings(timeout_s=5.0, enabled=True))

    async def handler(request):
        return "ok"

    assert await mw.awrap_model_call(object(), handler) == "ok"


async def test_watchdog_disabled_passthrough():
    # enabled=False → 不加 wait_for：慢调用也原样透传
    mw = ModelWatchdogMiddleware(ModelWatchdogSettings(timeout_s=0.01, enabled=False))

    async def handler(request):
        await asyncio.sleep(0.05)
        return "ok"

    assert await mw.awrap_model_call(object(), handler) == "ok"


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("OCTOP_MODEL_WATCHDOG_ENABLED", "1")
    monkeypatch.setenv("OCTOP_MODEL_WATCHDOG_TIMEOUT_S", "42")
    s = ModelWatchdogSettings.from_env()
    assert s.enabled is True and s.timeout_s == 42.0
