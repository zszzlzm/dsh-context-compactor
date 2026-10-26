"""gc_v2 验收：每字段预算、按引用保护、滞回裁剪、溯源记录、守恒校验、检索。

用法：
    .\\.venv\\Scripts\\python.exe src\_试作\\test_gc_v2.py src\_试作\\gc_v2_27b.py
"""
from __future__ import annotations

import copy
import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fixture_gc_v2 import (BUDGETS, NEW_ARCHIVE, OLD_ANCHOR, OLD_ARCHIVE,  # noqa: E402
                           SMALL, STATE)

MOD = None
KINDS = ("锚点", "决定", "待办", "已完成")


def plan(turn=104, ratio=0.7):
    return MOD.plan_gc_v2(STATE, BUDGETS, turn=turn, ratio=ratio)


def content(item):
    return item.get("内容") if isinstance(item, dict) else item


# ---------------------------------------------------------------- 预算

def t_state_chars():
    """state_chars 返回正数。"""
    n = MOD.state_chars(SMALL)
    return n > 10, f"state_chars={n}"


def t_field_chars_keys():
    """field_chars 恰好四个键。"""
    fc = MOD.field_chars(STATE)
    return set(fc) == set(KINDS), f"键={sorted(fc)}"


def t_field_chars_anchor_largest():
    """锚点字段最大。"""
    fc = MOD.field_chars(STATE)
    return max(fc, key=fc.get) == "锚点", f"各字段={fc}"


def t_over_budget():
    """超预算的类别要精确列出，且按固定顺序。"""
    got = MOD.over_budget(STATE, BUDGETS)
    return got == ["锚点", "决定", "已完成"], f"实际={got}"


def t_over_budget_small():
    """小状态不超预算。"""
    return MOD.over_budget(SMALL, BUDGETS) == [], "小状态被判超预算"


# ---------------------------------------------------------------- 引用保护

def t_referenced_anchors():
    """被待办引用的旧锚点要被识别出来。"""
    got = MOD.referenced_anchors(STATE)
    return got == [OLD_ANCHOR], f"实际={got}"


def t_protected_kept():
    """回收后旧锚点仍在热区。"""
    hot = plan()["state"]
    return OLD_ANCHOR in hot["锚点"], "在用锚点被回收了"


def t_anchor_within_budget():
    """锚点热区不超过预算。"""
    hot = plan()["state"]
    n = MOD.field_chars(hot)["锚点"]
    return n <= BUDGETS["锚点"], f"热区锚点 {n} 字 / 预算 {BUDGETS['锚点']}"


def t_done_within_budget():
    """已完成热区不超过预算。"""
    hot = plan()["state"]
    n = MOD.field_chars(hot)["已完成"]
    return n <= BUDGETS["已完成"], f"热区已完成 {n} 字 / 预算 {BUDGETS['已完成']}"


def t_untrimmed_untouched():
    """没超预算的类别原样保留（待办）。"""
    hot = plan()["state"]
    return hot["待办"] == STATE["待办"], "待办被动了"


def t_order_preserved():
    """热区锚点必须是原列表的子序列（顺序不变）。"""
    hot = plan()["state"]["锚点"]
    wanted = [x for x in STATE["锚点"] if x in set(hot)]
    return hot == wanted, "热区顺序被改动"


# ---------------------------------------------------------------- 归档记录

def t_archive_records():
    """归档记录带轮次和原因。"""
    arc = plan(turn=104)["archive"]
    bad = [r for r in arc["锚点"] if r.get("轮次") != 104 or r.get("原因") != "超预算"]
    return len(arc["锚点"]) > 0 and not bad, f"锚点归档 {len(arc['锚点'])} 条，异常 {bad[:2]}"


def t_archive_decision_reason():
    """决定的归档记录要带上理由。"""
    arc = plan()["archive"]["决定"]
    ok = bool(arc) and all(str(r.get("理由", "")).strip() for r in arc)
    return ok, f"决定归档 {len(arc)} 条"


def t_fields_untouched():
    """目标/红线原样保留，且不进归档。"""
    r = plan()
    ok = (r["state"]["目标"] == STATE["目标"] and r["state"]["红线"] == STATE["红线"]
          and "目标" not in r["archive"] and "红线" not in r["archive"])
    return ok, f"归档键={sorted(r['archive'])}"


def t_no_mutate():
    """不许修改入参。"""
    before = copy.deepcopy(STATE)
    MOD.plan_gc_v2(STATE, BUDGETS, turn=104)
    return STATE == before, "入参被改了"


# ---------------------------------------------------------------- 守恒

def t_verify_ok():
    """合法回收必须过守恒校验。"""
    r = plan()
    problems = MOD.verify_gc_v2(STATE, r["state"], r["archive"])
    return not problems, f"问题: {problems}"


def t_verify_catches_loss():
    """归档里偷偷少一条要被抓住。"""
    r = plan()
    arc = copy.deepcopy(r["archive"])
    arc["已完成"] = arc["已完成"][1:]
    problems = MOD.verify_gc_v2(STATE, r["state"], arc)
    return any("账目不符" in p for p in problems), f"问题: {problems}"


def t_verify_catches_protected():
    """把在用锚点从热区删掉要被抓住。"""
    r = plan()
    hot = copy.deepcopy(r["state"])
    hot["锚点"] = [a for a in hot["锚点"] if a != OLD_ANCHOR]
    problems = MOD.verify_gc_v2(STATE, hot, r["archive"])
    return any("在用锚点被回收" in p for p in problems), f"问题: {problems}"


