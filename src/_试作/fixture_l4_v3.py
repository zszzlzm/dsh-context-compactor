"""L4 v3 的固定素材：前置条件、基准哈希、跨列表不变量。"""

STATE = {
    "目标": "把 ch017 的试镜跑完",
    "待办": ["补 S02 素材", "跑试镜", "写 定稿清单"],
    "已完成": ["CPU 挡位实验"],
    "红线": ["不许动 定稿/ 目录", "复查 src\\bench_power.py"],
    "决定": [{"内容": "CPU 默认档改 full", "理由": "J/token 最低"}],
    "锚点": ["src\\bench_power.py", "src\\compact_context.py"],
}

OK_CHANGES = {
    "目标": {"值": "镜头表 v3 定稿", "变更": "用户改了优先级"},
    "待办": {
        "完成": [{"idx": 0, "expect": "补 S02 素材"}],
        "作废": [{"idx": 1, "expect": "跑试镜"}],
        "新增": ["核对 v3 审核表"],
    },
    "红线": {"新增": ["不许改 ch017 正文"], "作废": []},
    "决定": {"新增": [{"内容": "状态层用 precondition", "理由": "防双应用"}], "作废": []},
    "锚点": {"新增": ["src\\gc_v2_27b.py"]},
}

BAD_INDEX_CHANGES = {"待办": {"完成": [{"idx": 9, "expect": "不存在"}]}}
BAD_EXPECT_CHANGES = {"待办": {"完成": [{"idx": 0, "expect": "别的文本"}]}}
BAD_FORMAT_CHANGES = {"待办": {"完成": [0]}}
CONFLICT_CHANGES = {
    "待办": {"完成": [{"idx": 0, "expect": "补 S02 素材"}],
             "作废": [{"idx": 0, "expect": "补 S02 素材"}]},
}
DUP_CHANGES = {
    "待办": {"完成": [{"idx": 0, "expect": "补 S02 素材"},
                      {"idx": 0, "expect": "补 S02 素材"}]},
}
