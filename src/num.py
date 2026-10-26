"""num.py —— 数字计算与生成（禁止心算的落地工具，薄 CLI）。

选型（P-044 / 审查报告 2026-10-25_数字工具_num_py.md）：
- 表达式求值：simpleeval（MIT、单文件、无依赖；保留 4096 指数上限）
- 单位换算：pint（BSD、零依赖；mW→W、GB→GiB 等）
- 日期/时长：python-dateutil（已装；解析任意常见日期格式）
- 占比/聚合仍用本文件的 Decimal + ROUND_HALF_UP（半进位口径由测试锁死）

三条硬规则
----------
1. 表达式走 simpleeval 沙箱，不 eval 原文：属性访问/导入/lambda/列表/字典/未知名字全拒。
2. 百分比用 Decimal + ROUND_HALF_UP，默认一位小数。
3. 输出分两层：给人看的一行 + `--json` 机读；退出码 0=成功 / 2=输入或计算错误；
   `--save` 把本次参数与结果追加到 06_日志/num_calls.jsonl（可用 NUM_LOG_PATH 覆盖）。

用法
----
    python src/num.py eval "2*(3+4)"                 -> 14
    python src/num.py eval "sqrt(2)" --decimals 4     -> 1.4142
    python src/num.py pct 294 5863                   -> 5.0%
    python src/num.py agg 3 4 4 4 3                  -> count=5 sum=18 min=3 max=4 mean=3.6
    python src/num.py agg --file x.json --path "runs[*].seconds"
    python src/num.py unit "2500 mW" --to W           -> 2.5 W
    python src/num.py unit "1 GB" --to GiB            -> 0.9313225746154785 GiB
    python src/num.py date --from 2026-10-25T19:00:00 --to 2026-10-25T20:05:26
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import sys
import time
from decimal import Decimal, ROUND_HALF_UP

import simpleeval
from dateutil import parser as date_parser
from pint import UnitRegistry

MAX_EXPONENT = 4096  # 与旧实现一致；2**5000 这类直接拒绝
simpleeval.MAX_POWER = MAX_EXPONENT

FUNCS = {
    "sqrt": math.sqrt, "log": math.log, "log10": math.log10, "exp": math.exp,
    "floor": math.floor, "ceil": math.ceil, "abs": abs, "round": round,
    "min": min, "max": max,
}
CONSTS = {"pi": math.pi, "e": math.e}

UREG = UnitRegistry()
NUM_LOG_PATH = pathlib.Path(os.environ.get(
    "NUM_LOG_PATH", str(pathlib.Path(__file__).resolve().parent.parent / "06_日志" / "num_calls.jsonl")))


class NumError(ValueError):
    """输入或计算错误（退出码 2）。"""


def safe_eval(text: str):
    """simpleeval 沙箱求值；统一转成 NumError。"""
    evaluator = simpleeval.SimpleEval(functions=dict(FUNCS), names=dict(CONSTS))
    evaluator.ATTR_INDEX_FALLBACK = False
    try:
        return evaluator.eval(text)
    except ZeroDivisionError:
        raise NumError("除以零") from None
    except simpleeval.NumberTooHigh:
        raise NumError(f"指数过大（上限 {MAX_EXPONENT}）") from None
    except Exception as exc:  # noqa: BLE001 - 统一成可读错误
        raise NumError(f"表达式错误: {exc}") from None


def fmt(value, decimals: int | None = None) -> str:
    if decimals is not None:
        step = Decimal(1).scaleb(-decimals)
        return str(Decimal(str(value)).quantize(step, rounding=ROUND_HALF_UP))
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return repr(value)
    return str(value)


def pct(part: float, whole: float, *, decimals: int = 1) -> str:
    if whole == 0:
        raise NumError("分母为零，占比无定义")
    raw = Decimal(str(part)) / Decimal(str(whole)) * Decimal(100)
    step = Decimal(1).scaleb(-decimals)
    return str(raw.quantize(step, rounding=ROUND_HALF_UP))


def aggregate(values: list[float], *, decimals: int | None = None) -> dict:
    if not values:
        raise NumError("聚合需要至少一个数字")
    total = sum(values)
    mean = total / len(values)
    return {"count": len(values), "sum": total, "min": min(values),
            "max": max(values), "mean": round(mean, 6) if decimals is None else
            float(Decimal(str(mean)).quantize(Decimal(1).scaleb(-decimals),
                                              rounding=ROUND_HALF_UP))}


def convert_unit(expr: str, to: str = ""):
    """'2500 mW' --to W；返回 pint Quantity。"""
    try:
        quantity = UREG.parse_expression(expr)
    except Exception as exc:  # noqa: BLE001
        raise NumError(f"无法解析物理量: {exc}") from None
    if to:
        try:
            quantity = quantity.to(to)
        except Exception as exc:  # noqa: BLE001
            raise NumError(f"单位换算失败: {exc}") from None
    return quantity


def duration_between(start: str, end: str) -> dict:
    try:
        a = date_parser.parse(start)
        b = date_parser.parse(end)
    except (ValueError, OverflowError) as exc:
        raise NumError(f"日期解析失败: {exc}") from None
    delta = b - a
    seconds = delta.total_seconds()
    return {"start": a.isoformat(), "end": b.isoformat(),
            "seconds": seconds, "minutes": seconds / 60,
            "hours": seconds / 3600, "days": seconds / 86400,
            "text": (f"{seconds:.0f} s / {seconds / 60:.2f} min / "
                     f"{seconds / 3600:.3f} h / {seconds / 86400:.3f} d")}


def walk_path(data, expr: str) -> list:
    """从 JSON 里按 "a.b[*].c" / "a[0].b" 取数，返回数字列表。"""
    cur = [data]
    for part in expr.split("."):
        if not part:
            raise NumError(f"路径为空: {expr!r}")
        if part.endswith("[*]"):
            key = part[:-3]
            nxt = []
            for cell in cur:
                value = cell if key == "" else _key(cell, key)
                if not isinstance(value, list):
                    raise NumError(f"{part} 不是列表")
                nxt.extend(value)
            cur = nxt
        elif "[" in part and part.endswith("]"):
            key, idx = part[:-1].split("[")
            nxt = []
            for cell in cur:
                value = cell if key == "" else _key(cell, key)
                if not isinstance(value, list):
                    raise NumError(f"{part} 不是列表")
                nxt.append(value[int(idx)])
            cur = nxt
        else:
            cur = [_key(cell, part) for cell in cur]
    flat: list = []
    for cell in cur:
        if isinstance(cell, list):
            flat.extend(cell)
        else:
            flat.append(cell)
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in flat):
        raise NumError(f"路径 {expr} 取到的不是纯数字列表")
    return flat


def _key(cell, key):
    if not isinstance(cell, dict) or key not in cell:
        raise NumError(f"路径里找不到键 {key!r}")
    return cell[key]


def _save(args, payload: dict) -> None:
    """--save 留痕：追加一行 JSON（参数 + 结果 + 时间）。"""
    NUM_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
              "argv": sys.argv[1:], "result": payload}
    with NUM_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _emit(args, human: str, payload: dict) -> int:
    if getattr(args, "save", False):
        _save(args, payload)
    if args.json:
        print(json.dumps({"ok": True, **payload}, ensure_ascii=False))
    else:
        print(human)
    return 0


def cmd_eval(args) -> int:
    value = safe_eval(args.expr)
    return _emit(args, fmt(value, args.decimals),
                 {"value": value, "text": fmt(value, args.decimals)})


def cmd_pct(args) -> int:
    text = pct(args.part, args.whole, decimals=args.decimals)
    return _emit(args, f"{text}%", {"value": float(text), "text": f"{text}%"})


def cmd_agg(args) -> int:
    values = [float(v) for v in args.values]
    if args.file:
        data = json.loads(pathlib.Path(args.file).read_text(encoding="utf-8"))
        if not args.path:
            raise NumError("--file 需要配 --path")
        values += walk_path(data, args.path)
    result = aggregate(values, decimals=args.decimals)
    human = ("count={count} sum={sum} min={min} max={max} mean={mean}"
             .format(**{k: fmt(v) for k, v in result.items()}))
    return _emit(args, human, result)


def cmd_unit(args) -> int:
    quantity = convert_unit(args.expr, args.to)
    text = f"{quantity.magnitude:g} {quantity.units:~}"
    return _emit(args, text, {"value": quantity.magnitude,
                              "unit": str(quantity.units), "text": text})


def cmd_date(args) -> int:
    result = duration_between(args.start, args.end)
    return _emit(args, result["text"], result)


def main() -> int:
    ap = argparse.ArgumentParser(description="数字计算与生成（禁止心算）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(parser) -> None:
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--save", action="store_true", help="追加到 06_日志/num_calls.jsonl")

    p_eval = sub.add_parser("eval", help="安全表达式求值（simpleeval）")
    p_eval.add_argument("expr")
    p_eval.add_argument("--decimals", type=int, default=None)
    add_common(p_eval)
    p_eval.set_defaults(func=cmd_eval, json=False, save=False)

    p_pct = sub.add_parser("pct", help="占比（默认一位小数百分比）")
    p_pct.add_argument("part", type=float)
    p_pct.add_argument("whole", type=float)
    p_pct.add_argument("--decimals", type=int, default=1)
    add_common(p_pct)
    p_pct.set_defaults(func=cmd_pct, json=False, save=False)

    p_agg = sub.add_parser("agg", help="聚合：count/sum/min/max/mean")
    p_agg.add_argument("values", nargs="*", type=float)
    p_agg.add_argument("--file", default="", help="从 JSON 文件取数")
    p_agg.add_argument("--path", default="", help='JSON 路径，如 "runs[*].seconds"')
    p_agg.add_argument("--decimals", type=int, default=None)
    add_common(p_agg)
    p_agg.set_defaults(func=cmd_agg, json=False, save=False)

    p_unit = sub.add_parser("unit", help="单位换算（pint），如 '2500 mW' --to W")
    p_unit.add_argument("expr")
    p_unit.add_argument("--to", default="", help="目标单位，如 W / GiB / s")
    add_common(p_unit)
    p_unit.set_defaults(func=cmd_unit, json=False, save=False)

    p_date = sub.add_parser("date", help="日期/时长差（dateutil）")
    p_date.add_argument("--from", dest="start", required=True)
    p_date.add_argument("--to", dest="end", required=True)
    add_common(p_date)
    p_date.set_defaults(func=cmd_date, json=False, save=False)

    args = ap.parse_args()
    try:
        return args.func(args)
    except NumError as exc:
        print(f"num.py 错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
