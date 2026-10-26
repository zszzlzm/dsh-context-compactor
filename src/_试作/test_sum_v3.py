"""L2 v3 确定性配件验收：配额、落地检查、切块降级、溢出守卫、跨块去重。

用法：
    .\\.venv\\Scripts\\python.exe src\_试作\\test_sum_v3.py src\_试作\\sum_v3_27b.py
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fixture_sum_v3 import BAD_OBJ, CHUNKS, GOOD_OBJ, ITEMS, SOURCE  # noqa: E402

MOD = None


def check(obj, **kw):
    return {row["id"]: row for row in MOD.check_summary_v3(obj, SOURCE, **kw)}


# ---------------------------------------------------------------- 配额 / 守卫

def t_facts_cap_range():
    """按块字数缩放：1000→3, 3600→3, 4800→4, 6000→5, 12000→6。"""
    got = [MOD.facts_cap(n) for n in (1000, 3600, 4800, 6000, 12000)]
    return got == [3, 3, 4, 5, 6], f"实际={got}"


def t_facts_cap_min():
    """极小块也不低于 3。"""
    return MOD.facts_cap(0) == 3, f"实际={MOD.facts_cap(0)}"


# ---------------------------------------------------------------- 拆分块地板（P-016）

def t_split_cap_2500():
    """2500 字拆分块：facts 至少 4 条。"""
    got = MOD.facts_cap(2500, split=True)
    return got >= 4, f"实际={got}"


def t_split_cap_3000():
    """3000 字拆分块：facts 至少 4 条。"""
    got = MOD.facts_cap(3000, split=True)
    return got >= 4, f"实际={got}"


def t_split_cap_3500():
    """3500 字拆分块：facts 至少 4 条。"""
    got = MOD.facts_cap(3500, split=True)
    return got >= 4, f"实际={got}"


def t_split_cap_3900():
    """3900 字拆分块：facts 恰好 4 条（3900 // 900 = 4）。"""
    got = MOD.facts_cap(3900, split=True)
    return got == 4, f"实际={got}"


def t_split_cap_floor():
    """极短的拆分块地板是 4，不是 3。"""
    got = MOD.facts_cap(1500, split=True)
    return got == 4, f"实际={got}"


def t_split_cap_ceiling():
    """超长拆分块封顶 6。"""
    got = MOD.facts_cap(9000, split=True)
    return got == 6, f"实际={got}"


def t_nonsplit_unchanged():
    """非拆分块口径不许被顺手改掉：3900→3，6000→5。"""
    got = [MOD.facts_cap(3900), MOD.facts_cap(6000)]
    return got == [3, 5], f"实际={got}"


def t_split_cap_2986():
    """提案一指定点：2986 字拆分块恰为 4。"""
    got = MOD.facts_cap(2986, split=True)
    return got == 4, f"实际={got}"


def t_split_cap_3600():
    """提案一指定点：3600 字拆分块恰为 4。"""
    got = MOD.facts_cap(3600, split=True)
    return got == 4, f"实际={got}"


def t_plain_cap_2986():
    """提案一指定点：2986 字普通块仍为 3。"""
    got = MOD.facts_cap(2986)
    return got == 3, f"实际={got}"


def t_boundary_plain():
    """提案三：2499/2500/3599/3600/3899/3900 六个边界，普通块一律 3。"""
    got = [MOD.facts_cap(n) for n in (2499, 2500, 3599, 3600, 3899, 3900)]
    return got == [3, 3, 3, 3, 3, 3], f"实际={got}"


def t_boundary_split():
    """提案三：同一组边界，拆分块一律 4（3900 // 900 = 4 仍在地板区）。"""
    got = [MOD.facts_cap(n, split=True) for n in (2499, 2500, 3599, 3600, 3899, 3900)]
    return got == [4, 4, 4, 4, 4, 4], f"实际={got}"


def t_context_guard_ok():
    """未到 75% 不报警。"""
    return MOD.context_guard(5000, 8192) is False, "误报"


def t_context_guard_hit():
    """超过 75% 要报警。"""
    return MOD.context_guard(7000, 8192) is True, "漏报"


