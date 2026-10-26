"""merge_budget_27b.py — 输入预算辅助（截断修复 A）。

只依赖标准库。
"""

from __future__ import annotations

import math
from typing import Any, List


def estimate_tokens(text: str, chars_per_token: float = 2.3) -> int:
    """估算 token 数。

    返回 ceil(len(text) / chars_per_token)，最少 1；空串返回 0；
    chars_per_token <= 0 时按 2.3 处理。
    """
    if not isinstance(text, str):
        return 0
    if len(text) == 0:
        return 0

    cpt = chars_per_token
    if not isinstance(cpt, (int, float)) or isinstance(cpt, bool):
        cpt = 2.3
    if cpt <= 0:
        cpt = 2.3

    return max(1, math.ceil(len(text) / cpt))


def new_anchor_lines(
    anchors: List[str],
    existing: List[str],
    *,
    limit: int = 0,
) -> List[str]:
    """筛选新的 anchor 行。

    - 只保留字符串；
    - strip() 后为空或与 existing 中某项（同样 strip 后）相同的一律丢弃；
    - 自身重复也去重；
    - 保持输入顺序；
    - limit > 0 时最多返回 limit 条；
    - 不得修改输入列表。
    """
    if not isinstance(anchors, (list, tuple)):
        return []
    if not isinstance(existing, (list, tuple)):
        existing = []

    # 构建 existing 的 strip 后集合
    existing_set = set()
    for item in existing:
        if isinstance(item, str):
            s = item.strip()
            if s:
                existing_set.add(s)

    seen = set()
    result: List[str] = []
    for item in anchors:
        if not isinstance(item, str):
            continue
        s = item.strip()
        if not s:
            continue
        if s in existing_set:
            continue
        if s in seen:
            continue
        seen.add(s)
        # 返回 strip 后的字符串，保证一致性
        result.append(s)

    if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
        result = result[:limit]

    return result


def needs_lean(
    est_tokens: int,
    *,
    num_ctx: int,
    num_predict: int,
    reserve: int = 512,
) -> bool:
    """判断是否需要 lean 模式。

    返回 est_tokens + min(num_predict, 512) + reserve > num_ctx
    恰好等于时返回 False。
    """
    def _as_int(v: Any, default: int = 0) -> int:
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return int(v)
        return default

    et = _as_int(est_tokens, 0)
    nc = _as_int(num_ctx, 0)
    np_ = _as_int(num_predict, 0)
    rv = _as_int(reserve, 512)

    capped_predict = min(np_, 512)
    total = et + capped_predict + rv
    return total > nc
