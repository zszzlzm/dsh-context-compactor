"""slim_v2.py — 会话事件瘦身层 v2。

把 DSH 会话日志里的原始事件压成给下游摘要模型看的短文本行。
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = [
    "truncate_head_tail",
    "pick_budget",
    "render_args",
    "build_units_v2",
    "extract_anchors_v2",
]


def truncate_head_tail(text: str, *, head: int, tail: int, seq: int) -> str:
    """按头/尾预算截断文本，中间用固定标记占位。

    - 若 len(text) <= head + tail：原样返回
    - 否则：返回 text 前 head 个字符 + 标记 + text 的后 tail 个字符
    - head 或 tail 允许为 0
    - head 和 tail 都为 0 时，返回标记本身
    """
    if len(text) <= head + tail:
        return text
    removed = len(text) - head - tail
    marker = f"…[截断 中间{removed}字符 · 见 seq {seq}]"
    return text[:head] + marker + text[-tail:] if tail > 0 else text[:head] + marker


def pick_budget(name: str, is_error: bool, *, budget: int = 4000) -> tuple[int, int]:
    """根据工具名与是否报错，决定一条工具结果的头/尾预算。

    返回 (head, tail)，两者之和等于 budget。
    """
    if is_error:
        h = budget // 2
        return (h, budget - h)
    if name in ("pwsh", "bash"):
        h = int(budget * 0.25)
        return (h, budget - h)
    if name in ("read", "grep", "glob"):
        h = int(budget * 0.75)
        return (h, budget - h)
    h = int(budget * 0.4)
    return (h, budget - h)


def render_args(arguments: str, *, per_value: int = 200, total: int = 400) -> str:
    """把工具调用的 JSON 参数字符串渲染成紧凑的 "键=值" 文本。

    - 解析成功且为 dict：逐键值对生成 "键=值"，值超 per_value 截断加 "…"，
      用单空格连接；连接后超 total 截断加 "…"
    - 解析失败或非 dict：原始字符串超 total 截断加 "…"
    - 空字符串返回空字符串
    """
    if not arguments:
        return ""
    try:
        obj = json.loads(arguments)
    except (json.JSONDecodeError, TypeError):
        obj = None

    if isinstance(obj, dict):
        parts: list[str] = []
        for k, v in obj.items():
            sv = str(v)
            if len(sv) > per_value:
                sv = sv[:per_value] + "…"
            parts.append(f"{k}={sv}")
        joined = " ".join(parts)
        if len(joined) > total:
            joined = joined[:total] + "…"
        return joined

    # 解析失败或非 dict
    if len(arguments) > total:
        return arguments[:total] + "…"
    return arguments


def _extract_text_from_content(content: Any) -> str:
    """从 content（字符串或块列表）中提取纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return "".join(parts)
    return ""


def _count_images(content: Any) -> int:
    """统计 content 块列表中 type=="image" 的块数。"""
    if not isinstance(content, list):
        return 0
    n = 0
    for block in content:
        if isinstance(block, dict) and block.get("type") == "image":
            n += 1
    return n


def _normalize_for_dedup(text: str) -> str:
    """把文本中所有空白压成一个空格、去首尾、转小写，用于去重比较。"""
    return re.sub(r"\s+", " ", text).strip().lower()


def _resolve_tool_name(
    data: dict,
    call_id_to_name: dict[str, str],
) -> str:
    """从 tool/result 的 data 中解析工具名。"""
    msg = data.get("message") or {}
    tool_call_id = msg.get("toolCallId")
    if not tool_call_id:
        source = msg.get("source") or {}
        tool_call_id = source.get("callId")
    if tool_call_id and tool_call_id in call_id_to_name:
        return call_id_to_name[tool_call_id]
    return ""


def _get_result_text_and_images(data: dict) -> tuple[str, int]:
    """从 tool/result 的 data 中提取文本和图片块数。"""
    msg = data.get("message") or {}
    content = msg.get("content")
    text = _extract_text_from_content(content)
    img = _count_images(content)
    return text, img