def t_verify_catches_redline():
    """红线被改要报出来。"""
    r = plan()
    hot = copy.deepcopy(r["state"])
    hot["红线"] = list(reversed(hot["红线"]))
    problems = MOD.verify_gc_v2(STATE, hot, r["archive"])
    return any("红线被改动" in p for p in problems), f"问题: {problems}"


def t_verify_catches_untouched_change():
    """未回收的类别被改动要报出来。"""
    r = plan()
    hot = copy.deepcopy(r["state"])
    hot["待办"] = list(reversed(hot["待办"]))
    problems = MOD.verify_gc_v2(STATE, hot, r["archive"])
    return any("未回收却有改动" in p for p in problems), f"问题: {problems}"


# ---------------------------------------------------------------- 归档合并 / 检索

def t_merge_dedupe():
    """合并按内容去重，保留旧记录的轮次。"""
    merged = MOD.merge_archive_v2(OLD_ARCHIVE, NEW_ARCHIVE)
    rows = [r["内容"] for r in merged["锚点"]]
    return rows == ["a.py", "b.py"], f"锚点={rows}"


def t_merge_keys():
    """键取并集。"""
    merged = MOD.merge_archive_v2({"锚点": []}, NEW_ARCHIVE)
    return set(merged) == {"锚点", "决定", "待办", "已完成"}, f"键={sorted(merged)}"


def t_recall_hits():
    """关键词能命中归档并标出类别。"""
    r = plan()
    hits = MOD.recall(r["archive"], "方案")
    ok = bool(hits) and all(h.get("类别") == "决定" for h in hits)
    return ok, f"命中 {len(hits)} 条"


def t_recall_limit():
    """limit 生效。"""
    r = plan()
    hits = MOD.recall(r["archive"], "方案", limit=3)
    return len(hits) <= 3, f"命中 {len(hits)} 条"


def t_recall_empty_keyword():
    """空关键词返回空列表。"""
    return MOD.recall(plan()["archive"], "") == [], "空关键词不应命中"


# ---------------------------------------------------------------- 锚点归一化合并（P-058）

DUP_STATE = {
    "目标": "去重",
    "待办": ["普通任务"],
    "已完成": [],
    "红线": [],
    "决定": [],
    "锚点": ["路径 src\\a.py", "路径 src/a.py"],
}
BIG_BUDGETS = {"锚点": 100000, "决定": 100000, "待办": 100000, "已完成": 100000}


def t_dedupe_unreferenced():
    """未引用的归一化重复锚点被合并，且归档留痕。"""
    r = MOD.plan_gc_v2(DUP_STATE, BIG_BUDGETS, turn=7)
    return (r["state"]["锚点"] == ["路径 src\\a.py"]
            and len(r["archive"]["锚点"]) == 1
            and "归一化重复" in r["archive"]["锚点"][0]["原因"],
            f"hot={r['state']['锚点']} archive={r['archive']['锚点']}")


def t_dedupe_referenced_kept():
    """被引用的重复锚点保留，避免破坏引用。"""
    state = dict(DUP_STATE)
    state["待办"] = ["复核 路径 src/a.py 的默认值"]
    r = MOD.plan_gc_v2(state, BIG_BUDGETS, turn=7)
    return len(r["state"]["锚点"]) == 2, f"hot={r['state']['锚点']}"


def t_dedupe_conserved():
    """归一化合并后守恒校验仍通过。"""
    r = MOD.plan_gc_v2(DUP_STATE, BIG_BUDGETS, turn=7)
    problems = MOD.verify_gc_v2(DUP_STATE, r["state"], r["archive"])
    return not problems, f"问题: {problems}"


def t_validate_anchor_warning():
    """validate_state 对锚点归一化重复给软警告、不硬拦。"""
    import l4_v3_27b
    warnings: list[str] = []
    problems = l4_v3_27b.validate_state(DUP_STATE, DUP_STATE, warnings)
    return (not problems and any("归一化重复" in w for w in warnings),
            f"problems={problems} warnings={warnings}")


CASES = [
    t_state_chars, t_field_chars_keys, t_field_chars_anchor_largest, t_over_budget,
    t_over_budget_small, t_referenced_anchors, t_protected_kept, t_anchor_within_budget,
    t_done_within_budget, t_untrimmed_untouched, t_order_preserved, t_archive_records,
    t_archive_decision_reason, t_fields_untouched, t_no_mutate, t_verify_ok,
    t_verify_catches_loss, t_verify_catches_protected, t_verify_catches_redline,
    t_verify_catches_untouched_change, t_merge_dedupe, t_merge_keys, t_recall_hits,
    t_recall_limit, t_recall_empty_keyword,
    t_dedupe_unreferenced, t_dedupe_referenced_kept, t_dedupe_conserved,
    t_validate_anchor_warning,
]


def main() -> int:
    global MOD
    if len(sys.argv) < 2:
        print("用法: test_gc_v2.py <候选模块.py>")
        return 2
    spec = importlib.util.spec_from_file_location("candidate_gc_v2", sys.argv[1])
    MOD = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(MOD)

    passed = failed = 0
    for case in CASES:
        try:
            ok, msg = case()
        except Exception as exc:
            ok, msg = False, f"{type(exc).__name__}: {exc}"
        name = (case.__doc__ or case.__name__).strip().splitlines()[0]
        print(f"[{'PASS' if ok else 'FAIL'}] {case.__name__:<30} {name}")
        if not ok:
            print(f"       -> {msg}")
        passed += ok
        failed += not ok
    print(f"\n合计 {passed} 过 / {failed} 没过（共 {len(CASES)}）")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
