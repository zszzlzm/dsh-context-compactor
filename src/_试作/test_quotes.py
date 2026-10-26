"""引文回贴验收：归一化、最长匹配、确定性回贴、批量校验。

用法：
    .\\.venv\\Scripts\\python.exe src\_试作\\test_quotes.py src\_试作\\quotes_v2_27b.py
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fixture_quotes import (QUOTE_CURLY, QUOTE_FAKE, QUOTE_PREFIXED,  # noqa: E402
                            QUOTE_SPACING, QUOTE_TOO_SHORT, QUOTE_VERBATIM, SOURCE)

MOD = None


def rep(quote, **kw):
    return MOD.repair_quote(quote, SOURCE, **kw)


def is_substring_of_source(text: str) -> bool:
    return bool(text) and MOD.normalize(text) in MOD.normalize(SOURCE)


# ---------------------------------------------------------------- 归一化

def t_norm_fullwidth():
    """全角数字按 NFKC 归一。"""
    return MOD.normalize("１２３") == "123", f"实际={MOD.normalize('１２３')}"


def t_norm_quotes():
    """弯引号统一成直引号。"""
    out = MOD.normalize("他说“读不到”")
    return out == '他说"读不到"', f"实际={out}"


def t_norm_whitespace():
    """空白全部去掉。"""
    return MOD.normalize(" a \n b\tc ") == "abc", f"实际={MOD.normalize(' a \\n b\\tc ')}"


# ---------------------------------------------------------------- 最长匹配

def t_match_exact():
    """完全包含时匹配长度等于引文长度。"""
    size, _, _ = MOD.longest_match("结构是这五层里最扎实的", SOURCE)
    return size == MOD.normalize("结构是这五层里最扎实的").__len__(), f"size={size}"


def t_match_none():
    """毫无公共字符时返回全 0。"""
    return MOD.longest_match("zzzzzzzz", "abc") == (0, 0, 0), str(MOD.longest_match("zzzzzzzz", "abc"))


def t_match_prefixed_size():
    """真实失败案例的匹配长度应当远大于 min_run。"""
    size, _, _ = MOD.longest_match(QUOTE_PREFIXED, SOURCE)
    return size >= 15, f"size={size}"


# ---------------------------------------------------------------- 回贴

def t_verbatim():
    """逐字引文原样返回。"""
    r = rep(QUOTE_VERBATIM)
    return r["ok"] and r["status"] == "verbatim" and r["text"] == QUOTE_VERBATIM, str(r)


def t_spacing_fixed_by_norm():
    """仅空格差异的引文应判为逐字（归一化即可）。"""
    r = rep(QUOTE_SPACING)
    return r["ok"] and r["status"] == "verbatim", str(r)


def t_curly_ok():
    """弯引号引文应判为逐字。"""
    r = rep(QUOTE_CURLY)
    return r["ok"] and r["status"] == "verbatim", str(r)


def t_prefixed_repaired():
    """带评价前缀的引文应被回贴成原文片段。"""
    r = rep(QUOTE_PREFIXED)
    return r["ok"] and r["status"] == "repaired", str(r)


def t_prefixed_is_substring():
    """回贴结果必须是原文逐字子串。"""
    r = rep(QUOTE_PREFIXED)
    return is_substring_of_source(r["text"]), f"text={r['text']!r}"


def t_prefixed_keeps_core():
    """回贴结果保留核心内容。"""
    r = rep(QUOTE_PREFIXED)
    return "结构是这五层里最扎实的" in r["text"], f"text={r['text']!r}"


def t_fake_failed():
    """编造引文判失败且原样返回。"""
    r = rep(QUOTE_FAKE)
    return (not r["ok"]) and r["status"] == "failed" and r["text"] == QUOTE_FAKE, str(r)


def t_too_short_failed():
    """命中不足 min_run 判失败。"""
    r = rep(QUOTE_TOO_SHORT)
    return not r["ok"] and r["status"] == "failed", str(r)


def t_min_run_override():
    """提高 min_run 后，原本可修的引文也会失败。"""
    r = rep(QUOTE_PREFIXED, min_run=40)
    return not r["ok"], str(r)


def t_repaired_stripped():
    """回贴结果两端无空白。"""
    r = rep(QUOTE_PREFIXED)
    return r["text"] == r["text"].strip(), repr(r["text"][:20])


def t_repair_window_zero():
    """window=0 时只取匹配片段本身，仍应是逐字子串。"""
    r = rep(QUOTE_PREFIXED, window=0)
    return r["ok"] and is_substring_of_source(r["text"]), str(r)


# ---------------------------------------------------------------- 批量

def t_verify_all():
    """批量校验：逐字/修复/失败三种状态都要出现。"""
    rows = MOD.verify_quotes([QUOTE_VERBATIM, QUOTE_PREFIXED, QUOTE_FAKE], SOURCE)
    statuses = [r["status"] for r in rows]
    return statuses == ["verbatim", "repaired", "failed"], f"实际={statuses}"


def t_verify_length():
    """输出条数与输入一致。"""
    quotes = [QUOTE_VERBATIM, "", QUOTE_FAKE]
    return len(MOD.verify_quotes(quotes, SOURCE)) == 3, "条数不一致"


def t_verify_empty_quote():
    """空引文占位且判失败。"""
    rows = MOD.verify_quotes([""], SOURCE)
    return len(rows) == 1 and rows[0]["status"] == "failed" and rows[0]["text"] == "", str(rows)


def t_verify_keeps_original():
    """每条都带上原始引文，便于回溯。"""
    rows = MOD.verify_quotes([QUOTE_PREFIXED], SOURCE)
    return rows[0]["quote"] == QUOTE_PREFIXED, str(rows[0])


def t_verify_ok_flag():
    """ok 字段与 status 一致。"""
    for quote in (QUOTE_VERBATIM, QUOTE_PREFIXED, QUOTE_FAKE):
        row = MOD.verify_quotes([quote], SOURCE)[0]
        if row["ok"] != (row["status"] != "failed"):
            return False, f"不一致: {row}"
    return True, ""


CASES = [
    t_norm_fullwidth, t_norm_quotes, t_norm_whitespace,
    t_match_exact, t_match_none, t_match_prefixed_size,
    t_verbatim, t_spacing_fixed_by_norm, t_curly_ok, t_prefixed_repaired,
    t_prefixed_is_substring, t_prefixed_keeps_core, t_fake_failed, t_too_short_failed,
    t_min_run_override, t_repaired_stripped, t_repair_window_zero,
    t_verify_all, t_verify_length, t_verify_empty_quote, t_verify_keeps_original,
    t_verify_ok_flag,
]


def main() -> int:
    global MOD
    if len(sys.argv) < 2:
        print("用法: test_quotes.py <候选模块.py>")
        return 2
    spec = importlib.util.spec_from_file_location("candidate_quotes", sys.argv[1])
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
