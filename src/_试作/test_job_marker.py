"""P-073 配套验收：job_marker_27b.py（27B 产物）。"""
from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import job_marker as jm  # noqa: E402


def test_make_job_id():
    got = jm.make_job_id("ctx", 123, 43, "2026-10-25 22:53:51")
    return got == "ctx#123#t43#2026-10-25 22:53:51", f"实际={got}"


def test_make_job_id_empty_label():
    got = jm.make_job_id("", 1, 2, "t")
    return got == "context-compaction#1#t2#t", f"实际={got}"


def test_status_payload_keys():
    got = jm.status_payload(label="ctx", job_id="ctx#1#t2#t", pid=1, ppid=2,
                            mode="background", turn=2, session="s.jsonl",
                            started_at="t", phase="running")
    want = {"label", "job_id", "pid", "ppid", "mode", "turn", "session",
            "started_at", "phase"}
    return (set(got) == want and got["pid"] == 1 and isinstance(got["pid"], int)
            and got["phase"] == "running"), f"实际={got}"


def test_format_status():
    text = jm.format_status({"label": "ctx", "pid": 1, "mode": "background",
                             "turn": 2, "phase": "running", "started_at": "t"})
    return all(part in text for part in ("job=ctx", "pid=1", "mode=background",
                                         "turn=2", "phase=running")), text


def test_format_status_missing_key():
    try:
        text = jm.format_status({"label": "ctx"})
        return "pid=?" in text, text
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


CASES = [test_make_job_id, test_make_job_id_empty_label, test_status_payload_keys,
         test_format_status, test_format_status_missing_key]


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
