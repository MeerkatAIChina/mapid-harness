"""承重断言核验引擎（设计文档_R4 §3.1）：章级第五道门禁。

三路抽取并集：①关键词正则预筛 ②台账反查（指标名命中即强制核验，与正文
是否命中无关）③模型抽取（分类表形式化进提示词）。判定三档：
confirmed / refuted / unverifiable。infra 失败（模型/检索/抓取异常，
重试耗尽）向上抛，由 chapter_gate 按 fail 策略处置（章级 fail-open +
连续 3 次事故中止）。

全 mock 友好：``model_access`` / ``search`` / ``fetch`` 全部注入。
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from octop_harness.report_qa.citations import PENDING_MARK

logger = logging.getLogger(__name__)

# ①关键词预筛 + ②台账反查共用同一组关键词（设计 §3.1：上市|挂牌|借壳|并购|收购|股票代码|实控人）
KEYWORD_RE = re.compile(r"上市|挂牌|借壳|并购|收购|股票代码|实控人")

_SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?\n])")
_NUM_CLAIM_RE = re.compile(r"\d")

DEFAULT_TIMEOUT_S = 120.0
# 内部工序（核验/审校/出口核实）的模型调用标记：LangGraph messages 流会携带该 metadata，
# 协议层按 lc_source=verification 过滤，防止内部输出泄漏为用户可见回复（round-6 后端 bug⑤）。
INTERNAL_STREAM_CONFIG: dict[str, Any] = {"metadata": {"lc_source": "verification"}}
EXTRACT_MAX_TOKENS = 2000  # 抽取输出封顶：正常一章承重断言仅几百 token（防 temp=0 重复循环烧满窗口）
MAX_RETRIES = 2  # 有限重试（指数退避 1s/2s）
SAMPLE_PER_CHAPTER = 3  # 一般数值抽检上限（设计裁量）

_EXTRACT_SYSTEM = (
    "你是事实核验助手。从给定报告章节文本中抽取「承重断言」：一旦为假则整章结论崩塌的事实性断言。"
    "分类：上市/挂牌状态、并购/收购事件、实控人/控股股东、主体名称、关键年份、投资额。"
    "只抽取文本中实际出现的断言，不补全、不推测。最多 20 条，每条不超过 50 字。"
    '输出 JSON：{"assertions": ["断言1", "断言2"]}，没有则输出空数组。'
)

_JUDGE_SYSTEM = (
    "你是事实核验助手。给定一条断言和一份来源正文，判定断言是否成立。"
    '输出 JSON：{"verdict": "confirmed|refuted|unverifiable", "reason": "一句话理由"}。'
    "confirmed=正文明确支持；refuted=正文明确矛盾；unverifiable=正文无关或信息不足。"
    "保守判定：不确定一律 unverifiable。"
)


@dataclass
class Verdict:
    """单条断言的核验判定。"""

    result: str  # "confirmed" | "refuted" | "unverifiable"
    evidence_url: str | None = None
    reason: str = ""


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT_RE.split(text) if s.strip()]


def _parse_json(text: str) -> Any:
    """从模型输出提取 JSON（容忍 ```json 围栏与首尾废话）。"""
    cleaned = re.sub(r"```(?:json)?|```", "", text).strip()
    start = min((i for i in (cleaned.find("{"), cleaned.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError(f"模型输出无 JSON：{text[:120]}")
    return json.loads(cleaned[start:])


_SALVAGE_STR_RE = re.compile(r'"([^"\n]{4,})"')


def _salvage_assertions(raw: str) -> list[str]:
    """挽救式解析：严格 JSON 失败时，从残骸里抽 quoted 字符串（跳过 schema 键名）。

    round-6 实战：temp=0 下模型枚举断言陷重复循环并撞长度截断，完整 JSON
    必死；但残骸里真断言齐全，抽出去重后即可用（本函数出口下游本就有
    去重保序）。一条都抽不到则返回空，由调用方按 infra 失败抛错。
    """
    return [s for s in _SALVAGE_STR_RE.findall(raw) if s != "assertions"]


# ---------------------------------------------------------------------------
# 三路抽取
# ---------------------------------------------------------------------------


def extract_keyword_assertions(chapter_text: str) -> list[str]:
    """①关键词正则预筛：含承重关键词的句子。"""
    return [s for s in _split_sentences(chapter_text) if KEYWORD_RE.search(s)]


def extract_ledger_assertions(ledger_text: str) -> list[str]:
    """②台账反查（用户补充）：指标名含承重关键词的台账条目强制核验。

    与正文是否命中关键词无关——台账登记了这类指标，说明该事实被引用，
    必须核验。断言文本由表格行单元格拼成：「指标：数值单位（口径）」。
    """
    assertions: list[str] = []
    for line in ledger_text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not cells[0] or cells[0] in ("指标", "---", ":---"):
            continue
        if set(cells[0]) <= set("-: "):  # 分隔行
            continue
        if not KEYWORD_RE.search(cells[0]):
            continue
        value = cells[1] + (cells[2] if len(cells) > 2 else "")
        basis = f"（{cells[3]}）" if len(cells) > 3 and cells[3] else ""
        assertions.append(f"{cells[0]}：{value}{basis}")
    return assertions


async def extract_loadbearing_assertions(
    chapter_text: str,
    ledger_text: str,
    model_access: Any | None = None,
) -> list[str]:
    """三路并集抽取（去重保序）。模型抽取失败向上抛（= infra 失败）。"""
    found = extract_keyword_assertions(chapter_text) + extract_ledger_assertions(ledger_text)
    if model_access is not None:
        raw = await model_access.ainvoke_text(
            f"章节文本：\n{chapter_text}",
            system=_EXTRACT_SYSTEM,
            response_format="json",
            temperature=0,
            timeout_s=DEFAULT_TIMEOUT_S,
            max_tokens=EXTRACT_MAX_TOKENS,
            config=INTERNAL_STREAM_CONFIG,
        )
        try:
            data = _parse_json(raw)
            found += [str(a) for a in data.get("assertions", [])]
        except ValueError:
            salvaged = _salvage_assertions(raw)
            if not salvaged:
                raise
            logger.warning(
                "verification: 抽取输出非完整 JSON，挽救式解析救回 %d 条（去重前）",
                len(salvaged),
            )
            found += salvaged
    seen: set[str] = set()
    deduped: list[str] = []
    for a in found:
        if a not in seen:
            seen.add(a)
            deduped.append(a)
    return deduped


def sample_numeric_claims(
    chapter_text: str,
    exclude: list[str],
    k: int = SAMPLE_PER_CHAPTER,
    rng: random.Random | None = None,
) -> list[str]:
    """一般数值抽检：含数字且未被三路覆盖的句子，随机至多 k 条。"""
    cands = [
        s
        for s in _split_sentences(chapter_text)
        if _NUM_CLAIM_RE.search(s) and s not in exclude and not KEYWORD_RE.search(s)
    ]
    if rng is None:
        return random.sample(cands, min(k, len(cands)))
    return rng.sample(cands, min(k, len(cands)))


# ---------------------------------------------------------------------------
# 核验
# ---------------------------------------------------------------------------


_JUNK_START_CHARS = "|│#"  # 表格行 / 版式框线行 / 标题


def is_junk_assertion(text: str) -> bool:
    """非陈述句碎片不值得核验（round-7 实证：表格行/标题被当断言，必然 unverifiable，纯烧调用）。"""
    s = text.strip()
    return not s or s[0] in _JUNK_START_CHARS


def assertion_key(assertion: str) -> str:
    """断言缓存键：去全部空白（对措辞漂移的最小归一化，round-7 M1）。"""
    return re.sub(r"\s+", "", assertion)


async def verify_assertion(
    assertion: str,
    model_access: Any,
    search: Callable[[str], Awaitable[list[dict[str, Any]]]],
    fetch: Callable[[str], Awaitable[str]],
) -> Verdict:
    """检索 → 抓正文（web_fetch）→ 模型比对。模型/检索异常向上抛（= infra 失败）。"""
    hits = await search(assertion)
    evidence_url: str | None = None
    evidence_text = ""
    for hit in hits[:3]:
        url = str(hit.get("url") or "")
        if not url:
            continue
        text = await fetch(url)  # 抓取失败（Error: 开头）换下一条，不算 infra
        if text and not text.startswith("Error:"):
            evidence_url, evidence_text = url, text[:8000]
            break
    if not evidence_text:
        return Verdict("unverifiable", reason="检索无可用正文")
    raw = await model_access.ainvoke_text(
        f"断言：{assertion}\n\n来源：{evidence_url}\n\n正文（节选）：\n{evidence_text}",
        system=_JUDGE_SYSTEM,
        response_format="json",
        temperature=0,
        timeout_s=DEFAULT_TIMEOUT_S,
        config=INTERNAL_STREAM_CONFIG,
    )
    data = _parse_json(raw)
    result = str(data.get("verdict", "unverifiable"))
    if result not in ("confirmed", "refuted", "unverifiable"):
        result = "unverifiable"
    return Verdict(result, evidence_url=evidence_url, reason=str(data.get("reason", "")))


_REF_RE = re.compile(r"\[(\d{1,3})\]")  # 正文/断言里的来源编号 [n]
_REGISTERED_REF_RE = re.compile(r"^\|\s*(\d{1,3})\s*\|", re.M)  # 台账登记表首列数字行


def _registered_refs(ledger_text: str) -> set[str]:
    """台账引用编号登记表里的已登记编号（指标台账首列是中文指标名，不会误中）。"""
    return set(_REGISTERED_REF_RE.findall(ledger_text))


def is_acknowledged(assertion: str, body_text: str, ledger_text: str = "") -> bool:
    """查无实证断言是否已结案（降级标注 [待核实]，或凭台账来源编号）。

    三层判定：断言自带标注；断言/正文同事实句带已登记来源编号 [n]
    （round-8 实证：查无实证的主体是「有台账来源、外部检索未命中」，
    逐条虚假降级违反数据红线，编号在登记表内即凭台账结案，裁决仍留痕）；
    或正文存在同事实的已标注句——共享 ≥2 位数字串或同一承重关键词
    （终审复验补修：台账反查断言的字符串不含正文标注，需按事实匹配正文）。
    """
    if PENDING_MARK in assertion:
        return True
    registered = _registered_refs(ledger_text) if ledger_text else set()
    if registered and any(r in registered for r in _REF_RE.findall(assertion)):
        return True
    digits = re.findall(r"\d{2,}", assertion)
    kw = KEYWORD_RE.search(assertion)

    def same_fact(s: str) -> bool:
        return any(d in s for d in digits) or (kw is not None and kw.group(0) in s)

    for s in _split_sentences(body_text):
        if PENDING_MARK in s and same_fact(s):
            return True
        if registered and same_fact(s) and any(r in registered for r in _REF_RE.findall(s)):
            return True
    return False


async def with_retry(
    call: Callable[[], Awaitable[Any]],
    *,
    retries: int = MAX_RETRIES,
    backoff_s: float = 1.0,
) -> Any:
    """有限重试（指数退避）；耗尽后向上抛 = infra 失败。"""
    delay = backoff_s
    for attempt in range(retries + 1):
        try:
            return await call()
        except Exception:
            if attempt >= retries:
                raise
            logger.warning("verification: 调用失败，%.0fs 后第 %d 次重试", delay, attempt + 1, exc_info=True)
            await asyncio.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")


__all__ = [
    "INTERNAL_STREAM_CONFIG",
    "KEYWORD_RE",
    "Verdict",
    "assertion_key",
    "extract_keyword_assertions",
    "extract_ledger_assertions",
    "extract_loadbearing_assertions",
    "is_acknowledged",
    "is_junk_assertion",
    "sample_numeric_claims",
    "verify_assertion",
    "with_retry",
]
