"""ModelCapability：能力声明 + D-26 底线判定 + D-21 档位推导。

约定：
    - `tier_to_board_side_px` 是 Grid 档位推导的**唯一输入**（provider-compat.md §2），
      缺失即视为能力不达标（MODEL_CAPABILITY_UNSUPPORTED），不得猜测边长。
    - 任何"能力不足"都必须返回注册表里的错误码，绝不静默降档。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from shared.errors import IVError

REQUIRED_FIELDS = (
    "text_to_image",
    "reference_image_input",
    "image_edit",
    "supported_ratios",
    "supported_resolution_tiers",
    "tier_to_board_side_px",
    "custom_dimensions",
    "supports_async_result",
    "max_input_bytes",
    "response_image_formats",
    "distinguishable_errors",
    "adapter_version",
    "multiple_independent_requests",
)

SCALAR_BOOL_FIELDS = (
    "text_to_image",
    "reference_image_input",
    "image_edit",
    "custom_dimensions",
    "supports_async_result",
    "distinguishable_errors",
    "multiple_independent_requests",
)

TIER_ORDER = ("1K", "2K", "4K")


def config_hard_min(capability: "ModelCapability") -> int:
    """硬下限从配置读取（不写死）；能力层不导入配置以避免环依赖。"""
    from ..config import load_config

    return int(load_config()["tile_edge_hard_min_px"])


@dataclass
class ModelCapability:
    adapter_version: str
    text_to_image: bool = False
    reference_image_input: bool = False
    image_edit: bool = False
    multiple_independent_requests: bool = False
    supported_ratios: tuple[str, ...] = ()
    supported_resolution_tiers: tuple[str, ...] = ()
    tier_to_board_side_px: dict[str, int] = field(default_factory=dict)
    custom_dimensions: bool = False
    supports_async_result: bool = False
    max_input_bytes: int = 0
    response_image_formats: tuple[str, ...] = ()
    distinguishable_errors: bool = False
    provider_type: str = ""
    model_id: str = ""

    # ---------------------------------------------------------------- 解析
    @classmethod
    def from_declaration(cls, data: dict | None) -> "ModelCapability":
        if not data:
            raise IVError("MODEL_CAPABILITY_UNSUPPORTED", "适配器未声明任何能力")
        missing = [f for f in REQUIRED_FIELDS if f not in data]
        if missing:
            raise IVError("MODEL_CAPABILITY_UNSUPPORTED",
                          f"能力声明缺失字段 {missing}", missing=missing)
        bad = [f for f in SCALAR_BOOL_FIELDS if not isinstance(data[f], bool)]
        if bad:
            raise IVError("MODEL_CAPABILITY_UNSUPPORTED", f"布尔能力字段类型错误 {bad}", invalid=bad)
        tiers = tuple(data["supported_resolution_tiers"])
        unknown = [t for t in tiers if t not in TIER_ORDER]
        if unknown:
            raise IVError("MODEL_CAPABILITY_UNSUPPORTED", f"未知档位 {unknown}", unknown=unknown)
        table = dict(data["tier_to_board_side_px"])
        uncovered = [t for t in tiers if not isinstance(table.get(t), int) or table[t] <= 0]
        if uncovered:
            raise IVError("MODEL_CAPABILITY_UNSUPPORTED",
                          f"档位 {uncovered} 缺少 tier_to_board_side_px 声明", missing_tiers=uncovered)
        return cls(
            adapter_version=str(data["adapter_version"]),
            text_to_image=bool(data["text_to_image"]),
            reference_image_input=bool(data["reference_image_input"]),
            image_edit=bool(data["image_edit"]),
            multiple_independent_requests=bool(data["multiple_independent_requests"]),
            supported_ratios=tuple(data["supported_ratios"]),
            supported_resolution_tiers=tiers,
            tier_to_board_side_px=table,
            custom_dimensions=bool(data["custom_dimensions"]),
            supports_async_result=bool(data["supports_async_result"]),
            max_input_bytes=int(data["max_input_bytes"]),
            response_image_formats=tuple(f.lower() for f in data["response_image_formats"]),
            distinguishable_errors=bool(data["distinguishable_errors"]),
            provider_type=str(data.get("provider_type", "")),
            model_id=str(data.get("model_id", "")),
        )

    def to_declaration(self) -> dict:
        return {
            "text_to_image": self.text_to_image,
            "reference_image_input": self.reference_image_input,
            "image_edit": self.image_edit,
            "multiple_independent_requests": self.multiple_independent_requests,
            "supported_ratios": list(self.supported_ratios),
            "supported_resolution_tiers": list(self.supported_resolution_tiers),
            "tier_to_board_side_px": dict(self.tier_to_board_side_px),
            "custom_dimensions": self.custom_dimensions,
            "supports_async_result": self.supports_async_result,
            "max_input_bytes": self.max_input_bytes,
            "response_image_formats": list(self.response_image_formats),
            "distinguishable_errors": self.distinguishable_errors,
            "adapter_version": self.adapter_version,
            "provider_type": self.provider_type,
            "model_id": self.model_id,
        }

    # ---------------------------------------------------------------- 查询
    def supports_ratio(self, ratio: str) -> bool:
        return ratio in self.supported_ratios

    def board_side(self, tier: str) -> int:
        side = self.tier_to_board_side_px.get(tier)
        if not side:
            raise IVError("MODEL_CAPABILITY_UNSUPPORTED",
                          f"档位 {tier} 未声明 tier_to_board_side_px")
        return int(side)

    def tile_edge(self, tier: str, grid_size: int) -> int:
        """单格边长（按整板边长整除 N；不足 1 视为 0）。"""
        return max(0, self.board_side(tier) // grid_size)

    def tile_long_edge(self, tier: str, grid_size: int, aspect_ratio: str) -> int:
        """单格长边：整板比例与单格一致（N×N 等格），取长边。"""
        side = self.board_side(tier)
        num, den = (int(x) for x in aspect_ratio.split(":"))
        tile_short = side // grid_size
        if num >= den:
            return tile_short
        return max(1, round(tile_short * den / num))

    def tiers_sorted(self) -> list[str]:
        return sorted(self.supported_resolution_tiers, key=lambda t: TIER_ORDER.index(t))

    # ---------------------------------------------------------------- D-21
    def pick_tier(self, *, grid_size: int, aspect_ratio: str, tile_edge_min_px: int,
                  requested_tier: str | None = None, allow_below_min: bool = False) -> dict:
        """档位推导（SPEC 01 §3.4）。

        返回 {tier, policy, tile_long_edge_px, warnings:[(code, message, severity)]}。
        规范原文以"单格长边"为准；同时记录基线使用的 min 边，便于交叉校验。
        """
        warnings: list[tuple[str, str, str]] = []
        tiers = self.tiers_sorted()
        if not tiers:
            raise IVError("UNSUPPORTED_RESOLUTION", "适配器未声明任何可用档位")

        def metrics(tier: str) -> tuple[int, int]:
            side = self.board_side(tier)
            tile_short = side // grid_size
            num, den = (int(x) for x in aspect_ratio.split(":"))
            long_edge = tile_short if num >= den else max(1, round(tile_short * den / num))
            return tile_short, long_edge

        if requested_tier is not None:
            if requested_tier not in self.supported_resolution_tiers:
                raise IVError("UNSUPPORTED_RESOLUTION",
                              f"模型不支持档位 {requested_tier}，可用 {list(self.supported_resolution_tiers)}")
            short_edge, long_edge = metrics(requested_tier)
            if long_edge < tile_edge_min_px:
                warnings.append((
                    "TILE_RESOLUTION_RISK",
                    f"用户指定的 {requested_tier} 单格长边 {long_edge} 低于推荐下限 {tile_edge_min_px}",
                    "warning",
                ))
            return {
                "tier": requested_tier,
                "policy": "user_override",
                "tile_short_edge_px": short_edge,
                "tile_long_edge_px": long_edge,
                "warnings": warnings,
            }

        for tier in tiers:
            short_edge, long_edge = metrics(tier)
            if long_edge >= tile_edge_min_px:
                return {
                    "tier": tier,
                    "policy": "tile_min_edge_default",
                    "tile_short_edge_px": short_edge,
                    "tile_long_edge_px": long_edge,
                    "warnings": warnings,
                }

        best = tiers[-1]
        short_edge, long_edge = metrics(best)
        if long_edge < 512 and not allow_below_min:
            raise IVError("UNSUPPORTED_RESOLUTION",
                          f"最高可用档位 {best} 的单格长边仅 {long_edge}px，低于硬下限 512px")
        warnings.append((
            "TILE_RESOLUTION_RISK",
            f"所有可用档位都不满足单格长边下限 {tile_edge_min_px}，取最高档 {best}（{long_edge}px）并记录降档",
            "risk",
        ))
        return {
            "tier": best,
            "policy": "provider_degraded",
            "tile_short_edge_px": short_edge,
            "tile_long_edge_px": long_edge,
            "warnings": warnings,
        }

    # ---------------------------------------------------------------- D-26
    def check_baseline_requirements(self, *, grid_size: int, aspect_ratio: str,
                                    tile_edge_min_px: int, sequence_input_type: str | None,
                                    sequence_uses_frames: bool = False,
                                    reference_bytes: int | None = None,
                                    requested_tier: str | None = None) -> list[dict]:
        """排产前逐项过底线；返回 [{code, message}]，空列表=通过。"""
        problems: list[dict] = []

        def fail(code: str, message: str) -> None:
            problems.append({"code": code, "message": message})

        if not self.text_to_image:
            fail("MODEL_CAPABILITY_UNSUPPORTED", "缺少 text_to_image")
        if not self.distinguishable_errors:
            fail("MODEL_CAPABILITY_UNSUPPORTED",
                 "无法区分鉴权/限流/超时/内容拒绝：不得笼统重试")
        if sequence_input_type is not None and not self.multiple_independent_requests:
            fail("MODEL_CAPABILITY_UNSUPPORTED", "Sequence 需要 N 次互不污染的独立请求")
        if sequence_input_type is not None or reference_bytes is not None:
            needs_reference = sequence_uses_frames or reference_bytes is not None
            if needs_reference and not (self.reference_image_input or self.image_edit):
                fail("REFERENCE_REQUIRED",
                     "任务必须以图锚定，但所选模型不支持参考图输入/图像编辑")
        if reference_bytes is not None and self.max_input_bytes and reference_bytes > self.max_input_bytes:
            fail("INVALID_INPUT",
                 f"原图 {reference_bytes} 字节超过模型上限 {self.max_input_bytes}，请压缩或更换原图")
        if not self.supports_ratio(aspect_ratio):
            fail("UNSUPPORTED_RATIO",
                 f"模型不支持比例 {aspect_ratio}，可用 {list(self.supported_ratios)}")
        if requested_tier is not None and requested_tier not in self.supported_resolution_tiers:
            fail("UNSUPPORTED_RESOLUTION",
                 f"模型不支持档位 {requested_tier}，可用 {list(self.supported_resolution_tiers)}")
        try:
            pick = self.pick_tier(grid_size=grid_size, aspect_ratio=aspect_ratio,
                                  tile_edge_min_px=tile_edge_min_px,
                                  requested_tier=requested_tier)
            if pick["tile_long_edge_px"] < config_hard_min(self):
                # 用户显式指定的档位也要如实说明是"你选的档位不够"，不能甩锅给"最高可用档位"
                source = (f"你指定的 {requested_tier}" if requested_tier
                          else f"最高可用档位 {pick['tier']}")
                fail("UNSUPPORTED_RESOLUTION",
                     f"{source} 单格长边仅 {pick['tile_long_edge_px']}px，低于硬下限 "
                     f"{config_hard_min(self)}px；可用档位 {list(self.supported_resolution_tiers)}，"
                     "或改用更小的网格/比例")
        except IVError as exc:
            fail(exc.code, exc.message)
        if not set(self.response_image_formats) & {"png", "jpeg", "jpg", "webp"}:
            fail("IMAGE_VALIDATION_FAILED",
                 f"返回格式 {list(self.response_image_formats)} 无法解码为 PNG/JPEG/WebP")
        return problems
