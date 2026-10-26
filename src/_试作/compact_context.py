"""按轮增量压缩会话历史：L0 瘦身 → L1 锚点 → L2 分段摘要 → L3 状态变更集 → L4 校验落盘。

设计要点（对应 02_文档/工具与流程/上下文压缩.md）：
- 增量：只处理游标之后、且已经 turn/end 收尾的轮次，永远不碰正在进行的那一轮。
- 局部：一轮切成若干块逐块摘要，不把整段历史塞进一次调用。
- 结构：模型只输出“变化”，状态由程序 apply_changes 合并，锚点只许追加。
- 校验：摘要过 check_summary，状态过 validate_state；不过就带原因重试一次，再不过就报错退出。

用法：
    .\\.venv\\Scripts\\python.exe src\_试作\\compact_context.py --last-turns 1 --dry-run
    .\\.venv\\Scripts\\python.exe src\_试作\\compact_context.py           # 按游标增量跑
"""
from __future__ import annotations

import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.request

import zstandard
from filelock import FileLock, Timeout

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))

from cold_start_v2_27b import bootstrap, check_cold, norm  # noqa: E402
from compact_core_v2_27b import chunk_units  # noqa: E402
from l4_v3_27b import (apply_changes, check_changes, state_hash,  # noqa: E402
                       validate_state)
from quotes_v2_27b import verify_quotes  # noqa: E402
from gc_v2_27b import (merge_archive_v2, over_budget, plan_gc_v2, recall,  # noqa: E402
                       state_chars, verify_gc_v2)
from sum_v3_27b import (allocate_facts, check_summary_v3, context_guard, facts_cap,  # noqa: E402
                        split_chunk)
from qwen_client import (MODEL, URL, QwenError, chat, ensure_service,  # noqa: E402
                         parse_json)
from anchors_v2_27b import extract_anchors_v3  # noqa: E402
from slim_v2_27b import build_units_v2  # noqa: E402
import merge_budget  # noqa: E402
import job_marker  # noqa: E402
import pending_queue  # noqa: E402

SESSIONS = pathlib.Path.home() / ".dsh" / "sessions"
STATE_DIR = ROOT / "02_文档" / "会话状态"
STATE_PATH = STATE_DIR / "状态.json"
ARCHIVE_PATH = STATE_DIR / "归档.json"
CURSOR_PATH = STATE_DIR / "_游标.json"
JOB_STATUS_PATH = STATE_DIR / "compact.job.json"
PENDING_PATH = STATE_DIR / "compact.pending.json"
# 每字段字符预算（Letta 的 memory block 思路：按块算，不是全局一个数）
GC_BUDGETS = {"锚点": 4000, "决定": 4000, "待办": 2000, "已完成": 2000}
PROMPT_PATH = HERE / "merge_prompt_v3.txt"
SUM_PROMPT_PATH = HERE / "sum_prompt_v4.txt"
MERGE_SCHEMA = json.loads((HERE / "changeset_schema.json").read_text(encoding="utf-8"))
COLD_PROMPT_PATH = HERE / "coldstart_prompt.txt"
COLD_SCHEMA = json.loads((HERE / "coldstart_schema.json").read_text(encoding="utf-8"))
SUM_MAX_SUMMARY = 300
SUM_NUM_CTX = 8192
MERGE_NUM_CTX = 8192
MERGE_NUM_PREDICT = 1600

EMPTY_STATE = {"目标": "", "待办": [], "已完成": [], "红线": [], "决定": [], "锚点": []}
LOG_PATH = ROOT / "06_日志" / "compact_context.jsonl"
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUPS = 3
MAX_CALLS_DEFAULT = 60


def _rotate_log() -> None:
    """超过 LOG_MAX_BYTES 就把日志滚到 .1–.3（最旧一份丢弃）。"""
    if not LOG_PATH.exists() or LOG_PATH.stat().st_size < LOG_MAX_BYTES:
        return
    for i in range(LOG_BACKUPS, 0, -1):
        src = LOG_PATH.with_name(LOG_PATH.name + f".{i}")
        dst = LOG_PATH.with_name(LOG_PATH.name + f".{i + 1}")
        if not src.exists():
            continue
        if i == LOG_BACKUPS:
            src.unlink()
        else:
            src.replace(dst)
    LOG_PATH.replace(LOG_PATH.with_name(LOG_PATH.name + ".1"))


def hide_own_console() -> None:
    """插件由 GUI 宿主拉起时，Windows 会给 console 子进程新建终端窗口；把当前窗口藏掉。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:  # noqa: BLE001
        pass


def log(entry: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _rotate_log()
    entry["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def log_failure(*, error_code: str, stage: str, component: str, exit_code: int,
                message: str, retryable: bool = False, turn: int | None = None,
                **extra) -> None:
    """统一失败日志字段（P-057）：event/error_code/stage/component/exit_code/retryable/severity。"""
    entry = {"ok": False, "event": "run_failed", "severity": "warn" if retryable else "error",
             "error_code": error_code, "stage": stage, "component": component,
             "exit_code": exit_code, "retryable": retryable, "message": str(message)[:300]}
    if turn is not None:
        entry["turn"] = turn
    entry.update(extra)
    log(entry)


def queue_pending(session_obj, turn: int, *, reason: str) -> dict:
    """锁忙时登记排队请求（latest-wins），返回合并后的载荷。"""
    requested = time.strftime("%Y-%m-%d %H:%M:%S")
    new = pending_queue.pending_payload(str(session_obj), int(turn), requested, reason)
    existing = load_json(PENDING_PATH, {})
    merged = pending_queue.merge_pending(existing if isinstance(existing, dict) else None, new)
    PENDING_PATH.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
    return merged


def read_log_tail(n: int = 20) -> list[dict]:
    """读运行日志最后 n 行（--stats 用）。"""
    if not LOG_PATH.exists():
        return []
    rows: list[dict] = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows[-n:]


class BudgetExceeded(RuntimeError):
    """本轮模型调用超过上限。"""


_CALLS = {"used": 0, "max": MAX_CALLS_DEFAULT}


def tracked_chat(*args, **kwargs):
    """计数版 chat：超上限抛 BudgetExceeded，由 main 中止本轮（不提交、不推进游标）。"""
    if _CALLS["used"] >= _CALLS["max"]:
        raise BudgetExceeded(
            f"本轮模型调用已达上限 {_CALLS['max']} 次，整轮中止（不提交、不推进游标）")
    _CALLS["used"] += 1
    return chat(*args, **kwargs)


def load_progress(path: pathlib.Path, *, session: pathlib.Path, turn: int,
                  chunks_total: int, base_hash: str) -> tuple[list[dict], int]:
    """读「第N轮.blocks.jsonl」checkpoint：返回 (已完成的 items, 已完成块数)。

    checkpoint 是 append-only JSONL（meta 行 + 每块一行）；崩溃截断的最后一行会被忽略。
    输入/状态哈希/块数任一不匹配就丢弃重跑（对齐 BatchLLM 的 fail-fast 语义）。
    """
    if not path.exists():
        return [], 0
    parsed: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            break
    if not parsed:
        return [], 0
    meta = parsed[0]
    mismatched = (meta.get("kind") != "meta" or meta.get("session") != str(session)
                  or int(meta.get("turn") or -1) != int(turn)
                  or int(meta.get("chunks_total") or -1) != int(chunks_total)
                  or meta.get("base_hash") != base_hash)
    if mismatched:
        print("  ⚠ checkpoint 与当前输入/状态不匹配，丢弃重跑")
        path.unlink(missing_ok=True)
        return [], 0
    items: list[dict] = []
    done = 0
    for rec in parsed[1:]:
        if rec.get("kind") != "block":
            continue
        if int(rec.get("chunk") or -1) != done + 1:
            print("  ⚠ checkpoint 块序号不连续，丢弃重跑")
            path.unlink(missing_ok=True)
            return [], 0
        items.extend(rec.get("items") or [])
        done += 1
    if done > chunks_total:
        path.unlink(missing_ok=True)
        return [], 0
    return items, done


def append_progress(path: pathlib.Path, *, session: pathlib.Path, turn: int,
                    chunks_total: int, base_hash: str, chunk: int,
                    new_items: list[dict]) -> None:
    """每完成一块追加一行（首行是 meta），flush + fsync 后才算落盘。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        if path.stat().st_size == 0:
            fh.write(json.dumps({"kind": "meta", "session": str(session), "turn": int(turn),
                                 "chunks_total": int(chunks_total), "base_hash": base_hash,
                                 "ts": time.strftime("%Y-%m-%d %H:%M:%S")},
                                ensure_ascii=False) + "\n")
        fh.write(json.dumps({"kind": "block", "chunk": int(chunk), "items": new_items},
                            ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def write_progress_meta(path: pathlib.Path, *, session: pathlib.Path, turn: int,
                        chunks_total: int, base_hash: str) -> None:
    """只写 meta 行（0 块就超预算时用），保证下次运行能识别“有未完成 checkpoint”。"""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "meta", "session": str(session), "turn": int(turn),
                             "chunks_total": int(chunks_total), "base_hash": base_hash,
                             "ts": time.strftime("%Y-%m-%d %H:%M:%S")},
                            ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def clear_progress(path: pathlib.Path) -> None:
    path.unlink(missing_ok=True)


def spawn_background(session: pathlib.Path, turn: int) -> int:
    """Windows detached 子进程续跑同一轮（等锁 120s，退出后自动续）。"""
    log_path = ROOT / "06_日志" / "compact_background.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(pathlib.Path(__file__).resolve()),
           "--session", str(session), "--turn", str(turn), "--no-preflight",
           "--sync-budget", "0", "--lock-timeout", "120",
           "--job-label", "context-compaction", "--job-mode", "background"]
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    with log_path.open("a", encoding="utf-8", buffering=1) as fh:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=fh,
                                stderr=subprocess.STDOUT, close_fds=True,
                                creationflags=flags)
    return proc.pid


