"""L2 v3 确定性配件的固定素材。"""

SOURCE = (
    "【用户】把默认档改成 full，然后跑一次功耗测量\n"
    "【助手】先看 bench_power.py 的默认值。\n"
    "【调用 pwsh】python bench_power.py --mode full\n"
    "【结果】full 档 149.4 W，12.71 tok/s，11.76 J/token\n"
    "【用户】以后每轮结束都要跑一次压缩，别攒到快满才压\n"
)

GOOD_OBJ = {
    "title": "功耗测量",
    "summary": "用户要求把默认档改成 full 并测功耗；实测 full 档 149.4 W、12.71 tok/s，折合 11.76 J/token。",
    "facts": [
        "full 档 149.4 W",
        "12.71 tok/s",
        "11.76 J/token",
        "bench_power.py 是测量脚本",
    ],
    "quotes": [
        "把默认档改成 full",
        "full 档 149.4 W，12.71 tok/s，11.76 J/token",
    ],
    "做完": [],
    "未做": [],
    "用户要求": ["以后每轮结束都要跑一次压缩"],
}

BAD_OBJ = {
    "title": "功耗测量",
    "summary": "如前所述，这里推测功耗大概 200 W 左右。",
    "facts": ["功耗大约 200 W", "（与数据无关的断言）"],
    "quotes": ["把默认档改成 full", "这句话原文里没有"],
}

CHUNKS = [
    ["【用户】aaa", "【助手】bbb" * 10],
    ["单元一", "单元二", "单元三", "单元四"],
    ["唯一单元"],
]

ITEMS = [
    {"facts": ["full 档 149.4 W", "12.71 tok/s"]},
    {"facts": ["full 档 149.4 W", "12.71 tok/s", "11.76 J/token"]},
    {"facts": ["ab", "11.76  J/token"]},
]
