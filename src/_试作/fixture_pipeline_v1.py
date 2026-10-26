"""端到端回归的固定合成会话（P-041 拍 1）。

为什么不用真实会话：真实会话每轮内容都在变，断言只能写"看起来对"；这里固定 3 轮
（第 8/9/10 轮），故意用个位数轮次，暴露"按字符串排序会排成 10/8/9"的缺陷。

每轮：用户要求（含约束词）→ read 调用/结果 → pwsh 调用/结果 → 助手收尾。
8 个事件固定，L2 切块结果确定，失败注入用例可以精确断言调用次数。
"""
from __future__ import annotations

import json


def _turn(turn: int, base: int) -> list[dict]:
    return [
        {"type": "turn/start", "seq": base, "data": {"turn": turn}},
        {"type": "user/message", "seq": base + 1,
         "data": {"content": f"第{turn}轮：给 README 补一个示例，别自己改定稿目录。"}},
        {"type": "tool/call", "seq": base + 2,
         "data": {"name": "read",
                  "arguments": json.dumps({"file_path": f"02_文档/示例{turn}.md"},
                                          ensure_ascii=False),
                  "callId": f"c{turn}a"}},
        {"type": "tool/result", "seq": base + 3,
         "data": {"message": {"isError": False, "content": [
             {"type": "text", "text": f"第{turn}轮的文件内容，用来做摘要素材。"}]},
             "meta": {"path": f"02_文档\\示例{turn}.md"}}},
        {"type": "tool/call", "seq": base + 4,
         "data": {"name": "pwsh",
                  "arguments": json.dumps({"command": f"pytest -q test_{turn}.py",
                                           "description": f"第{turn}轮跑测试"},
                                          ensure_ascii=False),
                  "callId": f"c{turn}b"}},
        {"type": "tool/result", "seq": base + 5,
         "data": {"message": {"isError": False, "content": [
             {"type": "text", "text": "全部通过"}]}}},
        {"type": "assistant/message", "seq": base + 6,
         "data": {"message": {"content": [
             {"type": "text", "text": f"第{turn}轮完成。"}]}}},
        {"type": "turn/end", "seq": base + 7, "data": {"turn": turn}},
    ]


EVENTS: list[dict] = []
for _t, _base in ((8, 1), (9, 9), (10, 17)):
    EVENTS.extend(_turn(_t, _base))
