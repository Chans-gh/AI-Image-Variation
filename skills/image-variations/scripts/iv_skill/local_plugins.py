"""本地私有扩展的加载机制（公开仓库只带通用适配器）。

**为什么有这个模块**：真实平台接入（域名、模型 ID、实测回填的能力档案、轮询参数、
上游问题记录）属于**本机测试环境**，不进版本库。公开仓库只提供通用适配器
（`openai_image_compatible` / `gemini_image_compatible` / `mock`）与加载机制。

**本地目录布局**（默认 `<repoRoot>/local/`，`IV_LOCAL_ROOT` 可覆盖）。
扁平放或按平台分子目录都可以，两种都会被扫描：

```
local/
├── README.md
├── providers/                    # 扁平放法
│   └── <platform>_compat.py
├── tests/                        # 本地专属测试（run_all.py 自动发现）
├── provider-profiles.json        # 能力档案（按实测回填），按档案名并入基础档案
├── iv.config.local.json          # 配置覆盖（深合并到 iv.config.json 之上）
└── <platform>/                   # 按平台分组放法（推荐，便于整体清理/迁出）
    ├── providers/<platform>_compat.py
    ├── tests/test_<platform>.py
    ├── provider-profiles.json
    ├── iv.config.local.json
    └── docs/
```

**硬约束**：插件加载失败、档案文件损坏一律**显式报错**，不静默跳过——否则会出现
"provider_type 悄悄消失"这类难查故障。缺失本地目录是**正常情况**（公开仓库/CI 都没有），
此时请求未注册的 provider_type 会得到带指引的显式错误。
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path

LOCAL_ROOT_ENV = "IV_LOCAL_ROOT"
DEFAULT_LOCAL_DIRNAME = "local"

_LOADED: dict[str, str] = {}


def repo_root() -> Path:
    """仓库根：`IV_REPO_ROOT` 优先（部署/测试注入），否则按本文件位置推导。"""
    override = os.environ.get("IV_REPO_ROOT")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[4]


def local_root() -> Path:
    override = os.environ.get(LOCAL_ROOT_ENV)
    if override:
        return Path(override)
    return repo_root() / DEFAULT_LOCAL_DIRNAME


def providers_dirs() -> list[Path]:
    """所有插件目录：`local/providers` 与 `local/*/providers`（按路径排序，稳定可预期）。"""
    root = local_root()
    if not root.is_dir():
        return []
    dirs = [root / "providers"]
    dirs.extend(sorted(p / "providers" for p in root.iterdir()
                       if p.is_dir() and (p / "providers").is_dir()))
    return [d for d in dirs if d.is_dir()]


def profiles_paths() -> list[Path]:
    """所有本地档案文件：`local/provider-profiles.json` 与 `local/*/provider-profiles.json`。"""
    root = local_root()
    if not root.is_dir():
        return []
    paths = [root / "provider-profiles.json"]
    paths.extend(sorted(p / "provider-profiles.json" for p in root.iterdir() if p.is_dir()))
    return [p for p in paths if p.is_file()]


def config_override_paths() -> list[Path]:
    root = local_root()
    if not root.is_dir():
        return []
    paths = [root / "iv.config.local.json"]
    paths.extend(sorted(p / "iv.config.local.json" for p in root.iterdir() if p.is_dir()))
    return [p for p in paths if p.is_file()]


def local_tests_dirs() -> list[Path]:
    root = local_root()
    if not root.is_dir():
        return []
    dirs = [root / "tests"]
    dirs.extend(sorted(p / "tests" for p in root.iterdir() if p.is_dir() and (p / "tests").is_dir()))
    return [d for d in dirs if d.is_dir()]


def reset_cache() -> None:
    """清空插件加载缓存（测试用；生产不需要）。"""
    _LOADED.clear()


def iter_provider_modules() -> list[Path]:
    modules: list[Path] = []
    for directory in providers_dirs():
        modules.extend(p for p in directory.glob("*.py") if not p.name.startswith("_"))
    return sorted(modules)


def register_providers(registry: dict) -> list[str]:
    """把 `local/providers/*.py` 注册进 `registry`，返回本次注册的 provider_type 列表。

    每个插件模块必须提供 `register(registry)`，或声明 `ADAPTERS: dict[str, type]`。
    加载失败一律抛错（不静默跳过）。
    """
    from shared.errors import IVError

    registered: list[str] = []
    for path in iter_provider_modules():
        if _LOADED.get(str(path)) == "ok":
            continue
        module_name = f"iv_local_provider_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"无法为 {path.name} 构建导入规格")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 - 统一转成显式错误
            _LOADED.pop(str(path), None)
            raise IVError("BUILTIN_UNAVAILABLE",
                          f"本地 Provider 插件加载失败（{path}）：{type(exc).__name__}: {exc}") from exc

        before = set(registry)
        if hasattr(module, "register") and callable(module.register):
            module.register(registry)
        elif isinstance(getattr(module, "ADAPTERS", None), dict):
            registry.update(module.ADAPTERS)
        else:
            raise IVError("BUILTIN_UNAVAILABLE",
                          f"本地 Provider 插件缺少 register(registry) 或 ADAPTERS：{path}")
        new_types = sorted(set(registry) - before)
        if not new_types:
            raise IVError("BUILTIN_UNAVAILABLE",
                          f"本地 Provider 插件没有注册任何新的 provider_type：{path}")
        _LOADED[str(path)] = "ok"
        registered.extend(new_types)
    return registered


def load_json_if_present(path: Path, *, what: str) -> dict:
    """读取本地 JSON；文件不存在返回空 dict，存在但损坏则显式报错。"""
    from shared.errors import IVError

    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise IVError("INVALID_INPUT", f"本地{what}不是合法 JSON（{path}）：{exc}") from exc
    if not isinstance(data, dict):
        raise IVError("INVALID_INPUT", f"本地{what}顶层必须是对象（{path}）")
    return data


def merge_profiles(base: dict) -> dict:
    """把本地档案并入基础档案（后扫描到的同名档案覆盖先前的，本地整体优先）。"""
    from shared.errors import IVError

    merged = copy.deepcopy(base)
    merged.setdefault("profiles", {})
    sources: list[str] = []
    for path in profiles_paths():
        extra = load_json_if_present(path, what="能力档案")
        local_profiles = extra.get("profiles")
        if not isinstance(local_profiles, dict):
            raise IVError("INVALID_INPUT", f"本地能力档案缺少 profiles 段（{path}）")
        merged["profiles"].update(local_profiles)
        sources.append(str(path))
    if sources:
        merged["local_profiles_sources"] = sources
    return merged


def merge_config(base: dict) -> dict:
    """把本地配置覆盖深合并到基础配置之上（后扫描到的覆盖先前的）。"""
    merged = copy.deepcopy(base)
    for path in config_override_paths():
        merged = _deep_merge(merged, load_json_if_present(path, what="配置覆盖"))
    return merged


def _deep_merge(target: dict, overlay: dict) -> dict:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)
    return target


def status() -> dict:
    """本地扩展现状（供 `--self-check` 展示）。"""
    return {
        "local_root": str(local_root()),
        "present": local_root().is_dir(),
        "provider_modules": [str(p.relative_to(local_root())) for p in iter_provider_modules()],
        "profiles_files": [str(p.relative_to(local_root())) for p in profiles_paths()],
        "config_overrides": [str(p.relative_to(local_root())) for p in config_override_paths()],
        "tests_dirs": [str(p.relative_to(local_root())) for p in local_tests_dirs()],
    }
