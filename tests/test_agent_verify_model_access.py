"""``HarnessAgent._verify_model_access`` 直通测试（装配路径回归）。

背景：round-5 启动时 agent 创建即崩——方法内引用 ``_os`` 但从未导入
（octop.log 2026-11-04 14:33:18 NameError）。既有测试全部直接构造
ChapterGateMiddleware，从未覆盖「env 开关 + 真实 HarnessAgent」装配路径。

用 ``__new__`` 裸实例 + 属性桩，只测该方法自身的三条路径：
无 env 回落默认 ref / env 指定优先 / 工厂异常 fail-open 返回 None。
"""

from __future__ import annotations

from octop_harness.agent import HarnessAgent


class _CfgStub:
    def __init__(self, ref: str):
        self._ref = ref

    def pick_default_model_ref(self):
        return self._ref


class _FactoryStub:
    def __init__(self, access=None, exc: Exception | None = None):
        self._access = access
        self._exc = exc
        self.calls: list[str] = []

    def get_for(self, spec: str):
        self.calls.append(spec)
        if self._exc is not None:
            raise self._exc
        return self._access


def _bare_agent(cfg_ref: str, factory: _FactoryStub) -> HarnessAgent:
    agent = HarnessAgent.__new__(HarnessAgent)
    agent._config = _CfgStub(cfg_ref)
    agent._model_factory = factory
    return agent


def test_verify_model_access_falls_back_to_default_ref(monkeypatch):
    monkeypatch.delenv("OCTOP_VERIFY_MODEL", raising=False)
    access = object()
    factory = _FactoryStub(access=access)
    agent = _bare_agent("provider/main-model", factory)
    assert agent._verify_model_access() is access
    assert factory.calls == ["provider/main-model"]


def test_verify_model_access_env_spec_wins(monkeypatch):
    monkeypatch.setenv("OCTOP_VERIFY_MODEL", "provider/verify-model")
    factory = _FactoryStub(access=object())
    agent = _bare_agent("provider/main-model", factory)
    agent._verify_model_access()
    assert factory.calls == ["provider/verify-model"]


def test_verify_model_access_factory_failure_fail_open(monkeypatch):
    monkeypatch.delenv("OCTOP_VERIFY_MODEL", raising=False)
    agent = _bare_agent("provider/main-model", _FactoryStub(exc=RuntimeError("boom")))
    assert agent._verify_model_access() is None


def test_verify_model_access_no_spec_returns_none(monkeypatch):
    monkeypatch.delenv("OCTOP_VERIFY_MODEL", raising=False)
    agent = _bare_agent("", _FactoryStub())
    assert agent._verify_model_access() is None