def build_units_v2(
    events: list[dict],
    *,
    max_chars: int = 6000,
    budget: int = 4000,
) -> list[str]:
    """把事件列表按 seq 升序压成短文本行列表。

    只处理 user/message、assistant/message、tool/call、tool/result 四种 type。
    """
    # 按 seq 升序
    sorted_events = sorted(events, key=lambda e: e.get("seq", 0))

    # 建 callId -> name 映射
    call_id_to_name: dict[str, str] = {}
    for ev in sorted_events:
        if ev.get("type") == "tool/call":
            d = ev.get("data") or {}
            cid = d.get("callId")
            nm = d.get("name")
            if cid and nm:
                call_id_to_name[cid] = nm

    # 预扫描：为每条 tool/result 记录 (name, normalized_args) -> 后续 tool/result 的 seq
    # 用于判断"已过期"
    # 先收集所有 tool/call 的 (name, args) 及其 seq
    # 再收集所有 tool/result 的 (name, args) 及其 seq
    # 过期判断：同一个工具名 + 同样的 arguments 在后面又被调用过
    # 即：本条 tool/result 对应的 (name, args) 在后续某次 tool/call 中出现过
    # 且要取"后一次 tool/result 的 seq"

    # 收集所有 tool/call 的 (name, args) 列表（按 seq 升序）
    tool_calls: list[tuple[int, str, str]] = []  # (seq, name, args)
    for ev in sorted_events:
        if ev.get("type") == "tool/call":
            d = ev.get("data") or {}
            tool_calls.append((ev.get("seq", 0), d.get("name", ""), d.get("arguments", "")))

    # 收集所有 tool/result 的 (name, args, seq)
    # 需要把 tool/result 关联到对应的 tool/call 的 args
    # 通过 callId 关联
    result_entries: list[dict] = []
    for ev in sorted_events:
        if ev.get("type") != "tool/result":
            continue
        d = ev.get("data") or {}
        name = _resolve_tool_name(d, call_id_to_name)
        # 找对应的 tool/call 的 arguments
        msg = d.get("message") or {}
        tool_call_id = msg.get("toolCallId")
        if not tool_call_id:
            source = msg.get("source") or {}
            tool_call_id = source.get("callId")
        args = ""
        if tool_call_id:
            for cs, cn, ca in tool_calls:
                # 需要找到 callId 对应的 args
                # 但 tool_calls 里没存 callId，重新查
                pass
        # 重新遍历找 callId -> args
        for ev2 in sorted_events:
            if ev2.get("type") == "tool/call":
                d2 = ev2.get("data") or {}
                if d2.get("callId") == tool_call_id:
                    args = d2.get("arguments", "")
                    break
        result_entries.append({
            "seq": ev.get("seq", 0),
            "name": name,
            "args": args,
            "data": d,
        })

    # 对每条 result_entry，判断是否过期：
    # 同一个工具名 + 同样的 arguments 在后面又被调用过
    # 即存在后续 tool/call 的 (name, args) 与本条相同
    # 且要取"后一次 tool/result 的 seq"
    # 找到后续 tool/call 后，再找该 tool/call 对应的 tool/result 的 seq

    # 建 callId -> result_seq 映射
    call_id_to_result_seq: dict[str, int] = {}
    for ev in sorted_events:
        if ev.get("type") == "tool/result":
            d = ev.get("data") or {}
            msg = d.get("message") or {}
            tool_call_id = msg.get("toolCallId")
            if not tool_call_id:
                source = msg.get("source") or {}
                tool_call_id = source.get("callId")
            if tool_call_id:
                call_id_to_result_seq[tool_call_id] = ev.get("seq", 0)

    # 建 (name, args) -> 后续 tool/call 的 callId 列表（按 seq 升序）
    # 用于找"后一次"
    # 对每条 result_entry，找后续 tool/call 中 (name, args) 相同的第一个
    # 然后取该 tool/call 对应的 tool/result 的 seq

    # 预计算每条 result_entry 的过期信息
    expired_info: dict[int, int] = {}  # result_seq -> 后一次 result_seq
    for re_entry in result_entries:
        re_seq = re_entry["seq"]
        re_name = re_entry["name"]
        re_args = re_entry["args"]
        # 找后续 tool/call 中 (name, args) 相同的
        for ev in sorted_events:
            if ev.get("type") != "tool/call":
                continue
            if ev.get("seq", 0) <= re_seq:
                continue
            d = ev.get("data") or {}
            if d.get("name") == re_name and d.get("arguments") == re_args:
                # 找到后续 tool/call，取其 callId
                cid = d.get("callId")
                if cid and cid in call_id_to_result_seq:
                    expired_info[re_seq] = call_id_to_result_seq[cid]
                break

    # 去重：记录已见过的工具结果文本（归一化后）
    seen_result_texts: dict[str, int] = {}  # normalized_text -> first_seq

    lines: list[str] = []

    for ev in sorted_events:
        etype = ev.get("type")
        seq = ev.get("seq", 0)
        data = ev.get("data") or {}

        if etype == "user/message":
            content = data.get("content")
            text = _extract_text_from_content(content)
            if not text:
                continue
            if len(text) <= max_chars:
                lines.append("【用户】" + text)
            else:
                # 切成若干段，每段长度 = max_chars 减去前缀长度
                # 前缀长度取决于段数
                # 先估算段数
                # 每段可用长度 = max_chars - len(prefix)
                # prefix = "【用户 i/n】" 或 "【用户】"
                # 简化：先按 max_chars 切，再调整
                # 实际：n = ceil(len(text) / (max_chars - prefix_len))
                # 但 prefix_len 依赖 n
                # 迭代求解
                n = 1
                while True:
                    prefix = f"【用户 {1}/{n}】" if n > 1 else "【用户】"
                    seg_len = max_chars - len(prefix)
                    if seg_len <= 0:
                        seg_len = 1
                    needed = (len(text) + seg_len - 1) // seg_len
                    if needed == n:
                        break
                    n = needed
                # 切段
                prefix = f"【用户 {1}/{n}】" if n > 1 else "【用户】"
                seg_len = max_chars - len(prefix)
                if seg_len <= 0:
                    seg_len = 1
                for i in range(n):
                    start = i * seg_len
                    end = min(start + seg_len, len(text))
                    seg = text[start:end]
                    if i == 0:
                        p = prefix
                    else:
                        p = f"【用户 {i+1}/{n}】"
                    lines.append(p + seg)

        elif etype == "assistant/message":
            msg = data.get("message") or {}
            content = msg.get("content")
            text = _extract_text_from_content(content)
            if not text:
                continue
            line = "【助手】" + text
            if len(line) > max_chars:
                line = line[:max_chars - 20] + "…[超长截断]"
            lines.append(line)

        elif etype == "tool/call":
            name = data.get("name", "")
            args = data.get("arguments", "")
            rendered = render_args(args, per_value=200, total=400)
            if not rendered:
                continue
            lines.append(f"【调用 {name}】" + rendered)

        elif etype == "tool/result":
            name = _resolve_tool_name(data, call_id_to_name)
            msg = data.get("message") or {}
            is_error = bool(msg.get("isError"))
            text, img = _get_result_text_and_images(data)

            # 判断过期
            if seq in expired_info:
                n_chars = len(text)
                lines.append(f"【结果 已过期】被 seq {expired_info[seq]} 取代（原长{n_chars}字）")
                continue

            # 判断重复
            norm = _normalize_for_dedup(text)
            if len(norm) >= 200 and norm in seen_result_texts:
                n_chars = len(text)
                lines.append(f"【结果 重复】同 seq {seen_result_texts[norm]}（{n_chars}字）")
                continue
            if len(norm) >= 200:
                seen_result_texts[norm] = seq

            # 正常输出
            if not text:
                if img > 0:
                    body = "（仅图片）"
                else:
                    body = ""
            else:
                head, tail = pick_budget(name, is_error, budget=budget)
                body = truncate_head_tail(text, head=head, tail=tail, seq=seq)

            prefix = "【结果 错误】" if is_error else "【结果】"
            line = prefix + body
            if img > 0:
                line += f" · 图片{img}张"

            # 收口
            if len(line) > max_chars:
                line = truncate_head_tail(line, head=max_chars - 120, tail=0, seq=seq)

            lines.append(line)

        # 其它 type 跳过

    return lines


