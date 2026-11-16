"""无密钥自检用的**合成** Provider：产出确定性、可校验的图片。

用途只有一个：在没有任何真实 API Key 的情况下，把「预检 → 生成 → 校验 → 切图/锚点 →
落盘 → 打包」整条链路跑通并可回归。

诚实性要求：
    - `provider_type` 固定为 `mock_synthetic`，会原样写进 manifest；
    - 产物不是模型输出，不得用于对外交付或质量评估；
    - 合成内容由 prompt 哈希决定，因此同一任务可复现，不同帧/不同格彼此可区分。
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from PIL import Image, ImageDraw

from .provider import GenerationResult
from .provider.mock import MockAdapter, default_capability
from .provider.retry import Budget


def _seed_of(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:12], 16)


def _field(seed: int, size: tuple[int, int]) -> Image.Image:
    """由种子决定的平滑场：放大后仍连续，跨格线不会有断层。"""
    small = Image.new("L", (24, 24))
    pixels = small.load()
    state = seed % (2**31)
    for y in range(24):
        for x in range(24):
            state = (state * 1103515245 + 12345) % (2**31)
            pixels[x, y] = 40 + (state >> 16) % 160
    return small.resize(size, Image.BICUBIC).convert("RGB")


def board_size(aspect_ratio: str, long_edge: int, grid_size: int) -> tuple[int, int]:
    """整板尺寸：长边 = 档位边长，且两边都能被 N 整除。"""
    numerator, denominator = (int(part) for part in aspect_ratio.split(":"))
    if numerator >= denominator:
        wide, high = long_edge, max(grid_size, round(long_edge * denominator / numerator))
    else:
        wide, high = max(grid_size, round(long_edge * numerator / denominator)), long_edge
    return (_round_to(wide, grid_size), _round_to(high, grid_size))


def frame_size(aspect_ratio: str, long_edge: int) -> tuple[int, int]:
    numerator, denominator = (int(part) for part in aspect_ratio.split(":"))
    if numerator >= denominator:
        return long_edge, max(1, round(long_edge * denominator / numerator))
    return max(1, round(long_edge * numerator / denominator)), long_edge


def _round_to(value: int, step: int) -> int:
    return max(step, int(round(value / step)) * step)


def render_board(*, aspect_ratio: str, long_edge: int, grid_size: int, seed_text: str) -> bytes:
    size = board_size(aspect_ratio, long_edge, grid_size)
    image = _field(_seed_of(seed_text), size)
    draw = ImageDraw.Draw(image)
    radius = max(12, min(size) // 5)
    centre = (size[0] // 2, size[1] // 2)
    draw.ellipse([centre[0] - radius, centre[1] - radius,
                  centre[0] + radius, centre[1] + radius],
                 outline=(255, 255, 255), width=max(2, radius // 12))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def render_frame(*, aspect_ratio: str, long_edge: int, seed_text: str) -> bytes:
    size = frame_size(aspect_ratio, long_edge)
    seed = _seed_of(seed_text)
    image = _field(seed, size)
    draw = ImageDraw.Draw(image)
    radius = max(16, min(size) // 6)
    offset = (seed % (size[0] // 3)) - (size[0] // 6)
    centre = (size[0] // 2 + offset, size[1] // 2)
    draw.ellipse([centre[0] - radius, centre[1] - radius,
                  centre[0] + radius, centre[1] + radius],
                 outline=(255, 255, 255), width=max(2, radius // 10))
    draw.line([(0, centre[1] + radius // 2), (size[0], centre[1] + radius // 2)],
              fill=(240, 240, 240), width=max(1, radius // 14))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@dataclass
class SyntheticAdapter(MockAdapter):
    """直接返回合成图的适配器；不经过网络，但走同一套预算与结果模型。"""

    provider_type = "mock_synthetic"

    def __init__(self, *, grid_size: int = 2, capability=None, config=None) -> None:
        super().__init__(capability=capability or default_capability(provider_type="mock_synthetic"),
                         scenarios=[], config=config)
        self.grid_size = grid_size

    def generate(self, request, *, budget: Budget | None = None, policy=None, sleep=None
                 ) -> GenerationResult:
        if budget is not None:
            budget.consume(tier=request.resolution_tier or "1K")
        tier = request.resolution_tier or "1K"
        long_edge = int(self.capability.tier_to_board_side_px.get(tier, 1024))
        aspect_ratio = request.aspect_ratio or "1:1"
        if request.purpose == "grid_board":
            data = render_board(aspect_ratio=aspect_ratio, long_edge=long_edge,
                                grid_size=self.grid_size, seed_text=request.prompt)
        else:
            data = render_frame(aspect_ratio=aspect_ratio, long_edge=long_edge,
                                seed_text=request.prompt)
        return GenerationResult(image_bytes=data, media_type="image/png",
                                requested_tier=tier, adapter_version=self.adapter_version,
                                provider_raw_meta={"synthetic": True})


def build_synthetic_adapter(*, grid_size: int = 2, config: dict | None = None) -> SyntheticAdapter:
    return SyntheticAdapter(grid_size=grid_size, config=config)