OLLAMA_PS_URL = URL.rsplit("/api/", 1)[0] + "/api/ps"
COMFYUI_PORT = 8188


def gpu_status() -> dict:
    """查 Ollama 已加载模型（含 size_vram）、nvidia-smi 空闲显存、ComfyUI 占用。"""
    models: list[dict] = []
    try:
        with urllib.request.urlopen(OLLAMA_PS_URL, timeout=3) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        models = payload.get("models") or []
    except Exception:  # noqa: BLE001 - 探针失败不应中断主流程
        models = []
    total = free = -1
    try:
        raw = subprocess.run(["nvidia-smi", "--query-gpu=memory.total,memory.used,memory.free",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True,
                             timeout=10).stdout.strip().splitlines()[0]
        total, _used, free = [int(x) for x in raw.split(",")]
    except Exception:  # noqa: BLE001
        total = free = -1
    comfy = False
    try:
        with socket.create_connection(("127.0.0.1", COMFYUI_PORT), timeout=1):
            comfy = True
    except OSError:
        comfy = False
    if not comfy:
        try:
            import psutil
            comfy = any("comfyui" in (proc.info.get("name") or "").lower()
                        for proc in psutil.process_iter(["name"]))
        except Exception:  # noqa: BLE001
            comfy = False
    target = next((m for m in models
                   if m.get("name") == MODEL or m.get("model") == MODEL), None)
    size = int(target.get("size") or 0) if target else 0
    size_vram = int(target.get("size_vram") or 0) if target else 0
    return {"loaded": target is not None, "model": MODEL,
            "size": size, "size_vram": size_vram,
            "vram_share": round(size_vram / size, 3) if size else 0.0,
            "vram_total_mb": total, "vram_free_mb": free, "comfyui": comfy,
            "models": [m.get("name") for m in models]}


def gpu_decision(status: dict, *, min_free_gb: float = 12.0) -> tuple[int, str]:
    """GPU 预检判定：0=通过；77=GPU/显存不满足。已加载模型时 free 低是正常的。"""
    if status.get("comfyui"):
        return 77, "检测到 ComfyUI（8188 端口或进程），先关掉 ComfyUI 或卸载 27B 再压缩"
    if status.get("loaded"):
        share = float(status.get("vram_share") or 0)
        if share < 0.99:
            return 0, (f"目标模型已加载，但约 {round((1 - share) * 100)}% 在 CPU"
                       f"（显存 {status.get('vram_total_mb')} MB），速度会明显变慢")
        return 0, "目标模型已加载且全在 GPU"
    free = int(status.get("vram_free_mb") or 0)
    if free <= 0:
        return 0, "读不到显存（nvidia-smi 不可用），跳过显存判定"
    if free < min_free_gb * 1024:
        return 77, (f"显存空闲 {free} MB < 阈值 {min_free_gb} GB，27B 无法正常加载；"
                    f"先释放显存（关 ComfyUI/浏览器硬件加速等）")
    return 0, f"显存空闲 {free} MB，可加载"


def latest_session() -> pathlib.Path:
    files = sorted(SESSIONS.glob("**/session.v4.jsonl.zstd"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit("找不到会话日志")
    return files[0]


def read_events(path: pathlib.Path) -> list[dict]:
    dctx = zstandard.ZstdDecompressor()
    with path.open("rb") as fh:
        text = dctx.stream_reader(fh, read_across_frames=True).read().decode("utf-8", "replace")
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def turns(events: list[dict]) -> list[dict]:
    """把事件按轮切好，只保留 turn/end 已经收尾的轮。"""
    out: list[dict] = []
    start = None
    turn_no = None
    for ev in events:
        kind = ev.get("type")
        if kind == "turn/start":
            start = ev.get("seq")
            turn_no = (ev.get("data") or {}).get("turn")
        elif kind == "turn/end" and start is not None:
            out.append({"turn": turn_no, "from": start, "to": ev.get("seq")})
            start = None
    return out


def load_json(path: pathlib.Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def parse_prompt(path: pathlib.Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    if "===SYSTEM===" not in text or "===USER===" not in text:
        raise SystemExit(f"{path.name} 缺少 ===SYSTEM=== / ===USER=== 标记")
    system, user = text.split("===SYSTEM===", 1)[1].split("===USER===", 1)
    return system.strip(), user.strip()


def summarize_block(source: str, *, max_facts: int, fix: str = "") -> tuple[dict, int]:
    """L2：单块结构化摘要，返回 (结果对象, 本次 prompt_tokens)。

    提示词模板里的 {chunk_chars}/{max_facts}/{max_summary} 由这里填进去——
    facts 上限随块大小缩放，避免写死 3 条把信息砍掉。
    """
    system, tpl = parse_prompt(SUM_PROMPT_PATH)
    user = (tpl.replace("{chunk_text}", source)
               .replace("{chunk_chars}", str(len(source)))
               .replace("{max_facts}", str(max_facts))
               .replace("{max_summary}", str(SUM_MAX_SUMMARY)))
    msgs = [{"role": "system", "content": system},
            {"role": "user", "content": user}]
    if fix:
        msgs.append({"role": "user", "content": fix})
    res = tracked_chat(msgs, think=False, json_mode=True, num_ctx=SUM_NUM_CTX, num_predict=1200,
                       temperature=0.1, tag="compact:sum", retries=1)
    return (res.obj or {}), int(getattr(res, "prompt_tokens", 0) or 0)


def repair_quotes_in(obj: dict, source: str) -> dict:
    """把被改写的引文回贴成原文逐字片段（确定性，不调模型）。

    模型擅长挑句子、不擅长逐字抄写；所以让它"指"，由代码"抄"：
    取最长公共片段映射回原文位置，扩到自然边界，替换掉改写版。
    """
    quotes = obj.get("quotes")
    if not isinstance(quotes, list) or not quotes:
        legacy = obj.get("quote")
        quotes = [legacy] if isinstance(legacy, str) and legacy.strip() else []
    if not quotes:
        return obj
    rows = verify_quotes([str(q) for q in quotes], source)
    obj["quotes"] = [r["text"] if r["status"] != "failed" else r["quote"] for r in rows]
    stats = {"verbatim": 0, "repaired": 0, "failed": 0}
    for row in rows:
        stats[row["status"]] = stats.get(row["status"], 0) + 1
    obj["_quote_stats"] = stats
    return obj


def _summarize_once(chunk: list[str], idx: int, *, split_from: int | None) -> tuple[dict, bool]:
    """摘一块，失败就带原因重写一次；返回 (item, 是否彻底失败)。"""
    source = "\n".join(chunk)
    cap = facts_cap(len(source), split=split_from is not None)
    obj, checks, overflow = {}, [], False
    for attempt in (1, 2):
        fix = ""
        if attempt == 2:
            hard_reason = "；".join(f"{c['id']}: {c['detail']}" for c in checks
                                   if c["level"] == "hard" and not c["ok"])
            thin_reason = "；".join(c["detail"] for c in checks
                                   if c.get("id") == "summary_thin" and not c["ok"])
            reason = "；".join(x for x in (hard_reason, thin_reason) if x)
            fix = (f"上一版摘要没过检查：{reason}。请重写：quotes 必须是原文里逐字存在的句子，"
                   f"facts 必须是能从原文找到的短句，摘要要具体、不能少于 30 字，"
                   f"不要出现指代前文或推断性表述。")
        try:
            obj, prompt_tokens = summarize_block(source, max_facts=cap, fix=fix)
        except QwenError:
            item = {"index": idx, "chars": len("\n".join(chunk)), "obj": {},
                    "checks": [], "hard_failed": ["调用失败"], "overflow": False,
                    "split_from": split_from, "fact_cap": 0, "service_error": True}
            return item, True
        obj = repair_quotes_in(obj, source)
        checks = check_summary_v3(obj, source, max_summary=SUM_MAX_SUMMARY, max_facts=cap)
        overflow = context_guard(prompt_tokens, SUM_NUM_CTX)
        hard = [c for c in checks if c["level"] == "hard" and not c["ok"]]
        thin = [c for c in checks if c.get("id") == "summary_thin" and not c["ok"]]
        if not hard and not overflow and not thin:
            break
    item = {"index": idx, "chars": len(source), "obj": obj, "checks": checks,
            "hard_failed": [c["id"] for c in checks if c["level"] == "hard" and not c["ok"]],
            "soft_failed": [c["id"] for c in checks if c.get("soft_fail")],
            "overflow": overflow, "split_from": split_from, "fact_cap": cap,
            "quote_stats": obj.get("_quote_stats", {})}
    failed = bool(item["hard_failed"]) or overflow
    return item, failed


def summarize_turn(units: list[str], anchors: list[str], *, max_chunks: int = 0,
                   resume_items: list[dict] | None = None, done_chunks: int = 0,
                   on_chunk=None, deadline: float | None = None) -> dict:
    """L2：切块逐块摘要；二次仍不过（或 prompt 占用过高）就把这块对半切开重摘。

    长尾策略（P-005）：支持 resume_items/done_chunks（从 checkpoint 续跑）、
    on_chunk（每完成一块回调，用于落盘）、deadline（monotonic 超时点，块间协作式检查）。
    """
    chunks = chunk_units(units)
    if max_chunks:
        chunks = chunks[:max_chunks]
    items: list[dict] = list(resume_items or [])
    start = max(0, min(done_chunks, len(chunks)))

    def handle(chunk: list[str], idx: int, *, split_from: int | None, depth: int) -> None:
        item, failed = _summarize_once(chunk, idx, split_from=split_from)
        if failed and depth == 0:
            parts = split_chunk(chunk)
            if len(parts) == 2 and all(parts):
                for part in parts:
                    handle(part, len(items) + 1, split_from=idx, depth=1)
                return
        if not item:
            item = {"index": idx, "chars": len("\n".join(chunk)), "obj": {},
                    "checks": [], "hard_failed": ["调用失败"], "overflow": False,
                    "split_from": split_from, "fact_cap": 0}
        items.append(item)

    for idx, chunk in enumerate(chunks[start:], start + 1):
        if deadline is not None and time.monotonic() >= deadline:
            return {"ok": True, "items": items, "anchors": anchors, "deferred": True,
                    "done_chunks": start, "chunks_total": len(chunks), "error": ""}
        before = len(items)
        handle(chunk, idx, split_from=None, depth=0)
        start = idx
        if on_chunk is not None:
            on_chunk(idx, items[before:], len(chunks))
    return {"ok": True, "items": items, "anchors": anchors, "deferred": False,
            "done_chunks": start, "chunks_total": len(chunks), "error": ""}


def tidy_anchors(anchors: list[str], root: pathlib.Path) -> list[str]:
    """锚点后处理：绝对路径相对化、丢掉临时目录与碎片、行内去重。

    逐字原则仍然成立——只把前缀换成相对形式，路径主体的每个字符都不动。
    """
    root_prefix = str(root).rstrip("\\/") + "\\"
    temp_marks = ("\\appdata\\local\\temp\\", "/tmp/", "\\temp\\")
    out: list[str] = []
    for line in anchors:
        kind, _, text = line.partition(" ")
        if kind in ("路径", "编辑"):
            raw = text
            if any(mark in raw.lower() for mark in temp_marks):
                continue
            if raw.lower().startswith(root_prefix.lower()):
                raw = raw[len(root_prefix):]
            if len(raw) < 8 and "\\" not in raw and "/" not in raw:
                continue
            line = f"{kind} {raw}"
        if line not in out:
            out.append(line)
    return out


MERGE_TAIL = (
    "\n\n【输出格式硬约束】只输出一个 JSON 对象，顶层键只能是 目标 / 待办 / 红线 / 决定 / 锚点；"
    "待办里只分 完成（上一版待办的整数下标）/ 作废（整数下标）/ 新增（字符串）；"
    "没有变化的键直接省略。"
)


def is_cold_start(state: dict) -> bool:
    """六个字段全空即冷启动。"""
    return not any([state.get("目标"), state.get("待办"), state.get("已完成"), state.get("红线"),
                    state.get("决定"), state.get("锚点")])


def run_cold_start(turn_items: list[dict], anchors: list[str],
                   turn: int) -> tuple[dict, list[str], list[dict], dict]:
    """第一次压缩：三张清单由代码投影，模型只答「目标/决定」两道判断题。

    冷启动不是增量：没有上一版待办可参照，让模型做分类必然把本轮做完的事塞进待办
    （P-001）。L2 已经把 做完/未做/用户要求 逐条抽出来了，直接投影即可。
    """
    boot = bootstrap(turn_items, anchors)
    problems = check_cold(boot, turn_items)
    if problems:
        return boot, problems, [], {}

    system, tpl = parse_prompt(COLD_PROMPT_PATH)
    summaries = "\n".join(
        f"[第{i['index']}段] {i['obj'].get('title', '')}：{i['obj'].get('summary', '')}"
        for i in turn_items)
    user = tpl.replace("{summaries}", summaries)
    obj: dict = {}
    try:
        res = tracked_chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                           think=False, schema=COLD_SCHEMA, num_ctx=8192, num_predict=600,
                           temperature=0.1, tag="compact:cold", retries=1)
        obj = parse_json(res.text) or {}
    except BudgetExceeded:
        # 预算触发不是"模型不可用"，必须冒到 main 走 rc=2（整轮中止）
        raise
    except Exception as exc:
        # fail-closed：模型不可用时绝不提交"猜测出来的空状态"（P-054）
        log({"turn": turn, "cold_start": "模型调用失败", "error": str(exc)[:200]})
        log_failure(error_code="SERVICE_UNAVAILABLE", stage="cold_start", component="ollama",
                    exit_code=2, message=str(exc), retryable=True, turn=turn)
        return boot, [f"冷启动模型调用失败，本轮中止（不提交、不推进游标）：{exc}"], [], {}

    goal = ""
    decisions: list[dict] = []
    if isinstance(obj, dict):
        goal = str((obj.get("目标") or {}).get("值", "") or "").strip()
        for item in (obj.get("决定") or {}).get("新增") or []:
            if isinstance(item, dict) and str(item.get("内容", "")).strip():
                decisions.append({"内容": str(item["内容"]).strip(),
                                  "理由": str(item.get("理由", "")).strip()})
    if goal:
        boot["目标"] = goal
    boot["决定"] = decisions[:3]
    changes = {"mode": "cold_start",
               "counts": {"已完成": len(boot["已完成"]), "待办": len(boot["待办"]),
                          "红线": len(boot["红线"]), "锚点": len(boot["锚点"])},
               "目标": boot["目标"], "决定": boot["决定"]}
    return boot, [], [], changes


def build_merge_user(state: dict, turn_items: list[dict], anchors: list[str],
                     *, include_facts: bool = True, compact: bool = False) -> tuple[str, str]:
    """组装合并调用的 (system, user)。

    瘦身原则（对应 Lost-in-the-Middle）：中段只留结论性的四张清单，引文不进合并输入
    （锚点已经承载逐字事实）；全局事实最多 15 条；最后把 schema 摘要再压一遍——
    指令贴着输入末尾，长输入下才不会被稀释。
    compact=True 时用于 lean 重试：state JSON 去缩进、摘要/清单截断到 160 字。
    """
    system, user_tpl = parse_prompt(PROMPT_PATH)
    blocks = []

    def clip(value: object, limit: int) -> str:
        text = str(value)
        return text if len(text) <= limit else text[:limit] + "…"

    def joined(values: object, limit: int) -> str:
        text = "；".join(map(str, values or [])) or "（无）"
        return clip(text, limit) if compact else text

    for item in turn_items:
        obj = item["obj"]
        part = [f"[第{item['index']}段] {obj.get('title', '')}",
                f"摘要：{clip(obj.get('summary', ''), 160) if compact else obj.get('summary', '')}",
                f"做完：{joined(obj.get('做完'), 160)}",
                f"未做：{joined(obj.get('未做'), 160)}",
                f"用户要求：{joined(obj.get('用户要求'), 160)}"]
        if include_facts:
            part.append(f"事实：{joined(obj.get('facts'), 200)}")
        blocks.append("\n".join(part))
    summaries = "\n".join(blocks)
    if include_facts:
        facts = allocate_facts([i["obj"] for i in turn_items], cap=15)
        if facts:
            summaries += ("\n\n[全局事实（跨块去重，逐字取自原文）]\n"
                          + "\n".join(f"- {fact}" for fact in facts))
    state_text = (json.dumps(state, ensure_ascii=False, separators=(",", ":"))
                  if compact else json.dumps(state, ensure_ascii=False, indent=2))
    user = user_tpl.replace("{state_json}", state_text)
    user = user.replace("{summaries}", summaries).replace("{anchors}", "\n".join(anchors))
    return system, user + MERGE_TAIL


def run_merge(state: dict, turn_items: list[dict], anchors: list[str],
              turn: int) -> tuple[dict, list[str], list[dict], dict]:
    """L3+L4：拿变更集并合并校验。

    四层防线：schema 约束解码保形状 → check_changes 保前置条件/下标 →
    apply_changes fail-closed 保不半应用 → validate_state 保守恒与跨列表不变量。
    预算：state JSON 已含锚点，单独的 anchors 段只放「本轮新增」的去重项；
    估算按实际中文/JSON 的 ~1.7 chars/token；超限切 lean（紧凑 state + 去 facts + 摘要截断），
    截断时按 lean 重试一次。
    """
    schema = MERGE_SCHEMA
    problems: list[str] = []
    changes: dict = {}
    existing_anchors = state.get("锚点") or []
    anchor_lines = merge_budget.new_anchor_lines(anchors, existing_anchors)
    num_predict = MERGE_NUM_PREDICT
    system, user = build_merge_user(state, turn_items, anchor_lines, include_facts=True)
    est_tokens = merge_budget.estimate_tokens(system + user, chars_per_token=1.7)
    lean = merge_budget.needs_lean(est_tokens, num_ctx=MERGE_NUM_CTX,
                                   num_predict=600, reserve=256)
    if lean:
        anchor_lines = []
        num_predict = 600
        system, user = build_merge_user(state, turn_items, [], include_facts=False,
                                        compact=True)
        lean_est = merge_budget.estimate_tokens(system + user, chars_per_token=1.7)
        log({"turn": turn, "merge": "预算 lean", "est_tokens": est_tokens,
             "lean_est": lean_est, "anchor_lines": 0})
    include_facts = not lean
    modes = [include_facts] + ([False] if include_facts else [])

    for mode in modes:
        if mode != include_facts:
            system, user = build_merge_user(state, turn_items, anchor_lines,
                                            include_facts=mode, compact=lean)
        for attempt in (1, 2):
            try:
                res = tracked_chat([{"role": "system", "content": system},
                                    {"role": "user", "content": user}],
                                   think=False, schema=schema, num_ctx=MERGE_NUM_CTX,
                                   num_predict=num_predict, temperature=0.1,
                                   tag="compact:merge", retries=1)
            except QwenError as exc:
                if not getattr(exc, "truncated", False):
                    raise
                log({"turn": turn, "merge": "截断重试",
                     "prompt_tokens": getattr(exc, "prompt_tokens", 0),
                     "num_predict": num_predict, "attempt": attempt})
                anchor_lines, num_predict, mode = [], 600, False
                system, user = build_merge_user(state, turn_items, [], include_facts=False,
                                                compact=True)
                if attempt == 2:
                    problems = ["输出被长度上限截断"]
                    break
                continue

            if getattr(res, "truncated", False):
                log({"turn": turn, "merge": "截断重试",
                     "prompt_tokens": res.prompt_tokens,
                     "num_predict": num_predict, "attempt": attempt})
                anchor_lines, num_predict, mode = [], 600, False
                system, user = build_merge_user(state, turn_items, [], include_facts=False,
                                                compact=True)
                if attempt == 2:
                    problems = ["输出被长度上限截断"]
                    break
                continue

            try:
                changes = parse_json(res.text)
            except Exception as exc:  # noqa: BLE001
                problems = [f"变更集不是合法 JSON: {exc}"]
            else:
                if not isinstance(changes, dict):
                    problems = ["变更集不是 JSON 对象"]
                else:
                    changes["base_hash"] = state_hash(state)  # 基准哈希由程序注入
                    problems = check_changes(state, changes)
            if problems:
                user = user + "\n\n【上一版变更集没过校验，请修正】\n" + "\n".join(problems) + MERGE_TAIL
                continue
            try:
                new_state = apply_changes(state, changes)
            except ValueError as exc:
                problems = [f"应用被拒: {exc}"]
                user = user + "\n\n【上一版变更集应用被拒，请修正】\n" + "\n".join(problems) + MERGE_TAIL
                continue
            problems = validate_state(state, new_state)
            if not problems:
                retired = [{"内容": str(item.get("内容", "")),
                            "理由": "（被取代）",
                            "轮次": turn,
                            "原因": f"被取代：{item.get('因为', '')}"}
                           for item in ((changes.get("决定") or {}).get("作废") or [])
                           if isinstance(item, dict)]
                return new_state, [], retired, changes
            user = user + "\n\n【上一版没过校验，请修正】\n" + "\n".join(problems) + MERGE_TAIL
        log({"turn": turn, "merge": "降级", "include_facts": mode, "problems": problems})
    return state, problems, [], changes


def run_gc(state: dict, *, turn: int, budgets: dict | None = None
           ) -> tuple[dict, str, list[str], dict]:
    """L5：按字段预算回收冷数据，返回 (热区状态, 说明, 问题列表, 归档)。

    守恒由 verify_gc_v2 兜底：热区 + 归档 必须还原回收前（含在用锚点必须留下），
    否则拒绝采用。归档记录带轮次，能回溯。
    """
    budgets = budgets or GC_BUDGETS
    over = over_budget(state, budgets)
    if not over:
        return state, f"未触发（{state_chars(state)} 字符 / 预算 {budgets}）", [], {}
    plan = plan_gc_v2(state, budgets, turn=turn)
    problems = verify_gc_v2(state, plan["state"], plan["archive"])
    if problems:
        return state, "拒绝回收", problems, {}
    counts = {k: len(v) for k, v in plan["archive"].items() if v}
    if not counts:
        print(f"  ⚠ GC 超预算 {over} 但无可回收（条目全在被引用或体积来自长字符串）")
    report = (f"回收 {over}：{state_chars(state)} → {state_chars(plan['state'])} 字符，"
              f"归档 {counts}")
    return plan["state"], report, [], plan["archive"]


CONSTRAINT_MARKERS = ("不许", "不要", "别把", "别自己", "必须", "一律", "只能", "务必",
                      "禁止", "都要", "为主", "可以")


def constraint_user_asks(turn_items: list[dict]) -> list[str]:
    """本轮 L2 抽到的「用户要求」里，带约束词的那些（去重保序）。"""
    out: list[str] = []
    seen: set[str] = set()
    for item in turn_items:
        obj = item.get("obj") or {}
        for value in obj.get("用户要求") or []:
            if not isinstance(value, str) or not value.strip():
                continue
            key = norm(value)
            if key in seen:
                continue
            seen.add(key)
            if any(marker in value for marker in CONSTRAINT_MARKERS):
                out.append(value.strip())
    return out


def merge_completed(state: dict, turn_items: list[dict]) -> dict:
    """把本轮 L2 明确「做完」的事确定性并入 已完成（P-070）。

    增量 merge 只处理「上一版待办 → 完成」，本轮新做完、从来不在待办里的事会消失；
    这里按 norm 去重补进已完成，并从待办里移除同名项（避免跨列表重复）。
    """
    done_now: list[str] = []
    seen: set[str] = set()
    for item in turn_items:
        obj = item.get("obj") or {}
        for value in obj.get("做完") or []:
            if not isinstance(value, str) or not value.strip():
                continue
            key = norm(value)
            if key in seen:
                continue
            seen.add(key)
            done_now.append(value.strip())
    if not done_now:
        return state
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in state.items()}
    completed = list(out.get("已完成") or [])
    completed_keys = {norm(x) for x in completed}
    matched = {norm(v) for v in done_now}
    todo = [x for x in (out.get("待办") or []) if norm(x) not in matched]
    for value in done_now:
        key = norm(value)
        if key not in completed_keys:
            completed.append(value)
            completed_keys.add(key)
    out["已完成"] = completed
    out["待办"] = todo
    return out


def redline_missing(state: dict, turn_items: list[dict]) -> list[str]:
    """本轮带约束词的用户要求里，没进状态红线的那些（软检查，只警告）。"""
    asks = constraint_user_asks(turn_items)
    redlines = [norm(x) for x in (state.get("红线") or []) if isinstance(x, str)]
    missing = []
    for ask in asks:
        a = norm(ask)
        if not any(a in r or r in a for r in redlines):
            missing.append(ask)
    return missing


def merge_redlines(state: dict, turn_items: list[dict]) -> dict:
    """把本轮带约束词的用户要求确定性并入 红线（P-070）。

    merge 模型漏写红线时，纯靠 prompt 不可靠；这里只并入「带约束词」的要求
    （提问/流程描述不会命中 CONSTRAINT_MARKERS），并按 norm 去重，已有覆盖则跳过。
    """
    asks = constraint_user_asks(turn_items)
    if not asks:
        return state
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in state.items()}
    redlines = list(out.get("红线") or [])
    red_norms = [norm(x) for x in redlines if isinstance(x, str)]
    for ask in asks:
        a = norm(ask)
        if any(a in r or r in a for r in red_norms):
            continue
        redlines.append(ask)
        red_norms.append(a)
    out["红线"] = redlines
    return out


NOISE_VERBS = (
    "查询", "读取", "列出", "查看", "获取", "加载", "检查", "执行", "运行", "调用",
    "安装", "启动", "停止", "打开", "发送", "展示", "打印", "输出", "写入", "创建",
    "删除", "保存", "复制", "移动", "编译", "验证", "确认", "统计", "计算",
    "比较", "搜索", "下载", "上传", "重命名",
)
SUBAGENT_MARKS = ("父代理", "父会话", "上轮压缩状态", "compactor-state",
                  "读文件", "运行命令", "调用任何工具", "只回答", "猜测")


def is_operation_noise(text: str) -> bool:
    """判断一条「待办/已完成」是否只是工具操作流水或子代理指令（P-072）。"""
    t = (text or "").strip()
    if not t:
        return True
    if any(mark in t for mark in SUBAGENT_MARKS):
        return True
    return t.startswith(NOISE_VERBS)


def filter_operation_noise(state: dict) -> dict:
    """过滤 已完成/待办 的操作流水与子代理指令；红线里的子代理指令也清掉。"""
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in state.items()}
    for key in ("已完成", "待办"):
        items = out.get(key)
        if isinstance(items, list):
            out[key] = [x for x in items if isinstance(x, str) and not is_operation_noise(x)]
    redlines = out.get("红线")
    if isinstance(redlines, list):
        out["红线"] = [x for x in redlines if isinstance(x, str)
                      and not any(mark in x for mark in SUBAGENT_MARKS)]
    return out


def write_turn_doc(path: pathlib.Path, meta: dict, result: dict) -> None:
    lines = [f"# 第 {meta['turn']} 轮（seq {meta['from']}–{meta['to']}）", ""]
    lines += ["## 目标", (result["state"].get("目标") or "（空）"), ""]
    lines += ["## 分段摘要", ""]
    for item in result["summaries"]:
        obj = item["obj"]
        lines.append(f"### 第 {item['index']} 段 · {obj.get('title', '')}（{item['chars']} 字）")
        lines.append(str(obj.get("summary", "")))
        facts = obj.get("facts") or []
        if facts:
            lines.append("")
            lines += [f"- 事实：{f}" for f in facts]
        if item["hard_failed"]:
            lines.append("")
            lines.append(f"- ⚠ 未过的检查：{', '.join(item['hard_failed'])}")
        lines.append("")
    lines += ["## 锚点（逐字）", ""]
    lines += [f"- `{a}`" for a in result["anchors"]] or ["- （无）"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def commit_state(state: dict, cursor: dict, meta: dict) -> dict:
    """两阶段提交：先落 pending 游标 → 写状态 → 落定游标。

    这样即使中途崩掉，下次启动也能判断"状态到底写没写进去"，
    从而只补游标、不重放同一轮（重放会因为变更集前置条件不幂等而写错状态）。
    """
    digest = state_hash(state)
    pending = {"turn": meta["turn"], "to": meta["to"], "hash": digest}
    out = dict(cursor)
    out["pending"] = pending
    CURSOR_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out.update({"last_seq": meta["to"], "turn": meta["turn"], "state_hash": digest,
                "updated": time.strftime("%Y-%m-%d %H:%M:%S")})
    out.pop("pending", None)
    CURSOR_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def recover_pending(state: dict, cursor: dict) -> dict:
    """启动时修复上次只写一半的提交。"""
    pending = cursor.get("pending")
    if not isinstance(pending, dict):
        return cursor
    out = dict(cursor)
    if state_hash(state) == pending.get("hash"):
        out.update({"last_seq": pending.get("to"), "turn": pending.get("turn"),
                    "state_hash": pending.get("hash"),
                    "updated": time.strftime("%Y-%m-%d %H:%M:%S")})
        out.pop("pending", None)
        CURSOR_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"检测到上次只写完状态、没落定游标：已补游标（第 {pending.get('turn')} 轮），不重放")
        return out
    out.pop("pending", None)
    CURSOR_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"上次提交未完成（状态未写入），第 {pending.get('turn')} 轮将重新处理")
    return out


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="")
    ap.add_argument("--turn", type=int, default=0, help="只处理指定轮次（测试用，忽略游标）")
    ap.add_argument("--last-turns", type=int, default=0, help="只处理最近 N 轮（测试用，忽略游标）")
    ap.add_argument("--dry-run", action="store_true", help="不写状态和游标")
    ap.add_argument("--no-merge", action="store_true", help="只跑 L0–L2，不调状态合并")
    ap.add_argument("--max-chunks", type=int, default=0, help="每轮最多摘要几块（冒烟测用）")
    ap.add_argument("--l0-budget", type=int, default=2000,
                    help="单条工具结果的头+尾预算（默认 2000；DSH 活上下文用 8192，摘要管道可以更紧）")
    ap.add_argument("--gc-budget", type=int, default=0,
                    help="把四类字段预算统一改成这个值（冒烟测用；0 表示用默认预算）")
    ap.add_argument("--recall", default="", help="在归档里按关键词检索后退出")
    ap.add_argument("--replay", type=int, default=0,
                    help="只用已有的 第N轮.raw.json 重跑合并，打印变更集（不写状态）")
    ap.add_argument("--no-preflight", action="store_true",
                    help="跳过启动前的 Ollama 探活（测试用）")
    ap.add_argument("--max-calls", type=int, default=MAX_CALLS_DEFAULT,
                    help=f"本轮全局模型调用上限（默认 {MAX_CALLS_DEFAULT}），超限整轮中止")
    ap.add_argument("--stats", action="store_true", help="打印最近 20 轮运行指标后退出")
    ap.add_argument("--sync-budget", type=float, default=300.0,
                    help="单轮同步预算（秒）；超限把剩余块落 checkpoint 并退出 76（0=不限）")
    ap.add_argument("--background", action="store_true",
                    help="超预算时拉起 detached 子进程续跑剩余块")
    ap.add_argument("--lock-timeout", type=float, default=0.0,
                    help="等单实例锁的秒数（后台续跑用，默认 0=不等）")
    ap.add_argument("--job-label", default="context-compaction",
                    help="任务标识（写进 compact.job.json 与运行日志）")
    ap.add_argument("--job-mode", default="foreground",
                    choices=("foreground", "background", "plugin"),
                    help="任务来源：foreground/background/plugin")
    ap.add_argument("--hide-console", action="store_true",
                    help="隐藏本进程控制台窗口（GUI 宿主 spawn 时用）")
    ap.add_argument("--status", action="store_true",
                    help="打印当前压缩任务标识/阶段后退出（不占锁）")
    ap.add_argument("--skip-gpu-preflight", action="store_true",
                    help="跳过 GPU/显存/ComfyUI 预检")
    ap.add_argument("--gpu-min-free-gb", type=float, default=12.0,
                    help="模型未加载时要求的最小空闲显存 GB（默认 12）")
    args = ap.parse_args()

    if args.hide_console:
        hide_own_console()

    if args.status:
        if JOB_STATUS_PATH.exists():
            try:
                print(job_marker.format_status(
                    json.loads(JOB_STATUS_PATH.read_text(encoding="utf-8"))))
            except Exception as exc:  # noqa: BLE001
                print(f"任务状态读取失败：{exc}")
        else:
            print("没有正在运行的压缩任务（compact.job.json 不存在）")
        print(f"锁文件：{'存在' if (STATE_DIR / 'compact.lock').exists() else '无'}")
        if PENDING_PATH.exists():
            try:
                print(pending_queue.format_pending(
                    json.loads(PENDING_PATH.read_text(encoding="utf-8"))))
            except Exception:  # noqa: BLE001
                print("pending turn=?（读取失败）")
        return 0

    budgets = ({k: args.gc_budget for k in GC_BUDGETS} if args.gc_budget else dict(GC_BUDGETS))
    _CALLS["max"] = max(1, args.max_calls)
    _CALLS["used"] = 0

    if args.stats:
        rows = [e for e in read_log_tail(60) if "calls" in e]
        if not rows:
            print("还没有带指标的运行记录（旧日志没有 calls 字段）")
            return 0
        print(f"{'轮':>4} {'块':>4} {'调用':>4} {'秒':>6} {'回贴':>4} {'失败块':>4}")
        for e in rows[-20:]:
            quoted = e.get("quoted") or {}
            print(f"{e.get('turn', 0):>4} {e.get('blocks', 0):>4} {e.get('calls', 0):>4} "
                  f"{e.get('seconds', 0):>6} {quoted.get('repaired', 0):>4} {e.get('failed', 0):>4}")
        return 0

    if args.recall:
        hits = recall(load_json(ARCHIVE_PATH, {}), args.recall)
        print(f"归档命中 {len(hits)} 条")
        for hit in hits:
            print(f"[{hit.get('类别')}] 第{hit.get('轮次')}轮 {hit.get('原因', '')}")
            print(f"    {hit.get('内容')}")
        return 0

    session = pathlib.Path(args.session) if args.session else latest_session()

    if args.replay:
        raw = load_json(STATE_DIR / f"第{args.replay}轮.raw.json", None)
        if raw is None:
            raise SystemExit(f"没有 第{args.replay}轮.raw.json，先跑一次完整压缩")
        state = load_json(STATE_PATH, dict(EMPTY_STATE))
        new_state, problems, retired, _changes = run_merge(
            state, raw["items"], raw["anchors"], turn=args.replay)
        print(json.dumps({"合并后状态": new_state, "问题": problems,
                          "被取代的决定": retired}, ensure_ascii=False, indent=2))
        return 0 if not problems else 1

    if not args.no_preflight and not ensure_service():
        print("Ollama 服务未就绪（已尝试自动拉起仍失败），本轮不做任何压缩。先手动启动：\n"
              "  $env:OLLAMA_MODELS='C:\\path\\to\\ollama-models'\n"
              "  Start-Process \"$env:LOCALAPPDATA\\Programs\\Ollama\\ollama.exe\" -ArgumentList 'serve'")
        log_failure(error_code="SERVICE_UNAVAILABLE", stage="preflight", component="ollama",
                    exit_code=2, message="Ollama 服务未就绪（自动拉起失败）", retryable=True)
        return 2

    if not getattr(args, "skip_gpu_preflight", False):
        gstatus = gpu_status()
        gcode, gmsg = gpu_decision(gstatus, min_free_gb=args.gpu_min_free_gb)
        print(f"GPU 预检：{gmsg}")
        log({"gpu_preflight": gmsg,
             "vram_free_mb": gstatus.get("vram_free_mb"),
             "vram_total_mb": gstatus.get("vram_total_mb"),
             "loaded": gstatus.get("loaded"),
             "vram_share": gstatus.get("vram_share"),
             "comfyui": gstatus.get("comfyui")})
        if gcode != 0:
            log_failure(error_code="GPU_UNAVAILABLE", stage="gpu_preflight", component="gpu",
                        exit_code=int(gcode), message=gmsg, retryable=True)
            return gcode

    # 单实例锁：只保护真正的压缩段（--recall/--replay/预检不占锁）。
    # filelock 在 F 盘实测可用（367 µs/次）；拿不到锁用独立退出码 75，与"服务不可用 2"区分。
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(STATE_DIR / "compact.lock"))
    try:
        lock.acquire(timeout=max(0.0, float(getattr(args, "lock_timeout", 0.0))))
    except Timeout:
        payload = queue_pending(session, int(getattr(args, "turn", 0) or 0), reason="lock_busy")
        print("另一个压缩实例正在运行（compact.lock 被占用）；本轮已登记为排队请求，"
              "等当前压缩结束后自动补跑（不当作失败）。")
        print("  " + pending_queue.format_pending(payload))
        log({"event": "run_queued", "reason": "lock_busy", "pid": os.getpid(),
             "mode": args.job_mode, "turn": payload.get("turn"),
             "session": str(session)})
        return 0
    job_started = time.strftime("%Y-%m-%d %H:%M:%S")
    job_turn = int(getattr(args, "turn", 0) or 0)
    job_id = job_marker.make_job_id(args.job_label, os.getpid(), job_turn, job_started)
    status = job_marker.status_payload(
        label=args.job_label, job_id=job_id, pid=os.getpid(), ppid=os.getppid(),
        mode=args.job_mode, turn=job_turn, session=str(session),
        started_at=job_started, phase="running")
    JOB_STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    log({"event": "run_start", "job": args.job_label, "job_id": job_id,
         "mode": args.job_mode, "pid": os.getpid(), "turn": job_turn})
    try:
        rc = _compact(args, session, budgets)
        drained = 0
        while rc == 0 and drained < 3 and PENDING_PATH.exists():
            try:
                payload = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                payload = {}
            if not pending_queue.is_pending_for(payload, str(session)):
                break
            PENDING_PATH.unlink(missing_ok=True)
            print("发现排队请求，继续压缩下一轮……")
            log({"event": "run_pending", "session": str(session),
                 "turn": payload.get("turn")})
            args.turn = 0
            args.last_turns = 0
            rc = _compact(args, session, budgets)
            drained += 1
        return rc
    except BudgetExceeded as exc:
        print(f"模型调用预算触发：{exc}")
        log({"ok": False, "error": "budget_exceeded", "message": str(exc),
             "calls": _CALLS["used"]})
        log_failure(error_code="BUDGET_EXCEEDED", stage="budget", component="driver",
                    exit_code=2, message=str(exc), retryable=False, calls=_CALLS["used"])
        return 2
    except QwenError as exc:
        print(f"模型服务不可用：{exc}")
        log_failure(error_code="SERVICE_UNAVAILABLE", stage="run", component="ollama",
                    exit_code=2, message=str(exc), retryable=True)
        return 2
    finally:
        try:
            current = json.loads(JOB_STATUS_PATH.read_text(encoding="utf-8"))
            if current.get("job_id") == job_id:
                JOB_STATUS_PATH.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        lock.release()


