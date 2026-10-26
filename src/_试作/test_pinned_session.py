"""P-065 验收：pinned_session_27b.py（27B 产物）。"""
from __future__ import annotations

import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pinned_session as ps  # noqa: E402


def test_sha256_known():
    with tempfile.TemporaryDirectory() as raw:
        p = pathlib.Path(raw) / "a.bin"
        p.write_bytes(b"abc")
        got = ps.sha256_file(p)
        want = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        return got == want, f"实际={got}"


def test_verify_prefix_case_insensitive():
    with tempfile.TemporaryDirectory() as raw:
        p = pathlib.Path(raw) / "a.bin"
        p.write_bytes(b"abc")
        full = ps.sha256_file(p)
        got = ps.verify_pin(p, full[:12].upper())
        return got == full, f"实际={got}"


def test_verify_mismatch_raises():
    with tempfile.TemporaryDirectory() as raw:
        p = pathlib.Path(raw) / "a.bin"
        p.write_bytes(b"abc")
        try:
            ps.verify_pin(p, "deadbeef")
            return False, "没有抛异常"
        except ValueError as exc:
            return "SHA256" in str(exc), f"消息={exc}"


def test_empty_prefix_returns_hash():
    with tempfile.TemporaryDirectory() as raw:
        p = pathlib.Path(raw) / "a.bin"
        p.write_bytes(b"abc")
        full = ps.sha256_file(p)
        return ps.verify_pin(p, "  ") == full, "空前缀未直接返回 hash"


def test_describe_shape():
    with tempfile.TemporaryDirectory() as raw:
        p = pathlib.Path(raw) / "a.bin"
        p.write_bytes(b"abc")
        info = ps.describe(p)
        return (isinstance(info.get("path"), str) and info.get("bytes") == 3
                and info.get("sha256") == ps.sha256_file(p)), f"实际={info}"


CASES = [test_sha256_known, test_verify_prefix_case_insensitive, test_verify_mismatch_raises,
         test_empty_prefix_returns_hash, test_describe_shape]


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
