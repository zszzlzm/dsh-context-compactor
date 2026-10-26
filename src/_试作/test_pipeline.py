"""端到端回归：用 stub 替换模型调用，跑通 L0→L5 并断言产物齐全。

为什么要它：六层各有记分牌（201 条断言），但**驱动本身没有自动回归**——
`compact_context.py` 改完只能靠手跑 dry-run 看输出。P-038。

做法：
- 把 `compact_context.chat` 换成 stub，按 tag 返回合法形状（摘要 / 冷启动 / 合并）
- 所有落盘路径改到临时目录，**绝不碰真实的 状态.json / 游标 / 日志**
- 用真实 session 日志（不是合成事件），跑最近 2 轮：第一轮走冷启动、第二轮走增量合并
- 断言四件产物、游标哈希、冷启动标记、dry-run 不写盘、真实状态未被改动

用法：
    .\\.venv\\Scripts\\python.exe src\_试作\\test_pipeline.py
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import time
import types

import zstandard

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import compact_context as cc  # noqa: E402
from fixture_pipeline_v1 import EVENTS as SYNTHETIC_EVENTS  # noqa: E402
from l4_v3_27b import state_hash, validate_state  # noqa: E402
from qwen_client import QwenError  # noqa: E402

REAL_GPU_DECISION = cc.gpu_decision


class FakeCall:
    """冒充 qwen_client.Call。"""

    def __init__(self, obj: dict, text: str):
        self.obj = obj
        self.text = text
        self.prompt_tokens = 1200
        self.eval_tokens = 120
        self.seconds = 1.0
        self.tok_s = 120.0


ROW_PREFIX = ("【用户", "【助手", "【调用", "【结果")

HOLD_LOCK = (
    "import sys, time\n"
    "from filelock import FileLock\n"
    "lock = FileLock(sys.argv[1])\n"
    "lock.acquire()\n"
    "print('held', flush=True)\n"
    "time.sleep(30)\n"
)

# --session 指定后，所有 cc.main() 调用都钉住同一份会话日志（P-067）
SESSION_ARG: list[str] = []


def _chunk_of(messages: list[dict]) -> str:
    """从提示词里还原出真正的那块对话（行首是四类前缀的行）。

    注意不能随便截：提示词模板自身也带【】标记，早期版本从样板里截引文，
    导致"引文不是原文"的假失败，会掩盖真实回归。
    """
    text = "\n".join(m.get("content", "") for m in messages
                     if isinstance(m.get("content"), str))
    rows = [line for line in text.splitlines() if line.strip().startswith(ROW_PREFIX)]
    return "\n".join(rows)


def fake_chat(messages, *, tag: str = "", **kwargs) -> FakeCall:
    """按 tag 返回合法形状；摘要里的引文/事实取原文连续片段，保证能过校验。"""
    if tag == "compact:sum":
        chunk = _chunk_of(messages)
        quote = chunk[:20] or "回退引文内容"
        fact = chunk[5:25] or quote
        obj = {
            "title": "测试块",
            "summary": "本块摘要用于测试，内容写长一点以满足空壳检测的最短长度要求。",
            "facts": [fact],
            "quotes": [quote],
            "做完": ["测试用已完成项"],
            "未做": ["测试用未完成项"],
            "用户要求": [],
        }
        return FakeCall(obj, json.dumps(obj, ensure_ascii=False))
    if tag == "compact:cold":
        obj = {"目标": {"值": "测试目标"}, "决定": {"新增": []}}
        return FakeCall(obj, json.dumps(obj, ensure_ascii=False))
    obj: dict = {}
    return FakeCall(obj, "{}")


def redirect(tmp: pathlib.Path) -> None:
    """把所有落盘路径改到临时目录。"""
    cc.STATE_DIR = tmp
    cc.STATE_PATH = tmp / "状态.json"
    cc.CURSOR_PATH = tmp / "_游标.json"
    cc.ARCHIVE_PATH = tmp / "归档.json"
    cc.LOG_PATH = tmp / "compact_context.jsonl"
    cc.JOB_STATUS_PATH = tmp / "compact.job.json"
    cc.PENDING_PATH = tmp / "compact.pending.json"
    cc.chat = fake_chat
    # GPU 预检依赖真实环境，测试里默认放行；P-071 有专门用例直接测判定函数
    cc.gpu_status = lambda: {"loaded": True, "vram_share": 1.0, "vram_free_mb": 20000,
                             "vram_total_mb": 16303, "comfyui": False, "models": []}
    cc.gpu_decision = lambda *a, **kw: (0, "test: skip gpu")


def run(argv_tail: list[str]) -> int:
    old = sys.argv[:]
    sys.argv = ["compact_context.py", *SESSION_ARG, *argv_tail]
    try:
        return cc.main()
    finally:
        sys.argv = old


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def main(argv: list[str] | None = None) -> int:
    global SESSION_ARG
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="", help="固定会话日志（默认最新会话，不稳定；P-067）")
    args = ap.parse_args(argv)
    SESSION_ARG = ["--session", args.session] if args.session else []

    real_state = pathlib.Path(cc.ROOT) / "02_文档" / "会话状态" / "状态.json"
    real_before = digest(real_state)

    results: list[tuple[str, bool, str]] = []

    def case(name: str, ok: bool, msg: str = "") -> None:
        results.append((name, ok, msg))

    with tempfile.TemporaryDirectory() as raw:
        tmp = pathlib.Path(raw)
        redirect(tmp)

        # 固定合成会话（簇 C 拍 1 起用；预算等用例也钉它，避免依赖「最新会话」）
        syn = tmp / "synthetic_session.v4.jsonl.zstd"
        syn_lines = "\n".join(json.dumps(e, ensure_ascii=False) for e in SYNTHETIC_EVENTS)
        syn.write_bytes(zstandard.ZstdCompressor().compress(syn_lines.encode("utf-8")))

        # 第一次：真实跑最近 2 轮（第一轮冷启动，第二轮增量合并）
        code = run(["--last-turns", "2"])
        case("退出码为 0", code == 0, f"实际 {code}")

        state_path = cc.STATE_PATH
        cursor_path = cc.CURSOR_PATH
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        cursor = json.loads(cursor_path.read_text(encoding="utf-8")) if cursor_path.exists() else {}

        case("状态文件已生成", bool(state), "状态.json 不存在")
        case("目标来自冷启动模型", state.get("目标") == "测试目标", f"实际={state.get('目标')!r}")
        case("已完成非空", bool(state.get("已完成")), f"实际={state.get('已完成')}")
        case("待办来自未做", state.get("待办") == ["测试用未完成项"], f"实际={state.get('待办')}")
        case("游标无残留 pending", "pending" not in cursor, f"pending={cursor.get('pending')}")
        case("游标哈希与状态一致",
             cursor.get("state_hash") == state_hash(state),
             f"游标={cursor.get('state_hash')} 状态={state_hash(state)}")
        case("状态通过守恒校验", not validate_state(state, state), str(validate_state(state, state)))

        # 两轮的产物：md / raw / changes
        turns = sorted({p.name.split("轮")[0].lstrip("第") for p in tmp.glob("第*轮.md")}
                       | {p.name.split("轮")[0].lstrip("第") for p in tmp.glob("第*轮.raw.json")},
                       key=int)
        case("至少处理了 1 轮", len(turns) >= 1, f"轮次={turns}")
        for turn in turns:
            for suffix in (".md", ".raw.json", ".changes.json"):
                p = tmp / f"第{turn}轮{suffix}"
                case(f"第{turn}轮{suffix} 存在", p.exists(), "缺文件")

        # 摘要硬检查必须全过（否则 L2 的校验回归会被"跑通了"掩盖）
        for turn in turns:
            raw = tmp / f"第{turn}轮.raw.json"
            if not raw.exists():
                continue
            data = json.loads(raw.read_text(encoding="utf-8"))
            bad = [i["index"] for i in data.get("items", []) if i.get("hard_failed")]
            if SESSION_ARG:
                case(f"第{turn}轮摘要检查全过", not bad, f"未过块={bad}")
            else:
                case(f"第{turn}轮摘要结构可解析",
                     isinstance(data.get("items"), list) and bool(data["items"]), "items 缺失")

        # 第一轮应是冷启动，第二轮是增量（收尾轮不足 2 时跳过这两条，默认入口不崩）
        if len(turns) >= 2:
            first = tmp / f"第{turns[0]}轮.changes.json"
            second = tmp / f"第{turns[1]}轮.changes.json"
            if first.exists():
                data = json.loads(first.read_text(encoding="utf-8"))
                case("首轮走冷启动路径", data.get("mode") == "cold_start",
                     f"mode={data.get('mode')}")
            if second.exists():
                data = json.loads(second.read_text(encoding="utf-8"))
                case("次轮走增量路径且有 base_hash", "base_hash" in data,
                     f"keys={sorted(data)}")
        else:
            case("收尾轮不足 2（跳过冷启动/增量分支断言）", True, f"轮次={turns}")

        # dry-run 不写盘
        state_before = state_path.read_text(encoding="utf-8")
        cursor_before = cursor_path.read_text(encoding="utf-8")
        code = run(["--last-turns", "1", "--dry-run"])
        case("dry-run 退出码为 0", code == 0, f"实际 {code}")
        case("dry-run 不写状态",
             state_path.read_text(encoding="utf-8") == state_before, "状态被改动")
        case("dry-run 不写游标",
             cursor_path.read_text(encoding="utf-8") == cursor_before, "游标被改动")

        # ---- 簇 A 治理：每轮指标 / --stats / 日志轮转 ----
        log_path = tmp / "compact_context.jsonl"
        entries = [json.loads(x) for x in
                   log_path.read_text(encoding="utf-8").splitlines() if x.strip()]
        with_metrics = [e for e in entries if "calls" in e and "blocks" in e]
        need = {"turn", "blocks", "chunks", "calls", "seconds", "quoted", "failed"}
        case("每轮日志含指标字段",
             bool(with_metrics) and need <= set(with_metrics[0]),
             f"实际={sorted(with_metrics[0]) if with_metrics else entries[:1]}")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = run(["--stats"])
        case("--stats 退出码 0 且打印指标",
             code == 0 and "调用" in buf.getvalue(),
             f"rc={code} out={buf.getvalue()[:80]!r}")

        rot = tmp / "rot.jsonl"
        cc.LOG_PATH = rot
        with rot.open("a", encoding="utf-8") as fh:
            for _ in range(2200):
                fh.write("x" * 1000 + "\n")
        cc.log({"probe": "rotate"})
        rotated = sorted(p.name for p in tmp.glob("rot.jsonl*"))
        case("日志写满 2MB 后自动轮转",
             "rot.jsonl.1" in rotated and rot.stat().st_size < 2000,
             f"files={rotated} size={rot.stat().st_size}")

        # ---- 簇 A 治理：单实例锁（真跨进程） ----
        holder = subprocess.Popen([sys.executable, "-c", HOLD_LOCK, str(tmp / "compact.lock")],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            holder.stdout.readline()  # 等子进程拿锁
            state_before = state_path.read_text(encoding="utf-8")
            cursor_before = cursor_path.read_text(encoding="utf-8")
            code = run(["--last-turns", "1", "--no-preflight"])
            case("第二实例锁忙时登记排队（rc=0）",
                 code == 0 and cc.PENDING_PATH.exists(), f"rc={code} pending={cc.PENDING_PATH.exists()}")
            case("锁忙排队时状态不变",
                 state_path.read_text(encoding="utf-8") == state_before, "状态被改动")
            case("锁忙排队时游标不变",
                 cursor_path.read_text(encoding="utf-8") == cursor_before, "游标被改动")
        finally:
            holder.kill()
            holder.wait()

        # ---- 簇 A 治理：调用预算 ----
        with tempfile.TemporaryDirectory() as raw2:
            tmp2 = pathlib.Path(raw2)
            redirect(tmp2)
            cc.ensure_service = lambda **kw: True
            code = run(["--session", str(syn), "--turn", "8", "--no-preflight",
                        "--max-calls", "1"])
            case("超 --max-calls 中止（rc=2）", code == 2, f"实际 {code}")
            case("超预算不写状态", not cc.STATE_PATH.exists(), "状态被写了")
            case("超预算不写游标", not cc.CURSOR_PATH.exists(), "游标被写了")

        # ---- 簇 C 拍 1：固定合成会话 + 失败注入（P-041） ----
        # 合成会话已在上面创建；这里直接复用，避免重复写

        with tempfile.TemporaryDirectory() as raw3:
            tmp3 = pathlib.Path(raw3)
            redirect(tmp3)
            cc.ensure_service = lambda **kw: True
            code = run(["--session", str(syn), "--last-turns", "3", "--no-preflight"])
            case("合成会话跑通 3 轮", code == 0, f"实际 {code}")
            syn_turns = sorted({p.name.split("轮")[0].lstrip("第")
                                for p in tmp3.glob("第*轮.md")}, key=int)
            case("合成会话处理轮次 = 8/9/10",
                 syn_turns == ["8", "9", "10"], f"轮次={syn_turns}")

        # 失败注入 A：摘要第一次不过、第二次过 → 重试一次成功（2 次调用）
        with tempfile.TemporaryDirectory() as raw4:
            tmp4 = pathlib.Path(raw4)
            redirect(tmp4)
            cc.ensure_service = lambda **kw: True
            n_a = [0]

            def bad_first(messages, *, tag: str = "", **kw):
                n_a[0] += 1
                if tag == "compact:sum" and n_a[0] == 1:
                    return FakeCall({"title": "坏", "summary": "", "facts": [],
                                     "quotes": []}, "{}")
                return fake_chat(messages, tag=tag, **kw)

            cc.chat = bad_first
            code = run(["--session", str(syn), "--turn", "8", "--no-preflight", "--no-merge"])
            raws = list(tmp4.glob("第8轮.raw.json"))
            items = json.loads(raws[0].read_text(encoding="utf-8"))["items"] if raws else []
            case("摘要重试一次后成功",
                 code == 0 and n_a[0] == 2 and bool(items) and not items[0]["hard_failed"],
                 f"rc={code} calls={n_a[0]} items={len(items)}")

        # 失败注入 B：摘要两次都不过 → 对半降级（2+2+2=6 次调用，2 个 split 块）
        with tempfile.TemporaryDirectory() as raw5:
            tmp5 = pathlib.Path(raw5)
            redirect(tmp5)
            cc.ensure_service = lambda **kw: True
            n_b = [0]

            def bad_all(messages, *, tag: str = "", **kw):
                n_b[0] += 1
                if tag == "compact:sum":
                    return FakeCall({"title": "坏", "summary": "", "facts": [],
                                     "quotes": []}, "{}")
                return fake_chat(messages, tag=tag, **kw)

            cc.chat = bad_all
            code = run(["--session", str(syn), "--turn", "8", "--no-preflight", "--no-merge"])
            raws = list(tmp5.glob("第8轮.raw.json"))
            items = json.loads(raws[0].read_text(encoding="utf-8"))["items"] if raws else []
            split = [i for i in items if i.get("split_from") is not None]
            case("两次失败对半降级",
                 code == 0 and n_b[0] == 6 and len(split) == 2,
                 f"rc={code} calls={n_b[0]} split={len(split)} items={len(items)}")

        # 失败注入 C：摘要超长（summary_length 是 soft）→ 必须留下 soft_fail 标记（P-003）
        with tempfile.TemporaryDirectory() as raw6:
            tmp6 = pathlib.Path(raw6)
            redirect(tmp6)
            cc.ensure_service = lambda **kw: True

            def soft_bad(messages, *, tag: str = "", **kw):
                if tag == "compact:sum":
                    chunk = _chunk_of(messages)
                    obj = {"title": "软失败块", "summary": "这是一段很长的摘要。" * 40,
                           "facts": [chunk[5:25] or "回退事实"],
                           "quotes": [chunk[:20] or "回退引文"],
                           "做完": [], "未做": [], "用户要求": []}
                    return FakeCall(obj, json.dumps(obj, ensure_ascii=False))
                return fake_chat(messages, tag=tag, **kw)

            cc.chat = soft_bad
            code = run(["--session", str(syn), "--turn", "8", "--no-preflight", "--no-merge"])
            raws = list(tmp6.glob("第8轮.raw.json"))
            items = json.loads(raws[0].read_text(encoding="utf-8"))["items"] if raws else []
            soft = items[0].get("soft_failed") if items else None
            case("摘要 soft 不过时留 soft_fail 标记",
                 code == 0 and bool(items) and not items[0]["hard_failed"] and bool(soft),
                 f"rc={code} hard={items[0]['hard_failed'] if items else None} soft={soft}")

        # 失败注入 D：引文乱写 → 硬检查拦下并重试成功（2 次调用）
        with tempfile.TemporaryDirectory() as raw7:
            tmp7 = pathlib.Path(raw7)
            redirect(tmp7)
            cc.ensure_service = lambda **kw: True
            n_d = [0]

            def bad_quote(messages, *, tag: str = "", **kw):
                n_d[0] += 1
                if tag == "compact:sum" and n_d[0] == 1:
                    chunk = _chunk_of(messages)
                    obj = {"title": "乱写", "summary": "测试摘要。",
                           "facts": [chunk[5:25] or "回退事实"],
                           "quotes": ["完全编造的引文，原文里没有"],
                           "做完": [], "未做": [], "用户要求": []}
                    return FakeCall(obj, json.dumps(obj, ensure_ascii=False))
                return fake_chat(messages, tag=tag, **kw)

            cc.chat = bad_quote
            code = run(["--session", str(syn), "--turn", "8", "--no-preflight", "--no-merge"])
            raws = list(tmp7.glob("第8轮.raw.json"))
            items = json.loads(raws[0].read_text(encoding="utf-8"))["items"] if raws else []
            case("引文乱写被拦下并重试成功",
                 code == 0 and n_d[0] == 2 and bool(items) and not items[0]["hard_failed"],
                 f"rc={code} calls={n_d[0]} hard={items[0]['hard_failed'] if items else None}")

        # 强制降级：合并带事实两次不过 → 降级去掉事实再合（3 次 merge 调用）
        with tempfile.TemporaryDirectory() as raw8:
            tmp8 = pathlib.Path(raw8)
            redirect(tmp8)
            cc.ensure_service = lambda **kw: True
            n_m = [0]

            def degraded_merge(messages, *, tag: str = "", **kw):
                if tag == "compact:merge":
                    n_m[0] += 1
                    text = "\n".join(m.get("content", "") for m in messages
                                     if isinstance(m.get("content"), str))
                    if "全局事实" in text:
                        bad = {"待办": {"完成": [{"idx": 0, "expect": "不存在"}]}}
                        return FakeCall(bad, json.dumps(bad, ensure_ascii=False))
                    return FakeCall({}, "{}")
                return fake_chat(messages, tag=tag, **kw)

            cc.chat = degraded_merge
            code = run(["--session", str(syn), "--last-turns", "2", "--no-preflight"])
            entries = [json.loads(x) for x in
                       (tmp8 / "compact_context.jsonl").read_text(encoding="utf-8").splitlines()
                       if x.strip()]
            case("合并失败后强制降级（去掉事实）成功",
                 code == 0 and n_m[0] == 3 and any(e.get("merge") == "降级" for e in entries),
                 f"rc={code} merge_calls={n_m[0]}")

        # ---- 簇 C 拍 3：GC 真实归档 + 空归档告警（P-004 / P-009） ----
        def nochange(messages, *, tag: str = "", **kw):
            if tag == "compact:merge":
                return FakeCall({}, "{}")
            return fake_chat(messages, tag=tag, **kw)

        with tempfile.TemporaryDirectory() as raw9:
            tmp9 = pathlib.Path(raw9)
            redirect(tmp9)
            cc.ensure_service = lambda **kw: True
            big_state = {
                "目标": "归档测试",
                "待办": ["待办条目" * 3] * 10,
                "已完成": ["已完成项" * 3] * 10,
                "红线": [],
                "决定": [{"内容": f"决定{i}：" + "理由" * 5, "理由": "理由"} for i in range(15)],
                "锚点": [f"路径 02_文档/示例{i}.md" + "x" * 20 for i in range(40)],
            }
            cc.STATE_PATH.write_text(json.dumps(big_state, ensure_ascii=False), encoding="utf-8")
            cc.CURSOR_PATH.write_text(json.dumps({"last_seq": 0}), encoding="utf-8")
            cc.chat = nochange
            code = run(["--session", str(syn), "--turn", "8", "--no-preflight",
                        "--no-merge", "--gc-budget", "600"])
            archive = (json.loads(cc.ARCHIVE_PATH.read_text(encoding="utf-8"))
                       if cc.ARCHIVE_PATH.exists() else {})
            hot = json.loads(cc.STATE_PATH.read_text(encoding="utf-8"))
            case("GC 超预算时真实生成归档",
                 code == 0 and any(archive.get(k) for k in archive),
                 f"rc={code} archive_keys={sorted(archive)}")
            case("归档守恒通过",
                 not cc.verify_gc_v2(big_state, hot, archive),
                 f"{cc.verify_gc_v2(big_state, hot, archive)}")

        with tempfile.TemporaryDirectory() as raw10:
            tmp10 = pathlib.Path(raw10)
            redirect(tmp10)
            cc.ensure_service = lambda **kw: True
            long_anchor = "路径 超长锚点" + "y" * 3000
            stuck_state = {"目标": "", "待办": ["请按 " + long_anchor + " 执行"],
                           "已完成": [], "红线": [], "决定": [], "锚点": [long_anchor]}
            cc.STATE_PATH.write_text(json.dumps(stuck_state, ensure_ascii=False),
                                     encoding="utf-8")
            cc.CURSOR_PATH.write_text(json.dumps({"last_seq": 0}), encoding="utf-8")
            cc.chat = nochange
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = run(["--session", str(syn), "--turn", "8", "--no-preflight",
                            "--no-merge", "--gc-budget", "500"])
            out = buf.getvalue()
            case("GC 超预算但无可回收时打印告警",
                 code == 0 and "无可回收" in out and not cc.ARCHIVE_PATH.exists(),
                 f"rc={code} warn={'无可回收' in out} archive={cc.ARCHIVE_PATH.exists()}")

        # ---- P-069 漂移回归：merge prompt 必须与 changeset_schema 同口径 ----
        prompt_txt = (HERE / "merge_prompt_v3.txt").read_text(encoding="utf-8")
        schema_txt = (HERE / "changeset_schema.json").read_text(encoding="utf-8")
        case("merge prompt 使用 idx/expect 对象",
             '"idx"' in prompt_txt and "expect" in prompt_txt
             and "整数序号列表" not in prompt_txt,
             "prompt 仍在说整数序号列表")
        case("changeset schema 要求 idx/expect",
             '"expect"' in schema_txt and '"idx"' in schema_txt, "schema 缺字段")

        # ---- P-070：本轮做完补进已完成 + 红线软检查 ----
        base70 = {"目标": "", "待办": ["中文为主 + 英文术语"], "已完成": [],
                  "红线": [], "决定": [], "锚点": []}
        items70 = [{"index": 1, "obj": {
            "做完": ["中文为主 + 英文术语", "设计 S1.5 细节取材流程"],
            "用户要求": ["道具进3可以", "询问S2组提示词准备怎么做"]}}]
        merged70 = cc.merge_completed(base70, items70)
        case("本轮做完并入已完成且从待办移除",
             merged70["已完成"] == ["中文为主 + 英文术语", "设计 S1.5 细节取材流程"]
             and merged70["待办"] == [], f"state={merged70}")
        case("红线软检查只报约束性要求",
             cc.redline_missing(base70, items70) == ["道具进3可以"],
             f"missing={cc.redline_missing(base70, items70)}")
        case("并入后状态仍通过守恒校验",
             not cc.validate_state(merged70, merged70),
             str(cc.validate_state(merged70, merged70)))
        red70 = cc.merge_redlines(base70, items70)
        case("约束性用户要求确定性并入红线",
             red70["红线"] == ["道具进3可以"], f"红线={red70['红线']}")
        case("红线并入按 norm 去重",
             cc.merge_redlines({"红线": ["道具进3可以"]}, items70)["红线"] == ["道具进3可以"],
             "重复并入未去重")

        # ---- P-005 长尾策略：块间 deadline / checkpoint 续跑 ----
        units2 = ["甲" * 4000, "乙" * 4000]
        d = cc.summarize_turn(units2, [], deadline=time.monotonic() - 1)
        case("summarize_turn 超 deadline 返回 deferred",
             d.get("deferred") is True and d["items"] == [], f"result={d}")
        d2 = cc.summarize_turn(units2, [], resume_items=[], done_chunks=0)
        case("summarize_turn 无 deadline 时补完两块",
             d2.get("deferred") is False and len(d2["items"]) == 2,
             f"deferred={d2.get('deferred')} items={len(d2['items'])}")

        with tempfile.TemporaryDirectory() as raw11:
            tmp11 = pathlib.Path(raw11)
            redirect(tmp11)
            cc.ensure_service = lambda **kw: True
            # Windows time.monotonic 分辨率 ~15.6ms，1ms 预算区分不出来；注入可控时钟
            real_time = cc.time
            seq = {"n": 0}

            def mono():
                seq["n"] += 1
                return 0.0 if seq["n"] == 1 else 1000.0

            cc.time = types.SimpleNamespace(
                monotonic=mono, strftime=real_time.strftime, time=real_time.time,
                sleep=real_time.sleep, perf_counter=real_time.perf_counter)
            try:
                code = run(["--session", str(syn), "--turn", "8", "--no-preflight",
                            "--sync-budget", "300"])
            finally:
                cc.time = real_time
            progress = tmp11 / "第8轮.blocks.jsonl"
            case("超同步预算退出码 76", code == 76, f"实际 {code}")
            case("超预算写 checkpoint 且不提交状态",
                 progress.exists() and not cc.STATE_PATH.exists(),
                 f"progress={progress.exists()} state={cc.STATE_PATH.exists()}")
            code2 = run(["--session", str(syn), "--turn", "8", "--no-preflight",
                         "--sync-budget", "999"])
            case("续跑后完成并清理 checkpoint",
                 code2 == 0 and not progress.exists() and cc.STATE_PATH.exists(),
                 f"rc={code2} progress={progress.exists()} state={cc.STATE_PATH.exists()}")

        # ---- P-071 GPU 预检判定 ----
        g = REAL_GPU_DECISION
        ok_comfy = g({"comfyui": True, "loaded": False, "vram_free_mb": 20000})[0] == 77
        case("GPU 预检：ComfyUI 占用拒绝", ok_comfy, f"判定={g({'comfyui': True, 'loaded': False, 'vram_free_mb': 20000})}")
        loaded_low = {"comfyui": False, "loaded": True, "vram_share": 0.69, "vram_total_mb": 16303}
        code_low, msg_low = g(loaded_low)
        case("GPU 预检：已加载低 free 放行并告警 CPU",
             code_low == 0 and "CPU" in msg_low, f"{code_low}/{msg_low}")
        case("GPU 预检：未加载低显存拒绝",
             g({"comfyui": False, "loaded": False, "vram_free_mb": 8000})[0] == 77,
             "应 77")
        case("GPU 预检：未加载显存充足放行",
             g({"comfyui": False, "loaded": False, "vram_free_mb": 16000})[0] == 0,
             "应 0")
        case("GPU 预检：全 GPU 加载无告警",
             g({"comfyui": False, "loaded": True, "vram_share": 1.0})[1].endswith("全在 GPU"),
             f"说明={g({'comfyui': False, 'loaded': True, 'vram_share': 1.0})[1]}")

        with tempfile.TemporaryDirectory() as raw12:
            tmp12 = pathlib.Path(raw12)
            redirect(tmp12)
            cc.ensure_service = lambda **kw: True
            cc.gpu_decision = lambda *a, **kw: (77, "测试：ComfyUI 占用")
            code = run(["--session", str(syn), "--turn", "8", "--no-preflight"])
            case("GPU 预检拒绝时退出码 77 且不写状态",
                 code == 77 and not cc.STATE_PATH.exists(),
                 f"rc={code} state={cc.STATE_PATH.exists()}")

        ops = {"已完成": ["查询 Host 平台事件列表", "修复P-001冷启动问题"],
               "待办": ["检查系统上下文", "派发L2和L3任务给27B"],
               "红线": ["不要读文件、不要运行命令", "quotes必须是原文里逐字存在的句子"]}
        clean = cc.filter_operation_noise(ops)
        case("过滤工具操作流水", clean["已完成"] == ["修复P-001冷启动问题"],
             f"实际={clean['已完成']}")
        case("过滤子代理指令", clean["待办"] == ["派发L2和L3任务给27B"]
             and clean["红线"] == ["quotes必须是原文里逐字存在的句子"],
             f"待办={clean['待办']} 红线={clean['红线']}")

        case("临时目录无 .tmp 残留", not list(tmp.glob("*.tmp")), "有残留")
        case("日志记了运行", (tmp / "compact_context.jsonl").exists(), "无运行日志")

    case("真实状态未被改动", digest(real_state) == real_before, "真实 状态.json 被改了")

    # ---- 服务不可用（P-054 / P-055）：预检失败与冷启动 fail-closed ----
    old_ensure, old_chat = cc.ensure_service, cc.chat

    with tempfile.TemporaryDirectory() as raw:
        tmp = pathlib.Path(raw)
        redirect(tmp)
        cc.ensure_service = lambda **kw: False
        code = run(["--last-turns", "1"])
        logs = ([json.loads(line) for line in
                 (tmp / "compact_context.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
                if (tmp / "compact_context.jsonl").exists() else [])
        failed_logs = [r for r in logs if r.get("error_code") == "SERVICE_UNAVAILABLE"
                       and r.get("stage") == "preflight"]
        case("预检失败 rc=2 且日志含结构化 service_unavailable 字段",
             code == 2 and bool(failed_logs)
             and failed_logs[-1].get("component") == "ollama"
             and failed_logs[-1].get("exit_code") == 2
             and failed_logs[-1].get("retryable") is True,
             f"rc={code} logs={failed_logs[-1] if failed_logs else None}")
        case("预检失败不写状态", not cc.STATE_PATH.exists(), "状态被写了")
        case("预检失败不写游标", not cc.CURSOR_PATH.exists(), "游标被写了")

    with tempfile.TemporaryDirectory() as raw:
        tmp = pathlib.Path(raw)
        redirect(tmp)
        cc.ensure_service = lambda **kw: True

        def boom(*a, **kw):
            if kw.get("tag") == "compact:cold":
                raise QwenError("模拟冷启动服务不可用")
            return fake_chat(*a, **kw)

        cc.chat = boom
        code = run(["--last-turns", "1"])
        cold_logs = [json.loads(line) for line in
                     (tmp / "compact_context.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        service_logs = [r for r in cold_logs if r.get("error_code") == "SERVICE_UNAVAILABLE"
                        and r.get("stage") == "cold_start"]
        case("冷启动模型失败时 rc≠0 且日志含服务不可用字段",
             code != 0 and bool(service_logs), f"rc={code} logs={service_logs[-1] if service_logs else None}")
        case("冷启动失败不提交状态", not cc.STATE_PATH.exists(), "状态被提交了")
        case("冷启动失败不推进游标", not cc.CURSOR_PATH.exists(), "游标被推进了")

    with tempfile.TemporaryDirectory() as raw:
        tmp = pathlib.Path(raw)
        redirect(tmp)
        cc.ensure_service = lambda **kw: True
        cc.STATE_PATH.write_text(json.dumps(
            {"目标": "已有目标", "待办": [], "已完成": [], "红线": [], "决定": [], "锚点": []},
            ensure_ascii=False), encoding="utf-8")
        syn2 = tmp / "synthetic_l2.v4.jsonl.zstd"
        syn2.write_bytes(zstandard.ZstdCompressor().compress(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in SYNTHETIC_EVENTS).encode("utf-8")))

        def boom_l2(*a, **kw):
            raise QwenError("模拟 L2 服务不可用")

        cc.chat = boom_l2
        code = run(["--session", str(syn2), "--turn", "8", "--no-preflight"])
        l2_logs = [json.loads(line) for line in
                   (tmp / "compact_context.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        l2_service = [r for r in l2_logs if r.get("error_code") == "SERVICE_UNAVAILABLE"
                      and r.get("stage") == "l2"]
        case("L2 服务不可用中止且日志含 l2 结构化字段",
             code != 0 and bool(l2_service) and not (tmp / "第8轮.raw.json").exists(),
             f"rc={code} logs={l2_service[-1] if l2_service else None}")

    cc.ensure_service, cc.chat = old_ensure, old_chat

    # ---- P-052：thin 块触发一次重摘 ----
    source = "原文第一句。原文第二句。"
    calls: list[str] = []

    def fake_summary(text: str, *, max_facts: int, fix: str = ""):
        calls.append(fix)
        if len(calls) == 1:
            return {"summary": "短", "quotes": [text], "facts": [text]}, 100
        return {"summary": "这是一段足够长的摘要，包含原文里的关键信息与结论，长度超过三十个字。",
                "quotes": [text], "facts": [text]}, 100

    old_summarize = cc.summarize_block
    cc.summarize_block = fake_summary
    try:
        thin_item, thin_failed = cc._summarize_once([source], 1, split_from=None)
    finally:
        cc.summarize_block = old_summarize
    case("thin 块触发一次重摘且最终不软失败",
         len(calls) == 2 and not thin_failed and "summary_thin" not in thin_item["soft_failed"],
         f"calls={len(calls)} soft={thin_item['soft_failed']} failed={thin_failed}")

    # ---- 截断分类：done_reason=length 时抛带 truncated 标记的错误（不再把坏输出塞回重试）----
    import qwen_client as qc

    def fake_truncated_post(body, timeout):
        return {"message": {"content": '{"a":'}, "done_reason": "length",
                "prompt_eval_count": 7800, "eval_count": 200,
                "eval_duration": 10_000_000_000}

    old_post = qc._post
    qc._post = fake_truncated_post
    try:
        try:
            qc.chat([{"role": "user", "content": "x"}], json_mode=True, num_ctx=8192,
                    num_predict=1600, tag="test:trunc", retries=0, timeout=30)
            trunc_ok, trunc_info = False, "没有抛异常"
        except qc.QwenError as exc:
            trunc_ok = bool(getattr(exc, "truncated", False)) and "长度上限" in str(exc)
            trunc_info = str(exc)[:120]
    finally:
        qc._post = old_post
    case("done_reason=length 时抛截断错误", trunc_ok, trunc_info)

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    for name, ok, msg in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {msg}" if not ok and msg else ""))
    print(f"\n合计 {passed} 过 / {failed} 没过（共 {len(results)}）")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
