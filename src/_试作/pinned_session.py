"""pinned_session_27b.py — 会话快照钉住（P-065）。

只依赖标准库，不写文件、不打印、不联网。
"""

import hashlib
import os


def sha256_file(path) -> str:
    """以二进制读文件，返回 64 位小写十六进制 SHA256。

    路径不存在时让 FileNotFoundError 自然抛出。
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_pin(path, expected_sha_prefix: str) -> str:
    """校验文件 SHA256 前缀。

    - 若 expected_sha_prefix 去空白后为空，直接返回完整 hash；
    - 否则大小写不敏感地比较前缀，不匹配时抛 ValueError，
      消息里必须包含 "SHA256" 与实际 hash；
    - 匹配时返回完整 hash。
    """
    full_hash = sha256_file(path)
    prefix = (expected_sha_prefix or "").strip()
    if not prefix:
        return full_hash
    if not full_hash.lower().startswith(prefix.lower()):
        raise ValueError(
            "SHA256 mismatch: expected prefix {!r}, actual SHA256 is {}".format(
                prefix, full_hash
            )
        )
    return full_hash


def describe(path) -> dict:
    """返回 {"path": str(path), "sha256": <完整hash>, "bytes": <文件字节数>}。"""
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "bytes": os.path.getsize(path),
    }
