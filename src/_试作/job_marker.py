"""job_marker_27b.py — 压缩任务可识别标识（P-073 配套）。

仅依赖标准库，不写文件、不打印、不联网。
"""

from __future__ import annotations

__all__ = ["make_job_id", "status_payload", "format_status"]

_FALLBACK_LABEL = "context-compaction"


def make_job_id(label: str, pid: int, turn: int, started_at: str) -> str:
    """构造压缩任务标识。

    返回 ``f"{label}#{pid}#t{turn}#{started_at}"``；
    label 为空时回退为 ``"context-compaction"``；
    started_at 原样拼入（不解析）。
    """
    if not label:
        label = _FALLBACK_LABEL
    return f"{label}#{pid}#t{turn}#{started_at}"


def status_payload(
    *,
    label: str,
    job_id: str,
    pid: int,
    ppid: int,
    mode: str,
    turn: int,
    session: str,
    started_at: str,
    phase: str = "starting",
) -> dict:
    """构造状态载荷字典。

    返回恰好这些键：
    ``label/job_id/pid/ppid/mode/turn/session/started_at/phase``；
    ``pid/ppid/turn`` 转成 int，其余转成 str；不得修改输入。
    """
    return {
        "label": str(label),
        "job_id": str(job_id),
        "pid": int(pid),
        "ppid": int(ppid),
        "mode": str(mode),
        "turn": int(turn),
        "session": str(session),
        "started_at": str(started_at),
        "phase": str(phase),
    }


def format_status(payload: dict) -> str:
    """将状态载荷格式化为单行字符串。

    返回一行：
    ``job=<label> pid=<pid> mode=<mode> turn=<turn> phase=<phase> started=<started_at>``；
    缺键用 ``?`` 代替，不抛异常。
    """
    def _get(key: str) -> str:
        try:
            value = payload[key]
        except (KeyError, TypeError):
            return "?"
        if value is None:
            return "?"
        return str(value)

    return (
        f"job={_get('label')} "
        f"pid={_get('pid')} "
        f"mode={_get('mode')} "
        f"turn={_get('turn')} "
        f"phase={_get('phase')} "
        f"started={_get('started_at')}"
    )
