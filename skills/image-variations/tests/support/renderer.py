"""夹具图片渲染：确定性合成图，**不是模型输出**（与基线 examples/ 同性质）。

约定：
    - 每格图案不同，格内连续；
    - 格与格之间像素连续（无缝板），因此接缝评分天然应接近 1.0；
    - 负例用 `h_shift` 制造格间不连续，用于压低 seam_score。
"""
from __future__ import annotations

import io
import random
from pathlib import Path

from PIL import Image, ImageDraw

COLORS = [
    (143, 169, 196), (200, 168, 130), (129, 178, 154), (186, 141, 158),
    (150, 150, 190), (196, 170, 120), (120, 180, 190), (170, 150, 130),
]


def palette(index: int) -> tuple[int, int, int]:
    return COLORS[index % len(COLORS)]


def render_tile(width: int, height: int, index: int, seed: int = 0) -> Image.Image:
    """单格图案：主色 + 确定性噪点 + 几何线，格内自洽。"""
    tile = Image.new("RGB", (width, height), palette(index))
    rng = random.Random(seed * 1000 + index)
    draw = ImageDraw.Draw(tile)
    for _ in range(max(6, (width * height) // 4000)):
        x0 = rng.randrange(0, max(1, width - 8))
        y0 = rng.randrange(0, max(1, height - 8))
        w = rng.randrange(4, max(5, width // 3))
        h = rng.randrange(4, max(5, height // 3))
        shade = tuple(min(255, c + rng.randrange(-40, 41)) for c in palette(index))
        draw.rectangle([x0, y0, x0 + w, y0 + h], fill=shade)
    draw.line([(0, height // 2), (width, height // 2)], fill=(250, 250, 250), width=2)
    return tile


def render_board(grid_size: int, tile_w: int, tile_h: int, seed: int = 0) -> Image.Image:
    """无缝整板：把各格拼成一张连续图像。"""
    board = Image.new("RGB", (tile_w * grid_size, tile_h * grid_size), (0, 0, 0))
    for row in range(grid_size):
        for col in range(grid_size):
            index = row * grid_size + col + 1
            board.paste(render_tile(tile_w, tile_h, index, seed), (col * tile_w, row * tile_h))
    return board


def render_frame(width: int, height: int, index: int, seed: int = 0) -> Image.Image:
    return render_tile(width, height, index, seed)


def render_blank(size: tuple[int, int]) -> Image.Image:
    """纯色板：用于"格间无差异/无结构"的负例（无检测器可用的场景）。"""
    return Image.new("RGB", size, (12, 12, 12))


def png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def write_image(path: str | Path, image: Image.Image) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png_bytes(image))
    return path
