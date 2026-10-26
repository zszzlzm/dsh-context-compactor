"""记分牌：num.py 的安全求值、占比、聚合（禁止心算的落地工具）。

为什么要有它：规则写进 AGENTS 只是"要求"，工具能不能真的替代心算要靠断言证明。
这里固定 22 类用例：正常算术、精度、拒绝危险语法、JSON 输出、退出码。

用法：
    .\\.venv\\Scripts\\python.exe src\_试作\\test_num.py
"""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))

import num  # noqa: E402


def run(argv: list[str]) -> tuple[int, str, str]:
    old = sys.argv[:]
    sys.argv = ["num.py"] + argv
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = num.main()
            except SystemExit as exc:  # argparse 的用法错误
                code = int(exc.code or 0)
    finally:
        sys.argv = old
    return code, out.getvalue().strip(), err.getvalue().strip()


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def case(name: str, ok: bool, msg: str = "") -> None:
        results.append((name, ok, msg))

    # ---------- 表达式求值 ----------
    code, out, _ = run(["eval", "2+3*4"])
    case("优先级 2+3*4=14", code == 0 and out == "14", f"{code} {out}")
    case("幂 2**10=1024", run(["eval", "2**10"])[1] == "1024")
    case("整除 7//2=3", run(["eval", "7//2"])[1] == "3")
    case("取模 7%3=1", run(["eval", "7%3"])[1] == "1")
    case("一元负号 -3", run(["eval", "-3"])[1] == "-3")
    case("sqrt(2) 四位小数", run(["eval", "sqrt(2)", "--decimals", "4"])[1] == "1.4142")
    case("pi 两位小数", run(["eval", "pi", "--decimals", "2"])[1] == "3.14")

    # ---------- 拒绝危险/不支持的语法 ----------
    rejects = {
        "属性访问": "().__class__",
        "导入": "__import__('os')",
        "未知名字": "x",
        "lambda": "lambda: 1",
        "列表字面量": "[1, 2]",
        "花括号": "{1: 2}",
    }
    for label, expr in rejects.items():
        code, _, err = run(["eval", expr])
        case(f"拒绝{label}", code == 2 and "num.py 错误" in err, f"{code} {err}")
    code, _, err = run(["eval", "2**5000"])
    case("拒绝过大指数", code == 2 and "指数过大" in err, f"{code} {err}")
    code, _, err = run(["eval", "10/0"])
    case("除以零报错", code == 2 and "除以零" in err, f"{code} {err}")

    # ---------- JSON 输出 ----------
    code, out, _ = run(["eval", "2+3", "--json"])
    payload = json.loads(out) if code == 0 else {}
    case("eval --json 可机读", payload.get("ok") is True and payload.get("value") == 5, out)

    # ---------- 占比 ----------
    case("pct 294/5863=5.0%", run(["pct", "294", "5863"])[1] == "5.0%")
    case("pct 1/3=33.3%", run(["pct", "1", "3"])[1] == "33.3%")
    case("pct 半进位 1/16=6.3%（ROUND_HALF_UP）", run(["pct", "1", "16"])[1] == "6.3%")
    code, out, _ = run(["pct", "2", "3", "--json"])
    case("pct --json", code == 0 and json.loads(out)["value"] == 66.7, out)
    code, _, err = run(["pct", "1", "0"])
    case("pct 分母为零报错", code == 2 and "分母为零" in err, err)

    # ---------- 聚合 ----------
    code, out, _ = run(["agg", "3", "4", "4", "4", "3"])
    case("agg 五数", out == "count=5 sum=18 min=3 max=4 mean=3.6", out)
    code, out, _ = run(["agg", "3", "4", "4", "4", "3", "--json"])
    payload = json.loads(out) if code == 0 else {}
    case("agg --json", payload.get("count") == 5 and payload.get("sum") == 18, out)
    code, _, err = run(["agg"])
    case("agg 空输入报错", code == 2 and "聚合需要" in err, err)

    with tempfile.TemporaryDirectory() as raw:
        path = pathlib.Path(raw) / "runs.json"
        path.write_text(json.dumps({"runs": [{"seconds": 33.6}, {"seconds": 18.5}]}),
                        encoding="utf-8")
        code, out, _ = run(["agg", "--file", str(path), "--path", "runs[*].seconds", "--json"])
        payload = json.loads(out) if code == 0 else {}
        case("agg JSON 路径取数", payload.get("count") == 2 and payload.get("sum") == 52.1, out)
        code, _, err = run(["agg", "--file", str(path), "--path", "nope[*].x"])
        case("agg 坏路径报错", code == 2 and "找不到" in err, err)

    # ---------- 确定性 ----------
    first = run(["eval", "sqrt(2)", "--decimals", "6"])[1]
    second = run(["eval", "sqrt(2)", "--decimals", "6"])[1]
    case("同输入两次输出一致", first == second, f"{first} != {second}")

    # ---------- 单位换算（pint） ----------
    case("mW→W 2.5", run(["unit", "2500 mW", "--to", "W"])[1] == "2.5 W")
    code, out, err = run(["unit", "1 GB", "--to", "GiB"])
    case("GB→GiB 0.931323", code == 0 and out.startswith("0.931323"), f"{code} {out} {err}")
    case("h→s 3600", run(["unit", "1 h", "--to", "s"])[1] == "3600 s")
    code, _, err = run(["unit", "1 m", "--to", "kg"])
    case("量纲不符报错", code == 2 and "num.py 错误" in err, f"{code} {err}")

    # ---------- 日期时长（dateutil） ----------
    code, out, _ = run(["date", "--from", "2026-10-25T19:00:00", "--to", "2026-10-25T20:05:26"])
    case("时长 3926 s", code == 0 and out.startswith("3926 s"), f"{code} {out}")
    code, out, _ = run(["date", "--from", "2026-10-25T19:00:00",
                        "--to", "2026-10-25T20:05:26", "--json"])
    case("date --json seconds", code == 0 and json.loads(out)["seconds"] == 3926, out)

    # ---------- 留痕（--save） ----------
    with tempfile.TemporaryDirectory() as raw:
        log = pathlib.Path(raw) / "num_calls.jsonl"
        old_log = num.NUM_LOG_PATH
        num.NUM_LOG_PATH = log
        try:
            code, _, _ = run(["eval", "1+1", "--save"])
        finally:
            num.NUM_LOG_PATH = old_log
        lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        record = json.loads(lines[0]) if lines else {}
        case("--save 生成留痕", code == 0 and len(lines) == 1
             and record.get("result", {}).get("value") == 2, f"lines={len(lines)}")

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    for name, ok, msg in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {msg}" if not ok and msg else ""))
    print(f"\n合计 {passed} 过 / {failed} 没过（共 {len(results)}）")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
