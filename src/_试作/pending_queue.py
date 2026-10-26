"""pending_queue_27b.py — 压缩排队请求（P-073）。

仅依赖标准库，不写文件、不打印、不联网。
"""

from __future__ import annotations

__all__ = [
    "pending_payload",
    "merge_pending",
    "is_pending_for",
    "format_pending",
]


def pending_payload(
    session: str,
    turn: int,
    requested_at: str,
    reason: str = "lock_busy",
) -> dict:
    """构造一条排队请求载荷。

    返回恰好包含 ``{"session", "turn", "requested_at", "reason"}`` 四个键的 dict；
    ``session`` / ``requested_at`` / ``reason`` 转为 ``str``，``turn`` 转为 ``int``。
    """
    return {
        "session": str(session),
        "turn": int(turn),
        "requested_at": str(requested_at),
        "reason": str(reason),
    }


def merge_pending(existing: dict | None, new: dict) -> dict:
    """合并两条排队请求，保留 ``requested_at`` 较晚的一条。

    - ``existing`` 为 ``None`` 或空 dict 时，返回 ``new`` 的浅拷贝。
    - 否则按 ``requested_at`` 字符串比较，保留较晚的一条；相等时保留 ``new``。
    - 不修改传入的 ``existing`` 或 ``new``。
    """
    if existing is None or not existing:
        return dict(new)

    # 取 requested_at，缺失时视为空字符串（最小值）
    existing_at = str(existing.get("requested_at", ""))
    new_at = str(new.get("requested_at", ""))

    # 保留较晚的；相等时保留 new
    if new_at >= existing_at:
        return dict(new)
    return dict(existing)


def is_pending_for(payload: dict, session: str) -> bool:
    """判断 ``payload`` 是否属于指定 ``session``。

    比较 ``payload["session"].strip()`` 与 ``session.strip()`` 是否相等；
    ``payload`` 非 dict 或缺少 ``session`` 键时返回 ``False``。
    """
    if not isinstance(payload, dict):
        return False
    if "session" not in payload:
        return False
    try:
        return str(payload["session"]).strip() == str(session).strip()
    except Exception:
        return False


def format_pending(payload: dict) -> str:
    """将排队请求格式化为单行字符串。

    格式::

        pending turn=<turn> session=<session> requested=<requested_at> reason=<reason>

    缺键用 ``?`` 填充，不抛异常。
    """
    if not isinstance(payload, dict):
        payload = {}

    def _get(key: str) -> str:
        val = payload.get(key)
        if val is None:
            return "?"
        return str(val)

    return (
        f"pending turn={_get('turn')} "
        f"session={_get('session')} "
        f"requested={_get('requested_at')} "
        f"reason={_get('reason')}"
    )
