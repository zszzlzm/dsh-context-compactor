"""通用派活器：读一份契约文本，交给本地 27B 写代码，产物落盘。

用法：
    .\\.venv\\Scripts\\python.exe src\_试作\\delegate.py --spec spec_core.txt --out compact_core_27b.py
    .\\.venv\\Scripts\\python.exe src\_试作\\delegate.py --fix <文件> --out <文件>

只负责发活儿和存盘，不判断对错——对错由对应的 test_*.py 决定。
"""
from __future__ import annotations

import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from qwen_client import QwenError, chat  # noqa: E402

import delegate_checks  # noqa: E402

CODE_BLOCK = re.compile(r"```[a-zA-Z]*\s*\n(.*?)```", re.S)


def strip_fences(text: str) -> str:
    """取最外层围栏：从第一行以 ``` 开头的行，到最后一行内容恰为 ``` 的行。

    不能用正则匹配 ```...```——模型常在正文里提到 ```json 这类围栏，
    正则会提前在那一对中间闭合，把正文截断。
    """
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip().startswith("```")), None)
    if start is None:
        return text.strip()
    closes = [i for i, ln in enumerate(lines) if i > start and ln.strip() == "```"]
    if not closes:
        return "\n".join(lines[start + 1:]).strip()
    return "\n".join(lines[start + 1: closes[-1]]).strip()


def unescape_code(text: str) -> str:
    """模型有时把整段代码的换行写成字面量 \n；只在这种畸形情况下还原。"""
    if text.count("\n") > 5:
        return text
    return re.sub(r"(?<!\\)\\n", "\n", text).replace('\\"', '"')


def arg(name: str, default: str | None = None) -> str | None:
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def args_all(name: str) -> list[str]:
    """收集重复参数（如多个 --check）。"""
    return [sys.argv[i + 1] for i, value in enumerate(sys.argv) if value == name and i + 1 < len(sys.argv)]


def main() -> int:
    if "--fix" in sys.argv:
        raw = pathlib.Path(arg("--fix")).read_text(encoding="utf-8")
        out = pathlib.Path(arg("--out") or arg("--fix"))
        out.write_text(unescape_code(strip_fences(raw)) + "\n", encoding="utf-8")
        print(f"已还原转义并写回 {out}")
        return 0

    spec_path = pathlib.Path(arg("--spec") or HERE / "spec_slim.txt")
    out = pathlib.Path(arg("--out") or HERE / "slim_27b.py")
    spec = spec_path.read_text(encoding="utf-8")
    for warning in delegate_checks.lint_spec(spec):
        print(f"  ⚠ 契约提醒：{warning}")
    note = arg("--note")
    checks = args_all("--check")
    max_rounds = max(1, int(arg("--max-rounds", "2") or 2))
    base_spec = spec + ("\n\n============ 反馈（上一版没过的验收） ============\n" + note if note else "")
    feedback = ""
    print(f"派活给本地 27B：{spec_path.name} -> {out.name}，最多 {max_rounds} 轮，"
          f"验收命令 {len(checks)} 条，预计 1–3 分钟……", flush=True)

    raw = ""
    for round_no in range(1, max_rounds + 1):
        prompt = base_spec + ("\n\n" + feedback if feedback else "")
        print(f"  第 {round_no}/{max_rounds} 轮生成……", flush=True)
        try:
            res = chat(
                [{"role": "system", "content": "你是严谨的工程师，严格按用户给的契约输出；"
                                              "契约要求只输出代码就只给代码，要求 Markdown 就只给 Markdown。"},
                 {"role": "user", "content": prompt}],
                think=False, json_mode=False, num_ctx=int(arg("--num-ctx", 8192)),
                num_predict=int(arg("--max-tokens", 3000)),
                temperature=0.1, tag=out.stem, timeout=900,
            )
        except QwenError as exc:
            print("调用失败:", exc)
            return 1
        raw = res.text
        code = unescape_code(raw if "--raw" in sys.argv else strip_fences(raw))
        out.write_text(code + "\n", encoding="utf-8")
        out.with_suffix(f".round{round_no}.raw.txt").write_text(raw, encoding="utf-8")
        print(f"  已写 {out}（{len(code)} 字符）"
              f"；耗时 {res.seconds:.1f}s / {res.eval_tokens} tok / {res.tok_s:.1f} tok·s⁻¹")
        if not checks:
            break
        ok, failures, _ = delegate_checks.run_checks(checks)
        print(f"  验收：{'通过' if ok else f'未过 {len(failures)} 项'}")
        if ok:
            break
        feedback = delegate_checks.build_feedback(failures, code)
        if round_no == max_rounds:
            print("\n".join(failures)[:1500])

    out.with_suffix(".raw.txt").write_text(raw, encoding="utf-8")
    if checks:
        ok, failures, _ = delegate_checks.run_checks(checks)
        if not ok:
            print(f"最终验收仍未通过（{len(failures)} 项）：")
            print("\n".join(failures)[:1500])
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