def _compact(args, session, budgets) -> int:
    run_start = time.time()
    events = read_events(session)
    all_turns = turns(events)
    if not all_turns:
        print("没有已收尾的轮次")
        return 0

    cursor = recover_pending(load_json(STATE_PATH, dict(EMPTY_STATE)),
                             load_json(CURSOR_PATH, {"last_seq": 0}))
    state = load_json(STATE_PATH, dict(EMPTY_STATE))
    if args.turn:
        picked = [t for t in all_turns if t["turn"] == args.turn]
        if not picked:
            raise SystemExit(f"没有第 {args.turn} 轮")
    elif args.last_turns:
        picked = all_turns[-args.last_turns:]
    else:
        picked = [t for t in all_turns if t["to"] > cursor.get("last_seq", 0)]
    if not picked:
        print("没有新的已收尾轮次")
        return 0

    # 有未完成的 checkpoint 时优先续跑（长尾策略：不同轮次不能越过未完成的轮）
    pending_turns: list[int] = []
    for p in sorted(STATE_DIR.glob("第*轮.blocks.jsonl")):
        try:
            pending_turns.append(int(p.name[len("第"):].split("轮")[0]))
        except ValueError:
            continue
    for pt in sorted(set(pending_turns)):
        if pt not in [t["turn"] for t in picked]:
            found = next((x for x in all_turns if x["turn"] == pt), None)
            if found:
                picked.insert(0, found)
                print(f"发现第 {pt} 轮的未完成 checkpoint，优先续跑")

    total = 0
    failed_turns = 0
    for meta in picked:
        turn_start = time.time()
        calls_before = _CALLS["used"]
        sub = [e for e in events if meta["from"] <= e.get("seq", -1) <= meta["to"]]
        units = build_units_v2(sub, budget=args.l0_budget)
        anchors = tidy_anchors(extract_anchors_v3(sub), ROOT)
        print(f"第 {meta['turn']} 轮：{len(sub)} 事件 → {len(units)} 行 / {sum(map(len, units))} 字，"
              f"锚点 {len(anchors)} 条")
        chunks_total = len(chunk_units(units)[:args.max_chunks] if args.max_chunks
                           else chunk_units(units))
        ppath = STATE_DIR / f"第{meta['turn']}轮.blocks.jsonl"
        base_hash = state_hash(state)
        resume_items, done_chunks = load_progress(
            ppath, session=session, turn=meta["turn"], chunks_total=chunks_total,
            base_hash=base_hash)
        if done_chunks:
            print(f"  从 checkpoint 续跑：已完成 {done_chunks}/{chunks_total} 块")
        deadline = (time.monotonic() + args.sync_budget) if args.sync_budget > 0 else None
        result = summarize_turn(
            units, anchors, max_chunks=args.max_chunks,
            resume_items=resume_items, done_chunks=done_chunks, deadline=deadline,
            on_chunk=lambda idx, new, total: append_progress(
                ppath, session=session, turn=meta["turn"], chunks_total=total,
                base_hash=base_hash, chunk=idx, new_items=new))
        if result.get("deferred"):
            done = int(result.get("done_chunks") or 0)
            total_blocks = int(result.get("chunks_total") or 0)
            print(f"  达到单轮同步预算（{args.sync_budget} s）：已完成 {done}/{total_blocks} 块，"
                  f"剩余块已落 checkpoint，下次运行续跑。")
            log({"turn": meta["turn"], "ok": False, "deferred": True,
                 "event": "run_deferred", "error_code": "DEFERRED", "stage": "sync_budget",
                 "component": "driver", "exit_code": 76, "retryable": True, "severity": "warn",
                 "done_blocks": done, "total_blocks": total_blocks,
                 "sync_budget": args.sync_budget})
            write_progress_meta(ppath, session=session, turn=meta["turn"],
                                chunks_total=total_blocks, base_hash=base_hash)
            if args.background:
                pid = spawn_background(session, meta["turn"])
                print(f"  已拉起后台续跑进程 pid={pid}（日志 06_日志/compact_background.log）")
            return 76
        service_errors = [i["index"] for i in result["items"] if i.get("service_error")]
        if service_errors:
            message = f"L2 摘要调用失败（模型服务不可用），块 {service_errors}"
            print(f"  {message}；本轮中止（不提交、不推进游标）")
            log_failure(error_code="SERVICE_UNAVAILABLE", stage="l2", component="ollama",
                        exit_code=2, message=message, retryable=True,
                        turn=meta["turn"], blocks=service_errors)
            failed_turns += 1
            break
        clear_progress(ppath)
        if not result["ok"]:
            print("  摘要失败:", result["error"])
            log({"turn": meta["turn"], "ok": False, "error": result["error"]})
            break
        failed = [i["index"] for i in result["items"] if i["hard_failed"]]
        print(f"  {len(result['items'])} 块摘要完成，检查未过 {len(failed)} 块 {failed or ''}")
        soft = [i["index"] for i in result["items"] if i.get("soft_failed")]
        if soft:
            print(f"  soft 未过 {len(soft)} 块 {soft}（降级可见，不重试）")
        quoted = {"verbatim": 0, "repaired": 0, "failed": 0}
        for item in result["items"]:
            for key, value in (item.get("quote_stats") or {}).items():
                quoted[key] = quoted.get(key, 0) + int(value)
        if any(quoted.values()):
            print(f"  引文：逐字 {quoted['verbatim']} / 回贴 {quoted['repaired']} / 失败 {quoted['failed']}")
        for item in result["items"]:
            if item["hard_failed"]:
                detail = "；".join(f"{c['id']}={c['detail']}" for c in item["checks"]
                                  if c["level"] == "hard" and not c["ok"])
                extra = f"；overflow={item['overflow']}" if item.get("overflow") else ""
                print(f"    第{item['index']}块（{item['chars']}字，facts 上限 {item['fact_cap']}）: {detail}{extra}")

        # 缓存分段摘要：改合并 prompt 时不用重跑 L2（一轮 9 块要 7 分钟）
        if not args.dry_run:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            (STATE_DIR / f"第{meta['turn']}轮.raw.json").write_text(json.dumps(
                {"meta": meta, "items": result["items"], "anchors": anchors},
                ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

        retired: list[dict] = []
        changes: dict = {}
        if args.no_merge:
            state_out, problems = state, []
        elif is_cold_start(state):
            state_out, problems, retired, changes = run_cold_start(
                result["items"], anchors, meta["turn"])
            print(f"  冷启动投影：已完成 {len(state_out['已完成'])} / 待办 {len(state_out['待办'])} / "
                  f"红线 {len(state_out['红线'])} / 锚点 {len(state_out['锚点'])}；"
                  f"目标＝{state_out['目标'] or '（空）'}")
        else:
            state_out, problems, retired, changes = run_merge(
                state, result["items"], anchors, turn=meta["turn"])
            print(f"  状态合并：{'通过' if not problems else '未过 ' + str(problems)}")

        if problems:
            log({"turn": meta["turn"], "ok": False, "problems": problems})
            failed_turns += 1
            break

        if not args.no_merge:
            # P-070/072/058：确定性兜底 + 操作流水过滤 + 锚点归一化软警告
            supplemented = merge_completed(state_out, result["items"])
            redlined = merge_redlines(supplemented, result["items"])
            filtered = filter_operation_noise(redlined)
            state_warnings: list[str] = []
            problems2 = validate_state(filtered, filtered, state_warnings)
            if problems2:
                problems2 = validate_state(redlined, redlined, state_warnings)
            if problems2:
                log({"turn": meta["turn"], "supplement": "rejected",
                     "problems": problems2})
            else:
                state_out = filtered
            if state_warnings:
                for warning in state_warnings:
                    print(f"  ⚠ 状态警告：{warning}")
                log({"turn": meta["turn"], "state_warnings": state_warnings})
            missing = redline_missing(state_out, result["items"])
            if missing:
                print(f"  ⚠ 仍有用户要求未进红线：{missing}")
                log({"turn": meta["turn"], "redline_missing": missing})
        state = state_out

        # L5 垃圾回收：按字段预算动手，收割可恢复（写归档 + 状态留热区 + 记录被取代的决定）
        before_gc = state
        state, gc_report, gc_problems, gc_archive = run_gc(
            state, turn=meta["turn"], budgets=budgets)
        print(f"  GC：{gc_report}")
        if gc_problems:
            print("  GC 校验未过:", gc_problems)
            log({"turn": meta["turn"], "ok": False, "gc_problems": gc_problems})
            break

        incoming = {k: list(v) for k, v in gc_archive.items() if v}
        if retired:
            incoming["决定"] = incoming.get("决定", []) + retired
            print(f"  决定被取代 {len(retired)} 条，已转归档留痕")
        if incoming and not args.dry_run:
            merged = merge_archive_v2(load_json(ARCHIVE_PATH, {}), incoming)
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            ARCHIVE_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")
        _ = before_gc

        if not args.dry_run:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            # 变更集存档：出问题能回溯"这一轮到底改了什么"
            (STATE_DIR / f"第{meta['turn']}轮.changes.json").write_text(
                json.dumps(changes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            write_turn_doc(STATE_DIR / f"第{meta['turn']}轮.md",
                           meta, {"state": state, "summaries": result["items"],
                                  "anchors": anchors})
            cursor = commit_state(state, cursor, meta)
        total += 1
        log({"turn": meta["turn"], "ok": True,
             "blocks": len(result["items"]), "chunks": len(result["items"]),
             "calls": _CALLS["used"] - calls_before,
             "seconds": round(time.time() - turn_start, 1),
             "quoted": quoted, "failed": len(failed),
             "dry_run": args.dry_run})

    print(f"\n完成 {total} 轮；状态文件 {'未写（dry-run）' if args.dry_run else STATE_PATH}")
    log({"ok": failed_turns == 0, "turns": total, "failed_turns": failed_turns,
         "calls": _CALLS["used"], "seconds": round(time.time() - run_start, 1),
         "dry_run": args.dry_run, "session": str(session)})
    return 1 if failed_turns else 0


if __name__ == "__main__":
    raise SystemExit(main())
