from anchors_v2_27b import anchor_norm
"""l4_v3.py - 变更集校验与合并层"""

import json
import hashlib
import copy
from typing import Any

# 变更集契约的单一来源：schema 与 validate 共用这些键集合（P-053/L4 契约测试）
TOP_LEVEL_KEYS = {"目标", "待办", "红线", "决定", "锚点"}
INJECTED_KEYS = {"base_hash"}  # 程序注入，不由模型输出，schema 里不应出现
NESTED_KEYS = {
    "目标": {"值", "变更"},
    "待办": {"完成", "作废", "新增"},
    "红线": {"新增", "作废"},
    "决定": {"新增", "作废"},
    "锚点": {"新增"},
}
TARGET_REQUIRED = {"值", "变更"}
NEW_ITEM_FIELDS = ("待办", "红线", "锚点")


def state_hash(state: dict) -> str:
    """
    对 state 取 sha256：先 json.dumps(state, ensure_ascii=False, sort_keys=True)，
    再对 UTF-8 编码取 sha256 hexdigest，返回前 8 个字符。
    """
    serialized = json.dumps(state, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(serialized.encode('utf-8')).hexdigest()
    return digest[:8]


def check_changes(prev: dict, changes: dict) -> list[str]:
    """
    返回问题列表（空列表 = 没问题），按发现顺序。
    """
    issues: list[str] = []
    
    # 1. 顶层键检查
    allowed_keys = TOP_LEVEL_KEYS | INJECTED_KEYS
    for key in changes:
        if key not in allowed_keys:
            issues.append(f"未知字段: {key}")
    
    # 2. base_hash 检查
    if "base_hash" in changes:
        given_hash = changes["base_hash"]
        actual_hash = state_hash(prev)
        if given_hash != actual_hash:
            issues.append(f"基准哈希不符: {given_hash} != {actual_hash}")
    
    # 3. 待办 完成/作废 检查
    todo_changes = changes.get("待办", {})
    complete_list = todo_changes.get("完成", [])
    void_list = todo_changes.get("作废", [])
    
    prev_todo = prev.get("待办", [])
    prev_todo_len = len(prev_todo)
    
    # 辅助函数：检查单条
    def check_entry(field_name: str, entry: Any) -> list[str]:
        local_issues = []
        if not isinstance(entry, dict):
            local_issues.append(f"条目格式错误: {field_name} / {entry}")
            return local_issues
        
        idx = entry.get("idx")
        expect = entry.get("expect")
        
        # idx 必须是整数（bool 不算）
        if not isinstance(idx, int) or isinstance(idx, bool):
            local_issues.append(f"条目格式错误: {field_name} / {entry}")
            return local_issues
        
        # expect 必须是字符串
        if not isinstance(expect, str):
            local_issues.append(f"条目格式错误: {field_name} / {entry}")
            return local_issues
        
        # idx 范围检查
        if idx < 0 or idx >= prev_todo_len:
            local_issues.append(f"下标越界: {field_name} / {idx}")
            return local_issues
        
        # expect 匹配检查
        actual = prev_todo[idx]
        if expect != actual:
            local_issues.append(f"前置条件不符: {field_name}[{idx}] 期望「{expect}」实际「{actual}」")
        
        return local_issues
    
    # 检查完成列表
    seen_complete_idxs = set()
    for entry in complete_list:
        local_issues = check_entry("完成", entry)
        issues.extend(local_issues)
        if isinstance(entry, dict) and isinstance(entry.get("idx"), int) and not isinstance(entry.get("idx"), bool):
            idx = entry["idx"]
            if idx in seen_complete_idxs:
                issues.append(f"重复下标: 完成 / {idx}")
            seen_complete_idxs.add(idx)
    
    # 检查作废列表
    seen_void_idxs = set()
    for entry in void_list:
        local_issues = check_entry("作废", entry)
        issues.extend(local_issues)
        if isinstance(entry, dict) and isinstance(entry.get("idx"), int) and not isinstance(entry.get("idx"), bool):
            idx = entry["idx"]
            if idx in seen_void_idxs:
                issues.append(f"重复下标: 作废 / {idx}")
            seen_void_idxs.add(idx)
    
    # 冲突下标检查
    for idx in seen_complete_idxs:
        if idx in seen_void_idxs:
            issues.append(f"冲突下标: {idx}")
    
    # 4. 新增条目检查（待办/红线/锚点）
    for field in NEW_ITEM_FIELDS:
        field_changes = changes.get(field, {})
        new_items = field_changes.get("新增", [])
        for item in new_items:
            if not isinstance(item, str) or item == "":
                issues.append(f"新增条目非法: {field} / {item}")
    
    # 5. 决定检查
    decision_changes = changes.get("决定", {})
    
    # 决定新增
    new_decisions = decision_changes.get("新增", [])
    for entry in new_decisions:
        if not isinstance(entry, dict):
            issues.append(f"决定条目非法: {entry}")
            continue
        content = entry.get("内容")
        reason = entry.get("理由")
        if not isinstance(content, str) or content == "" or not isinstance(reason, str) or reason == "":
            issues.append(f"决定条目非法: {entry}")
    
    # 决定作废
    void_decisions = decision_changes.get("作废", [])
    for entry in void_decisions:
        if not isinstance(entry, dict):
            issues.append(f"决定条目非法: {entry}")
            continue
        content = entry.get("内容")
        if not isinstance(content, str) or content == "":
            issues.append(f"决定条目非法: {entry}")
    
    return issues


def apply_changes(state: dict, changes: dict) -> dict:
    """
    返回新 state（深拷贝，绝不修改入参）。fail-closed：任何前置条件不满足就抛 ValueError。
    """
    new_state = copy.deepcopy(state)
    
    # 1. base_hash 检查
    if "base_hash" in changes:
        given_hash = changes["base_hash"]
        actual_hash = state_hash(state)
        if given_hash != actual_hash:
            raise ValueError(f"基准哈希不符: {given_hash} != {actual_hash}")
    
    # 2. 目标
    if "目标" in changes:
        target_changes = changes["目标"]
        if isinstance(target_changes, dict):
            change_val = target_changes.get("变更", "")
            if isinstance(change_val, str) and change_val != "":
                new_val = target_changes.get("值")
                if isinstance(new_val, str):
                    new_state["目标"] = new_val
    
    # 3. 待办
    if "待办" in changes:
        todo_changes = changes["待办"]
        complete_list = todo_changes.get("完成", [])
        void_list = todo_changes.get("作废", [])
        new_items = todo_changes.get("新增", [])
        
        original_todo = state.get("待办", [])
        original_completed = state.get("已完成", [])
        
        # 校验并收集要移除的索引
        remove_indices = set()
        
        # 处理完成
        for entry in complete_list:
            if not isinstance(entry, dict):
                raise ValueError(f"条目格式错误: 完成 / {entry}")
            idx = entry.get("idx")
            expect = entry.get("expect")
            if not isinstance(idx, int) or isinstance(idx, bool):
                raise ValueError(f"条目格式错误: 完成 / {entry}")
            if not isinstance(expect, str):
                raise ValueError(f"条目格式错误: 完成 / {entry}")
            if idx < 0 or idx >= len(original_todo):
                raise ValueError(f"下标越界: 完成 / {idx}")
            if original_todo[idx] != expect:
                raise ValueError(f"前置条件不符: 完成[{idx}] 期望「{expect}」实际「{original_todo[idx]}」")
            remove_indices.add(idx)
        
        # 处理作废
        for entry in void_list:
            if not isinstance(entry, dict):
                raise ValueError(f"条目格式错误: 作废 / {entry}")
            idx = entry.get("idx")
            expect = entry.get("expect")
            if not isinstance(idx, int) or isinstance(idx, bool):
                raise ValueError(f"条目格式错误: 作废 / {entry}")
            if not isinstance(expect, str):
                raise ValueError(f"条目格式错误: 作废 / {entry}")
            if idx < 0 or idx >= len(original_todo):
                raise ValueError(f"下标越界: 作废 / {idx}")
            if original_todo[idx] != expect:
                raise ValueError(f"前置条件不符: 作废[{idx}] 期望「{expect}」实际「{original_todo[idx]}」")
            remove_indices.add(idx)
        
        # 构建新的待办列表
        new_todo = []
        for i, item in enumerate(original_todo):
            if i not in remove_indices:
                new_todo.append(item)
        
        # 将完成的条目添加到已完成
        for entry in complete_list:
            if isinstance(entry, dict):
                idx = entry.get("idx")
                if isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < len(original_todo):
                    item = original_todo[idx]
                    if item not in new_state.get("已完成", []):
                        new_state.setdefault("已完成", []).append(item)
        
        # 添加新增条目
        for item in new_items:
            if not isinstance(item, str) or item == "":
                raise ValueError(f"新增条目非法: 待办 / {item}")
            if item not in new_todo and item not in new_state.get("已完成", []):
                new_todo.append(item)
        
        new_state["待办"] = new_todo
    
    # 4. 红线
    if "红线" in changes:
        redline_changes = changes["红线"]
        new_items = redline_changes.get("新增", [])
        void_items = redline_changes.get("作废", [])
        
        current_redlines = new_state.get("红线", [])
        
        # 先追加新增
        for item in new_items:
            if not isinstance(item, str) or item == "":
                raise ValueError(f"新增条目非法: 红线 / {item}")
            if item not in current_redlines:
                current_redlines.append(item)
        
        # 再移除作废
        for item in void_items:
            if item in current_redlines:
                current_redlines.remove(item)
        
        new_state["红线"] = current_redlines
    
    # 5. 决定
    if "决定" in changes:
        decision_changes = changes["决定"]
        new_decisions = decision_changes.get("新增", [])
        void_decisions = decision_changes.get("作废", [])
        
        current_decisions = new_state.get("决定", [])
        
        # 先移除作废
        for entry in void_decisions:
            if not isinstance(entry, dict):
                raise ValueError(f"决定条目非法: {entry}")
            content = entry.get("内容")
            if not isinstance(content, str) or content == "":
                raise ValueError(f"决定条目非法: {entry}")
            # 按内容相等移除
            new_decisions_list = []
            for d in current_decisions:
                if isinstance(d, dict) and d.get("内容") == content:
                    continue
                new_decisions_list.append(d)
            current_decisions = new_decisions_list
        
        # 再追加新增
        for entry in new_decisions:
            if not isinstance(entry, dict):
                raise ValueError(f"决定条目非法: {entry}")
            content = entry.get("内容")
            reason = entry.get("理由")
            if not isinstance(content, str) or content == "" or not isinstance(reason, str) or reason == "":
                raise ValueError(f"决定条目非法: {entry}")
            # 检查内容是否已存在
            exists = False
            for d in current_decisions:
                if isinstance(d, dict) and d.get("内容") == content:
                    exists = True
                    break
            if not exists:
                current_decisions.append(entry)
        
        new_state["决定"] = current_decisions
    
    # 6. 锚点
    if "锚点" in changes:
        anchor_changes = changes["锚点"]
        new_items = anchor_changes.get("新增", [])

        current_anchors = new_state.get("锚点", [])
        existing_keys = {anchor_norm(a) for a in current_anchors}
        for item in new_items:
            if not isinstance(item, str) or item == "":
                raise ValueError(f"新增条目非法: 锚点 / {item}")
            key = anchor_norm(item)
            if key in existing_keys:
                continue
            current_anchors.append(item)
            existing_keys.add(key)

        new_state["锚点"] = current_anchors
    
    return new_state


def validate_state(prev: dict, new: dict, warnings: list[str] | None = None) -> list[str]:
    """
    返回问题列表（空 = 通过）。

    warnings 传入列表时，把「锚点归一化重复」这类非阻断问题追加进去（P-058）。
    """
    issues: list[str] = []
    
    # 7. 类型检查
    if not isinstance(new.get("目标"), str):
        issues.append("类型错误: 目标")
    for key in ["待办", "已完成", "红线", "锚点"]:
        if not isinstance(new.get(key), list):
            issues.append(f"类型错误: {key}")
    if not isinstance(new.get("决定"), list):
        issues.append("类型错误: 决定")
    else:
        for item in new["决定"]:
            if not isinstance(item, dict):
                issues.append("类型错误: 决定")
                break
    
    # 1. 字段丢失检查
    for key in prev:
        if key not in new:
            issues.append(f"字段丢失: {key}")
    
    # 2. 锚点包含检查
    prev_anchors = prev.get("锚点", [])
    new_anchors = new.get("锚点", [])
    if isinstance(prev_anchors, list) and isinstance(new_anchors, list):
        for anchor in prev_anchors:
            if anchor not in new_anchors:
                issues.append(f"锚点丢失: {anchor}")
    
    # 3. 重复项检查（锚点不按精确重复硬拦，改由归一化软警告，P-058）
    for key in ["待办", "已完成", "红线"]:
        items = new.get(key, [])
        if isinstance(items, list):
            seen = set()
            for item in items:
                if item in seen:
                    issues.append(f"重复项: {key} / {item}")
                seen.add(item)
    if warnings is not None:
        anchors = new.get("锚点", [])
        if isinstance(anchors, list):
            seen_keys: dict[str, str] = {}
            for anchor in anchors:
                if not isinstance(anchor, str):
                    continue
                key = anchor_norm(anchor)
                if key in seen_keys:
                    warnings.append(f"锚点归一化重复: {anchor!r} 与 {seen_keys[key]!r}")
                else:
                    seen_keys[key] = anchor
    
    # 4. 跨列表不变量
    todo_items = new.get("待办", [])
    completed_items = new.get("已完成", [])
    if isinstance(todo_items, list) and isinstance(completed_items, list):
        completed_set = set(completed_items)
        for item in todo_items:
            if item in completed_set:
                issues.append(f"条目同时存在于待办与已完成: {item}")
    
    # 5. 引用不变量
    prev_anchors_set = set(prev.get("锚点", [])) if isinstance(prev.get("锚点"), list) else set()
    new_anchors_set = set(new.get("锚点", [])) if isinstance(new.get("锚点"), list) else set()
    
    # 收集 prev 中引用的锚点
    referenced_anchors = set()
    
    # 待办
    for item in prev.get("待办", []):
        if isinstance(item, str):
            for anchor in prev_anchors_set:
                if anchor in item:
                    referenced_anchors.add(anchor)
    
    # 红线
    for item in prev.get("红线", []):
        if isinstance(item, str):
            for anchor in prev_anchors_set:
                if anchor in item:
                    referenced_anchors.add(anchor)
    
    # 决定
    for decision in prev.get("决定", []):
        if isinstance(decision, dict):
            content = decision.get("内容", "")
            reason = decision.get("理由", "")
            if isinstance(content, str):
                for anchor in prev_anchors_set:
                    if anchor in content:
                        referenced_anchors.add(anchor)
            if isinstance(reason, str):
                for anchor in prev_anchors_set:
                    if anchor in reason:
                        referenced_anchors.add(anchor)
    
    # 检查引用的锚点是否在 new 中
    for anchor in referenced_anchors:
        if anchor not in new_anchors_set:
            issues.append(f"引用锚点丢失: {anchor}")
    
    # 6. 决定内容检查
    for decision in new.get("决定", []):
        if isinstance(decision, dict):
            content = decision.get("内容")
            if not isinstance(content, str) or content == "":
                issues.append("决定的 内容 为空")
    
    return issues
