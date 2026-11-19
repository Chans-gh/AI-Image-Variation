import json
import stat
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from shared.artifacts import verify_delivery
from shared.paths import safe_path

from .errors import require
from .imaging import sha256
from .resources import contracts_root


def now():
    return datetime.now(UTC).isoformat()


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def inventory(root, package_id):
    root = Path(root)
    entries = []
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), "UNSAFE_PATH", "交付目录不能含符号链接")
        if not path.is_file() or path.name == "delivery-package.json":
            continue
        name = path.relative_to(root).as_posix()
        data = safe_path(root, name).read_bytes()
        entries.append(
            {
                "path": name,
                "kind": "image" if path.suffix.lower() in {".png", ".jpg", ".webp"} else "metadata",
                "bytes": len(data),
                "sha256": sha256(data),
            }
        )
    result = {
        "package_id": package_id,
        "package_type": "image_variations",
        "root": ".",
        "manifest_path": "manifest.json",
        "entries": entries,
        "total_files": len(entries),
        "total_bytes": sum(x["bytes"] for x in entries),
        "created_at": now(),
    }
    write_json(root / "delivery-package.json", result)
    verify_delivery(root, contracts_root())
    return result


def package_artifact(root, target):
    root, target = Path(root), Path(target)
    require(not target.resolve().is_relative_to(root.resolve()), "UNSAFE_PATH", "ZIP 须写到产物目录外")
    verify_delivery(root, contracts_root())
    target.parent.mkdir(parents=True, exist_ok=True)
    require(not target.exists(), "ARTIFACT_EXISTS", "禁止覆盖已存在的交付 ZIP")
    with ZipFile(target, "x", ZIP_DEFLATED) as z:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                name = path.relative_to(root).as_posix()
                z.writestr(name, safe_path(root, name).read_bytes())
    return target


def import_package(archive, target, max_bytes=256 * 1024 * 1024):
    """No extraction until every entry is bounded and collision-free."""
    target = Path(target)
    require(not target.exists(), "ARTIFACT_EXISTS", "导入目标必须是新目录")
    with ZipFile(archive) as z:
        members = z.infolist()
        require(len(members) <= 1024, "INVALID_PACKAGE", "ZIP 条目过多")
        names, total = set(), 0
        for item in members:
            require(not item.is_dir(), "INVALID_PACKAGE", "包只应包含文件条目")
            safe_path(target, item.filename)
            require(not stat.S_ISLNK(item.external_attr >> 16), "UNSAFE_PATH", "ZIP 不允许符号链接")
            folded = item.filename.casefold()
            require(folded not in names, "INVALID_PACKAGE", "ZIP 路径冲突")
            require(
                not any(folded.startswith(n + "/") or n.startswith(folded + "/") for n in names),
                "INVALID_PACKAGE",
                "ZIP 文件和目录路径冲突",
            )
            names.add(folded)
            total += item.file_size
            require(
                total <= max_bytes
                and item.file_size <= max_bytes
                and item.file_size / max(1, item.compress_size) <= 1000,
                "INVALID_PACKAGE",
                "ZIP 展开大小或压缩比超限",
            )
        target.mkdir(parents=True)
        try:
            for item in members:
                path = safe_path(target, item.filename)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(z.read(item))
            verify_delivery(target, contracts_root())
        except Exception:
            import shutil

            shutil.rmtree(target)
            raise
    return target
