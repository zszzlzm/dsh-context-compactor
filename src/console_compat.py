"""console_compat_27b.py — 控制台编码兼容模块（仅标准库）。"""

import sys


def configure_stream(stream) -> None:
    """若 stream 支持 reconfigure，则将其编码设为 utf-8 / errors=replace。

    - 无 reconfigure 方法：静默跳过。
    - reconfigure 抛 ValueError / OSError：静默吞掉。
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        pass


def configure_stdio() -> None:
    """依次对 sys.stdout、sys.stderr 调用 configure_stream。"""
    configure_stream(sys.stdout)
    configure_stream(sys.stderr)
