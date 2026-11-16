"""Grid 后处理：像素对齐规范化 + 确定性切图 + 尺寸/比例校验。

顺序（SPEC 01 §7.4、packaging.md §1.1）：
    1. 原始整板解码；
    2. 规范化：把整板边长对齐到 N 的整数倍（对齐量受 board_side_tolerance_ratio 限制，
       超限判 GRID_VALIDATION_FAILED，不静默裁剪）；
    3. 比例校验：对齐后整板比例与请求比例之差必须 ≤ aspect_ratio_tolerance_ratio；
    4. 单格尺寸分级：长边 <768 记 TILE_RESOLUTION_RISK；长边 <512 判 TILE_RESOLUTION_TOO_LOW；
    5. 行优先切图，crop_box 整数、无重叠、无缝覆盖整板。
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

from PIL import Image

from shared.errors import IVError

from .config import load_config


@dataclass
class NormalizedBoard:
    image: Image.Image
    width: int
    height: int
    original_width: int
    original_height: int
    alignment_loss_px: int

    @property
    def alignment_loss_ratio(self) -> float:
        base = max(1, self.original_width * self.original_height)
        return self.alignment_loss_px / base


@dataclass
class Tile:
    index: int
    row: int
    column: int
    crop_box: tuple[int, int, int, int]
    image: Image.Image

    @property
    def size(self) -> tuple[int, int]:
        return self.image.size


@dataclass
class GridPostprocess:
    board: NormalizedBoard
    tiles: list[Tile]
    tile_size: tuple[int, int]
    warnings: list[dict] = field(default_factory=list)
    validation: dict = field(default_factory=dict)


def decode_board(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        return image.convert("RGB")
    except Exception as exc:  # noqa: BLE001
        raise IVError("IMAGE_VALIDATION_FAILED", f"整板无法解码: {exc}") from exc


def normalize_board(image: Image.Image, grid_size: int, config: dict | None = None) -> NormalizedBoard:
    """把边长对齐到 N 的整数倍（最多按比例裁剪，不做拉伸、不做补边）。"""
    config = config or load_config()
    tolerance = float(config["board_side_tolerance_ratio"])
    width, height = image.size
    target_w = max(grid_size, (width // grid_size) * grid_size)
    target_h = max(grid_size, (height // grid_size) * grid_size)
    removed = (width * height) - (target_w * target_h)
    loss_ratio = removed / max(1, width * height)
    if loss_ratio > tolerance:
        raise IVError("GRID_VALIDATION_FAILED",
                      f"整板 {width}×{height} 无法在不损失超过 {tolerance:.1%} 的前提下被 {grid_size} 整除"
                      f"（需裁掉 {loss_ratio:.2%}）",
                      loss_ratio=round(loss_ratio, 6))
    if (target_w, target_h) != (width, height):
        image = image.crop((0, 0, target_w, target_h))
    return NormalizedBoard(image=image, width=target_w, height=target_h,
                           original_width=width, original_height=height, alignment_loss_px=removed)


def ratio_of(width: int, height: int) -> float:
    return width / height


def check_ratio(width: int, height: int, requested: str, config: dict | None = None) -> float:
    config = config or load_config()
    num, den = (float(x) for x in requested.split(":"))
    target = num / den
    actual = ratio_of(width, height)
    error = abs(actual - target) / target
    if error > float(config["aspect_ratio_tolerance_ratio"]):
        raise IVError("GRID_VALIDATION_FAILED",
                      f"整板实际比例 {width}:{height}（{actual:.4f}）偏离请求 {requested}"
                      f"（误差 {error:.2%} > {config['aspect_ratio_tolerance_ratio']:.0%}）",
                      aspect_ratio_error_ratio=round(error, 6))
    return error


def split_tiles(board: Image.Image, grid_size: int) -> list[Tile]:
    """行优先确定性切图：单格尺寸 = (W/N, H/N)，格子无缝覆盖整板。"""
    width, height = board.size
    if width % grid_size or height % grid_size:
        raise IVError("GRID_VALIDATION_FAILED", f"整板 {width}×{height} 不能被 {grid_size} 整除")
    tile_w, tile_h = width // grid_size, height // grid_size
    tiles: list[Tile] = []
    for row in range(grid_size):
        for col in range(grid_size):
            index = row * grid_size + col + 1
            box = (col * tile_w, row * tile_h, (col + 1) * tile_w, (row + 1) * tile_h)
            tiles.append(Tile(index=index, row=row + 1, column=col + 1, crop_box=box,
                              image=board.crop(box)))
    return tiles


def check_tile_crop_coverage(tiles: list[Tile], board_size: tuple[int, int], grid_size: int) -> None:
    """crop_box 必须整数、无重叠、无缝覆盖整板（AC-14）。"""
    expected = set()
    tile_w, tile_h = board_size[0] // grid_size, board_size[1] // grid_size
    for row in range(grid_size):
        for col in range(grid_size):
            expected.add((col * tile_w, row * tile_h, (col + 1) * tile_w, (row + 1) * tile_h))
    actual = {tile.crop_box for tile in tiles}
    if actual != expected:
        raise IVError("GRID_VALIDATION_FAILED", "crop_box 集合与行优先坐标不一致（重叠或缺漏）")
    if [tile.index for tile in tiles] != list(range(1, grid_size * grid_size + 1)):
        raise IVError("GRID_VALIDATION_FAILED", "tile.index 必须是 1..N² 连续唯一")


def tile_edge_metrics(tile_size: tuple[int, int], config: dict | None = None) -> dict:
    """单格长边分级（§3.4）；硬下限用配置值。"""
    config = config or load_config()
    long_edge = max(tile_size)
    short_edge = min(tile_size)
    warnings: list[dict] = []
    if long_edge < config["tile_edge_hard_min_px"]:
        raise IVError("TILE_RESOLUTION_TOO_LOW",
                      f"单格长边 {long_edge}px 低于硬下限 {config['tile_edge_hard_min_px']}px",
                      tile_long_edge_px=long_edge)
    if long_edge < config["tile_edge_warn_px"]:
        warnings.append({"code": "TILE_RESOLUTION_RISK",
                         "message": f"单格长边 {long_edge}px 低于警告值 {config['tile_edge_warn_px']}px",
                         "severity": "warning"})
    elif long_edge < config["tile_edge_min_px"]:
        warnings.append({"code": "TILE_RESOLUTION_RISK",
                         "message": f"单格长边 {long_edge}px 低于推荐下限 {config['tile_edge_min_px']}px",
                         "severity": "warning"})
    return {
        "tile_edge_px": short_edge,
        "tile_long_edge_px": long_edge,
        "tile_edge_min_required_px": config["tile_edge_min_px"],
        "tile_edge_ok": long_edge >= config["tile_edge_min_px"],
        "warnings": warnings,
    }


def postprocess(board_bytes: bytes, *, grid_size: int, requested_aspect_ratio: str,
                config: dict | None = None) -> GridPostprocess:
    """整板解码 → 对齐 → 比例校验 → 切图 → 覆盖校验 → 单格分级。"""
    config = config or load_config()
    image = decode_board(board_bytes)
    normalized = normalize_board(image, grid_size, config)
    ratio_error = check_ratio(normalized.width, normalized.height, requested_aspect_ratio, config)
    tiles = split_tiles(normalized.image, grid_size)
    check_tile_crop_coverage(tiles, normalized.image.size, grid_size)
    metrics = tile_edge_metrics(tiles[0].size, config)
    validation = {
        "asset_integrity_passed": True,
        "grid_structure_passed": True,
        "aspect_ratio_error_ratio": round(ratio_error, 6),
        "tile_edge_px": metrics["tile_edge_px"],
        "tile_long_edge_px": metrics["tile_long_edge_px"],
        "tile_edge_min_required_px": metrics["tile_edge_min_required_px"],
        "tile_edge_ok": metrics["tile_edge_ok"],
        "tile_size_px": list(tiles[0].size),
        "board_size_px": [normalized.width, normalized.height],
        "board_original_size_px": [normalized.original_width, normalized.original_height],
        "alignment_loss_px": normalized.alignment_loss_px,
        "checks": ["decode_board", "align_to_grid", "ratio_within_tolerance",
                   "split_row_major", "crop_box_covers_board", "tile_size_uniform"],
    }
    return GridPostprocess(board=normalized, tiles=tiles, tile_size=tiles[0].size,
                           warnings=metrics["warnings"], validation=validation)
