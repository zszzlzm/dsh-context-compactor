"""P-073 验收：pending_queue_27b.py（27B 产物）。"""
from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pending_queue as pq  # noqa: E402


def test_payload_shape():
    got = pq.pending_payload("s.jsonl", 45, "2026-10-25 23:00:00")
    want = {"session", "turn", "requested_at", "reason"}
    return (set(got) == want and got["turn"] == 45 and got["reason"] == "lock_busy"), f"实际={got}"


def test_merge_empty():
    new = {"session": "s", "turn": 45, "requested_at": "t2", "reason": "lock_busy"}
    got = pq.merge_pending(None, new)
    return got == new and got is not new, f"实际={got}"


def test_merge_later_wins():
    old = {"session": "s", "turn": 43, "requested_at": "2026-10-25 23:00:00", "reason": "x"}
    new = {"session": "s", "turn": 45, "requested_at": "2026-10-25 23:05:00", "reason": "y"}
    old_copy, new_copy = dict(old), dict(new)
    got = pq.merge_pending(old, new)
    return (got == new and old == old_copy and new == new_copy), f"实际={got}"


def test_merge_older_loses():
    old = {"session": "s", "turn": 45, "requested_at": "2026-10-25 23:05:00", "reason": "y"}
    new = {"session": "s", "turn": 43, "requested_at": "2026-10-25 23:00:00", "reason": "x"}
    got = pq.merge_pending(old, new)
    return got == old, f"实际={got}"


def test_pending_for_and_format():
    payload = {"session": " a.jsonl ", "turn": 45, "requested_at": "t", "reason": "lock_busy"}
    ok = pq.is_pending_for(payload, "a.jsonl")
    text = pq.format_pending(payload)
    bad = pq.is_pending_for(payload, "b.jsonl")
    return ok and not bad and "turn=45" in text, f"{ok}/{bad}/{text}"


CASES = [test_payload_shape, test_merge_empty, test_merge_later_wins,
         test_merge_older_loses, test_pending_for_and_format]


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
