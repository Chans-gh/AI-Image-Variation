"""规范基线访问层：路径解析 + 只读完整性校验。

设计决策（O-02 / 方案 B）：
    - 基线包作为**唯一真相源**入库，但任何写入都视为违规；
    - 运行时读取契约、方向注册表、错误码、Prompt 模板均经由本模块；
    - `shared/baseline.manifest.json` 记录基线全部文件的 SHA256，校验哈希即可发现改动。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .hashing import snapshot_tree
from .jsonio import read_json

BASELINE_DIRNAME = "Image_Variations_Dual_Skill_SPEC_v1.1"
MANIFEST_PATH = Path(__file__).resolve().parent / "baseline.manifest.json"


def repo_root() -> Path:
    """仓库根：优先环境变量，其次按本文件位置推断。"""
    override = os.environ.get("IV_REPO_ROOT")
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parents[1]


def baseline_root() -> Path:
    """基线目录：优先 IV_BASELINE_ROOT，确保测试可注入临时基线。"""
    override = os.environ.get("IV_BASELINE_ROOT")
    if override:
        return Path(override).resolve()
    return repo_root() / BASELINE_DIRNAME


def contracts_dir() -> Path:
    return baseline_root() / "contracts"


def references_dir() -> Path:
    return baseline_root() / "references"


def contract_path(name: str) -> Path:
    """契约文件路径；name 形如 'image-artifact.schema.json'。"""
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"PATH_TRAVERSAL_BLOCKED: 非法契约名 {name!r}")
    path = contracts_dir() / name
    if not path.is_file():
        raise FileNotFoundError(f"契约不存在: {path}")
    return path


def load_contract(name: str) -> dict:
    return read_json(contract_path(name))


def load_direction_registry() -> dict:
    return read_json(references_dir() / "directions" / "registry.json")


def load_error_registry() -> dict:
    return read_json(references_dir() / "error-registry.json")


def load_platform_config() -> dict:
    return read_json(references_dir() / "platform-config.json")


def load_prompt_template(kind: str) -> str:
    """kind: grid / sequence；返回模板正文（markdown 原文，供构建器解析）。"""
    if kind not in ("grid", "sequence"):
        raise ValueError(f"UNKNOWN_TEMPLATE: {kind!r}")
    return (references_dir() / f"{kind}-prompt.md").read_text(encoding="utf-8")


def load_schema_lite():
    """复用基线自带的 Draft 2020-12 子集校验器，避免两套 Schema 语义。"""
    import importlib.util

    path = baseline_root() / "tests" / "schema_lite.py"
    spec = importlib.util.spec_from_file_location("iv_baseline_schema_lite", path)
    if spec is None or spec.loader is None:  # pragma: no cover - 防御
        raise ImportError(f"无法加载基线 schema_lite: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_instance(contract_name: str, instance: Any) -> list:
    """用基线 schema_lite 校验实例；contract_name 决定用哪份 schema。"""
    schema = load_contract(contract_name)
    return load_schema_lite().validate(instance, schema, schema)


def verify_baseline(manifest_path: str | Path | None = None) -> list[str]:
    """按哈希清单校验基线；返回问题列表，空列表表示未被改动。"""
    manifest_file = Path(manifest_path) if manifest_path else MANIFEST_PATH
    manifest = read_json(manifest_file)
    root = baseline_root()
    declared: dict[str, str] = manifest["files"]
    problems: list[str] = []
    actual = snapshot_tree(root)
    missing = sorted(set(declared) - set(actual))
    if missing:
        problems.append(f"基线缺失文件 {len(missing)} 个: {missing[:5]}")
    extra = sorted(set(actual) - set(declared))
    if extra:
        problems.append(f"基线出现未登记文件 {len(extra)} 个: {extra[:5]}")
    changed = [rel for rel in sorted(set(declared) & set(actual)) if declared[rel] != actual[rel]]
    if changed:
        problems.append(f"基线文件被修改 {len(changed)} 个: {changed[:5]}")
    return problems


def build_manifest(root: str | Path, out_path: str | Path) -> Path:
    """生成哈希清单（仅在用户显式决定重建基线快照时使用）。"""
    from .jsonio import write_json

    files = snapshot_tree(root)
    payload = {
        "baseline": BASELINE_DIRNAME,
        "note": "冻结基线快照。若校验失败说明基线被改动：恢复原文件，不要更新本清单。",
        "file_count": len(files),
        "files": files,
    }
    return write_json(out_path, payload)
