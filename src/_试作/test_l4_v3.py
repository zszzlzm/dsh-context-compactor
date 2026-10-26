"""L4 v3 验收：前置条件、基准哈希、fail-closed、跨列表不变量。

用法：
    .\\.venv\\Scripts\\python.exe src\_试作\\test_l4_v3.py src\_试作\\l4_v3_27b.py
"""
from __future__ import annotations

import copy
import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fixture_l4_v3 import (BAD_EXPECT_CHANGES, BAD_FORMAT_CHANGES,  # noqa: E402
                           BAD_INDEX_CHANGES, CONFLICT_CHANGES, DUP_CHANGES,
                           OK_CHANGES, STATE)

MOD = None


def applied():
    return MOD.apply_changes(STATE, OK_CHANGES)


def raises(fn) -> tuple[bool, str]:
    try:
        fn()
    except ValueError as exc:
        return True, str(exc)
    except Exception as exc:  # 抛了别的异常也算不合格
        return False, f"抛了 {type(exc).__name__}: {exc}"
    return False, "没有抛 ValueError"


# ---------------------------------------------------------------- 哈希

def t_hash_len():
    """哈希是 8 位十六进制。"""
    h = MOD.state_hash(STATE)
    return len(h) == 8 and all(c in "0123456789abcdef" for c in h), f"实际={h}"


def t_hash_stable():
    """键顺序不影响哈希。"""
    a = MOD.state_hash(STATE)
    b = MOD.state_hash(dict(reversed(list(STATE.items()))))
    return a == b, f"{a} vs {b}"


def t_hash_changes():
    """内容变了哈希就变。"""
    other = dict(STATE, 目标="别的事")
    return MOD.state_hash(STATE) != MOD.state_hash(other), "哈希没变"


# ---------------------------------------------------------------- check_changes

def t_check_ok():
    """合法变更集不报问题。"""
    problems = MOD.check_changes(STATE, OK_CHANGES)
    return not problems, f"问题: {problems}"


def t_check_unknown_key():
    """未知顶层键要报。"""
    problems = MOD.check_changes(STATE, {"红钱": {"新增": ["x"]}})
    return any("未知字段" in p for p in problems), f"问题: {problems}"


def t_check_base_hash_bad():
    """基准哈希不符要报。"""
    problems = MOD.check_changes(STATE, {"base_hash": "deadbeef"})
    return any("基准哈希不符" in p for p in problems), f"问题: {problems}"


def t_check_base_hash_ok():
    """基准哈希正确不报。"""
    problems = MOD.check_changes(STATE, {"base_hash": MOD.state_hash(STATE)})
    return not problems, f"问题: {problems}"


def t_check_format():
    """裸整数下标要报格式错误。"""
    problems = MOD.check_changes(STATE, BAD_FORMAT_CHANGES)
    return any("条目格式错误" in p for p in problems), f"问题: {problems}"


def t_check_range():
    """越界下标要报。"""
    problems = MOD.check_changes(STATE, BAD_INDEX_CHANGES)
    return any("下标越界" in p for p in problems), f"问题: {problems}"


def t_check_dup():
    """同字段重复下标要报。"""
    problems = MOD.check_changes(STATE, DUP_CHANGES)
    return any("重复下标" in p for p in problems), f"问题: {problems}"


def t_check_conflict():
    """同一下标同时完成与作废要报冲突。"""
    problems = MOD.check_changes(STATE, CONFLICT_CHANGES)
    return any("冲突下标" in p for p in problems), f"问题: {problems}"


def t_check_expect():
    """expect 与原文不符要报前置条件不符。"""
    problems = MOD.check_changes(STATE, BAD_EXPECT_CHANGES)
    return any("前置条件不符" in p for p in problems), f"问题: {problems}"


def t_check_empty_new():
    """空的新增条目要报。"""
    problems = MOD.check_changes(STATE, {"待办": {"新增": [""]}})
    return any("新增条目非法" in p for p in problems), f"问题: {problems}"


def t_check_decision_bad():
    """决定缺内容或理由要报。"""
    problems = MOD.check_changes(STATE, {"决定": {"新增": [{"内容": "", "理由": ""}]}})
    return any("决定条目非法" in p for p in problems), f"问题: {problems}"


# ---------------------------------------------------------------- apply_changes

