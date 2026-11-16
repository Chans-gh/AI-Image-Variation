"""Skill 01 专属语义校验（基线 checks.py 未覆盖的部分）。

复用基线的 `check_grid_structure` / `check_sequence_structure` / `check_images_on_disk`
（它们属于冻结规范的行为定义），本模块只补：比例容差、方向注册表、错误码绑定、执行记录。
"""
from __future__ import annotations

import re

from fractions import Fraction

import paths  # noqa: E402  (sys.path 装配)

from shared import baseline, errors, pkg  # noqa: E402
from shared.jsonio import read_json  # noqa: E402
from iv_skill.config import load_bindings, load_config  # noqa: E402

try:  # 基线业务校验器（冻结）
    import checks as baseline_checks  # type: ignore  # noqa: E402
except ImportError:  # pragma: no cover
    baseline_checks = None


def _baseline_checks():
    global baseline_checks
    if baseline_checks is None:
        import importlib.util

        path = paths.baseline_test_dir() / "checks.py"
        spec = importlib.util.spec_from_file_location("iv_baseline_checks", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        baseline_checks = module
    return baseline_checks


def ratio_value(text: str) -> float:
    num, den = (float(x) for x in text.split(":"))
    return num / den


def check_ratio(folder, manifest, config: dict | None = None):
    """实际比例与请求比例的一致性与记录完整性。"""
    config = config or load_config()
    errs = []
    requested = manifest.get("requested_aspect_ratio")
    actual = manifest.get("actual_aspect_ratio")
    if actual is None:
        if manifest.get("status") == "completed":
            errs.append(("IMAGE_VALIDATION_FAILED", "completed 状态必须记录 actual_aspect_ratio"))
        return errs
    target = ratio_value(requested)
    real = ratio_value(actual)
    error_ratio = abs(real - target) / target
    tolerance = config["aspect_ratio_tolerance_ratio"]
    recorded = manifest.get("validation", {}).get("aspect_ratio_error_ratio")
    if recorded is not None and abs(float(recorded) - error_ratio) > 1e-6:
        errs.append(("IMAGE_VALIDATION_FAILED",
                     f"记录的比例误差 {recorded} 与实际 {round(error_ratio, 6)} 不符"))
    if error_ratio > tolerance:
        errs.append(("IMAGE_VALIDATION_FAILED",
                     f"实际比例 {actual} 偏离请求 {requested}：{error_ratio:.4f} > {tolerance}"))
    return errs


def check_direction_registry(manifest, registry: dict | None = None):
    """方向必须是注册表内单一合法 key（多方向在任务层已被 MULTIPLE_DIRECTIONS 拦下）。"""
    registry = registry or baseline.load_direction_registry()
    keys = {d["key"] for d in registry["directions"]}
    direction = str(manifest.get("direction", ""))
    if direction not in keys:
        # 形如 "light+scene" 的多方向声明：语义是"要求了多个方向"，单独成码
        parts = [part for part in re.split(r"[+,/、\s]+", direction) if part]
        if len([part for part in parts if part in keys]) > 1:
            return [("MULTIPLE_DIRECTIONS",
                     f"一个任务只能有一个方向，收到 {parts}；请只选一个")]
        return [("INVALID_INPUT",
                 f"方向 {direction!r} 不是注册表内单一 key；可用 {sorted(keys)}")]
    version = manifest.get("direction_registry_version")
    if version != registry["registry_version"]:
        return [("SCHEMA_MAJOR_UNSUPPORTED",
                 f"声明 registry 版本 {version!r} 与基线 {registry['registry_version']!r} 不一致")]
    return []


def check_execution(manifest, config: dict | None = None):
    """预算与调用记录：超预算必须显式标记，不得静默。"""
    config = config or load_config()
    execution = manifest.get("execution") or {}
    errs = []
    used = execution.get("calls_used")
    limit = execution.get("call_budget_limit")
    if used is not None and limit is not None and used > limit and not execution.get("budget_exceeded"):
        errs.append(("BUDGET_EXCEEDED", f"calls_used={used} 超过预算 {limit} 却未标记 budget_exceeded"))
    return errs


def check_error_codes(manifest, required: list[str] | None = None):
    """错误/警告码绑定：必须来自基线注册表；required 中的码必须已落盘。

    用于负例断言"能力不足/超预算必须显式报码，不得静默"。
    """
    errs = []
    declared: set[str] = set()
    for item in manifest.get("errors", []) or []:
        code = item.get("code")
        declared.add(code)
        if not code or not errors.is_known(code):
            errs.append(("SCHEMA_MAJOR_UNSUPPORTED", f"未登记错误码 {code!r}"))
    for item in manifest.get("warnings", []) or []:
        code = item.get("code")
        declared.add(code)
        if not code or not errors.is_known(code):
            errs.append(("SCHEMA_MAJOR_UNSUPPORTED", f"未登记警告码 {code!r}"))
    for code in required or []:
        if code not in declared:
            errs.append((code, f"期望的错误码 {code} 未出现在 manifest.errors/warnings 中"))
    return errs


def registry_code_bindings() -> list[str]:
    """校验 config/error-registry-bindings.json 引用的错误码全部已登记。"""
    problems = []
    for code in {c for item in load_bindings()["acceptance_bindings"] for c in item.get("codes", [])}:
        if not errors.is_known(code):
            problems.append(code)
    return sorted(problems)


def check_tile_edge_extra(manifest, config: dict | None = None):
    """单格长边下限（§3.4）：在基线 min 边安全下限之上，补规范口径的长边分级。

    规范原文："单格长边不低于下限"，"低于 768 产生 TILE_RESOLUTION_RISK，低于 512 默认阻断"。
    基线 checks.py 使用 min 边（更保守），两者同时成立才算合格。
    """
    config = config or load_config()
    validation = manifest.get("validation", {}) or {}
    long_edge = validation.get("tile_long_edge_px")
    if long_edge is None:
        return []
    required = validation.get("tile_long_edge_required_px", config["tile_edge_min_px"])
    errs = []
    if long_edge < config["tile_edge_hard_min_px"]:
        errs.append(("TILE_RESOLUTION_TOO_LOW",
                     f"单格长边 {long_edge} < 硬下限 {config['tile_edge_hard_min_px']}"))
    elif long_edge < config["tile_edge_warn_px"]:
        errs.append(("TILE_RESOLUTION_RISK",
                     f"单格长边 {long_edge} < 警告值 {config['tile_edge_warn_px']}"))
    elif long_edge < config["tile_edge_min_px"]:
        errs.append(("TILE_RESOLUTION_RISK",
                     f"单格长边 {long_edge} < 推荐下限 {config['tile_edge_min_px']}"))
    expected_ok = long_edge >= required
    if validation.get("tile_edge_ok") is not expected_ok:
        errs.append(("GRID_VALIDATION_FAILED",
                     f"tile_edge_ok={validation.get('tile_edge_ok')} 与长边判定 {expected_ok} 不一致"))
    return errs


def check_seam(manifest, config: dict | None = None):
    """接缝评分与检测方法（D-24）。"""
    config = config or load_config()
    validation = manifest.get("validation", {}) or {}
    errs = []
    if validation.get("seam_check_method") not in (None, config["seam"]["method"]):
        errs.append(("GRID_VALIDATION_FAILED",
                     f"未知接缝检测方法 {validation.get('seam_check_method')!r}"))
    return errs


def image_artifact_errors(folder, manifest, config: dict | None = None):
    """负例断言用：把基线与本模块的语义错误合并。"""
    checks = _baseline_checks()
    errs = []
    errs += checks.check_schema_major(manifest)
    errs += checks.check_images_on_disk(folder, manifest)
    if manifest.get("output_mode") == "grid":
        errs += checks.check_grid_structure(folder, manifest)
        errs += check_tile_edge_extra(manifest, config)
        errs += check_seam(manifest, config)
    elif manifest.get("output_mode") == "sequence":
        errs += checks.check_sequence_structure(manifest)
    errs += check_ratio(folder, manifest, config)
    errs += check_direction_registry(manifest)
    errs += check_execution(manifest, config)
    return errs


def package_errors(folder) -> list[tuple[str, str]]:
    """交付包错误：返回 (code, message)，路径问题优先报 PATH_TRAVERSAL_BLOCKED。"""
    manifest_file = folder / pkg.MANIFEST_NAME
    if not manifest_file.is_file():
        return [("PACKAGE_MANIFEST_INVALID", "缺 delivery-package.json")]
    return pkg.verify_manifest(folder, read_json(manifest_file))


def load_manifest(folder):
    return read_json(folder / "manifest.json")


def fraction_of(width: int, height: int) -> Fraction:
    return Fraction(width, height).limit_denominator(1000)