def t_context_guard_ratio():
    """ratio 可覆盖。"""
    return MOD.context_guard(7000, 8192, ratio=0.9) is False, "ratio 没生效"


# ---------------------------------------------------------------- 切块降级

def t_split_into_two():
    """四单元一块切成两块。"""
    parts = MOD.split_chunk(CHUNKS[1])
    return len(parts) == 2 and all(parts), f"实际={parts}"


def t_split_single_unit():
    """单单元无法再切，原样返回。"""
    parts = MOD.split_chunk(CHUNKS[2])
    return parts == [CHUNKS[2]], f"实际={parts}"


def t_split_preserves_content():
    """拼接回去必须等于原块。"""
    for chunk in CHUNKS:
        parts = MOD.split_chunk(chunk)
        flat = [u for part in parts for u in part]
        if flat != chunk:
            return False, f"还原不一致: {chunk} -> {parts}"
    return True, ""


def t_split_balanced():
    """两半都不为空。"""
    parts = MOD.split_chunk(CHUNKS[0])
    return len(parts) == 2 and len(parts[0]) >= 1 and len(parts[1]) >= 1, f"实际={parts}"


def t_split_max_parts_one():
    """max_parts=1 时不切。"""
    parts = MOD.split_chunk(CHUNKS[1], max_parts=1)
    return parts == [CHUNKS[1]], f"实际={parts}"


# ---------------------------------------------------------------- 事实落地

def t_grounded_positive():
    """源文里有的片段算落地。"""
    return MOD.fact_grounded("full 档 149.4 W", SOURCE) is True, "误判为未落地"


def t_grounded_negative():
    """源文里没有的算未落地。"""
    return MOD.fact_grounded("功耗大约 200 W", SOURCE) is False, "误判为落地"


def t_grounded_short():
    """比 min_run 还短的不算落地。"""
    return MOD.fact_grounded("full", SOURCE, min_run=6) is False, "短事实被放行"


def t_grounded_whitespace():
    """空白差异不影响落地判断。"""
    return MOD.fact_grounded("full档149.4W", SOURCE) is True, "空白敏感"


# ---------------------------------------------------------------- 检查清单

def t_check_ids_order():
    """八条检查，id 顺序固定。"""
    ids = [r["id"] for r in MOD.check_summary_v3(GOOD_OBJ, SOURCE)]
    want = ["summary_nonempty", "summary_length", "summary_independent",
            "summary_no_guess", "quotes_verbatim", "facts_grounded", "facts_count",
            "summary_thin"]
    return ids == want, f"实际={ids}"


def t_check_good_all_pass():
    """干净摘要全过。"""
    rows = check(GOOD_OBJ)
    bad = [k for k, v in rows.items() if not v["ok"]]
    return not bad, f"未过={bad}"


def t_check_independent_fail():
    """前指代要被抓。"""
    return check(BAD_OBJ)["summary_independent"]["ok"] is False, "没抓到前指代"


def t_check_guess_fail():
    """推断词要被抓。"""
    return check(BAD_OBJ)["summary_no_guess"]["ok"] is False, "没抓到推断词"


def t_check_quote_legacy():
    """只有单条 quote 字段也要能用。"""
    obj = {"summary": "s", "quote": "把默认档改成 full", "facts": ["full 档 149.4 W"]}
    return check(obj)["quotes_verbatim"]["ok"] is True, "单引文兼容失败"


def t_check_quotes_empty():
    """没有引文判失败。"""
    obj = {"summary": "s", "facts": ["full 档 149.4 W"]}
    return check(obj)["quotes_verbatim"]["ok"] is False, "空引文被放行"


def t_check_facts_empty():
    """没有事实判失败。"""
    obj = {"summary": "s", "quotes": ["把默认档改成 full"], "facts": []}
    rows = check(obj)
    return rows["facts_grounded"]["ok"] is False and rows["facts_grounded"]["detail"] == "没有事实", \
        f"实际={rows['facts_grounded']}"


def t_check_grounded_fail():
    """伪造事实要被抓。"""
    rows = check(BAD_OBJ)
    return rows["facts_grounded"]["ok"] is False and "未落地" in rows["facts_grounded"]["detail"], \
        f"实际={rows['facts_grounded']}"