def t_apply_ok():
    """合法变更集应用出预期结果。"""
    st = applied()
    ok = (st["目标"] == "镜头表 v3 定稿"
          and "补 S02 素材" in st["已完成"] and "补 S02 素材" not in st["待办"]
          and "跑试镜" not in st["待办"] and "跑试镜" not in st["已完成"]
          and st["待办"][-1] == "核对 v3 审核表"
          and "不许改 ch017 正文" in st["红线"]
          and any(d["内容"] == "状态层用 precondition" for d in st["决定"])
          and "src\\gc_v2_27b.py" in st["锚点"])
    return ok, f"结果={st}"


def t_apply_no_mutate():
    """不许修改入参。"""
    before = copy.deepcopy(STATE)
    MOD.apply_changes(STATE, OK_CHANGES)
    return STATE == before, "入参被改了"


def t_apply_double_raises():
    """关键：同一变更集应用两次，第二次必须抛错。"""
    once = MOD.apply_changes(STATE, OK_CHANGES)
    ok, msg = raises(lambda: MOD.apply_changes(once, OK_CHANGES))
    return ok, f"第二次应用: {msg}"


def t_apply_range_raises():
    """越界下标必须抛错而不是静默跳过。"""
    ok, msg = raises(lambda: MOD.apply_changes(STATE, BAD_INDEX_CHANGES))
    return ok, msg


def t_apply_expect_raises():
    """expect 不符必须抛错。"""
    ok, msg = raises(lambda: MOD.apply_changes(STATE, BAD_EXPECT_CHANGES))
    return ok, msg


def t_apply_hash_raises():
    """基准哈希不符必须抛错。"""
    ok, msg = raises(lambda: MOD.apply_changes(STATE, {"base_hash": "deadbeef"}))
    return ok, msg


def t_apply_format_raises():
    """裸整数下标必须抛错。"""
    ok, msg = raises(lambda: MOD.apply_changes(STATE, BAD_FORMAT_CHANGES))
    return ok, msg


def t_apply_anchor_dedupe():
    """锚点重复新增只保留一次。"""
    changes = {"锚点": {"新增": ["src\\bench_power.py"]}}
    st = MOD.apply_changes(STATE, changes)
    return st["锚点"].count("src\\bench_power.py") == 1, f"锚点={st['锚点']}"


def t_apply_todo_order():
    """剩余待办保持原顺序。"""
    st = applied()
    return st["待办"] == ["写 定稿清单", "核对 v3 审核表"], f"待办={st['待办']}"


# ---------------------------------------------------------------- validate_state

def t_val_ok():
    """合法流转通过校验。"""
    problems = MOD.validate_state(STATE, applied())
    return not problems, f"问题: {problems}"


def t_val_cross_list():
    """同一条同时在待办与已完成要报。"""
    bad = dict(STATE, 已完成=STATE["已完成"] + ["补 S02 素材"])
    problems = MOD.validate_state(STATE, bad)
    return any("同时存在于待办与已完成" in p for p in problems), f"问题: {problems}"


def t_val_ref_anchor():
    """被引用的锚点丢失要报。"""
    bad = dict(STATE, 锚点=["src\\compact_context.py"])
    problems = MOD.validate_state(STATE, bad)
    return any("引用锚点丢失" in p for p in problems), f"问题: {problems}"


def t_val_field_loss():
    """丢字段要报。"""
    bad = applied()
    del bad["红线"]
    problems = MOD.validate_state(STATE, bad)
    return any("字段丢失" in p for p in problems), f"问题: {problems}"


def t_val_type():
    """类型错要报。"""
    bad = dict(STATE, 目标=["不是字符串"])
    problems = MOD.validate_state(STATE, bad)
    return any("类型错误" in p for p in problems), f"问题: {problems}"


def t_val_dup():
    """列表内重复要报。"""
    bad = dict(STATE, 红线=STATE["红线"] + [STATE["红线"][0]])
    problems = MOD.validate_state(STATE, bad)
    return any("重复项" in p for p in problems), f"问题: {problems}"


# cluster F 断言


def t_anchor_variants_collapse():
    """同一条路径的五种写法跨轮只留 1 条。"""
    prev = {"目标": "t", "待办": [], "已完成": [], "红线": [], "决定": [],
            "锚点": ["路径 src\\a.py"]}
    changes = {"base_hash": MOD.state_hash(prev), "锚点": {"新增": [
        "路径 src/a.py", "路径 src\\A.py",
        "路径 src//a.py", "路径 ./src/a.py"]}}
    new = MOD.apply_changes(prev, changes)
    return len(new["锚点"]) == 1, f"锚点={new['锚点']}"


