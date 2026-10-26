"""compact_core_v2 模块"""
from __future__ import annotations
import copy

INDEPENDENT_WORDS = ("如前所述", "上一段", "前文", "综上", "承接上", "接着上",
                     "上文提到", "见前", "前面说到", "此前提到")

def chunk_units(units: list[str], *, max_chars: int = 6000) -> list[list[str]]:
    """将单元列表分块，每块连接后长度不超过 max_chars"""
    filtered = [u for u in units if u.strip()]
    chunks: list[list[str]] = []
    current: list[str] = []
    current_len = 0
    
    for unit in filtered:
        unit_len = len(unit)
        # 如果单元本身超过限制，单独成块
        if unit_len > max_chars:
            if current:
                chunks.append(current)
                current = []
                current_len = 0
            chunks.append([unit])
            continue
            
        # 检查加入当前块是否超限
        # 如果当前块为空，直接加入
        # 否则，当前长度 + 1 (换行符) + 单元长度 <= max_chars
        if not current:
            current.append(unit)
            current_len = unit_len
        else:
            if current_len + 1 + unit_len <= max_chars:
                current.append(unit)
                current_len += 1 + unit_len
            else:
                chunks.append(current)
                current = [unit]
                current_len = unit_len
                
    if current:
        chunks.append(current)
        
    return chunks

def check_summary(obj: dict, source: str, *, max_summary: int = 220) -> list[dict]:
    """检查摘要质量"""
    results = []
    summary = obj.get("summary", "")
    quote = obj.get("quote", "")
    
    # 1. summary_nonempty
    s_strip = summary.strip()
    ok1 = len(s_strip) > 0
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
    hits = [w for w in INDEPENDENT_WORDS if w in summary]
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
    hits4 = [w for w in guess_words if w in summary]
    ok4 = len(hits4) == 0
    detail4 = f"推断性表述: {hits4}" if hits4 else "无推断"
    results.append({
        "id": "summary_no_guess",
        "ok": ok4,
        "level": "hard",
        "detail": detail4
    })
    
    # 5. quote_verbatim
    def norm(s: str) -> str:
        return "".join(s.split())
        
    n_quote = norm(quote)
    n_source = norm(source)
    
    if not n_quote:
        ok5 = False
        detail5 = "引文不是原文"
    else:
        prefix = n_quote[:40]
        ok5 = prefix in n_source
        detail5 = "引文逐字命中" if ok5 else "引文不是原文"
        
    results.append({
        "id": "quote_verbatim",
        "ok": ok5,
        "level": "hard",
        "detail": detail5
    })
        
    return results

def apply_changes(state: dict, changes: dict) -> dict:
    """应用变更到状态，返回新状态"""
    new_state = copy.deepcopy(state)
    
    # 目标
    if "目标" in changes:
        target_change = changes["目标"]
        if target_change.get("变更", ""):
            new_state["目标"] = target_change["值"]
            
    # 待办
    if "待办" in changes:
        todo_change = changes["待办"]
        complete_indices = todo_change.get("完成", [])
        void_indices = todo_change.get("作废", [])
        new_items = todo_change.get("新增", [])
        
        original_todos = new_state["待办"]
        original_completed = new_state["已完成"]
        
        # 1. 处理完成
        completed_set = set(original_completed)
        new_completed = list(original_completed)
        removed_indices = set()
        
        for idx in complete_indices:
            if not isinstance(idx, int) or isinstance(idx, bool):
                continue
            if 0 <= idx < len(original_todos):
                item = original_todos[idx]
                removed_indices.add(idx)
                if item not in completed_set:
                    new_completed.append(item)
                    completed_set.add(item)
                    
        # 2. 处理作废
        for idx in void_indices:
            if not isinstance(idx, int) or isinstance(idx, bool):
                continue
            if 0 <= idx < len(original_todos):
                removed_indices.add(idx)
                
        # 3. 构建新待办列表
        new_todos = []
        for i, item in enumerate(original_todos):
            if i not in removed_indices:
                new_todos.append(item)
                
        # 4. 处理新增
        existing_todos = set(new_todos)
        existing_completed = set(new_completed)
        for item in new_items:
            if item not in existing_todos and item not in existing_completed:
                new_todos.append(item)
                existing_todos.add(item)
                
        new_state["待办"] = new_todos
        new_state["已完成"] = new_completed
        
    # 红线
    if "红线" in changes:
        red_change = changes["红线"]
        new_reds = red_change.get("新增", [])
        void_reds = red_change.get("作废", [])
        
        current_reds = new_state["红线"]
        # 先追加新增
        for item in new_reds:
            if item not in current_reds:
                current_reds.append(item)
        # 再移除作废
        new_reds_list = [item for item in current_reds if item not in void_reds]
        new_state["红线"] = new_reds_list
        
    # 决定
    if "决定" in changes:
        dec_change = changes["决定"]
        void_decs = dec_change.get("作废", [])
        new_decs = dec_change.get("新增", [])
        
        current_decs = new_state["决定"]
        
        # 移除作废
        void_contents = {d["内容"] for d in void_decs}
        filtered_decs = [d for d in current_decs if d["内容"] not in void_contents]
        
        # 追加新增
        existing_contents = {d["内容"] for d in filtered_decs}
        for d in new_decs:
            if d["内容"] not in existing_contents:
                filtered_decs.append(d)
                existing_contents.add(d["内容"])
                
        new_state["决定"] = filtered_decs
        
    # 锚点
    if "锚点" in changes:
        anchor_change = changes["锚点"]
        new_anchors = anchor_change.get("新增", [])
        current_anchors = new_state["锚点"]
        for item in new_anchors:
            if item not in current_anchors:
                current_anchors.append(item)
        new_state["锚点"] = current_anchors
        
    return new_state

