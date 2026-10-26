"""P-056 修复 A 验收：merge_budget_27b.py（27B 产物）。"""
from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import merge_budget as mb  # noqa: E402


def test_estimate_empty():
    return mb.estimate_tokens("") == 0, f"实际={mb.estimate_tokens('')}"


def test_estimate_ratio():
    got = mb.estimate_tokens("a" * 23)
    return got == 10, f"实际={got}"


def test_new_anchors_order_and_dedupe():
    anchors = ["路径 a.py", "路径 b.py", "路径 a.py", "", None, 123, "  路径 c.py  "]
    existing = ["路径 b.py"]
    got = mb.new_anchor_lines(anchors, existing)
    return got == ["路径 a.py", "路径 c.py"], f"实际={got}"


def test_new_anchors_limit_and_no_mutation():
    anchors = ["一", "二", "三", "四"]
    before = list(anchors)
    got = mb.new_anchor_lines(anchors, [], limit=2)
    return got == ["一", "二"] and anchors == before, f"实际={got}"


def test_needs_lean_boundary():
    # 7000 + 512 + 512 = 8024 <= 8192
    low = mb.needs_lean(7000, num_ctx=8192, num_predict=1600, reserve=512)
    # 7200 + 512 + 512 = 8224 > 8192
    high = mb.needs_lean(7200, num_ctx=8192, num_predict=1600, reserve=512)
    exact = mb.needs_lean(7168, num_ctx=8192, num_predict=1600, reserve=512)
    return (low is False and high is True and exact is False,
            f"{low}/{high}/{exact}")


def test_non_string_existing():
    got = mb.new_anchor_lines(["x", "y"], [None, "", " y "])
    return got == ["x"], f"实际={got}"


CASES = [test_estimate_empty, test_estimate_ratio, test_new_anchors_order_and_dedupe,
         test_new_anchors_limit_and_no_mutation, test_needs_lean_boundary, test_non_string_existing]


def main() -> int:
    failed = 0
    for case in CASES:
        try:
            ok, msg = case()
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, f"{type(exc).__name__}: {exc}"
        print(f"[{'PASS' if ok else 'FAIL'}] {case.__name__}" + ("" if ok else f"  -> {msg}"))
        failed += 0 if ok else 1
    print(f"\n合计 {len(CASES) - failed} 过 / {failed} 没过（共 {len(CASES)}）")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
