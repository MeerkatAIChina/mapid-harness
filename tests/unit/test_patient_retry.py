"""patient_retry 的单元测试（T4）。

模拟断流：fake handler 前 N 次抛网络异常后成功 → turn 内自动恢复；
预算耗尽 → 抛给外层（on_failure="error"）。
"""

from __future__ import annotations

import logging

import pytest
from langchain.agents.middleware import ModelRetryMiddleware
from langgraph.errors import GraphBubbleUp

from octop_harness.middleware.patient_retry import (
    DEFAULT_MAX_RETRIES,
    PatientRetrySettings,
    build_patient_model_retry,
)


class _FakeModelCall:
    """前 fail_times 次抛 exc，之后返回 sentinel。"""

    def __init__(self, exc: Exception, fail_times: int, sentinel: object):
        self.exc = exc
        self.fail_times = fail_times
        self.sentinel = sentinel
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return self.sentinel


def _fast_settings(**overrides) -> PatientRetrySettings:
    # initial_delay=0 → 退避全为 0，测试不真睡
    return PatientRetrySettings(initial_delay=0.0, **overrides)


def test_factory_uses_default_params():
    mw = build_patient_model_retry()
    assert mw.max_retries == DEFAULT_MAX_RETRIES
    assert mw.on_failure == "error"


def test_factory_settings_override():
    mw = build_patient_model_retry(PatientRetrySettings(max_retries=9, max_delay=5.0))
    assert mw.max_retries == 9
    assert mw.max_delay == 5.0


def test_settings_from_dict_ignores_unknown_keys():
    s = PatientRetrySettings.from_dict({"max_retries": 3, "bogus": 1})
    assert s.max_retries == 3
    assert not hasattr(s, "bogus")


def test_recovers_when_failure_within_budget():
    sentinel = object()
    handler = _FakeModelCall(ConnectionError("model down"), fail_times=3, sentinel=sentinel)
    mw = build_patient_model_retry(_fast_settings())
    assert mw.wrap_model_call(object(), handler) is sentinel
    assert handler.calls == 4  # 1 次原始 + 3 次重试内恢复


def test_raises_after_budget_exhausted(caplog):
    handler = _FakeModelCall(TimeoutError("still down"), fail_times=99, sentinel=None)
    mw = build_patient_model_retry(_fast_settings(max_retries=2))
    with caplog.at_level(logging.WARNING), pytest.raises(TimeoutError):
        mw.wrap_model_call(object(), handler)
    assert handler.calls == 3  # 1 + 2 次重试后放弃
    retry_logs = [r for r in caplog.records if "patient_retry" in r.message]
    assert len(retry_logs) == 3  # 每次失败都有记录（含最后一次）


def test_graph_bubble_up_never_retried():
    exc = GraphBubbleUp("interrupt")
    handler = _FakeModelCall(exc, fail_times=99, sentinel=None)
    mw = build_patient_model_retry(_fast_settings())
    with pytest.raises(GraphBubbleUp):
        mw.wrap_model_call(object(), handler)
    assert handler.calls == 1


def test_name_distinct_from_octop_outer_retry():
    """langchain 按 middleware.name 去重；内层须与 Octop 外层 ModelRetryMiddleware 不同名。

    防回归：R1 装载时两个同名实例导致 create_agent 断言失败、专家无法启动。
    """
    outer = ModelRetryMiddleware(max_retries=2)
    inner = build_patient_model_retry(_fast_settings())
    chain = [outer, inner]
    assert len({m.name for m in chain}) == len(chain)
    assert inner.name == "PatientModelRetryMiddleware"


def test_default_retry_on_accepts_watchdog_timeout():
    """看门狗超时异常必须被 default_retry_on 判可重试（执行计划 Task 5 拔测）。"""
    from langchain.agents.middleware._retry import default_retry_on

    assert default_retry_on(TimeoutError("model call watchdog 300s")) is True
