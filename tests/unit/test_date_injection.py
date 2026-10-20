"""date_injection 的单元测试（T5）。"""

from __future__ import annotations

from datetime import UTC, datetime

from langchain.agents.middleware.types import ModelRequest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import SystemMessage

from octop_harness.middleware.date_injection import (
    DateInjectionMiddleware,
    date_note,
    inject_current_date,
)

_NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)  # 周一


def _request(system_message=None, system_prompt=None) -> ModelRequest:
    return ModelRequest(
        model=GenericFakeChatModel(messages=iter([])),
        messages=[],
        system_message=system_message,
        system_prompt=system_prompt if system_message is None else None,
    )


def test_date_note_contains_date_and_weekday():
    note = date_note(_NOW)
    assert "2026-10-19" in note
    assert "周一" in note
    assert note.startswith("[系统注入]")


def test_inject_appends_to_string_system_message():
    req = _request(system_message=SystemMessage(content="你是助手。"))
    out = inject_current_date(req, now=_NOW)
    assert "你是助手。" in out.system_message.content
    assert "2026-10-19" in out.system_message.content


def test_inject_is_idempotent():
    req = _request(system_message=SystemMessage(content="你是助手。"))
    once = inject_current_date(req, now=_NOW)
    twice = inject_current_date(once, now=_NOW)
    assert twice.system_message.content.count("当前日期：") == 1


def test_inject_appends_block_to_list_content():
    req = _request(system_message=SystemMessage(content=[{"type": "text", "text": "你是助手。"}]))
    out = inject_current_date(req, now=_NOW)
    blocks = out.system_message.content
    assert isinstance(blocks, list) and len(blocks) == 2
    assert "2026-10-19" in blocks[-1]["text"]


def test_inject_falls_back_to_system_prompt():
    req = _request(system_prompt="你是助手。")
    out = inject_current_date(req, now=_NOW)
    assert "2026-10-19" in out.system_prompt


def test_middleware_wrap_calls_handler_with_injected_request():
    seen = []

    def handler(request):
        seen.append(request)
        return "ok"

    mw = DateInjectionMiddleware()
    req = _request(system_message=SystemMessage(content="你是助手。"))
    assert mw.wrap_model_call(req, handler) == "ok"
    assert "当前日期：" in seen[0].system_message.content
