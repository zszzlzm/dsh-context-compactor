"""gc_v2.py — 会话状态垃圾回收模块（纯函数；锚点归一化复用 anchors_v2_27b）。"""

from __future__ import annotations

import json
import copy

from anchors_v2_27b import anchor_norm

KINDS = ("锚点", "决定", "待办", "已完成")


def state_chars(state: dict) -> int:
    """返回整个 state 序列化后的字符数。"""
    return len(json.dumps(state, ensure_ascii=False))


def field_chars(state: dict) -> dict[str, int]:
    """返回每个 KINDS 类别列表序列化后的字符数。"""
    result: dict[str, int] = {}
    for kind in KINDS:
        items = state.get(kind, [])
        result[kind] = len(json.dumps(items, ensure_ascii=False))
    return result


def over_budget(state: dict, budgets: dict[str, int]) -> list[str]:
    """返回超预算的类别名列表（按 KINDS 顺序）。"""
    fc = field_chars(state)
    out: list[str] = []
    for kind in KINDS:
        if kind not in budgets:
            continue
        if fc[kind] > budgets[kind]:
            out.append(kind)
    return out


def referenced_anchors(state: dict) -> list[str]:
    """返回被待办/红线/决定引用到的锚点（保持原顺序，去重）。"""
    anchors: list[str] = state.get("锚点", [])
    if not anchors:
        return []

    # 收集所有引用文本
    texts: list[str] = []
    for item in state.get("待办", []):
        if isinstance(item, str):
            texts.append(item)
    for item in state.get("红线", []):
        if isinstance(item, str):
            texts.append(item)
    for item in state.get("决定", []):
        if isinstance(item, dict):
            texts.append(item.get("内容", ""))
            texts.append(item.get("理由", ""))

    # 判断每个锚点是否被引用
    seen: set[str] = set()
    result: list[str] = []
    for a in anchors:
        if a in seen:
            continue
        for t in texts:
            if a in t:
                result.append(a)
                seen.add(a)
                break
    return result


def _item_content(item) -> str:
    """提取条目的'内容'字段用于比较。"""
    if isinstance(item, dict):
        return item.get("内容", "")
    return str(item)


def _anchor_key(text: str) -> str:
    """锚点归一化键，直接复用 anchors_v2_27b.anchor_norm（单一实现）。"""
    return anchor_norm(text)


def _state_texts(state: dict) -> list[str]:
    """收集待办/红线/决定里的引用文本。"""
    texts: list[str] = []
    for kind in ("待办", "红线"):
        for item in state.get(kind, []):
            if isinstance(item, str):
                texts.append(item)
    for item in state.get("决定", []):
        if isinstance(item, dict):
            texts.append(item.get("内容", ""))
            texts.append(item.get("理由", ""))
    return texts


def dedupe_anchors(state: dict, *, turn: int) -> dict:
    """按归一化键合并历史重复锚点（P-058）。

    - 未被引用的重复：只留首次出现，其余归档（原因「归一化重复」），守恒可验
    - 被引用的重复：保留（否则会破坏待办/决定里的原文引用），交给预算裁剪按引用保护处理
    """
    hot = copy.deepcopy(state)
    anchors = hot.get("锚点", [])
    if not isinstance(anchors, list) or not anchors:
        return {"state": hot, "archive": {"锚点": []}}
    texts = _state_texts(state)
    seen: dict[str, str] = {}
    keep: list = []
    archived: list[dict] = []
    for item in anchors:
        if not isinstance(item, str):
            keep.append(item)
            continue
        key = _anchor_key(item)
        if key in seen:
            if any(item in text for text in texts):
                keep.append(item)
                continue
            archived.append({"内容": item, "轮次": turn,
                             "原因": f"归一化重复（并入 {seen[key]!r}）"})
            continue
        seen[key] = item
        keep.append(item)
    hot["锚点"] = keep
    return {"state": hot, "archive": {"锚点": archived}}


