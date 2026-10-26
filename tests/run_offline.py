"""Run the offline (no Ollama/GPU) test suite."""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = sys.executable
HERE = ROOT / "src" / "_试作"

CASES = [
    ("test_anchors_v2.py", [str(HERE / "anchors_v2_27b.py")]),
    ("test_gc_v2.py", [str(HERE / "gc_v2_27b.py")]),
    ("test_l4_v3.py", [str(HERE / "l4_v3_27b.py")]),
    ("test_sum_v3.py", [str(HERE / "sum_v3_27b.py")]),
    ("test_quotes.py", [str(HERE / "quotes_v2_27b.py")]),
    ("test_merge_budget.py", []),
    ("test_pending_queue.py", []),
    ("test_job_marker.py", []),
    ("test_service_wait.py", []),
    ("test_console_compat.py", []),
    ("test_thin_check.py", []),
    ("test_pinned_session.py", []),
    ("test_delegate_checks.py", []),
    ("test_schema_contract.py", []),
    ("test_num.py", []),
    ("test_pipeline.py", []),
]

failed = 0
for name, extra in CASES:
    cmd = [PY, str(HERE / name), *extra]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    done = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True,
                          encoding="utf-8", errors="replace")
    ok = done.returncode == 0
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name} rc={done.returncode}")
    if not ok:
        tail = (done.stdout or "") + (done.stderr or "")
        print(tail)
print(f"\n{len(CASES) - failed} passed / {failed} failed")
raise SystemExit(0 if failed == 0 else 1)