def extract_anchors_v2(
    events: list[dict],
    *,
    max_items: int = 30,
) -> list[str]:
    """从事件列表中提取逐字锚点行，去重，最多 max_items 条。

    - tool/call 且 name 是 "pwsh"/"bash"：取 command 首行
    - tool/call 且 name 不是上面两个：行 = "CALL " + name
    - tool/result 且 isError 为真：取结果文本结尾 200 字符
    """
    sorted_events = sorted(events, key=lambda e: e.get("seq", 0))

    # 建 callId -> name 映射
    call_id_to_name: dict[str, str] = {}
    for ev in sorted_events:
        if ev.get("type") == "tool/call":
            d = ev.get("data") or {}
            cid = d.get("callId")
            nm = d.get("name")
            if cid and nm:
                call_id_to_name[cid] = nm

    seen: set[str] = set()
    anchors: list[str] = []

    for ev in sorted_events:
        etype = ev.get("type")
        data = ev.get("data") or {}

        if etype == "tool/call":
            name = data.get("name", "")
            if name in ("pwsh", "bash"):
                args = data.get("arguments", "")
                try:
                    obj = json.loads(args)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(obj, dict):
                    continue
                command = obj.get("command")
                if not command or not isinstance(command, str):
                    continue
                first_line = command.split("\n", 1)[0].strip()
                if not first_line:
                    continue
                if len(first_line) > 120:
                    first_line = first_line[:120] + "…"
                line = f"CMD {name}: {first_line}"
            else:
                line = f"CALL {name}"

            if line not in seen:
                seen.add(line)
                anchors.append(line)
                if len(anchors) >= max_items:
                    break

        elif etype == "tool/result":
            msg = data.get("message") or {}
            if not msg.get("isError"):
                continue
            text = _extract_text_from_content(msg.get("content"))
            if not text:
                continue
            tail_200 = text[-200:] if len(text) > 200 else text
            tail_200 = tail_200.strip()
            if not tail_200:
                continue
            line = f"ERR {tail_200}"
            if line not in seen:
                seen.add(line)
                anchors.append(line)
                if len(anchors) >= max_items:
                    break

    return anchors
