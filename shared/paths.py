"""安全路径：所有产物路径必须落在给定根目录内。

对应 SPEC 01 §9.5 / packaging.md §4 与错误码 PATH_TRAVERSAL_BLOCKED。
"""
from __future__ import annotations

import os
from pathlib import Path


class PathTraversalError(ValueError):
    """路径逃出资产根目录；code 固定为 PATH_TRAVERSAL_BLOCKED。"""

    code = "PATH_TRAVERSAL_BLOCKED"


def is_illegal_component(rel: str) -> bool:
    """绝对路径、盘符、反斜杠、控制字符、空字节与 .. 分量一律非法。"""
    if not isinstance(rel, str) or not rel:
        return True
    if "\x00" in rel or "\\" in rel:
        return True
    if any(ord(ch) < 0x20 for ch in rel):
        return True
    if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        return True
    return any(part == ".." for part in rel.split("/"))


def safe_resolve(root: str | Path, rel: str, *, must_exist: bool = False) -> Path:
    """把相对路径解析到 root 内；越界抛 PathTraversalError。"""
    if is_illegal_component(rel):
        raise PathTraversalError(f"PATH_TRAVERSAL_BLOCKED: 非法相对路径 {rel!r}")
    root_path = Path(root).resolve()
    target = (root_path / rel).resolve()
    if target != root_path and root_path not in target.parents:
        raise PathTraversalError(f"PATH_TRAVERSAL_BLOCKED: {rel!r} 逃出根目录")
    if must_exist and not target.is_file():
        raise PathTraversalError(f"PATH_TRAVERSAL_BLOCKED: {rel!r} 不存在")
    return target


def ensure_within(root: str | Path, path: str | Path) -> Path:
    """校验绝对/相对路径均落在 root 内并返回绝对路径。"""
    root_path = Path(root).resolve()
    target = Path(path).resolve()
    if target != root_path and root_path not in target.parents:
        raise PathTraversalError(f"PATH_TRAVERSAL_BLOCKED: {path!r} 逃出根目录 {root_path}")
    return target


def rel_posix(root: str | Path, path: str | Path) -> str:
    """返回相对 root 的 posix 路径，并确认没有逃逸。"""
    return ensure_within(root, path).relative_to(Path(root).resolve()).as_posix()


def atomic_write(path: str | Path, data: bytes) -> Path:
    """先写临时文件再改名，避免半成品被当成完整产物。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    return path
