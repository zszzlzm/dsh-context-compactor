"""P-052 验收：thin_check_27b.py（27B 产物）的空壳判定。"""
from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import thin_check as tc  # noqa: E402

SOURCE = "原文第一句。原文第二句。"


def test_thin_short_summary():
    thin, reason = tc.summary_is_thin({"summary": "太短", "facts": ["原文第一句"]}, SOURCE)
    return thin and isinstance(reason, str) and "summary" in reason, f"{thin}/{reason}"


def test_thin_no_facts():
    summary = "这是一段足够长的摘要，用来确认 facts 为空时仍然会被判定为薄。" * 2
    thin, reason = tc.summary_is_thin({"summary": summary, "facts": []}, SOURCE)
    return thin and "facts" in reason, f"{thin}/{reason}"


def test_thin_missing_summary_key():
    thin, reason = tc.summary_is_thin({"facts": ["原文第一句"]}, SOURCE)
    return thin and "summary" in reason, f"{thin}/{reason}"


def test_thin_none_summary():
    thin, reason = tc.summary_is_thin({"summary": None, "facts": ["原文第一句"]}, SOURCE)
    return thin and "summary" in reason, f"{thin}/{reason}"


def test_thin_blank_facts():
    summary = "这是一段足够长的摘要，用来确认空白 facts 会被当作空处理。" * 2
    thin, reason = tc.summary_is_thin({"summary": summary,
                                       "facts": [None, "", "   ", 123]}, SOURCE)
    return thin and "facts" in reason, f"{thin}/{reason}"


def test_good_summary():
    summary = "这是一段足够长且具体的摘要，包含原文里的关键信息与结论，长度超过三十个字。"
    thin, reason = tc.summary_is_thin({"summary": summary,
                                       "facts": ["原文第一句", "原文第二句"]}, SOURCE)
    return (not thin and reason == "ok"), f"{thin}/{reason}"


CASES = [test_thin_short_summary, test_thin_no_facts, test_thin_missing_summary_key,
         test_thin_none_summary, test_thin_blank_facts, test_good_summary]


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
