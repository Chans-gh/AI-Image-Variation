"""交付包清单与 ZIP 安全（D-27 / D-28）。

语义来源：references/packaging.md §3–§4、contracts/delivery-package.schema.json。
本模块只实现机制，包内"该有哪些文件"由 Skill 决定。
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from .errors import IVError
from .hashing import sha256_file
from .jsonio import read_json, write_json
from .paths import PathTraversalError, ensure_within, rel_posix, safe_resolve

MANIFEST_NAME = "delivery-package.json"

DEFAULT_ZIP_LIMITS = {
    "max_entry_bytes": 200 * 1024 * 1024,
    "max_total_bytes": 2 * 1024 * 1024 * 1024,
    "max_entries": 5000,
    "max_compression_ratio": 200,
}

KIND_BY_SUFFIX = {
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".json": "metadata",
    ".md": "text",
    ".html": "text",
    ".txt": "text",
    ".zip": "other",
}


def classify(rel: str) -> str:
    """按相对路径归类；covers/ 单独标注。"""
    if rel.startswith("covers/"):
        return "cover"
    if rel.startswith("research/"):
        return "research"
    return KIND_BY_SUFFIX.get(Path(rel).suffix.lower(), "other")


def scan_package_files(root: str | Path) -> list[str]:
    """列出包内所有文件（不含 delivery-package.json 自身），posix 相对路径升序。"""
    root_path = Path(root).resolve()
    files = [rel_posix(root_path, p) for p in root_path.rglob("*") if p.is_file()]
    return sorted(rel for rel in files if rel != MANIFEST_NAME)


def build_entries(root: str | Path) -> list[dict]:
    root_path = Path(root).resolve()
    entries = []
    for rel in scan_package_files(root_path):
        path = root_path / rel
        entry = {
            "path": rel,
            "kind": classify(rel),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        entries.append(entry)
    return entries


def build_manifest(root: str | Path, *, package_id: str, package_type: str,
                   root_label: str | None = None, created_at: str | None = None) -> dict:
    """生成 delivery-package 对象；写盘由 write_manifest 负责。

    必填字段与 contracts/delivery-package.schema.json 对齐：
    package_id / package_type / root / entries / manifest_path / total_files / created_at。
    """
    root_path = Path(root).resolve()
    entries = build_entries(root_path)
    if not entries:
        raise IVError("PACKAGE_MANIFEST_INVALID", "包内没有任何文件，无法生成清单")
    payload = {
        "package_id": package_id,
        "package_type": package_type,
        "root": root_label or root_path.name,
        "entries": entries,
        "manifest_path": "manifest.json",
        "total_files": len(entries),
        "total_bytes": sum(e["bytes"] for e in entries),
        "created_at": created_at or _utc_now(),
    }
    return payload


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_manifest(root: str | Path, manifest: dict) -> Path:
    return write_json(Path(root) / MANIFEST_NAME, manifest)


def verify_manifest(root: str | Path, manifest: dict | None = None) -> list[tuple[str, str]]:
    """校验清单：路径安全、哈希、字节数、覆盖率、total_files。

    返回 [(code, message)]（空列表=通过）。路径非法优先报 PATH_TRAVERSAL_BLOCKED，
    其余清单问题报 PACKAGE_MANIFEST_INVALID。
    """
    root_path = Path(root).resolve()
    if manifest is None:
        manifest_file = root_path / MANIFEST_NAME
        if not manifest_file.is_file():
            return [("PACKAGE_MANIFEST_INVALID", "缺 delivery-package.json")]
        manifest = read_json(manifest_file)
    problems: list[tuple[str, str]] = []
    declared: dict[str, dict] = {}
    for entry in manifest.get("entries", []):
        rel = entry.get("path", "")
        try:
            path = safe_resolve(root_path, rel)
        except PathTraversalError as exc:
            problems.append(("PATH_TRAVERSAL_BLOCKED", str(exc)))
            continue
        if not path.is_file():
            problems.append(("PACKAGE_MANIFEST_INVALID", f"清单引用的文件不存在: {rel}"))
            continue
        declared[rel] = entry
        if sha256_file(path) != entry.get("sha256"):
            problems.append(("PACKAGE_MANIFEST_INVALID", f"{rel}: 哈希不匹配"))
        if entry.get("bytes") is not None and path.stat().st_size != entry["bytes"]:
            problems.append(("PACKAGE_MANIFEST_INVALID", f"{rel}: 字节数不匹配"))
    on_disk = set(scan_package_files(root_path))
    undeclared = on_disk - set(declared)
    if undeclared:
        problems.append(("PACKAGE_MANIFEST_INVALID", f"存在未登记文件: {sorted(undeclared)[:5]}"))
    missing = set(declared) - on_disk
    if missing:
        problems.append(("PACKAGE_MANIFEST_INVALID", f"清单登记了不存在的文件: {sorted(missing)[:5]}"))
    if manifest.get("total_files") != len(declared):
        problems.append(("PACKAGE_MANIFEST_INVALID",
                         f"total_files={manifest.get('total_files')} != entries={len(declared)}"))
    return [(code, message) for code, message in sorted(set(problems))]


def _entry_is_illegal(name: str) -> bool:
    if name.startswith("/") or "\\" in name or "\x00" in name:
        return True
    if len(name) > 1 and name[1] == ":":
        return True
    parts = [p for p in name.rstrip("/").split("/") if p not in ("", ".")]
    return any(part == ".." for part in parts)


def export_zip(
    root: str | Path,
    zip_path: str | Path,
    manifest: dict | None = None,
    limits: dict | None = None,
) -> Path:
    """导出 ZIP：先写清单再打包；条目仅相对路径；ZIP 哈希写回 archive。"""
    root_path = Path(root).resolve()
    zip_path = Path(zip_path)
    manifest = manifest if manifest is not None else read_json(root_path / MANIFEST_NAME)
    problems = verify_manifest(root_path, manifest)
    if problems:
        raise IVError("PACKAGE_MANIFEST_INVALID", f"清单校验未通过: {problems}")
    limits = {**DEFAULT_ZIP_LIMITS, **(limits or {})}
    files = scan_package_files(root_path)
    if len(files) + 1 > limits["max_entries"]:
        raise IVError("ZIP_SLIP_BLOCKED", "条目数量超过上限")
    total = sum((root_path / rel).stat().st_size for rel in files)
    if total > limits["max_total_bytes"]:
        raise IVError("ZIP_SLIP_BLOCKED", "总大小超过上限")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for rel in files:
            path = root_path / rel
            if path.stat().st_size > limits["max_entry_bytes"]:
                raise IVError("ZIP_SLIP_BLOCKED", f"单条目过大: {rel}")
            archive.write(path, arcname=rel)
    archive_meta = {
        "path": zip_path.name,
        "sha256": sha256_file(zip_path),
        "format": "zip",
        "bytes": zip_path.stat().st_size,
    }
    manifest = dict(manifest)
    manifest["archive"] = archive_meta
    write_manifest(root_path, manifest)
    return zip_path


def inspect_zip(zip_path: str | Path, limits: dict | None = None) -> list[str]:
    """预检 ZIP：路径安全、条目数、解压总量与压缩比。返回问题码列表。"""
    limits = {**DEFAULT_ZIP_LIMITS, **(limits or {})}
    problems: list[str] = []
    try:
        with zipfile.ZipFile(zip_path) as archive:
            infos = archive.infolist()
            if len(infos) > limits["max_entries"]:
                problems.append("ZIP_SLIP_BLOCKED")
            total_uncompressed = 0
            for info in infos:
                if _entry_is_illegal(info.filename):
                    problems.append("ZIP_SLIP_BLOCKED")
                    continue
                if info.file_size > limits["max_entry_bytes"]:
                    problems.append("ZIP_SLIP_BLOCKED")
                total_uncompressed += info.file_size
                if info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > limits["max_compression_ratio"]:
                        problems.append("ZIP_SLIP_BLOCKED")
            if total_uncompressed > limits["max_total_bytes"]:
                problems.append("ZIP_SLIP_BLOCKED")
    except zipfile.BadZipFile:
        problems.append("ZIP_SLIP_BLOCKED")
    return sorted(set(problems))


def extract_zip_safe(zip_path: str | Path, dest: str | Path, limits: dict | None = None) -> None:
    """安全解压：先预检，再逐条解析到 dest 内，遇到同名文件不覆盖。"""
    problems = inspect_zip(zip_path, limits)
    if problems:
        raise IVError(problems[0], f"ZIP 预检失败: {problems}")
    dest_path = Path(dest)
    dest_path.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            target = ensure_within(dest_path, dest_path / info.filename)
            if target.exists():
                raise IVError("ZIP_SLIP_BLOCKED", f"目标已存在，拒绝覆盖: {info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