def t_anchor_keep_first_form():
    """去重时保留首见原文形态（不把老锚点改写）。"""
    prev = {"目标": "t", "待办": [], "已完成": [], "红线": [], "决定": [],
            "锚点": ["路径 src\\a.py"]}
    changes = {"base_hash": MOD.state_hash(prev), "锚点": {"新增": ["路径 src/a.py"]}}
    new = MOD.apply_changes(prev, changes)
    return new["锚点"] == ["路径 src\\a.py"], f"锚点={new['锚点']}"


def t_anchor_distinct_kept():
    """不同文件仍然各自保留。"""
    prev = {"目标": "t", "待办": [], "已完成": [], "红线": [], "决定": [],
            "锚点": ["路径 src/a.py"]}
    changes = {"base_hash": MOD.state_hash(prev), "锚点": {"新增": ["路径 src/b.py"]}}
    new = MOD.apply_changes(prev, changes)
    return len(new["锚点"]) == 2, f"锚点={new['锚点']}"


def t_anchor_casefold():
    """大小写不同视为同一路径（Windows 语义）。"""
    prev = {"目标": "t", "待办": [], "已完成": [], "红线": [], "决定": [],
            "锚点": ["路径 src/a.py"]}
    changes = {"base_hash": MOD.state_hash(prev), "锚点": {"新增": ["路径 src/A.py"]}}
    return len(MOD.apply_changes(prev, changes)["锚点"]) == 1, "大小写未折叠"


def t_anchor_dot_prefix():
    """./ 前缀不产生新锚点。"""
    prev = {"目标": "t", "待办": [], "已完成": [], "红线": [], "决定": [],
            "锚点": ["路径 src/a.py"]}
    changes = {"base_hash": MOD.state_hash(prev), "锚点": {"新增": ["路径 ./src/a.py"]}}
    return len(MOD.apply_changes(prev, changes)["锚点"]) == 1, "./ 前缀未剥除"


def t_anchor_double_slash():
    """重复斜杠不产生新锚点。"""
    prev = {"目标": "t", "待办": [], "已完成": [], "红线": [], "决定": [],
            "锚点": ["路径 src/a.py"]}
    changes = {"base_hash": MOD.state_hash(prev), "锚点": {"新增": ["路径 src//a.py"]}}
    return len(MOD.apply_changes(prev, changes)["锚点"]) == 1, "重复斜杠未合并"


CASES = [
    t_anchor_variants_collapse, t_anchor_keep_first_form, t_anchor_distinct_kept, t_anchor_casefold, t_anchor_dot_prefix, t_anchor_double_slash,
    t_hash_len, t_hash_stable, t_hash_changes,
    t_check_ok, t_check_unknown_key, t_check_base_hash_bad, t_check_base_hash_ok,
    t_check_format, t_check_range, t_check_dup, t_check_conflict, t_check_expect,
    t_check_empty_new, t_check_decision_bad,
    t_apply_ok, t_apply_no_mutate, t_apply_double_raises, t_apply_range_raises,
    t_apply_expect_raises, t_apply_hash_raises, t_apply_format_raises,
    t_apply_anchor_dedupe, t_apply_todo_order,
    t_val_ok, t_val_cross_list, t_val_ref_anchor, t_val_field_loss, t_val_type, t_val_dup,
]


def main() -> int:
    global MOD
    if len(sys.argv) < 2:
        print("用法: test_l4_v3.py <候选模块.py>")
        return 2
    spec = importlib.util.spec_from_file_location("candidate_l4_v3", sys.argv[1])
    MOD = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(MOD)

    passed = failed = 0
    for case in CASES:
        try:
            ok, msg = case()
        except Exception as exc:
            ok, msg = False, f"{type(exc).__name__}: {exc}"
        name = (case.__doc__ or case.__name__).strip().splitlines()[0]
        print(f"[{'PASS' if ok else 'FAIL'}] {case.__name__:<26} {name}")
        if not ok:
            print(f"       -> {msg}")
        passed += ok
        failed += not ok
    print(f"\n合计 {passed} 过 / {failed} 没过（共 {len(CASES)}）")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
