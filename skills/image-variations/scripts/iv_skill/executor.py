"""Grid 模式执行器：一次成板 → 校验 → 确定性切图 → 接缝评分（含重试预算）。

不变量（SPEC 01 §7 / §9.7）：
    - 整板只调用**一次**模型；失败重试是"再要一张整板"，绝不改成拼接 N 张单图；
    - 重试次数取配置 `retry.grid_max_attempts`，每次重试计入 `execution.calls_used`；
    - 接缝 <0.60 或检测到框线 → 判失败（GRID_VALIDATION_FAILED），不输出"已通过"的切图；
    - 0.60–0.85 → 可交付但必须带 GRID_SEAM_RISK 警告，不得宣称无缝；
    - 单格长边 <512 → TILE_RESOLUTION_TOO_LOW 阻断。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from shared.errors import IVError

from .config import load_config
from .grid import GridPostprocess, postprocess
from .preflight import plan_generation, retry_policy_for
from .prompt_builder import DirectionRegistry, build_grid_prompt
from .provider import GenerationRequest, ReferenceImage
from .provider.retry import Budget, RetryPolicy
from .seam import SeamReport, classify_seam, measure_seam


@dataclass
class GridAttempt:
    attempt: int
    postprocess: GridPostprocess
    seam: SeamReport
    verdict: str
    warnings: list[dict] = field(default_factory=list)


@dataclass
class GridExecution:
    plan: dict
    prompt_text: str
    prompt_meta: dict
    attempts: list[GridAttempt]
    result: GridAttempt
    budget: dict
    warnings: list[dict]

    @property
    def board(self):
        return self.result.postprocess.board

    @property
    def tiles(self):
        return self.result.postprocess.tiles


def _grid_supported(registry: DirectionRegistry, direction_key: str) -> bool:
    item = registry._dirs.get(direction_key)
    if item is None:
        raise IVError("INVALID_INPUT", f"方向 {direction_key!r} 不在注册表内；"
                      "可用键见 references/directions/registry.json")
    return "grid" in item.get("modes", ["grid"])


def execute_grid(*, adapter, direction_key: str, grid_size: int, aspect_ratio: str,
                 input_type: str, subject_anchor: str, reference_image: ReferenceImage | None = None,
                 requested_tier: str | None = None, registry: DirectionRegistry | None = None,
                 config: dict | None = None, budget: Budget | None = None,
                 sleep=None) -> GridExecution:
    """执行一次 Grid 任务（含校验失败重试）。"""
    config = config or load_config()
    registry = registry or DirectionRegistry()
    if not _grid_supported(registry, direction_key):
        raise IVError("INVALID_MODE_OR_COUNT", f"方向 {direction_key} 不支持 Grid 模式")

    plan_result = plan_generation(
        output_mode="grid", grid_size=grid_size, image_count=None, aspect_ratio=aspect_ratio,
        input_type=input_type, direction=direction_key, branch=None,
        capability=adapter.capability, requested_tier=requested_tier, config=config,
        provider_type=adapter.provider_type,
        reference_bytes=len(reference_image.data) if reference_image else None)
    plan_result.raise_if_blocked()
    plan = plan_result.plan

    prompt = build_grid_prompt(registry=registry, direction_key=direction_key, grid_size=grid_size,
                               aspect_ratio=aspect_ratio, resolution_tier=plan["tier"],
                               subject_anchor=subject_anchor,
                               tile_edge_min_px=config["tile_edge_min_px"])

    budget = budget or Budget.plan(output_mode="grid", image_count=grid_size * grid_size,
                                   input_type=input_type, tier=plan["tier"], config=config)
    budget.preflight()
    policy = retry_policy_for("grid", config)
    max_attempts = int(config["retry"]["grid_max_attempts"])
    attempts: list[GridAttempt] = []
    warnings: list[dict] = list(plan["tier_warnings"] and [
        {"code": code, "message": message, "severity": severity}
        for code, message, severity in plan["tier_warnings"]
    ] or [])

    last_error: IVError | None = None
    for attempt in range(1, max_attempts + 1):
        request = GenerationRequest(
            prompt=prompt.text, aspect_ratio=aspect_ratio, resolution_tier=plan["tier"],
            reference_image=reference_image, purpose="grid_board")
        try:
            # 整板级重试由本层负责（次数取自 retry.grid_max_attempts），
            # 适配器内层不再重复重试，否则实际调用次数会超过预算计划。
            result = adapter.generate(request, budget=budget, policy=single_attempt(config),
                                      sleep=sleep)
        except IVError as exc:
            last_error = exc
            # 「blocking」只表示必须显式报给用户，不代表可重试；
            # 是否重试由 retry_on 集合与尝试次数决定。
            if not policy.should_retry(exc.code, attempt):
                raise
            continue

        processed = postprocess(result.image_bytes, grid_size=grid_size,
                                requested_aspect_ratio=aspect_ratio, config=config)
        seam = measure_seam(processed.board.image, grid_size, config)
        verdict, code = classify_seam(seam, config)
        attempt_warnings = list(processed.warnings)
        if code:
            attempt_warnings.append({"code": code, "message": _seam_message(verdict, seam, config),
                                     "severity": "warning" if verdict == "risk" else "risk"})
        record = GridAttempt(attempt=attempt, postprocess=processed, seam=seam,
                             verdict=verdict, warnings=attempt_warnings)
        attempts.append(record)
        if verdict in ("pass", "risk"):
            warnings.extend(w for w in attempt_warnings if _not_duplicate(warnings, w))
            validation = dict(processed.validation)
            validation.update(seam.to_validation())
            validation["grid_attempts"] = attempt
            if verdict == "risk":
                validation["grid_structure_passed"] = True
            processed.validation = validation
            return GridExecution(plan=plan, prompt_text=prompt.text,
                                 prompt_meta={"template_version": prompt.template_version,
                                              "cell_plan": prompt.cell_plan},
                                 attempts=attempts, result=record,
                                 budget=budget.to_execution(), warnings=warnings)
        last_error = IVError("GRID_VALIDATION_FAILED",
                             _seam_message(verdict, seam, config),
                             seam_score=round(seam.score, 6), attempt=attempt)
        if attempt >= max_attempts:
            break

    assert last_error is not None
    raise IVError(last_error.code,
                  f"整板校验在 {max_attempts} 次尝试内未通过：{last_error.message}",
                  grid_attempts=len(attempts), seam_scores=[round(a.seam.score, 6) for a in attempts],
                  retryable=True)


def single_attempt(config: dict) -> RetryPolicy:
    """把适配器内层重试关掉（保留可重试码集合），由上层执行器统一掌控次数。"""
    return RetryPolicy.from_config(config, max_extra_retries=0)


def _seam_message(verdict: str, seam: SeamReport, config: dict) -> str:
    thresholds = config["seam"]
    if seam.has_border_line:
        return f"检测到边界高对比线/留白带 {seam.border_lines}，判为有可见接缝"
    return (f"seam_score={seam.score:.3f}（pass≥{thresholds['pass_threshold']}, "
            f"risk≥{thresholds['risk_threshold']}）→ {verdict}")


def _not_duplicate(existing: list[dict], candidate: dict) -> bool:
    return not any(item.get("code") == candidate.get("code")
                   and item.get("message") == candidate.get("message") for item in existing)
