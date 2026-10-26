"""派活契约的 lint 与验收反馈辅助（P-008）。

思路（spec-driven + execution feedback，见《委托协议》§8）：
1. 硬约束必须集中放在 `## 约束清单` 一节，按编号列出；散落在其它段落的约束
   容易被 27B 顾此失彼（P-008 实测：修好 ERR 就坏兜底）。
2. 派活后跑 `--check` 验收命令；未过则把失败原因 + 输出末尾作为反馈重派，
   而不是让模型“自由发挥再改一版”。
"""
from __future__ import annotations

import re
import subprocess

CONSTRAINT_MARKERS = ("必须", "不得", "禁止", "只能", "不许", "务必", "严禁")
ITEM_RE = re.compile(r"^\s*(?:[-*]|\d+[.、)])\s+(.*\S)\s*$")


def _sections(spec: str) -> list[tuple[str, list[str]]]:
    """按 `## ` 切段，返回 [(标题, 行列表), ...]（首个标题前的行标题记为空串）。"""
    out: list[tuple[str, list[str]]] = []
    title, lines = "", []
    for line in spec.splitlines():
        if line.startswith("## "):
            out.append((title, lines))
            title, lines = line[3:].strip(), []
        else:
            lines.append(line)
    out.append((title, lines))
    return out


def extract_constraints(spec: str) -> list[str]:
    """取 `## 约束清单`（或标题含「约束」的节）里的编号/列表项。"""
    for title, lines in _sections(spec):
        if "约束" not in title:
            continue
        items = []
        for line in lines:
            match = ITEM_RE.match(line)
            if match:
                items.append(match.group(1).strip())
        if items:
            return items
    return []


def lint_spec(spec: str) -> list[str]:
    """返回契约问题（非阻断）：约束没集中、验收缺失。"""
    problems: list[str] = []
    constraints = extract_constraints(spec)
    if not constraints:
        problems.append("没有 `## 约束清单` 一节；多条约束混在正文里时 27B 容易反复摇摆")
    in_block = False
    outside: list[str] = []
    for title, lines in _sections(spec):
        in_block = "约束" in title
        if in_block:
            continue
        for line in lines:
            if line.lstrip().startswith("#"):
                continue
            if any(marker in line for marker in CONSTRAINT_MARKERS):
                outside.append(line.strip())
    if outside:
        preview = "；".join(outside[:3])
        problems.append(f"有 {len(outside)} 条约束散落在「约束清单」之外（不相邻约束易摇摆）：{preview}")
    if "## 验收" not in spec and "--check" not in spec:
        problems.append("没有 `## 验收` 一节；建议把验收命令写进契约并用 delegate.py --check 执行")
    return problems


def build_feedback(failures: list[str], output_tail: str = "", *, limit: int = 1200) -> str:
    """把未过检查与输出末尾拼成下一轮反馈；只要求针对失败点修改。"""
    lines = ["上一版未过以下验收检查，请只针对这些点修改，不要重写无关部分："]
    lines += [f"- {item}" for item in failures[:20]]
    if output_tail.strip():
        lines += ["", "上一版输出末尾（供定位）：", "```", output_tail[-limit:], "```"]
    return "\n".join(lines)


def run_checks(commands: list[str]) -> tuple[bool, list[str], str]:
    """依次执行验收命令；返回 (是否全过, 失败命令的输出摘要, 最近一次输出)。"""
    failures: list[str] = []
    last_output = ""
    for command in commands:
        done = subprocess.run(command, shell=True, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        output = (done.stdout or "") + (done.stderr or "")
        last_output = output
        if done.returncode != 0:
            tail = "\n".join(output.strip().splitlines()[-12:]) or f"退出码 {done.returncode}"
            failures.append(f"命令失败（rc={done.returncode}）：{command}\n{tail}")
    return not failures, failures, last_output
