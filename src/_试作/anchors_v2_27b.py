"""anchors_v2.py - 逐字锚点抽取模块"""

import json
import re
from typing import Any

DEFAULT_QUOTAS: dict[str, int] = {
    "路径": 12,
    "编辑": 8,
    "CMD": 6,
    "ERR": 4,
    "要求": 4,
    "CALL": 6
}

# 约束词表
CONSTRAINT_WORDS = (
    "不许", "不要", "别自己", "别把", "必须", "以后都要", "都要", "一律",
    "只能", "务必", "禁止"
)

# 路径正则：允许中文、字母、数字、下划线、斜杠、点、短横线
# 扩展名限制
PATH_REGEX = re.compile(
    r'([A-Za-z0-9_\u4e00-\u9fa5\\/.\-]+\.(?:py|ps1|md|json|jsonl|txt|jpg|png|yaml|yml|toml|js|ts))'
)

# 句子分割正则
SENTENCE_SPLIT_REGEX = re.compile(r'[。！？；\n]')


def norm_key(kind: str, text: str) -> str:
    """
    生成用于判重的归一化键。

    参数:
        kind: 锚点类型 ("路径", "编辑", "CMD", "ERR", "要求", "CALL")
        text: 原始文本

    返回:
        归一化后的字符串，仅用于比较
    """
    # ERR 必须先按原始文本的行切分，再折叠空白（否则换行早被折成空格）
    if kind == "ERR":
        raw_lines = [line.strip() for line in str(text).splitlines() if line.strip()]
        return re.sub(r'\s+', ' ', raw_lines[-1]).strip() if raw_lines else ""

    # 其它类型：压缩连续空白为单个空格，去首尾空白
    normalized = re.sub(r'\s+', ' ', text).strip()

    if kind in ("路径", "编辑"):
        # 反斜杠替换为正斜杠
        normalized = normalized.replace('\\', '/')
        # 去掉首尾的引号 (' " `)
        normalized = normalized.strip('\'"`')
        # 去掉末尾的冒号/逗号/分号
        normalized = normalized.rstrip(':,;')
        # 合并连续斜杠
        normalized = re.sub(r'/{2,}', '/', normalized)
        # 反复剥除开头的 "./"
        while normalized.startswith('./'):
            normalized = normalized[2:]
        # 去掉末尾的 "/"
        normalized = normalized.rstrip('/')
        # 整串小写
        normalized = normalized.lower()

    return normalized


def anchor_norm(anchor: str) -> str:
    """
    状态里的锚点字符串 → 可比较的键。

    参数:
        anchor: 形如 "路径 src/a.py"、"CMD pwsh: ls" 的锚点字符串

    返回:
        归一化后的字符串，仅用于比较
    """
    if ' ' in anchor:
        kind, rest = anchor.split(' ', 1)
    else:
        kind, rest = "", anchor
    return norm_key(kind, rest)
def constraint_sentences(text: str) -> list[str]:
    """
    提取文本中包含约束词的句子。
    
    参数:
        text: 输入文本
    
    返回:
        包含约束词的句子列表，按出现顺序
    """
    # 按 。！？；\n 分割
    raw_sentences = SENTENCE_SPLIT_REGEX.split(text)
    
    result: list[str] = []
    seen: set[str] = set()
    
    for raw in raw_sentences:
        # 压缩连续空白
        sentence = re.sub(r'\s+', ' ', raw).strip()
        
        # 长度小于 4 的不算
        if len(sentence) < 4:
            continue
        
        # 检查是否包含约束词
        has_constraint = any(word in sentence for word in CONSTRAINT_WORDS)
        if not has_constraint:
            continue
        
        # 超过 80 字截断
        if len(sentence) > 80:
            sentence = sentence[:80] + "…"
        
        # 判重
        key = sentence
        if key in seen:
            continue
        seen.add(key)
        
        result.append(sentence)
    
    return result


