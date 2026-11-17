"""配置层：所有阈值/默认值只能从这里取。

硬约束 2（用户要求）：1024/768/512、seam 0.85/0.60、放大 1.5× 等数值必须来自配置，
不得写死在业务代码里。本模块同时提供"配置完整性"校验，缺键即失败。
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from .local_plugins import merge_config, merge_profiles

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "iv.config.json"
BINDINGS_PATH = Path(__file__).resolve().parents[2] / "config" / "error-registry-bindings.json"
PROFILES_PATH = Path(__file__).resolve().parents[2] / "config" / "provider-profiles.json"

REQUIRED_BINDING_CODES = (
    "AC-01", "AC-02", "AC-04", "AC-06", "AC-07", "AC-09", "AC-10", "AC-13", "AC-14",
    "AC-15", "AC-20", "AC-21", "AC-22", "AC-23", "AC-24", "AC-27", "AC-28", "AC-29",
    "AC-30", "AC-31", "AC-32",
)

REQUIRED_KEYS: tuple[tuple[str, ...], ...] = (
    ("config_version",),
    ("tile_edge_min_px",),
    ("tile_edge_warn_px",),
    ("tile_edge_hard_min_px",),
    ("board_side_tolerance_ratio",),
    ("aspect_ratio_tolerance_ratio",),
    ("seam", "method"),
    ("seam", "band_px"),
    ("seam", "epsilon"),
    ("seam", "pass_threshold"),
    ("seam", "risk_threshold"),
    ("seam", "grade_mode"),
    ("seam", "border_line_detect", "min_run_px"),
    ("seam", "border_line_detect", "min_contrast"),
    ("seam", "border_line_detect", "blank_band_std_max"),
    ("retry", "grid_max_attempts"),
    ("retry", "frame_max_extra_retries"),
    ("retry", "backoff_base_sec"),
    ("retry", "backoff_max_sec"),
    ("retry", "jitter_ratio"),
    ("retry", "retry_on"),
    ("retry", "never_retry_on"),
    ("budget", "default_call_budget_limit"),
    ("budget", "batch_default_max_items"),
    ("budget", "cost_per_call"),
    ("budget", "cost_currency"),
    ("sequence", "min_frames"),
    ("sequence", "max_frames"),
    ("sequence", "near_duplicate_similarity_max"),
    ("grid", "allowed_sizes"),
    ("defaults", "output_mode"),
    ("defaults", "grid_size"),
    ("defaults", "direction"),
    ("defaults", "sequence_image_count"),
    ("defaults", "text_only_aspect_ratio"),
    ("defaults", "sequence_resolution_tier"),
    ("local_endpoint", "allow_local_endpoint"),
    ("local_endpoint", "allowed_hosts"),
    ("packaging", "max_entry_bytes"),
    ("packaging", "max_total_bytes"),
    ("packaging", "max_entries"),
    ("packaging", "max_compression_ratio"),
)
# 说明：Provider 专属的运行参数（轮询间隔等）由**本地配置覆盖**提供，不在这里要求——
# 公开仓库不带任何真实平台参数，见 iv_skill/local_plugins.py。


def _dig(data: dict, path: tuple[str, ...]) -> Any:
    node: Any = data
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise KeyError(".".join(path))
        node = node[key]
    return node


def load_config(path: str | Path | None = None) -> dict:
    """读取配置；环境变量 IV_CONFIG 可覆盖路径（测试注入用）。

    只有在使用**默认路径**时才深合并本地覆盖（`local/**/iv.config.local.json`），
    避免测试注入的配置被本机环境悄悄改掉。
    """
    injected = path is not None or bool(os.environ.get("IV_CONFIG"))
    target = Path(path) if path else Path(os.environ.get("IV_CONFIG", CONFIG_PATH))
    data = json.loads(target.read_text(encoding="utf-8"))
    if not injected:
        data = merge_config(data)
    missing = [".".join(key) for key in REQUIRED_KEYS if _missing(data, key)]
    if missing:
        raise ValueError(f"INVALID_INPUT: 配置缺少必填键 {missing}")
    if data["tile_edge_hard_min_px"] > data["tile_edge_warn_px"] > data["tile_edge_min_px"]:
        raise ValueError("INVALID_INPUT: 单格阈值必须满足 hard_min < warn < min")
    if data["seam"]["risk_threshold"] >= data["seam"]["pass_threshold"]:
        raise ValueError("INVALID_INPUT: seam risk_threshold 必须小于 pass_threshold")
    if data["sequence"]["min_frames"] > data["sequence"]["max_frames"]:
        raise ValueError("INVALID_INPUT: sequence 张数区间非法")
    return data


def _missing(data: dict, path: tuple[str, ...]) -> bool:
    node: Any = data
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return True
        node = node[key]
    return False


def load_provider_profiles(path: str | Path | None = None) -> dict:
    """读取 Provider 能力档案（按实测回填的 ModelCapability 声明）。

    对应 provider-compat.md §104："各档位对应的真实边长必须在真实服务上测量并回填"。
    档案只放**声明与实测依据**，不含任何密钥。
    """
    injected = path is not None or bool(os.environ.get("IV_PROFILES"))
    target = Path(path) if path else Path(os.environ.get("IV_PROFILES", PROFILES_PATH))
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data.get("profiles"), dict):
        raise ValueError("INVALID_INPUT: provider-profiles.json 缺少 profiles 段")
    if not injected:
        data = merge_profiles(data)
    return data


def load_bindings(path: str | Path | None = None) -> dict:
    """读取验收项 ↔ 夹具 ↔ 错误码 绑定。"""
    target = Path(path) if path else Path(os.environ.get("IV_BINDINGS", BINDINGS_PATH))
    return json.loads(target.read_text(encoding="utf-8"))


def binding_codes(bindings: dict | None = None) -> list[str]:
    """返回绑定文件中引用的全部错误码（去重、排序）。"""
    data = bindings if bindings is not None else load_bindings()
    codes: set[str] = set()
    for item in data.get("acceptance_bindings", []):
        codes.update(item.get("codes", []))
    return sorted(codes)


def with_overrides(config: dict, overrides: dict | None) -> dict:
    """深度合并调用级覆盖（例如 CLI 传入的 --tile-edge-min-px），不修改原对象。"""
    if not overrides:
        return config
    merged = copy.deepcopy(config)

    def merge(target: dict, patch: dict) -> None:
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge(target[key], value)
            else:
                target[key] = value

    merge(merged, overrides)
    return merged