def plan_gc_v2(
    state: dict,
    budgets: dict[str, int],
    *,
    turn: int,
    ratio: float = 0.7,
) -> dict:
    """
    执行垃圾回收，返回 {"state": 热区状态, "archive": {类别名: [归档记录, ...]}}。
    绝不修改入参。
    """
    # 深拷贝 state 作为热区基础；先按归一化键合并历史重复锚点（P-058）
    dedup = dedupe_anchors(state, turn=turn)
    hot = dedup["state"]
    archive: dict[str, list] = {kind: [] for kind in KINDS}
    archive["锚点"] = list(dedup["archive"]["锚点"])

    over = over_budget(hot, budgets)
    if not over:
        return {"state": hot, "archive": archive}

    # 预计算 referenced_anchors（基于去重后的热区）
    ref_anchors = referenced_anchors(hot)
    ref_anchor_set = set(ref_anchors)

    for kind in over:
        items = hot.get(kind, [])
        if not items:
            continue

        # 确定 protected 下标集合
        protected_idx: set[int] = set()
        if kind == "锚点":
            for i, item in enumerate(items):
                if item in ref_anchor_set:
                    protected_idx.add(i)
        # 其它类别 protected 为空

        # base: 非 protected 的条目，保持原顺序
        base_indices: list[int] = [i for i in range(len(items)) if i not in protected_idx]

        # 从 base 尾部往前累加，找最小条数使累计长度 >= target
        target = ratio * budgets[kind]
        if not base_indices:
            keep_base_idx: set[int] = set()
        else:
            # 从尾部开始累加
            acc = 0
            keep_count = 0
            for idx in reversed(base_indices):
                item = items[idx]
                item_len = len(json.dumps(item, ensure_ascii=False))
                acc += item_len
                keep_count += 1
                if acc >= target:
                    break
            # keep_base_idx: base_indices 中最后 keep_count 个
            keep_base_idx = set(base_indices[-keep_count:]) if keep_count > 0 else set()

        # 热区保留的下标 = protected_idx ∪ keep_base_idx
        keep_idx = protected_idx | keep_base_idx

        # 构建热区列表（保持原顺序）
        hot_items = [items[i] for i in range(len(items)) if i in keep_idx]
        hot[kind] = hot_items

        # 归档：其余条目，按原顺序
        for i in range(len(items)):
            if i in keep_idx:
                continue
            item = items[i]
            if kind == "决定":
                rec = {
                    "内容": item.get("内容", ""),
                    "理由": item.get("理由", ""),
                    "轮次": turn,
                    "原因": "超预算",
                }
            else:
                rec = {
                    "内容": item if isinstance(item, str) else str(item),
                    "轮次": turn,
                    "原因": "超预算",
                }
            archive[kind].append(rec)

    return {"state": hot, "archive": archive}


def verify_gc_v2(prev: dict, hot: dict, archive: dict) -> list[str]:
    """守恒校验，返回问题列表，空列表表示没问题。"""
    problems: list[str] = []

    for kind in KINDS:
        prev_items = prev.get(kind, [])
        hot_items = hot.get(kind, [])
        arch_items = archive.get(kind, [])

        # 1. 账目守恒
        prev_contents = sorted(_item_content(x) for x in prev_items)
        hot_contents = sorted(_item_content(x) for x in hot_items)
        arch_contents = sorted(_item_content(x) for x in arch_items)
        combined = sorted(hot_contents + arch_contents)
        if combined != prev_contents:
            problems.append(f"账目不符: {kind}")

        # 2. 未回收却有改动
        if not arch_items:
            if hot_items != prev_items:
                problems.append(f"未回收却有改动: {kind}")

        # 5. 归档记录合法性
        for rec in arch_items:
            if not isinstance(rec, dict):
                problems.append(f"归档记录不合法: {kind}")
                break
            content = rec.get("内容")
            if not isinstance(content, str) or not content:
                problems.append(f"归档记录不合法: {kind}")
                break
            if not isinstance(rec.get("轮次"), int):
                problems.append(f"归档记录不合法: {kind}")
                break

    # 3. 在用锚点被回收
    ref_anchors = referenced_anchors(prev)
    hot_anchors = hot.get("锚点", [])
    hot_anchor_set = set(hot_anchors)
    for a in ref_anchors:
        if a not in hot_anchor_set:
            problems.append(f"在用锚点被回收: {a}")

    # 4. 目标和红线
    if hot.get("目标") != prev.get("目标"):
        problems.append("目标被改动")
    if hot.get("红线") != prev.get("红线"):
        problems.append("红线被改动")

    return problems


def merge_archive_v2(old: dict, new: dict) -> dict:
    """把新归档追加到旧归档，返回新 dict（不修改入参）。"""
    all_keys = set(old.keys()) | set(new.keys())
    result: dict[str, list] = {}
    for key in all_keys:
        old_list = old.get(key, [])
        new_list = new.get(key, [])
        # 判重只看 "内容"
        seen_contents: set[str] = set()
        merged: list = []
        for rec in old_list:
            merged.append(rec)
            if isinstance(rec, dict):
                seen_contents.add(rec.get("内容", ""))
        for rec in new_list:
            if isinstance(rec, dict):
                c = rec.get("内容", "")
                if c in seen_contents:
                    continue
                seen_contents.add(c)
            merged.append(rec)
        result[key] = merged
    return result


def recall(archive: dict, keyword: str, *, limit: int = 20) -> list[dict]:
    """在归档里按关键词检索，返回命中记录的浅拷贝列表（带'类别'键）。"""
    if not keyword:
        return []
    kw_lower = keyword.lower()
    results: list[dict] = []
    for kind in KINDS:
        items = archive.get(kind, [])
        for rec in items:
            if not isinstance(rec, dict):
                continue
            content = rec.get("内容", "")
            reason = rec.get("理由", "")
            if kw_lower in content.lower() or kw_lower in reason.lower():
                copy_rec = dict(rec)
                copy_rec["类别"] = kind
                results.append(copy_rec)
                if len(results) >= limit:
                    return results
    return results
