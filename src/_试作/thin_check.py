"""L2 摘要空壳检测模块。"""

from __future__ import annotations


def summary_is_thin(obj: dict, source: str) -> tuple[bool, str]:
    """判断摘要是否为空壳。

    规则（按顺序判断，命中即返回）：
    1. summary 非 str 时先 str()；strip() 后长度 < 30 → (True, "summary N 字 < 30")
    2. facts 非 list 视为空；只统计 strip() 后非空的字符串；无有效 fact → (True, "facts 为空")
    3. 以上都不命中 → (False, "ok")

    Args:
        obj: 待检测的字典对象
        source: 来源标识（保留参数，本函数不使用）

    Returns:
        (is_thin, reason) 元组
    """
    # 规则 1：检查 summary
    summary = obj.get("summary", "")
    if not isinstance(summary, str):
        summary = str(summary)
    stripped = summary.strip()
    n = len(stripped)
    if n < 30:
        return (True, f"summary {n} 字 < 30")

    # 规则 2：检查 facts
    facts = obj.get("facts", [])
    if not isinstance(facts, list):
        facts = []

    valid_count = 0
    for item in facts:
        if isinstance(item, str):
            if item.strip():
                valid_count += 1
        # None、非字符串等一律视为无效

    if valid_count == 0:
        return (True, "facts 为空")

    # 规则 3：通过
    return (False, "ok")