def validate_state(prev: dict, new: dict) -> list[str]:
    """验证新状态的有效性"""
    errors = []
    
    # 1. 字段丢失
    for key in prev.keys():
        if key not in new:
            errors.append(f"字段丢失: {key}")
            
    # 5. 类型检查 (先做类型检查，避免后续访问出错)
    if "目标" in new and not isinstance(new["目标"], str):
        errors.append("类型错误: 目标")
        
    list_keys = ["待办", "已完成", "红线", "锚点"]
    for key in list_keys:
        if key in new and not isinstance(new[key], list):
            errors.append(f"类型错误: {key}")
            
    if "决定" in new:
        if not isinstance(new["决定"], list):
            errors.append("类型错误: 决定")
        else:
            for item in new["决定"]:
                if not isinstance(item, dict):
                    errors.append("类型错误: 决定")
                    break
                    
    # 2. 锚点丢失
    if "锚点" in prev and "锚点" in new:
        prev_anchors = prev["锚点"]
        new_anchors = new["锚点"]
        if isinstance(prev_anchors, list) and isinstance(new_anchors, list):
            for anchor in prev_anchors:
                if anchor not in new_anchors:
                    errors.append(f"锚点丢失: {anchor}")
                    
    # 3. 重复项检查
    for key in list_keys:
        if key in new and isinstance(new[key], list):
            items = new[key]
            seen = set()
            for item in items:
                if item in seen:
                    errors.append(f"重复项: {key} / {item}")
                else:
                    seen.add(item)
                    
    # 4. 决定内容非空
    if "决定" in new and isinstance(new["决定"], list):
        for item in new["决定"]:
            if isinstance(item, dict):
                content = item.get("内容")
                if not isinstance(content, str) or not content:
                    errors.append("决定的 内容 为空")
                    
    return errors

def check_changes(prev: dict, changes: dict) -> list[str]:
    """检查变更字典的有效性"""
    errors = []
    known_keys = {"目标", "待办", "红线", "决定", "锚点"}
    
    # 未知字段
    for key in changes.keys():
        if key not in known_keys:
            errors.append(f"未知字段: {key}")
            
    # 待办检查
    if "待办" in changes:
        todo_change = changes["待办"]
        prev_todos = prev.get("待办", [])
        len_todos = len(prev_todos) if isinstance(prev_todos, list) else 0
        
        for field in ["完成", "作废"]:
            if field in todo_change:
                indices = todo_change[field]
                if not isinstance(indices, list):
                    continue
                seen_indices = set()
                for idx in indices:
                    # 类型检查
                    if not isinstance(idx, int) or isinstance(idx, bool):
                        errors.append(f"索引类型错误: {field} / {idx}")
                        continue
                    # 越界检查
                    if idx < 0 or idx >= len_todos:
                        errors.append(f"索引越界: {field} / {idx}")
                        continue
                    # 重复检查
                    if idx in seen_indices:
                        errors.append(f"重复索引: {field} / {idx}")
                    else:
                        seen_indices.add(idx)
                        
        if "新增" in todo_change:
            new_items = todo_change["新增"]
            if isinstance(new_items, list):
                for item in new_items:
                    if item == "":
                        errors.append("新增为空: 待办")
                        
    # 红线检查
    if "红线" in changes:
        red_change = changes["红线"]
        if "新增" in red_change:
            new_items = red_change["新增"]
            if isinstance(new_items, list):
                for item in new_items:
                    if item == "":
                        errors.append("新增为空: 红线")
                        
    # 决定检查
    if "决定" in changes:
        dec_change = changes["决定"]
        if "新增" in dec_change:
            new_items = dec_change["新增"]
            if isinstance(new_items, list):
                for item in new_items:
                    if isinstance(item, dict):
                        content = item.get("内容")
                        if content == "":
                            errors.append("新增为空: 决定")
                    else:
                        if item == "":
                            errors.append("新增为空: 决定")
                            
    # 锚点检查
    if "锚点" in changes:
        anchor_change = changes["锚点"]
        if "新增" in anchor_change:
            new_items = anchor_change["新增"]
            if isinstance(new_items, list):
                for item in new_items:
                    if item == "":
                        errors.append("新增为空: 锚点")
                        
    return errors
