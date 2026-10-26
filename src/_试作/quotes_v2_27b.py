"""quotes_v2.py — 确定性引文回贴模块。"""

from __future__ import annotations

import difflib
import re
import unicodedata

# 私有辅助：归一化时同步记录原文下标映射
def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """对 text 做 NFKC + 引号统一 + 去空白，并返回 (归一化串, 每个归一化字符对应的原文下标列表)。"""
    nfkc = unicodedata.normalize("NFKC", text)
    # 引号替换
    nfkc = nfkc.replace("\u201c", '"').replace("\u201d", '"')
    nfkc = nfkc.replace("\u201e", '"').replace("\u201f", '"')
    nfkc = nfkc.replace("\u2018", "'").replace("\u2019", "'")
    nfkc = nfkc.replace("\u201a", "'").replace("\u201b", "'")
    # 去空白
    result_chars: list[str] = []
    result_map: list[int] = []
    for i, ch in enumerate(nfkc):
        if ch.isspace():
            continue
        result_chars.append(ch)
        result_map.append(i)
    return "".join(result_chars), result_map


def normalize(text: str) -> str:
    """生成用于比较的归一化文本（不用于显示）。

    步骤：
    1. NFKC 归一化
    2. 统一引号为 ASCII
    3. 去掉所有空白字符
    """
    nfkc = unicodedata.normalize("NFKC", text)
    nfkc = nfkc.replace("\u201c", '"').replace("\u201d", '"')
    nfkc = nfkc.replace("\u201e", '"').replace("\u201f", '"')
    nfkc = nfkc.replace("\u2018", "'").replace("\u2019", "'")
    nfkc = nfkc.replace("\u201a", "'").replace("\u201b", "'")
    return re.sub(r"\s+", "", nfkc)


def longest_match(quote: str, source: str) -> tuple[int, int, int]:
    """返回 (匹配长度, 引文里的起点, 原文里的起点)，基于归一化后的字符串。

    若没有任何公共字符，返回 (0, 0, 0)。
    """
    q = normalize(quote)
    s = normalize(source)
    if not q or not s:
        return (0, 0, 0)
    sm = difflib.SequenceMatcher(None, q, s, autojunk=False)
    match = sm.find_longest_match(0, len(q), 0, len(s))
    if match.size == 0:
        return (0, 0, 0)
    return (match.size, match.a, match.b)


def repair_quote(
    quote: str,
    source: str,
    *,
    min_run: int = 8,
    window: int = 60,
) -> dict:
    """尝试将 quote 回贴到 source 中，返回修复结果字典。

    返回格式：
    {"ok": bool, "text": str, "status": "verbatim"|"repaired"|"failed", "size": int}
    """
    nq = normalize(quote)
    ns = normalize(source)

    # 1. 逐字匹配
    if nq and nq in ns:
        return {"ok": True, "text": quote, "status": "verbatim", "size": len(nq)}

    # 2. 最长公共片段
    size, q_start, s_start = longest_match(quote, source)
    if size < min_run:
        return {"ok": False, "text": quote, "status": "failed", "size": size}

    # 3. 回贴：需要把归一化位置映射回原文下标
    # 构造 source 的归一化版本及下标映射
    _, src_map = _normalize_with_map(source)
    # src_map[i] = 归一化串第 i 个字符对应的原文下标

    # 匹配片段在归一化 source 中的区间 [s_start, s_start + size)
    # 映射到原文下标
    if s_start >= len(src_map) or s_start + size > len(src_map):
        # 越界保护
        return {"ok": False, "text": quote, "status": "failed", "size": size}

    start_orig = src_map[s_start]
    end_orig = src_map[s_start + size - 1] + 1  # 最后一个字符的原文下标 + 1

    # 向左右扩展，最多 window 个字符，遇到句子边界停止
    stop_chars = set("。？！\n\r\u3000")  # 句号/问号/叹号/换行/全角空格
    # 扩展左边界
    left = start_orig
    left_limit = max(0, start_orig - window)
    while left > left_limit:
        left -= 1
        if source[left] in stop_chars:
            left += 1
            break
    # 扩展右边界
    right = end_orig
    right_limit = min(len(source), end_orig + window)
    while right < right_limit:
        if source[right] in stop_chars:
            break
        right += 1

    cand = source[left:right].strip()

    # 验证 cand 是 source 的逐字子串
    if cand and cand in source:
        return {"ok": True, "text": cand, "status": "repaired", "size": size}

    # 退化为只取原始匹配区间
    fallback = source[start_orig:end_orig]
    if fallback and fallback in source:
        return {"ok": True, "text": fallback, "status": "repaired", "size": size}

    return {"ok": False, "text": quote, "status": "failed", "size": size}


def verify_quotes(
    quotes: list[str],
    source: str,
    *,
    min_run: int = 8,
) -> list[dict]:
    """对每条引文调用 repair_quote，返回同样长度的结果列表。

    每条结果格式：
    {"quote": 原始引文, "text": 修复后文本, "status": "verbatim"|"repaired"|"failed", "ok": bool}
    空引文条目占位，status="failed"、text=""。
    """
    results: list[dict] = []
    for q in quotes:
        if not q or not q.strip():
            results.append({
                "quote": q,
                "text": "",
                "status": "failed",
                "ok": False,
            })
            continue
        r = repair_quote(q, source, min_run=min_run)
        results.append({
            "quote": q,
            "text": r["text"],
            "status": r["status"],
            "ok": r["ok"],
        })
    return results
