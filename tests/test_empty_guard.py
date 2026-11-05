"""空完成护栏测试（验收记录_R5 §候选改进①：API 200+空内容不再静默当成功）。"""

from __future__ import annotations

import pytest
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage

from octop_harness.middleware.empty_guard import (
    EmptyCompletionError,
    EmptyCompletionGuardMiddleware,
    EmptyGuardSettings,
)
from octop_harness.middleware.patient_retry import _logging_retry_on


def _resp(content="", tool_calls=None):
    return ModelResponse(result=[AIMessage(content=content, tool_calls=tool_calls or [])])


async def test_guard_raises_on_empty_content():
    mw = EmptyCompletionGuardMiddleware(EmptyGuardSettings(enabled=True))

    async def handler(request):
        return _resp("")

    with pytest.raises(EmptyCompletionError):
        await mw.awrap_model_call(object(), handler)


async def test_guard_raises_on_whitespace_content():
    mw = EmptyCompletionGuardMiddleware(EmptyGuardSettings(enabled=True))

    async def handler(request):
        return _resp("  \n\t ")

    with pytest.raises(EmptyCompletionError):
        await mw.awrap_model_call(object(), handler)


async def test_guard_raises_on_empty_block_content():
    mw = EmptyCompletionGuardMiddleware(EmptyGuardSettings(enabled=True))

    async def handler(request):
        return _resp([{"type": "text", "text": ""}])

    with pytest.raises(EmptyCompletionError):
        await mw.awrap_model_call(object(), handler)


async def test_guard_raises_on_zero_messages():
    mw = EmptyCompletionGuardMiddleware(EmptyGuardSettings(enabled=True))

    async def handler(request):
        return ModelResponse(result=[])

    with pytest.raises(EmptyCompletionError):
        await mw.awrap_model_call(object(), handler)


async def test_guard_passes_tool_call_response():
    mw = EmptyCompletionGuardMiddleware(EmptyGuardSettings(enabled=True))
    resp = _resp("", tool_calls=[{"name": "write_file", "args": {}, "id": "1"}])

    async def handler(request):
        return resp

    assert await mw.awrap_model_call(object(), handler) is resp


async def test_guard_passes_nonempty_content():
    mw = EmptyCompletionGuardMiddleware(EmptyGuardSettings(enabled=True))
    resp = _resp("第一章已完成。")

    async def handler(request):
        return resp

    assert await mw.awrap_model_call(object(), handler) is resp


async def test_guard_disabled_passthrough():
    mw = EmptyCompletionGuardMiddleware(EmptyGuardSettings(enabled=False))
    resp = _resp("")

    async def handler(request):
        return resp

    assert await mw.awrap_model_call(object(), handler) is resp


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("OCTOP_EMPTY_GUARD_ENABLED", "1")
    assert EmptyGuardSettings.from_env().enabled is True
    monkeypatch.setenv("OCTOP_EMPTY_GUARD_ENABLED", "")
    assert EmptyGuardSettings.from_env().enabled is False


def test_patient_retry_treats_empty_completion_retryable():
    # 空完成交内层耐心重试吸收（瞬时）；耗尽后由外层转可见错误
    assert _logging_retry_on(EmptyCompletionError("empty")) is True
