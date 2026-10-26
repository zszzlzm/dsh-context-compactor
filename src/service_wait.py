"""service_wait_27b.py — 服务等待与结果记录（P-056 验证底座）。

只依赖标准库；不写文件、不打印、不联网。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict


def wait_until(
    probe: Callable[[], Any],
    *,
    timeout: float,
    interval: float = 0.5,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """先探测一次；若为真立即返回 True。

    否则在 ``monotonic()`` 到达 ``timeout`` 之前循环：
    ``sleep(interval)`` → ``probe()``。
    ``probe()`` 抛任何异常都当作 False；超时返回 False。
    ``timeout <= 0`` 时只探测一次。
    """

    def _probe_bool() -> bool:
        try:
            return bool(probe())
        except Exception:
            return False

    # 第一次探测
    if _probe_bool():
        return True

    # timeout <= 0：只探测一次，不进入 sleep 循环
    if timeout <= 0:
        return False

    deadline = monotonic() + timeout
    while True:
        # 在 sleep 前检查是否已超时
        if monotonic() >= deadline:
            return False
        sleep(interval)
        if _probe_bool():
            return True
        # sleep 之后再次检查是否已超时
        if monotonic() >= deadline:
            return False


def ensure_result(
    *,
    already_up: bool,
    started: bool,
    ok: bool,
    seconds: float,
    exe: str,
    tags_url: str,
) -> Dict[str, Any]:
    """返回恰好包含六个键的 dict，并做类型归一化。"""
    return {
        "already_up": bool(already_up),
        "started": bool(started),
        "ok": bool(ok),
        "seconds": float(seconds),
        "exe": str(exe),
        "tags_url": str(tags_url),
    }
