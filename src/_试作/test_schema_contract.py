"""P-053/L4 契约测试：changeset_schema.json 与 l4 validator 键集合一致。

正向：真实 schema 必须与 l4_v3_27b 的常量完全对齐。
负向：故意制造漂移，比对器必须报错（避免测试本身变成空转）。

用法：
    .\\.venv\\Scripts\\python.exe src\_试作\\test_schema_contract.py
"""
from __future__ import annotations

import copy
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import schema_contract  # noqa: E402
import l4_v3_27b as l4  # noqa: E402

SCHEMA_PATH = HERE / "changeset_schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def check(schema):
    return schema_contract.schema_contract(
        schema, top_level=l4.TOP_LEVEL_KEYS, nested=l4.NESTED_KEYS,
        target_required=l4.TARGET_REQUIRED)


def test_real_schema_aligned():
    problems = check(SCHEMA)
    return problems == [], f"问题={problems}"


def test_base_hash_is_injected_only():
    top = set(SCHEMA.get("properties") or {})
    return ("base_hash" not in top
            and "base_hash" in l4.INJECTED_KEYS
            and "base_hash" not in l4.TOP_LEVEL_KEYS), \
        f"schema={sorted(top)} injected={l4.INJECTED_KEYS}"


def test_negative_extra_top_key():
    bad = copy.deepcopy(SCHEMA)
    bad["properties"]["base_hash"] = {"type": "string"}
    problems = check(bad)
    return any("多余顶层键" in p and "base_hash" in p for p in problems), f"问题={problems}"


def test_negative_missing_nested_key():
    bad = copy.deepcopy(SCHEMA)
    bad["properties"]["待办"]["properties"].pop("作废", None)
    problems = check(bad)
    return any("待办" in p and "作废" in p for p in problems), f"问题={problems}"


CASES = [test_real_schema_aligned, test_base_hash_is_injected_only,
         test_negative_extra_top_key, test_negative_missing_nested_key]


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
