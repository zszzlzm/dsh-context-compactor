"""P-066 验收：console_compat_27b.py（27B 产物）的编码兼容行为。

用法：
    .\\.venv\\Scripts\\python.exe src\_试作\\test_console_compat.py
"""
from __future__ import annotations

import io
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import console_compat as ccmod  # noqa: E402


class FakeStream:
    def __init__(self, *, raise_error: bool = False) -> None:
        self.calls: list[dict] = []
        self.raise_error = raise_error

    def reconfigure(self, **kwargs) -> None:
        self.calls.append(kwargs)
        if self.raise_error:
            raise ValueError("模拟 reconfigure 失败")


def test_functions_exist():
    ok = callable(getattr(ccmod, "configure_stream", None)) and callable(getattr(ccmod, "configure_stdio", None))
    return ok, "缺少 configure_stream / configure_stdio"


def test_reconfigure_kwargs():
    stream = FakeStream()
    ccmod.configure_stream(stream)
    return (stream.calls == [{"encoding": "utf-8", "errors": "replace"}],
            f"实际={stream.calls}")


def test_stream_without_reconfigure():
    try:
        ccmod.configure_stream(io.StringIO())
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def test_reconfigure_error_swallowed():
    try:
        ccmod.configure_stream(FakeStream(raise_error=True))
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def test_gbk_console_survives():
    code = (
        "import sys; sys.path.insert(0, " + repr(str(HERE.parent)) + "); sys.path.insert(0, " + repr(str(HERE)) + "); "
        "from console_compat import configure_stdio; configure_stdio(); "
        "print('关联 ↔ 中文')"
    )
    env = dict(os.environ, PYTHONIOENCODING="gbk")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          env=env, timeout=60)
    text = done.stdout.decode("utf-8", "replace")
    return (done.returncode == 0 and "↔" in text,
            f"rc={done.returncode} out={text!r} err={done.stderr.decode('utf-8','replace')[:200]!r}")


CASES = [test_functions_exist, test_reconfigure_kwargs, test_stream_without_reconfigure,
         test_reconfigure_error_swallowed, test_gbk_console_survives]


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
