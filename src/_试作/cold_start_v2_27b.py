"""cold_start_v2: 冷启动 L2 投影与校验模块。"""

import re
from typing import Any


def norm(text: str) -> str:
    """判重用的归一化：去空白、转小写、去首尾空白。

    顺序：先 str()，再压空白，再 lower，再 strip。
    """
    s = str(text)
    s = re.sub(r"\s+", "", s)
    s = s.lower()
    s = s.strip()
    return s


def collect(items: list[dict], field: str) -> list[str]:
    """按顺序遍历 items，取每项 obj 里的指定字段列表，去重后返回。

    - 只保留字符串条目；str 后 strip 为空的丢弃
    - 按 norm() 判重，只保留第一次出现的原样文本
    - 返回去重后的列表（保持首次出现顺序）
    """
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        obj = item.get("obj", {})
        if not isinstance(obj, dict):
            continue
        raw_list = obj.get(field, [])
        if not isinstance(raw_list, list):
            continue
        for entry in raw_list:
            if not isinstance(entry, str):
                continue
            stripped = entry.strip()
            if not stripped:
                continue
            key = norm(entry)
            if key in seen:
                continue
            seen.add(key)
            result.append(entry)
    return result


MIN_FUZZY = 6


def _lcs_len(a: str, b: str) -> int:
    """最长公共子串长度（norm 后的短串 DP，O(n*m)）。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for ch in a:
        cur = [0] * (len(b) + 1)
        for j, other in enumerate(b, 1):
            if ch == other:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def _later_done_covers(entry: str, later: dict) -> bool:
    """后续块的"做完"是否覆盖这条"未做"（≥6 字模糊包含，P-035）。"""
    obj = later.get("obj") or {}
    for done in obj.get("做完") or []:
        if isinstance(done, str) and _lcs_len(norm(done), norm(entry)) >= MIN_FUZZY:
            return True
    return False


def bootstrap(items: list[dict], anchors: list[str]) -> dict:
    """将 L2 结构化清单投影为初始状态字典。

    返回包含 目标/待办/已完成/红线/决定/锚点 六个键的字典。
    所有字段均为新建对象，不修改入参。

    待办 = 未做 − 已完成 − 后续块的做完（P-035：按块局部抽取时，
    早期块说的"未做"不会因为后续块做完而自动作废）。
    """
    done = collect(items, "做完")
    requirements = collect(items, "用户要求")

    done_norms = {norm(x) for x in done}
    todo: list[str] = []
    seen_todo: set[str] = set()
    for i, item in enumerate(items):
        obj = item.get("obj") or {}
        if not isinstance(obj, dict):
            continue
        for entry in obj.get("未做") or []:
            if not isinstance(entry, str) or not entry.strip():
                continue
            key = norm(entry)
            if key in seen_todo:
                continue
            seen_todo.add(key)
            if key in done_norms:
                continue
            if any(_later_done_covers(entry, items[j]) for j in range(i + 1, len(items))):
                continue
            todo.append(entry)

    anchor_set: set[str] = set()
    anchor_list: list[str] = []
    for a in anchors:
        if not isinstance(a, str):
            continue
        stripped = a.strip()
        if not stripped:
            continue
        key = norm(a)
        if key in anchor_set:
            continue
        anchor_set.add(key)
        anchor_list.append(a)

    return {
        "目标": "",
        "待办": todo,
        "已完成": done,
        "红线": requirements,
        "决定": [],
        "锚点": anchor_list,
    }


def check_cold(state: dict, items: list[dict]) -> list[str]:
    """校验投影结果，返回问题列表（空列表表示通过）。

    检查项包括字段完整性、已完成/待办/红线与源清单的一致性、
    待办与已完成无重复、锚点类型正确等。
    """
    problems: list[str] = []

    required_keys = ["目标", "待办", "已完成", "红线", "决定", "锚点"]
    for key in required_keys:
        if key not in state:
            problems.append(f"字段丢失: {key}")

    done = collect(items, "做完")
    todo_raw = collect(items, "未做")
    requirements = collect(items, "用户要求")

    # 检查已完成
    completed = state.get("已完成", [])
    if not isinstance(completed, list):
        completed = []
    completed_norms = {norm(x) for x in completed if isinstance(x, str)}

    for entry in done:
        if norm(entry) not in completed_norms:
            problems.append(f"已完成缺条目: {entry}")

    if done and not completed:
        problems.append("冷启动已完成为空")

    # 检查待办
    todo = state.get("待办", [])
    if not isinstance(todo, list):
        todo = []
    todo_norms = {norm(x) for x in todo if isinstance(x, str)}
    todo_raw_norms = {norm(x) for x in todo_raw if isinstance(x, str)}

    for entry in todo:
        if not isinstance(entry, str):
            continue
        if norm(entry) not in todo_raw_norms:
            problems.append(f"待办含无依据条目: {entry}")

    # 待办与已完成不得有 norm 相同的条目
    for entry in todo:
        if not isinstance(entry, str):
            continue
        if norm(entry) in completed_norms:
            problems.append(f"待办与已完成重复: {entry}")

    # 检查红线
    redlines = state.get("红线", [])
    if not isinstance(redlines, list):
        redlines = []
    redline_norms = {norm(x) for x in redlines if isinstance(x, str)}

    for entry in requirements:
        if norm(entry) not in redline_norms:
            problems.append(f"红线缺条目: {entry}")

    # 检查锚点类型
    anchors = state.get("锚点")
    if not isinstance(anchors, list):
        problems.append("类型错误: 锚点")

    return problems
