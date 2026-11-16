"""排产前预检（D-26）：参数 → 能力 → 预算 → 档位，全部通过才允许调用模型。

输出结构写入 job-state / manifest 的 planning 区；任何不通过都返回注册表错误码，
且**不发起任何模型调用**。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from shared.errors import IVError

from .config import load_config
from .provider.capability import ModelCapability
from .provider.retry import Budget, RetryPolicy


@dataclass
class PreflightResult:
    ok: bool
    errors: list[dict] = field(default_factory=list)
    plan: dict = field(default_factory=dict)

    def raise_if_blocked(self) -> None:
        if self.ok:
            return
        first = self.errors[0]
        raise IVError(first["code"], first["message"], **{k: v for k, v in first.items()
                                                         if k not in ("code", "message")})


def capabilities_declaration_for(provider_type: str, override: dict | None = None) -> dict | None:
    """能力来源：适配器已知声明 + 连接/预检结果；不接受"叫兼容就假定支持"。"""
    if override is not None:
        return override
    if provider_type in ("mock",):
        from .provider.mock import DEFAULT_CAPABILITY_DECLARATION

        return dict(DEFAULT_CAPABILITY_DECLARATION)
    return None


def plan_generation(*, output_mode: str, grid_size: int | None, image_count: int | None,
                    aspect_ratio: str, input_type: str, direction: str, branch: str | None,
                    capability: ModelCapability | None, requested_tier: str | None = None,
                    config: dict | None = None, provider_type: str = "external",
                    reference_bytes: int | None = None) -> PreflightResult:
    """完整预检：模式/张数/方向 → 能力底线 → 档位推导 → 预算。"""
    config = config or load_config()
    errors: list[dict] = []

    if output_mode not in ("grid", "sequence"):
        errors.append({"code": "INVALID_MODE_OR_COUNT", "message": f"output_mode 非法: {output_mode}"})
        return PreflightResult(False, errors)

    if output_mode == "grid":
        allowed = config["grid"]["allowed_sizes"]
        if grid_size not in allowed:
            return PreflightResult(False, [{
                "code": "INVALID_MODE_OR_COUNT",
                "message": f"Grid 只支持 2/3/4（N×N），收到 {grid_size!r}",
            }])
        derived_count = grid_size * grid_size
        if image_count is not None and image_count != derived_count:
            errors.append({"code": "INVALID_MODE_OR_COUNT",
                           "message": f"Grid 的 image_count 必须由 N² 推导（{derived_count}），不能单独设置"})
        if branch is not None:
            errors.append({"code": "INVALID_MODE_OR_COUNT", "message": "Grid 不接收 branch"})
    else:
        lo, hi = config["sequence"]["min_frames"], config["sequence"]["max_frames"]
        if image_count is None or not (lo <= int(image_count) <= hi):
            return PreflightResult(False, [{
                "code": "INVALID_MODE_OR_COUNT",
                "message": f"Sequence 张数必须在 {lo}–{hi}，收到 {image_count!r}",
            }])
        if branch not in ("variants", "progression", "narrative"):
            return PreflightResult(False, [{
                "code": "INVALID_MODE_OR_COUNT",
                "message": f"Sequence 必须且只能选一个分支，收到 {branch!r}",
            }])
        derived_count = int(image_count)
        if grid_size is not None:
            errors.append({"code": "INVALID_MODE_OR_COUNT", "message": "Sequence 不接收 grid_size"})

    if not direction:
        errors.append({"code": "INVALID_INPUT", "message": "必须选择一个变化方向"})

    if capability is None:
        errors.append({"code": "MODEL_CAPABILITY_UNSUPPORTED",
                       "message": "适配器未提供能力声明，排产前阻断"})
        return PreflightResult(False, errors)

    capability_errors = capability.check_baseline_requirements(
        grid_size=grid_size or 1,
        aspect_ratio=aspect_ratio,
        tile_edge_min_px=config["tile_edge_min_px"],
        sequence_input_type=input_type if output_mode == "sequence" else None,
        sequence_uses_frames=output_mode == "sequence",
        reference_bytes=reference_bytes,
        requested_tier=requested_tier,
    )
    errors.extend(capability_errors)
    if errors:
        return PreflightResult(False, errors)

    tier_pick = capability.pick_tier(grid_size=grid_size or 1, aspect_ratio=aspect_ratio,
                                     tile_edge_min_px=config["tile_edge_min_px"],
                                     requested_tier=requested_tier)
    budget = Budget.plan(output_mode=output_mode, image_count=derived_count,
                         input_type=input_type, tier=tier_pick["tier"], config=config)
    try:
        budget.preflight()
    except IVError as exc:
        errors.append({"code": exc.code, "message": exc.message,
                       "split_suggestion": exc.detail.get("split_suggestion")})
        return PreflightResult(False, errors)

    plan = {
        "output_mode": output_mode,
        "grid_size": grid_size,
        "image_count": derived_count,
        "aspect_ratio": aspect_ratio,
        "direction": direction,
        "branch": branch,
        "input_type": input_type,
        "requested_tier": requested_tier,
        "tier": tier_pick["tier"],
        "tier_policy": tier_pick["policy"],
        "tile_short_edge_px": tier_pick["tile_short_edge_px"],
        "tile_long_edge_px": tier_pick["tile_long_edge_px"],
        "tier_warnings": tier_pick["warnings"],
        "calls_planned": budget.planned,
        "cost_estimate": budget.cost_estimate,
        "cost_currency": budget.currency,
        "retry_policy": {
            "grid_max_attempts": config["retry"]["grid_max_attempts"],
            "frame_max_extra_retries": config["retry"]["frame_max_extra_retries"],
        },
        "provider_type": provider_type,
        "adapter_version": capability.adapter_version,
        "model_id": capability.model_id,
    }
    return PreflightResult(True, [], plan)


def retry_policy_for(output_mode: str, config: dict | None = None) -> RetryPolicy:
    return RetryPolicy.from_config(config, unit="grid" if output_mode == "grid" else "frame")
