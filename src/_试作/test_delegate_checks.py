"""delegate_checks 的单元测试（不调模型，秒级）。

用法：
    .\\.venv\\Scripts\\python.exe src\_试作\\test_delegate_checks.py
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import delegate_checks as dc  # noqa: E402

GOOD = """# 任务：实现 clamp

## 约束清单
1. lo > hi 时返回 lo
2. 输入 bool 必须抛 TypeError

## 验收
- python test_clamp.py
"""

SCATTERED = """# 任务：实现 clamp

lo > hi 时返回 lo（必须做到）。

中间说明文字。

输入 bool 必须抛 TypeError。
"""


def test_extract_constraints():
    got = dc.extract_constraints(GOOD)
    return got == ["lo > hi 时返回 lo", "输入 bool 必须抛 TypeError"], f"实际={got}"


def test_lint_good():
    return dc.lint_spec(GOOD) == [], f"实际={dc.lint_spec(GOOD)}"


def test_lint_scattered():
    problems = dc.lint_spec(SCATTERED)
    return (any("约束清单" in p for p in problems)
            and any("散落" in p for p in problems)), f"实际={problems}"


def test_feedback_contains_failures():
    text = dc.build_feedback(["约束 1 未满足"], "print('x')")
    return "约束 1 未满足" in text and "print('x')" in text, text[:120]


def test_run_checks_pass_fail():
    with tempfile.TemporaryDirectory() as raw:
        script = pathlib.Path(raw) / "ok.py"
        script.write_text("print('ok')\n", encoding="utf-8")
        ok, failures, _ = dc.run_checks([f'"{sys.executable}" "{script}"'])
        bad, bad_failures, _ = dc.run_checks([f'"{sys.executable}" -c "raise SystemExit(3)"'])
    return ok and not failures and not bad and bool(bad_failures), f"{ok}/{failures}/{bad_failures}"


CASES = [test_extract_constraints, test_lint_good, test_lint_scattered,
         test_feedback_contains_failures, test_run_checks_pass_fail]


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
