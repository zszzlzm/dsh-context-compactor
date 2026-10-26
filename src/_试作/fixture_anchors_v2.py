"""L1 v2 的固定素材：六类锚点 + 结构化来源 + 归一化去重 + 配额。"""

SAME_ERR_PREAMBLE = "收集失败\n" * 50
SAME_ERR_LINE = "E   AssertionError: assert 1 == 2"

CONSTRAINT_USER = (
    "别自己决定去改 定稿/ 目录。这是另一句话。"
    "以后每轮结束都要跑一次压缩！"
    "不要闷头干，先查文档；"
)

EVENTS = [
    {"type": "turn/start", "seq": 1, "data": {"turn": 1}},
    # 用户约束三句
    {"type": "user/message", "seq": 2, "data": {"content": CONSTRAINT_USER}},
    # read 调用带 file_path
    {"type": "tool/call", "seq": 3,
     "data": {"name": "read", "arguments": "{\"file_path\": \"02_文档\\\\项目说明.md\"}",
              "callId": "c1"}},
    {"type": "tool/result", "seq": 4, "data": {"message": {
        "isError": False, "content": [{"type": "text", "text": "文件内容"}]},
        "meta": {"path": "02_文档\\项目说明.md"}}},
    # 同一个路径用正斜杠再出现一次（应去重）
    {"type": "tool/call", "seq": 5,
     "data": {"name": "read", "arguments": "{\"file_path\": \"02_文档/项目说明.md\"}",
              "callId": "c2"}},
    {"type": "tool/result", "seq": 6, "data": {"message": {
        "isError": False, "content": [{"type": "text", "text": "又是文件内容"}]},
        "meta": {"path": "02_文档/项目说明.md"}}},
    # edit 调用 + 带 operation 的结果 → 编辑
    {"type": "tool/call", "seq": 7,
     "data": {"name": "edit", "arguments": "{\"file_path\": \"src\\\\x.py\"}",
              "callId": "c3"}},
    {"type": "tool/result", "seq": 8, "data": {"message": {
        "isError": False, "content": [{"type": "text", "text": "已修改"}]},
        "meta": {"path": "src\\x.py", "operation": "edit", "diffs": [{"+1": "x"}]}}},
    # glob 结果带 paths 列表
    {"type": "tool/call", "seq": 9,
     "data": {"name": "glob", "arguments": "{\"pattern\": \"*.md\"}", "callId": "c4"}},
    {"type": "tool/result", "seq": 10, "data": {"message": {
        "isError": False, "content": [{"type": "text", "text": "两个文件"}]},
        "meta": {"shape": "paths", "paths": ["a.md", "b.md"]}}},
    # grep 结果带 files + 行号
    {"type": "tool/call", "seq": 11,
     "data": {"name": "grep", "arguments": "{\"pattern\": \"TODO\"}", "callId": "c5"}},
    {"type": "tool/result", "seq": 12, "data": {"message": {
        "isError": False, "content": [{"type": "text", "text": "命中"}]},
        "meta": {"shape": "matches", "files": [
            {"path": "02_文档\\三料训练方案.md", "matches": [{"lineNumber": 38, "line": "x"}]}]}}},
    # 两次同样的错误 → ERR 去重
    {"type": "tool/call", "seq": 13,
     "data": {"name": "pwsh", "arguments": "{\"command\": \"pytest -q\", \"description\": \"跑测试\"}",
              "callId": "c6"}},
    {"type": "tool/result", "seq": 14, "data": {"message": {
        "isError": True,
        "content": [{"type": "text", "text": SAME_ERR_PREAMBLE + SAME_ERR_LINE}]}}},
    {"type": "tool/call", "seq": 15,
     "data": {"name": "pwsh", "arguments": "{\"command\": \"pytest -q\"}", "callId": "c7"}},
    {"type": "tool/result", "seq": 16, "data": {"message": {
        "isError": True,
        "content": [{"type": "text", "text": SAME_ERR_PREAMBLE + SAME_ERR_LINE}]}}},
    # 非 shell 工具 → CALL 带参数
    {"type": "tool/call", "seq": 17,
     "data": {"name": "web_search", "arguments": "{\"queries\": \"上下文压缩\"}", "callId": "c8"}},
    # 兜底：只有 assistant 文本里出现路径（本批已有结构化路径，所以兜底不该触发）
    {"type": "assistant/message", "seq": 18, "data": {"message": {"content": [
        {"type": "text", "text": "我改了 src/bench_power.py 的默认值。"}]}}},
]

FALLBACK_EVENTS = [
    {"type": "user/message", "seq": 1, "data": {"content": "看看 02_文档/项目说明.md 和 a/b/c.py"}},
    {"type": "assistant/message", "seq": 2, "data": {"message": {"content": [
        {"type": "text", "text": "还有 src/x.py。"}]}}},
]

MANY_PATHS = [
    {"type": "tool/call", "seq": 100 + i,
     "data": {"name": "read", "arguments": "{\"file_path\": \"f%d.py\"}" % i, "callId": "m%d" % i}}
    for i in range(20)
]

MANY_SHELLS = [
    {"type": "tool/call", "seq": 200 + i,
     "data": {"name": "pwsh", "arguments": "{\"command\": \"cmd%d\"}" % i, "callId": "s%d" % i}}
    for i in range(8)
]
