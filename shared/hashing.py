"""哈希工具：文件 SHA256、目录树快照。"""
from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: str | Path) -> str:
    """逐块计算文件的 SHA256 十六进制摘要。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def snapshot_tree(root: str | Path, ignore_dirs: tuple[str, ...] = ("__pycache__",)) -> dict[str, str]:
    """返回 {相对路径(posix): sha256}，按路径排序，用于检测基线被改动。"""
    root = Path(root)
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in ignore_dirs for part in rel.parts):
            continue
        if path.suffix == ".pyc":
            continue
        result[rel.as_posix()] = sha256_file(path)
    return result
