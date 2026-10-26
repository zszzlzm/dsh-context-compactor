"""本地 Qwen3.8-27B 客户端。

设计取向：质量优先，但先走便宜的档，不行再升级：
- 默认 think=False，可传 "low"/"medium"；由调用方按失败情况升档；
- num_ctx（上下文窗口）默认 4096；调用方可按估算升到 8192（改这个值会触发重载）；
- num_predict（生成上限）由调用方按块大小给；撞上限会被调用方诊断出来；
- JSON 模式 + 解析失败自动纠正重试；
- keep_alive 30m，批量跑时避免反复冷加载（冷加载约 47s，很贵）；
- 每次调用记一行日志到 out/qwen_calls.jsonl，便于算吞吐和失败率；
- 只走 /api/chat，不依赖 ollama CLI，中文按 UTF-8 发送。

用法（自检）：
    .\\.venv\\Scripts\\python.exe tools\\qwen_client.py --ping
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from service_wait import ensure_result, wait_until

URL = os.environ.get("QWEN_URL", "http://127.0.0.1:11434/api/chat")
MODEL = os.environ.get("QWEN_MODEL", "qwen3.8:27b-mtp-q4_K_M")
ROOT = pathlib.Path(__file__).resolve().parents[1]
LOG = ROOT / "06_日志" / "qwen_calls.jsonl"
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUPS = 3

_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


class QwenError(RuntimeError):
    pass


@dataclass
class Call:
    """一次调用的结果与成本。"""
    obj: object
    text: str
    thinking: str
    seconds: float
    prompt_tokens: int
    eval_tokens: int
    attempts: int
    eval_seconds: float = 0.0
    num_ctx: int = 4096
    num_predict: int = 0
    tag: str = ""
    done_reason: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def tok_s(self) -> float:
        return self.eval_tokens / self.eval_seconds if self.eval_seconds > 0 else 0.0

    @property
    def truncated(self) -> bool:
        return self.done_reason == "length"


def parse_json(text: str):
    s = text.strip()
    if s.startswith("```"):
        s = _FENCE.sub("", s).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            return json.loads(s[i:j + 1])
        raise


def _tags_url() -> str:
    """由 chat URL 推出 /api/tags 探活地址。"""
    return URL.rsplit("/api/", 1)[0] + "/api/tags"


def service_alive(probe_timeout: float = 1.0) -> bool:
    """探活 /api/tags；连接拒绝/超时都当作未就绪。"""
    try:
        with urllib.request.urlopen(_tags_url(), timeout=probe_timeout) as resp:
            return getattr(resp, "status", 200) == 200
    except Exception:
        return False


def _exe_path() -> str:
    return os.environ.get("OLLAMA_EXE") or os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")


def ensure_service(*, probe_timeout: float = 1.0, wait_seconds: float = 15.0,
                   autostart: bool | None = None, exe: str | None = None) -> bool:
    """探测 Ollama 是否就绪；未就绪时可尝试拉起 `ollama serve`。返回是否可用。

    设计：探活失败不是异常，而是返回值——调用方（驱动/管道）据此决定退出还是继续。
    连接被拒不重试（AWS 构建块：只对瞬时错误重试）；拉起后最多等 wait_seconds。
    """
    if autostart is None:
        autostart = os.environ.get("QWEN_NO_AUTOSTART", "") != "1"

    if service_alive(probe_timeout):
        return True
    if not autostart:
        return False
    exe_path = exe or _exe_path()
    if not os.path.exists(exe_path):
        return False
    try:
        subprocess.Popen([exe_path, "serve"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=0x08000000)
    except Exception:
        return False
    return wait_until(lambda: service_alive(probe_timeout),
                      timeout=wait_seconds, interval=0.5)


def _is_refused(err: Exception) -> bool:
    """连接被拒（服务未起）与超时/断开要区别对待。"""
    text = str(err).lower()
    return "10061" in text or "connection refused" in text or "积极拒绝" in text


_SERVICE_ENSURED = False


def _post(body: dict, timeout: float) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(URL, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _rotate_log() -> None:
    """超过 LOG_MAX_BYTES 就把调用日志滚到 .1–.3（最旧一份丢弃）。"""
    if not LOG.exists() or LOG.stat().st_size < LOG_MAX_BYTES:
        return
    for i in range(LOG_BACKUPS, 0, -1):
        src = LOG.with_name(LOG.name + f".{i}")
        dst = LOG.with_name(LOG.name + f".{i + 1}")
        if not src.exists():
            continue
        if i == LOG_BACKUPS:
            src.unlink()
        else:
            src.replace(dst)
    LOG.replace(LOG.with_name(LOG.name + ".1"))


def _log(entry: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    _rotate_log()
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def chat(
    messages: list[dict],
    *,
    think: bool | str = False,
    json_mode: bool = True,
    schema: dict | None = None,
    num_ctx: int = 4096,
    num_predict: int = 900,
    temperature: float = 0.2,
    seed: int | None = None,
    deterministic: bool = False,
    tag: str = "",
    timeout: float = 900,
    retries: int = 2,
    keep_alive: str = "30m",
) -> Call:
    """发一次 chat；失败重试，JSON 不合法时追加纠正消息再试。

    schema 传 JSON Schema 时走 Ollama 的结构化输出（约束解码）——
    形状不对的 token 在采样阶段就被禁止，比"提示词 + 事后校验"强一个档。

    deterministic=True 时强制 temperature=0、seed 默认 42，适合评估/对照类调用；
    num_ctx 需调用方自己传固定值（不同取值会改变数值路径）。
    """
    eff_temperature = 0.0 if deterministic else temperature
    eff_seed = seed if seed is not None else (42 if deterministic else None)
    body = {
        "model": MODEL,
        "messages": list(messages),
        "stream": False,
        "think": think,
        "keep_alive": keep_alive,
        "options": {
            "num_ctx": num_ctx,
            "temperature": eff_temperature,
            "num_predict": num_predict,
            "top_p": 0.9,
        },
    }
    if eff_seed is not None:
        body["options"]["seed"] = eff_seed
    # 省电模式：把计算线程压到能效核数量（QWEN_NUM_THREAD=8 配 E 核亲和性）
    _nt = int(os.environ.get("QWEN_NUM_THREAD", "0") or 0)
    if _nt > 0:
        body["options"]["num_thread"] = _nt
    if schema is not None:
        body["format"] = schema
    elif json_mode:
        body["format"] = "json"

    t_start = time.time()
    attempts = 0
    last_err = ""
    while attempts <= retries:
        attempts += 1
        t0 = time.time()
        try:
            res = _post(body, timeout)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            last_err = f"HTTP {e.code}: {detail}"
            if e.code == 400 and "think" in body:
                body.pop("think", None)  # 旧服务不支持 think 时退化
                continue
            if e.code == 400 and "format" in body:
                body.pop("format", None)
                continue
            time.sleep(2 * attempts)
            continue
        except Exception as e:  # 超时、连接重置等
            last_err = f"{type(e).__name__}: {e}"
            if _is_refused(e):
                # 服务没起：拉起一次再试；拉不起来就快速失败（不把重试预算浪费在不可用服务上）
                global _SERVICE_ENSURED
                if not _SERVICE_ENSURED:
                    _SERVICE_ENSURED = True
                    if ensure_service():
                        attempts -= 1
                        continue
                raise QwenError(
                    "Ollama 服务不可用（连接被拒）。先启动：\n"
                    "  $env:OLLAMA_MODELS='C:\\path\\to\\ollama-models'\n"
                    "  Start-Process \"$env:LOCALAPPDATA\\Programs\\Ollama\\ollama.exe\" -ArgumentList 'serve'"
                ) from e
            time.sleep(2 * attempts)
            continue

        dt = time.time() - t0
        msg = res.get("message", {}) or {}
        content = msg.get("content", "") or ""
        thinking = msg.get("thinking", "") or ""
        ec = int(res.get("eval_count", 0) or 0)
        ed = res.get("eval_duration", 0) or 1
        done_reason = str(res.get("done_reason", "") or "")
        entry = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "tag": tag,
            "think": body.get("think"),
            "json": "format" in body,
            "seconds": round(dt, 1),
            "prompt_tokens": int(res.get("prompt_eval_count", 0) or 0),
            "eval_tokens": ec,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
            "done_reason": done_reason,
            "temperature": eff_temperature,
            "seed": eff_seed,
            "tok_s": round(ec / (ed / 1e9), 1) if ed else 0,
            "attempt": attempts,
            "ok": True,
        }
        try:
            obj = parse_json(content) if json_mode else content
        except json.JSONDecodeError as e:
            entry["ok"] = False
            entry["error"] = f"json: {e}"
            entry["error_kind"] = "truncated" if done_reason == "length" else "json"
            _log(entry)
            if done_reason == "length":
                err = QwenError(
                    f"输出达到长度上限被截断（prompt={entry['prompt_tokens']} / "
                    f"num_ctx={num_ctx} / num_predict={num_predict}）")
                err.truncated = True
                err.prompt_tokens = entry["prompt_tokens"]
                raise err from e
            last_err = f"JSON 不合法: {e}"
            if attempts <= retries:
                body["messages"] = body["messages"] + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": "上面的输出不是合法 JSON。请只输出一个 JSON 对象，不要任何解释、不要代码块。"},
                ]
            continue

        _log(entry)
        return Call(
            obj=obj,
            text=content,
            thinking=thinking,
            seconds=dt,
            prompt_tokens=int(res.get("prompt_eval_count", 0) or 0),
            eval_tokens=ec,
            attempts=attempts,
            eval_seconds=ed / 1e9 if ed else 0.0,
            num_ctx=num_ctx,
            num_predict=num_predict,
            tag=tag,
            done_reason=done_reason,
            extra={"total_s": round(time.time() - t_start, 1)},
        )

    raise QwenError(f"调用失败（{attempts} 次）：{last_err}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ping", action="store_true", help="跑一次最小对话")
    ap.add_argument("--ensure-service", action="store_true",
                    help="探活并在未就绪时自动拉起 Ollama，打印 JSON 结果")
    ap.add_argument("--think", default="low")
    args = ap.parse_args()
    think = False if args.think.lower() in ("false", "0", "no") else args.think

    if args.ensure_service:
        before = service_alive()
        started_at = time.time()
        ok = ensure_service()
        result = ensure_result(already_up=before, started=(not before and ok), ok=ok,
                               seconds=time.time() - started_at, exe=_exe_path(),
                               tags_url=_tags_url())
        print(json.dumps(result, ensure_ascii=False))
        return 0 if ok else 2

    if args.ping:
        r = chat(
            [{"role": "user", "content": '只输出 JSON：{"ok": true, "model": "?"}'}],
            think=think, num_predict=120, tag="ping",
        )
        print(json.dumps(r.obj, ensure_ascii=False))
        print(f"seconds={r.seconds:.1f} prompt={r.prompt_tokens} eval={r.eval_tokens} tok/s={r.tok_s:.1f}")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