def t_check_facts_count_soft():
    """事实超上限只记 soft。"""
    obj = dict(GOOD_OBJ)
    obj["facts"] = ["full 档 149.4 W"] * 8
    rows = check(obj)
    return (rows["facts_count"]["ok"] is False and rows["facts_count"]["level"] == "soft"), \
        f"实际={rows['facts_count']}"


def t_check_summary_300():
    """摘要上限 300：250 过、350 不过。"""
    short = dict(GOOD_OBJ, summary="字" * 250)
    long = dict(GOOD_OBJ, summary="字" * 350)
    return (check(short)["summary_length"]["ok"] is True
            and check(long)["summary_length"]["ok"] is False), "长度上限不对"


# ---------------------------------------------------------------- 跨块去重

def t_dedupe_order():
    """去重保序。"""
    got = MOD.dedupe_facts(ITEMS)
    want = ["full 档 149.4 W", "12.71 tok/s", "11.76 J/token"]
    return got == want, f"实际={got}"


def t_dedupe_minlen():
    """过短的丢弃。"""
    got = MOD.dedupe_facts(ITEMS)
    return not any(len(x) < 4 for x in got), f"实际={got}"


def t_allocate_facts_blocks():
    """全局事实按块分配：9 块 27 条时每块至少 1 条且总数 ≤15（P-042）。"""
    items = [{"facts": [f"块{b}事实{j}" for j in range(3)]} for b in range(9)]
    got = MOD.allocate_facts(items, cap=15)
    blocks = [b for b in range(9) if any(v.startswith(f"块{b}") for v in got)]
    dup = len(got) - len({v for v in got})
    return len(got) == 15 and len(blocks) == 9 and dup == 0, \
        f"n={len(got)} blocks={blocks} dup={dup}"


def t_summary_thin_soft():
    """空壳摘要触发 summary_thin 软检查（P-052）。"""
    checks = MOD.check_summary_v3({"summary": "太短", "quotes": [], "facts": []}, "原文。")
    thin = [c for c in checks if c["id"] == "summary_thin"]
    return (bool(thin) and thin[0]["ok"] is False and thin[0].get("soft_fail") is True,
            f"实际={thin}")


def t_summary_not_thin():
    """正常摘要不触发 summary_thin。"""
    obj = {"summary": "这是一段足够长的摘要，包含原文里的关键信息与结论，长度超过三十个字。",
           "quotes": ["原文。"], "facts": ["原文。"]}
    checks = MOD.check_summary_v3(obj, "原文。")
    thin = [c for c in checks if c["id"] == "summary_thin"]
    return bool(thin) and thin[0]["ok"] is True and not thin[0].get("soft_fail"), f"实际={thin}"


CASES = [
    t_allocate_facts_blocks,
    t_summary_thin_soft, t_summary_not_thin,
    t_facts_cap_range, t_facts_cap_min,
    t_split_cap_2500, t_split_cap_3000, t_split_cap_3500, t_split_cap_3900,
    t_split_cap_floor, t_split_cap_ceiling, t_nonsplit_unchanged,
    t_split_cap_2986, t_split_cap_3600, t_plain_cap_2986,
    t_boundary_plain, t_boundary_split,
    t_context_guard_ok, t_context_guard_hit,
    t_context_guard_ratio,
    t_split_into_two, t_split_single_unit, t_split_preserves_content, t_split_balanced,
    t_split_max_parts_one,
    t_grounded_positive, t_grounded_negative, t_grounded_short, t_grounded_whitespace,
    t_check_ids_order, t_check_good_all_pass, t_check_independent_fail, t_check_guess_fail,
    t_check_quote_legacy, t_check_quotes_empty, t_check_facts_empty, t_check_grounded_fail,
    t_check_facts_count_soft, t_check_summary_300,
    t_dedupe_order, t_dedupe_minlen,
]


def main() -> int:
    global MOD
    if len(sys.argv) < 2:
        print("用法: test_sum_v3.py <候选模块.py>")
        return 2
    spec = importlib.util.spec_from_file_location("candidate_sum_v3", sys.argv[1])
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
