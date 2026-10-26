"""L1 v2 验收：六类锚点、结构化优先、归一化去重、按类型配额。

用法：
    .\\.venv\\Scripts\\python.exe src\_试作\\test_anchors_v2.py src\_试作\\anchors_v2_27b.py
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fixture_anchors_v2 import (EVENTS, FALLBACK_EVENTS, MANY_PATHS,  # noqa: E402
                                MANY_SHELLS)

MOD = None


def anchors(events=None, **kw):
    return MOD.extract_anchors_v3(EVENTS if events is None else events, **kw)


def by_kind(kind):
    return [a for a in anchors() if a.startswith(kind + " ")]


# ---------------------------------------------------------------- 路径类

def t_path_from_file_path():
    """调用参数里的 file_path 要进路径类。"""
    got = by_kind("路径")
    return any("项目说明.md" in a for a in got), f"路径={got}"


def t_path_sep_dedupe():
    """反斜杠与正斜杠写法要合并成一条。"""
    norm = "02_文档/项目说明.md"
    hits = [a for a in by_kind("路径") if MOD.norm_key("路径", a[3:]) == norm]
    return len(hits) == 1, f"命中 {len(hits)} 条：{hits}"


def t_path_from_meta_paths():
    """meta.paths 列表要逐个进锚点。"""
    got = by_kind("路径")
    return any(a.endswith("a.md") for a in got) and any(a.endswith("b.md") for a in got), f"路径={got}"


def t_path_with_line():
    """meta.files 的行号要带上。"""
    return any(a.endswith(":38") for a in by_kind("路径")), f"路径={by_kind('路径')}"


def t_path_from_call_paths_arg():
    """调用参数里的 paths 列表也要抽。"""
    ev = [{"type": "tool/call", "seq": 1,
           "data": {"name": "read", "arguments": "{\"paths\": [\"x.py\", \"y.py\"]}",
                    "callId": "z"}}]
    got = [a for a in anchors(ev) if a.startswith("路径 ")]
    return len(got) == 2, f"路径={got}"


# ---------------------------------------------------------------- 编辑类

def t_edit_kind():
    """带 operation/diffs 的结果要产出编辑类。"""
    got = by_kind("编辑")
    return len(got) == 1 and "x.py" in got[0], f"编辑={got}"


def t_edit_no_false_positive():
    """普通读取结果不该产生编辑。"""
    got = by_kind("编辑")
    return all("项目说明" not in a for a in got), f"编辑={got}"


# ---------------------------------------------------------------- CMD 类

def t_cmd_with_description():
    """命令要带上 description。"""
    got = by_kind("CMD")
    return any("· 跑测试" in a for a in got), f"CMD={got}"


def t_cmd_count():
    """两次 shell 调用各一条。"""
    return len(by_kind("CMD")) == 2, f"CMD={by_kind('CMD')}"


def t_cmd_first_line():
    """多行命令只取首行。"""
    ev = [{"type": "tool/call", "seq": 1,
           "data": {"name": "pwsh", "arguments": "{\"command\": \"cd C:\\\\x\\npytest -q\"}",
                    "callId": "z"}}]
    got = by_kind("CMD") if False else [a for a in anchors(ev) if a.startswith("CMD ")]
    return len(got) == 1 and "cd C:\\x" in got[0] and "pytest" not in got[0], f"CMD={got}"


# ---------------------------------------------------------------- ERR 类

def t_err_dedupe():
    """同样的异常行只留一条。"""
    return len(by_kind("ERR")) == 1, f"ERR={by_kind('ERR')}"


def t_err_tail():
    """错误锚点取尾部，含异常类型。"""
    got = by_kind("ERR")
    return bool(got) and "AssertionError" in got[0], f"ERR={got}"


# ---------------------------------------------------------------- 要求类

def t_user_constraints():
    """用户约束句要进要求类（三句）。"""
    got = by_kind("要求")
    return len(got) == 3, f"要求={got}"


def t_constraint_short():
    """要求条目不超过 80 字 + 省略号。"""
    worst = max((len(a) - 3 for a in by_kind("要求")), default=0)
    return worst <= 81, f"最长 {worst}"


def t_constraint_sentences_direct():
    """constraint_sentences 只留含约束词的句子。"""
    got = MOD.constraint_sentences("别自己决定去改 定稿/。这是另一句话。以后都要跑压缩！")
    return len(got) == 2 and all("另一句" not in s for s in got), f"got={got}"


def t_constraint_empty():
    """没有约束词就返回空。"""
    return MOD.constraint_sentences("今天天气不错。") == [], "误报"


# ---------------------------------------------------------------- CALL 类

def t_call_with_value():
    """非 shell 工具要带首个有效参数值（glob/grep 的 pattern 也算）。"""
    got = by_kind("CALL")
    hit = any("web_search" in a and "上下文压缩" in a for a in got)
    return hit and all(": " in a for a in got), f"CALL={got}"


def t_call_skips_noise_keys():
    """只有 old_string 这类噪声参数时不产出 CALL 行。"""
    ev = [{"type": "tool/call", "seq": 1,
           "data": {"name": "edit",
                    "arguments": "{\"old_string\": \"aaa\", \"content\": \"bbb\"}",
                    "callId": "z"}}]
    got = [a for a in anchors(ev) if a.startswith("CALL ")]
    return got == [], f"CALL={got}"


# ---------------------------------------------------------------- 归一化

def t_norm_path():
    """路径归一化：斜杠、引号、结尾标点。"""
    a = MOD.norm_key("路径", "02_文档\\x.py")
    b = MOD.norm_key("路径", '"02_文档/x.py:"')
    return a == b, f"{a!r} vs {b!r}"


def t_norm_whitespace():
    """空白折叠。"""
    return MOD.norm_key("要求", "不许   改   定稿") == "不许 改 定稿", \
        repr(MOD.norm_key("要求", "不许   改   定稿"))


def t_norm_err_last_line():
    """ERR 归一化只取最后一个非空行。"""
    a = MOD.norm_key("ERR", "收集\n中间\nE AssertionError: x")
    b = MOD.norm_key("ERR", "别的\nE AssertionError: x")
    return a == b and a.startswith("E AssertionError"), f"{a!r} vs {b!r}"


# ---------------------------------------------------------------- 配额 / 顺序

def t_quota_path():
    """路径配额默认 12。"""
    got = [a for a in anchors(MANY_PATHS) if a.startswith("路径 ")]
    return len(got) == 12, f"实际 {len(got)} 条"


def t_quota_cmd():
    """CMD 配额默认 6。"""
    got = [a for a in anchors(MANY_SHELLS) if a.startswith("CMD ")]
    return len(got) == 6, f"实际 {len(got)} 条"


def t_quota_override():
    """配额可覆盖。"""
    got = [a for a in anchors(quotas={"路径": 2}) if a.startswith("路径 ")]
    return len(got) == 2, f"实际 {len(got)} 条"


def t_kind_order():
    """输出按 路径→编辑→CMD→ERR→要求→CALL 排序。"""
    kinds = [a.split(" ", 1)[0] for a in anchors()]
    order = ["路径", "编辑", "CMD", "ERR", "要求", "CALL"]
    idx = [order.index(k) for k in kinds]
    return idx == sorted(idx), f"顺序={kinds}"


def t_max_items():
    """整体上限生效。"""
    got = anchors(max_items=5)
    return len(got) == 5 and all(a.startswith("路径 ") for a in got), f"实际={len(got)}"


# ---------------------------------------------------------------- 兜底 / 其它

def t_fallback_paths():
    """没有结构化路径时才用正则兜底。"""
    got = [a for a in anchors(FALLBACK_EVENTS) if a.startswith("路径 ")]
    return len(got) >= 3 and any("项目说明.md" in a for a in got), f"路径={got}"


def t_fallback_not_triggered():
    """有结构化路径时不跑兜底。"""
    return not any("bench_power.py" in a for a in by_kind("路径")), "兜底误触发"


def t_empty_events():
    """空输入返回空列表。"""
    return anchors([]) == [], "空输入没返回空"


def t_skip_irrelevant():
    """无关事件不产生锚点。"""
    ev = [{"type": "job/settled", "seq": 1, "data": {"jobId": "j1"}}]
    return anchors(ev) == [], "无关事件产生了锚点"


# cluster F 断言


def t_cmd_line_len():
    """CMD 锚点行总长不超过 90 字。"""
    ev = {"data": {"name": "pwsh", "arguments": json.dumps({
        "command": "Get-ChildItem -Recurse -Force /path/to/your/workspace\\02_文档 "
                   "| Select-Object Name,Length,LastWriteTime",
        "description": "列出文档目录里的全部文件及其大小和最后修改时间用于排查问题原因"})}}
    line = MOD._extract_cmd_from_call(ev)
    return len(line) <= 90, f"长度={len(line)} 行={line!r}"


def t_cmd_command_capped():
    """CMD 的命令部分不超过 61 字（60 + 省略号）。"""
    ev = {"data": {"name": "pwsh", "arguments": json.dumps({"command": "x" * 200})}}
    line = MOD._extract_cmd_from_call(ev)
    head = line.split(": ", 1)[1].split(" · ")[0]
    return len(head) <= 61, f"命令段长度={len(head)}"


def t_cmd_desc_capped():
    """说明段长度受整行 ≤90 约束（P-059：不再硬截 20 字）。"""
    ev = {"data": {"name": "pwsh",
                   "arguments": json.dumps({"command": "ls", "description": "描述" * 40})}}
    line = MOD._extract_cmd_from_call(ev)
    desc = line.split(" · ", 1)[1] if " · " in line else ""
    return len(line) <= 90 and len(desc) >= 25, f"说明={len(desc)} 行长={len(line)}"


# P-059 验收


def t_cmd_adaptive_desc():
    """说明段应尽量保留：短命令下说明可超过 20 字。"""
    ev = {"data": {"name": "pwsh", "arguments": json.dumps({
        "command": "ls 02_文档",
        "description": "列出文档目录里的全部文件按修改时间排序后只显示最后十个"})}}
    line = MOD._extract_cmd_from_call(ev)
    desc = line.split(" · ", 1)[1] if " · " in line else ""
    return len(desc) >= 25 and len(line) <= 90, f"说明长度={len(desc)} 行长={len(line)}"


def t_cmd_head_tail():
    """长命令保留尾部信息（路径/参数在尾部时不丢）。"""
    cmd = "Get-ChildItem -Recurse -Force | Where-Object { $_.Length -gt 1000 } | "
    cmd += "Select-Object FullName,Length -First 20 02_文档/项目说明.md"
    ev = {"data": {"name": "pwsh", "arguments": json.dumps({"command": cmd})}}
    line = MOD._extract_cmd_from_call(ev)
    return "项目说明.md" in line and len(line) <= 90, f"行={line!r}"


def t_cmd_multiline_marker():
    """多行命令要标出还有几行。"""
    ev = {"data": {"name": "pwsh", "arguments": json.dumps({
        "command": "cd C:\\workspace\nGet-ChildItem\nSet-Location .."})}}
    line = MOD._extract_cmd_from_call(ev)
    return "(+2 行)" in line, f"行={line!r}"


def t_cmd_line_always_90():
    """长命令 + 长说明 + 多行组合下仍守住 90 字。"""
    ev = {"data": {"name": "pwsh", "arguments": json.dumps({
        "command": ("Get-ChildItem -Recurse -Force /path/to/your/workspace\\02_文档 "
                    "\nSelect-Object Name,Length\nMeasure-Object"),
        "description": "列出文档目录里全部文件的大小并统计汇总情况用于排查问题原因"})}}
    line = MOD._extract_cmd_from_call(ev)
    return len(line) <= 90, f"长度={len(line)} 行={line!r}"


def t_cmd_desc_trunc_ellipsis():
    """说明被截断时必须带 …（P-062）。"""
    ev = {"data": {"name": "pwsh", "arguments": json.dumps({
        "command": "ls", "description": "描" * 200})}}
    line = MOD._extract_cmd_from_call(ev)
    desc = line.split(" · ", 1)[1] if " · " in line else ""
    return len(line) <= 90 and desc.endswith("…"), f"行={line!r}"


CASES = [
    t_cmd_desc_trunc_ellipsis,
    t_cmd_adaptive_desc, t_cmd_head_tail, t_cmd_multiline_marker, t_cmd_line_always_90,
    t_cmd_line_len, t_cmd_command_capped, t_cmd_desc_capped,
    t_path_from_file_path, t_path_sep_dedupe, t_path_from_meta_paths, t_path_with_line,
    t_path_from_call_paths_arg,
    t_edit_kind, t_edit_no_false_positive,
    t_cmd_with_description, t_cmd_count, t_cmd_first_line,
    t_err_dedupe, t_err_tail,
    t_user_constraints, t_constraint_short, t_constraint_sentences_direct, t_constraint_empty,
    t_call_with_value, t_call_skips_noise_keys,
    t_norm_path, t_norm_whitespace, t_norm_err_last_line,
    t_quota_path, t_quota_cmd, t_quota_override, t_kind_order, t_max_items,
    t_fallback_paths, t_fallback_not_triggered, t_empty_events, t_skip_irrelevant,
]


def main() -> int:
    global MOD
    if len(sys.argv) < 2:
        print("用法: test_anchors_v2.py <候选模块.py>")
        return 2
    spec = importlib.util.spec_from_file_location("candidate_anchors", sys.argv[1])
    MOD = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(MOD)

    passed = failed = 0
    for case in CASES:
        try:
            ok, msg = case()
        except Exception as exc:
            ok, msg = False, f"{type(exc).__name__}: {exc}"
        name = (case.__doc__ or case.__name__).strip().splitlines()[0]
        print(f"[{'PASS' if ok else 'FAIL'}] {case.__name__:<28} {name}")
        if not ok:
            print(f"       -> {msg}")
        passed += ok
        failed += not ok
    print(f"\n合计 {passed} 过 / {failed} 没过（共 {len(CASES)}）")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
