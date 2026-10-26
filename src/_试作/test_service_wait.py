"""P-056 验收：service_wait_27b.py（27B 产物）。"""
from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import service_wait as sw  # noqa: E402


def test_immediate_true():
    calls = []

    def probe():
        calls.append(1)
        return True

    ok = sw.wait_until(probe, timeout=5)
    return ok and len(calls) == 1, f"ok={ok} calls={len(calls)}"


def test_retries_then_true():
    values = iter([False, False, True])
    sleeps = []

    def probe():
        return next(values)

    ok = sw.wait_until(probe, timeout=5, interval=1,
                       monotonic=lambda: 0, sleep=sleeps.append)
    return ok and len(sleeps) == 2, f"ok={ok} sleeps={len(sleeps)}"


def test_timeout_false():
    sleeps = []
    ok = sw.wait_until(lambda: False, timeout=0, sleep=sleeps.append)
    return ok is False and sleeps == [], f"ok={ok} sleeps={sleeps}"


def test_probe_exception_is_false():
    def probe():
        raise RuntimeError("boom")

    ok = sw.wait_until(probe, timeout=0)
    return ok is False, f"ok={ok}"


def test_ensure_result_shape():
    got = sw.ensure_result(already_up=True, started=False, ok=True, seconds=2,
                           exe="ollama.exe", tags_url="http://x/api/tags")
    want = {"already_up", "started", "ok", "seconds", "exe", "tags_url"}
    return (set(got) == want and got["seconds"] == 2.0
            and isinstance(got["seconds"], float) and got["exe"] == "ollama.exe"), f"实际={got}"


CASES = [test_immediate_true, test_retries_then_true, test_timeout_false,
         test_probe_exception_is_false, test_ensure_result_shape]


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