def _extract_text_from_content(content: Any) -> str:
    """从 content 字段提取纯文本"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _parse_arguments(args: Any) -> dict[str, Any]:
    """解析 arguments 字段为字典"""
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def _get_meta(event: dict) -> dict[str, Any]:
    """获取 tool/result 事件的 meta 字段"""
    data = event.get("data", {})
    meta = data.get("meta", {})
    if isinstance(meta, dict):
        return meta
    return {}


def _extract_paths_from_call(event: dict) -> list[str]:
    """从 tool/call 事件提取路径"""
    paths = []
    data = event.get("data", {})
    args = _parse_arguments(data.get("arguments"))
    
    # file_path / path
    for key in ("file_path", "path"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            paths.append(val)
    
    # paths 列表
    paths_list = args.get("paths")
    if isinstance(paths_list, list):
        for p in paths_list:
            if isinstance(p, str) and p.strip():
                paths.append(p)
    
    return paths


def _extract_paths_from_result(event: dict) -> list[str]:
    """从 tool/result 事件提取路径"""
    paths = []
    meta = _get_meta(event)
    
    # meta["path"]
    path_val = meta.get("path")
    if isinstance(path_val, str) and path_val.strip():
        paths.append(path_val)
    
    # meta["paths"]
    paths_list = meta.get("paths")
    if isinstance(paths_list, list):
        for p in paths_list:
            if isinstance(p, str) and p.strip():
                paths.append(p)
    
    # meta["files"]
    files = meta.get("files")
    if isinstance(files, list):
        for f in files:
            if isinstance(f, dict):
                f_path = f.get("path")
                if isinstance(f_path, str) and f_path.strip():
                    matches = f.get("matches")
                    if isinstance(matches, list) and len(matches) > 0:
                        first_match = matches[0]
                        if isinstance(first_match, dict):
                            line_num = first_match.get("lineNumber")
                            if isinstance(line_num, int):
                                paths.append(f"{f_path}:{line_num}")
                                continue
                    paths.append(f_path)
    
    return paths


def _extract_edits_from_result(event: dict) -> list[str]:
    """从 tool/result 事件提取编辑锚点"""
    edits = []
    meta = _get_meta(event)
    
    # 检查是否存在 operation 或 diffs 键
    if "operation" in meta or "diffs" in meta:
        path_val = meta.get("path")
        if isinstance(path_val, str) and path_val.strip():
            edits.append(path_val)
        else:
            paths_list = meta.get("paths")
            if isinstance(paths_list, list):
                for p in paths_list:
                    if isinstance(p, str) and p.strip():
                        edits.append(p)
    
    return edits


def _extract_cmd_from_call(event: dict) -> str | None:
    """从 tool/call 事件提取 CMD 锚点"""
    data = event.get("data", {})
    name = data.get("name", "")

    if name not in ("pwsh", "bash"):
        return None

    args = _parse_arguments(data.get("arguments"))
    command = args.get("command")

    if not isinstance(command, str) or not command.strip():
        return None

    # 取第一行
    first_line = command.strip().split('\n')[0].strip()
    if not first_line:
        return None

    # 命令段自适应
    if len(first_line) <= 50:
        cmd_part = first_line
    else:
        cmd_part = first_line[:35] + "…" + first_line[-15:]

    # 多行标记
    lines_after_first = command.strip().split('\n')[1:]
    n = sum(1 for l in lines_after_first if l.strip())
    marker = f" (+{n} 行)" if n > 0 else ""

    # 组装
    prefix = f"CMD {name}: "
    line = prefix + cmd_part + marker

    # 说明段（P-062：截断时必须留省略号）
    description = args.get("description")
    if isinstance(description, str) and description.strip():
        desc = description.strip()
        room = 90 - len(line) - 3
        if room > 0:
            if len(desc) > room:
                line += " · " + (desc[:room - 1] + "…" if room >= 2 else "…")
            else:
                line += " · " + desc

    # 整行硬上限 90
    if len(line) > 90:
        # 重新计算说明段
        desc = ""
        if isinstance(description, str) and description.strip():
            desc = description.strip()

        # 缩短 cmd_part
        min_cmd_len = 10
        while len(line) > 90:
            # 当前 cmd_part 长度（不含省略号）
            if "…" in cmd_part:
                current_len = len(cmd_part) - 1
            else:
                current_len = len(cmd_part)

            if current_len <= min_cmd_len:
                break

            # 缩短
            new_len = current_len - 1
            if new_len <= min_cmd_len:
                cmd_part = first_line[:min_cmd_len] + "…"
            else:
                cmd_part = first_line[:new_len] + "…"

            # 重新组装
            line = prefix + cmd_part + marker
            if desc:
                room = 90 - len(line) - 3
                if room > 0:
                    line += " · " + desc[:room]

    return line

def _extract_err_from_result(event: dict) -> str | None:
    """从 tool/result 事件提取 ERR 锚点"""
    data = event.get("data", {})
    message = data.get("message", {})
    
    if not isinstance(message, dict):
        return None
    
    if not message.get("isError"):
        return None
    
    content = message.get("content")
    text = _extract_text_from_content(content)
    
    if not text.strip():
        return None
    
    # 取末尾 200 字
    stripped = text.strip()
    if len(stripped) > 200:
        tail = stripped[-200:]
    else:
        tail = stripped
    
    return f"ERR {tail}"


def _extract_requirements_from_user(event: dict) -> list[str]:
    """从 user/message 事件提取要求锚点"""
    data = event.get("data", {})
    content = data.get("content")
    
    text = _extract_text_from_content(content)
    if not text:
        return []
    
    sentences = constraint_sentences(text)
    return [f"要求 {s}" for s in sentences]


def _extract_call_from_call(event: dict) -> str | None:
    """从 tool/call 事件提取 CALL 锚点"""
    data = event.get("data", {})
    name = data.get("name", "")
    
    # 跳过 pwsh/bash
    if name in ("pwsh", "bash"):
        return None
    
    args = _parse_arguments(data.get("arguments"))
    
    # 跳过的键
    skip_keys = {"description", "file_path", "path", "paths", "old_string", "new_string", "content"}
    
    # 按顺序找第一个值是非空字符串的键
    for key, value in args.items():
        if key in skip_keys:
            continue
        if isinstance(value, str) and value.strip():
            val = value.strip()
            if len(val) > 80:
                val = val[:80] + "…"
            return f"CALL {name}: {val}"
    
    return None


def _extract_fallback_paths(events: list[dict]) -> list[str]:
    """兜底路径提取：扫描 user/message 和 assistant/message 的文本"""
    paths = []
    seen = set()
    
    for event in events:
        etype = event.get("type", "")
        if etype not in ("user/message", "assistant/message"):
            continue
        
        data = event.get("data", {})
        if etype == "assistant/message":
            content = (data.get("message") or {}).get("content")
        else:
            content = data.get("content")
        
        text = _extract_text_from_content(content)
        if not text:
            continue
        
        # 正则匹配路径
        matches = PATH_REGEX.findall(text)
        for match in matches:
            if match not in seen:
                seen.add(match)
                paths.append(match)
                if len(paths) >= 5:
                    return paths
    
    return paths


def extract_anchors_v3(events: list[dict], *, quotas: dict[str, int] | None = None,
                       max_items: int = 40) -> list[str]:
    """
    从事件流中抽取锚点。
    
    参数:
        events: 事件列表
        quotas: 各类锚点的配额，默认为 DEFAULT_QUOTAS
        max_items: 最终输出的最大条数
    
    返回:
        锚点字符串列表，格式为 "类型 内容"
    """
    if quotas is None:
        quotas = DEFAULT_QUOTAS.copy()
    
    # 按类型收集锚点，保持 seq 顺序
    anchors_by_type: dict[str, list[tuple[int, str]]] = {
        "路径": [],
        "编辑": [],
        "CMD": [],
        "ERR": [],
        "要求": [],
        "CALL": []
    }
    
    # 第一遍：结构化提取
    for event in events:
        etype = event.get("type", "")
        seq = event.get("seq", 0)
        if not isinstance(seq, int):
            seq = 0
        
        if etype == "tool/call":
            # 路径
            for p in _extract_paths_from_call(event):
                anchors_by_type["路径"].append((seq, f"路径 {p}"))
            
            # CMD
            cmd = _extract_cmd_from_call(event)
            if cmd:
                anchors_by_type["CMD"].append((seq, cmd))
            
            # CALL
            call = _extract_call_from_call(event)
            if call:
                anchors_by_type["CALL"].append((seq, call))
        
        elif etype == "tool/result":
            # 路径
            for p in _extract_paths_from_result(event):
                anchors_by_type["路径"].append((seq, f"路径 {p}"))
            
            # 编辑
            for e in _extract_edits_from_result(event):
                anchors_by_type["编辑"].append((seq, f"编辑 {e}"))
            
            # ERR
            err = _extract_err_from_result(event)
            if err:
                anchors_by_type["ERR"].append((seq, err))
        
        elif etype == "user/message":
            # 要求
            for req in _extract_requirements_from_user(event):
                anchors_by_type["要求"].append((seq, req))
    
    # 检查是否需要兜底路径
    if not anchors_by_type["路径"]:
        fallback = _extract_fallback_paths(events)
        for p in fallback:
            # 需要找到对应的 seq，这里简化处理，使用 0
            anchors_by_type["路径"].append((0, f"路径 {p}"))
    
    # 按类型应用配额和判重
    result: list[str] = []
    type_order = ["路径", "编辑", "CMD", "ERR", "要求", "CALL"]
    
    for kind in type_order:
        quota = quotas.get(kind, 0)
        if quota <= 0:
            continue
        
        # 按 seq 排序
        items = sorted(anchors_by_type[kind], key=lambda x: x[0])
        
        # 判重
        seen_keys: set[str] = set()
        count = 0
        for seq, text in items:
            if count >= quota:
                break
            
            # 提取显示文本（去掉类型前缀）
            # 格式是 "类型 内容"，所以内容是从第一个空格后开始
            parts = text.split(' ', 1)
            display_text = parts[1] if len(parts) > 1 else ""
            
            key = norm_key(kind, display_text)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            
            result.append(text)
            count += 1
    
    # 整体截断
    if len(result) > max_items:
        result = result[:max_items]
    
    return result
