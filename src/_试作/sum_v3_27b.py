"""sum_v3.py - 分段摘要的确定性配件模块。"""

from __future__ import annotations

import re
from typing import Any

from thin_check import summary_is_thin


def facts_cap(chars: int, *, split: bool = False) -> int:
    """按块字数决定 facts 上限。

    普通块：max(3, min(6, chars // 1200))；降级拆分出来的块用更高的地板与更小的除数
    （max(4, min(6, chars // 900))）——拆分块本来就短，按普通口径会恒定只给 3 条。

    Args:
        chars: 块的字符数。
        split: 该块是否由 split_chunk 降级切分而来。

    Returns:
        facts 上限，普通块 [3, 6]，拆分块 [4, 6]。
    """
    if split:
        return max(4, min(6, chars // 900))
    return max(3, min(6, chars // 1200))


def context_guard(prompt_tokens: int, num_ctx: int, *, ratio: float = 0.75) -> bool:
    """判断上一次调用的 prompt_tokens 是否超过 num_ctx * ratio。

    Args:
        prompt_tokens: 上一次调用的 prompt token 数。
        num_ctx: 模型上下文窗口大小。
        ratio: 阈值比例，默认 0.75。

    Returns:
        若 prompt_tokens > num_ctx * ratio 则返回 True，否则 False。
    """
    return prompt_tokens > num_ctx * ratio


def split_chunk(chunk: list[str], *, max_parts: int = 2) -> list[list[str]]:
    """把一块（字符串列表）按累计字符数的中点对半切开。

    Args:
        chunk: 字符串列表。
        max_parts: 最大切分块数，默认 2。

    Returns:
        切分后的块列表，各块顺序与原文一致，拼接回去等于原 chunk。
    """
    if len(chunk) <= 1:
        return [list(chunk)]

    # 计算每个单元的"权重"：len(单元) + 1
    weights = [len(s) + 1 for s in chunk]
    total = sum(weights)
    half = total / 2.0

    # 找到第一个使前半不超过一半的下标
    cumulative = 0
    split_idx = len(chunk) - 1  # 默认切在最后一个位置之前
    for i, w in enumerate(weights):
        cumulative += w
        if cumulative >= half:
            split_idx = i
            break

    # 确保切分点落在 1..len-1 之间
    if split_idx < 1:
        split_idx = 1
    if split_idx > len(chunk) - 1:
        split_idx = len(chunk) - 1

    part1 = chunk[:split_idx]
    part2 = chunk[split_idx:]

    if max_parts <= 1:
        return [list(chunk)]

    return [list(part1), list(part2)]


def _quote_list(obj: dict) -> list[str]:
    """从 obj 中提取引文列表。

    Args:
        obj: 包含引文的字典。

    Returns:
        引文字符串列表。
    """
    if "quotes" in obj and isinstance(obj["quotes"], list):
        return [q for q in obj["quotes"] if isinstance(q, str)]
    if "quote" in obj and isinstance(obj["quote"], str) and obj["quote"].strip():
        return [obj["quote"]]
    return []


def fact_grounded(fact: str, source: str, *, min_run: int = 6) -> bool:
    """判断一条事实是否"落地"。

    Args:
        fact: 事实字符串。
        source: 源文本。
        min_run: 最小连续片段长度，默认 6。

    Returns:
        若 fact 去空白后存在长度 min_run 的连续片段出现在 source 去空白文本中则返回 True，否则 False。
    """
    if not fact or not source:
        return False

    fact_clean = re.sub(r'\s+', '', fact)
    source_clean = re.sub(r'\s+', '', source)

    if len(fact_clean) < min_run:
        return False

    # 检查是否存在长度 min_run 的连续片段出现在 source 中
    for i in range(len(fact_clean) - min_run + 1):
        segment = fact_clean[i:i + min_run]
        if segment in source_clean:
            return True

    return False


def check_summary_v3(
    obj: dict,
    source: str,
    *,
    max_summary: int = 300,
    max_facts: int = 6
) -> list[dict]:
    """检查摘要的多个维度，返回检查结果列表。

    Args:
        obj: 包含 summary、quotes/quote、facts 的字典。
        source: 源文本。
        max_summary: 摘要最大长度，默认 300。
        max_facts: 事实最大条数，默认 6。

    Returns:
        检查结果列表，每项包含 id、ok、level、detail。
    """
    results: list[dict] = []

    # 1. summary_nonempty
    summary = obj.get("summary", "")
    if not isinstance(summary, str):
        summary = str(summary)
    summary_stripped = summary.strip()
    ok1 = len(summary_stripped) > 0
    results.append({
        "id": "summary_nonempty",
        "ok": ok1,
        "level": "hard",
        "detail": f"{len(summary)} 字"
    })

    # 2. summary_length
    ok2 = len(summary) <= max_summary
    results.append({
        "id": "summary_length",
        "ok": ok2,
        "level": "soft",
        "detail": f"{len(summary)} 字 / 上限 {max_summary}"
    })

    # 3. summary_independent
    forbidden_words = [
        "如前所述", "上一段", "前文", "综上", "承接上",
        "接着上", "上文提到", "见前", "前面说到", "此前提到"
    ]
    hits = [w for w in forbidden_words if w in summary]
    ok3 = len(hits) == 0
    detail3 = f"依赖前文的词: {hits}" if hits else "可独立阅读"
    results.append({
        "id": "summary_independent",
        "ok": ok3,
        "level": "hard",
        "detail": detail3
    })

    # 4. summary_no_guess
    guess_words = ["推断", "推测", "臆测", "猜测"]
    guess_hits = [w for w in guess_words if w in summary]
    ok4 = len(guess_hits) == 0
    detail4 = f"含推断词: {guess_hits}" if guess_hits else "无推断"
    results.append({
        "id": "summary_no_guess",
        "ok": ok4,
        "level": "hard",
        "detail": detail4
    })

    # 5. quotes_verbatim
    quotes = _quote_list(obj)
    if not quotes:
        results.append({
            "id": "quotes_verbatim",
            "ok": False,
            "level": "hard",
            "detail": "没有引文"
        })
    else:
        source_clean = re.sub(r'\s+', '', source)
        hit_count = 0
        miss_list = []
        for q in quotes:
            q_clean = re.sub(r'\s+', '', q)
            if q_clean[:40] in source_clean:
                hit_count += 1
            else:
                miss_list.append(q)
        ok5 = hit_count == len(quotes)
        if ok5:
            detail5 = f"引文逐字命中 {hit_count}/{len(quotes)}"
        else:
            shown = miss_list[:2]
            detail5 = f"引文不是原文: {shown}"
        results.append({
            "id": "quotes_verbatim",
            "ok": ok5,
            "level": "hard",
            "detail": detail5
        })

    # 6. facts_grounded
    facts = obj.get("facts", [])
    if not isinstance(facts, list):
        facts = []
    facts = [f for f in facts if isinstance(f, str)]

    if not facts:
        results.append({
            "id": "facts_grounded",
            "ok": False,
            "level": "hard",
            "detail": "没有事实"
        })
    else:
        grounded_count = 0
        miss_list = []
        for f in facts:
            if fact_grounded(f, source):
                grounded_count += 1
            else:
                miss_list.append(f)
        ok6 = grounded_count == len(facts)
        if ok6:
            detail6 = f"事实落地 {grounded_count}/{len(facts)}"
        else:
            shown = miss_list[:2]
            detail6 = f"事实未落地: {shown}"
        results.append({
            "id": "facts_grounded",
            "ok": ok6,
            "level": "hard",
            "detail": detail6
        })

    # 7. facts_count
    ok7 = len(facts) <= max_facts
    results.append({
        "id": "facts_count",
        "ok": ok7,
        "level": "soft",
        "detail": f"{len(facts)} 条 / 上限 {max_facts}"
    })

    # 8. summary_thin（P-052：空壳不 hard 拦，交给驱动带原因重摘一次）
    thin, thin_reason = summary_is_thin(obj, source)
    results.append({
        "id": "summary_thin",
        "ok": not thin,
        "level": "soft",
        "detail": thin_reason,
    })

    # soft_fail 标记：soft 检查没过的项显式标出，便于降级可见（P-003）
    for c in results:
        if c.get("level") == "soft" and not c.get("ok"):
            c["soft_fail"] = True
    return results


def allocate_facts(items: list[dict], *, cap: int = 15, min_len: int = 4) -> list[str]:
    """按块分配全局事实（P-042）。

    旧做法 dedupe_facts(...)[:cap] 按块序先到先得，事实多的轮次会让后段块零覆盖。
    这里改为：每块先取 1 条（去重，重复则顺延到该块下一条），余量按块序轮转补满，
    总数不超过 cap；块内与跨块都保持首次出现顺序与原文。
    """
    per_block: list[list[str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        facts = item.get("facts", [])
        per_block.append([f for f in facts if isinstance(f, str)] if isinstance(facts, list) else [])

    seen: set[str] = set()
    out: list[str] = []
    idx = [0] * len(per_block)

    def key(text: str) -> str:
        return re.sub(r"\s+", "", text).lower()

    def take(block: int) -> bool:
        while idx[block] < len(per_block[block]):
            fact = per_block[block][idx[block]]
            idx[block] += 1
            k = key(fact)
            if len(k) >= min_len and k not in seen:
                seen.add(k)
                out.append(fact)
                return True
        return False

    for block in range(len(per_block)):
        if len(out) >= cap:
            break
        take(block)
    while len(out) < cap:
        progressed = False
        for block in range(len(per_block)):
            if len(out) >= cap:
                break
            if take(block):
                progressed = True
        if not progressed:
            break
    return out


def dedupe_facts(items: list[dict], *, min_len: int = 4) -> list[str]:
    """跨块事实去重。

    Args:
        items: 包含 "facts" 列表的字典列表。
        min_len: 归一化后最小长度，默认 4。

    Returns:
        去重后的原始字符串列表，保持首次出现的原样和顺序。
    """
    seen: set[str] = set()
    result: list[str] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        facts = item.get("facts", [])
        if not isinstance(facts, list):
            continue
        for f in facts:
            if not isinstance(f, str):
                continue
            key = re.sub(r'\s+', '', f).lower()
            if len(key) < min_len:
                continue
            if key not in seen:
                seen.add(key)
                result.append(f)

    return result
