"""接缝评分（`boundary_gradient_v1`，D-24）与框线检测。

按 SPEC 01 §3.4「无缝的可测量定义」逐条实现（阈值与 ε 都取配置）：

1. 边界梯度：对每条内部网格边界（N−1 条竖线、N−1 条横线），
   取**边界两侧各 `band_px`**，`boundary_diff` = 两侧条带逐行均值之差的平均绝对差；
2. 内部参考：同一边界**附近、各自格内**相邻像素的平均绝对差 `interior_diff`；
3. `seam_ratio = boundary_diff / (interior_diff + ε)`；
4. `seam_score = clamp(1 − (seam_ratio − 1) / 2, 0, 1)`；
5. 分级：`≥0.85` 通过；`0.60–0.85` 记 `GRID_SEAM_RISK`（可交付但必须提示）；`<0.60` 失败。

实现要点：
    - 用**局部**梯度做参考而不是全图均值。画面不同区域的纹理密度天然不同，
      用全图均值会把"本来就细腻的格线"误判成接缝、把"本来就粗糙处"的接缝漏掉。
    - 只依赖 Pillow（`ImageChops.difference` + `ImageStat`），不引入 numpy。

框线检测（两条独立规则，任一命中即判有可见接缝）：
    - 边界处存在高对比硬线：`|原图 − 1px 模糊|` 在边界线上的均值 ≥ `min_contrast`
      且该带方差高于全图参考（排除内容本身的高频）；
    - 边界列/行标准差 < `blank_band_std_max`：纯色留白带。
"""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageChops, ImageFilter, ImageStat

from .config import load_config


@dataclass
class SeamReport:
    score: float
    method: str
    boundary_means: list[float]
    interior_means: list[float]
    border_lines: list[str]
    seam_ratio: float = 1.0

    @property
    def has_border_line(self) -> bool:
        return bool(self.border_lines)

    def to_validation(self) -> dict:
        return {
            "seam_score": round(self.score, 6),
            "seam_check_method": self.method,
            "seam_ratio": round(self.seam_ratio, 6),
            "seam_boundary_mean": round(_mean(self.boundary_means), 6),
            "seam_interior_mean": round(_mean(self.interior_means), 6),
            "seam_border_lines": list(self.border_lines),
        }


def _mean(values: list[float]) -> float:
    return float(sum(values)) / len(values) if values else 0.0


def _mean_abs_diff(first: Image.Image, second: Image.Image) -> float:
    """两张同尺寸图的逐通道平均绝对差。"""
    stat = ImageStat.Stat(ImageChops.difference(first, second))
    return sum(stat.mean) / len(stat.mean)


def _strip_mean(image: Image.Image, axis: str, start: int, band: int) -> Image.Image | None:
    """把 `band` 像素宽的条带按 BOX 滤波压成 1 像素宽，得到该侧的代表值。"""
    width, height = image.size
    end = start + band
    if start < 0 or end > (height if axis == "horizontal" else width):
        return None
    if axis == "vertical":
        strip = image.crop((start, 0, end, height))
        return strip.resize((1, height), Image.BOX)
    strip = image.crop((0, start, width, end))
    return strip.resize((width, 1), Image.BOX)


def _boundary_diff(image: Image.Image, axis: str, index: int, band: int) -> float | None:
    """跨边界跳变量：两侧各 band 像素的代表值之差的平均绝对值。"""
    near = _strip_mean(image, axis, index - band, band)
    far = _strip_mean(image, axis, index, band)
    if near is None or far is None:
        return None
    return _mean_abs_diff(near, far)


def _pair(image: Image.Image, axis: str, first: int, second: int) -> float | None:
    width, height = image.size
    limit = width if axis == "vertical" else height
    if not (0 <= first < limit and 0 <= second < limit):
        return None
    if axis == "vertical":
        return _mean_abs_diff(image.crop((first, 0, first + 1, height)),
                              image.crop((second, 0, second + 1, height)))
    return _mean_abs_diff(image.crop((0, first, width, first + 1)),
                          image.crop((0, second, width, second + 1)))


# 格内参考位置：与边界使用**完全相同的几何**（band 宽条带、band 间距），
# 只是整体挪到格内，避免"用 1px 差去比 2px 差"的口径偏差。
LOCAL_OFFSETS = (4, 6, 8)


def _interior_diff(image: Image.Image, axis: str, index: int, band: int) -> list[float]:
    """格内参考量：同样的条带对比，但整条挪进格子里。"""
    values: list[float] = []
    for distance in LOCAL_OFFSETS:
        for position in (index - distance, index + distance):
            value = _boundary_diff(image, axis, position, band)
            if value is not None:
                values.append(value)
    return values


def _is_visible_line(grey: Image.Image, index: int, axis: str, *, band: int,
                     std_threshold: float, contrast_threshold: float,
                     reference_std: float) -> bool:
    """该格线是否"被画出来了"（高对比硬线或纯色留白带）。"""
    width, height = grey.size
    if axis == "vertical":
        strip = grey.crop((max(0, index - band), 0, min(width, index + band), height))
        line = grey.crop((index, 0, index + 1, height))
        blurred = grey.filter(ImageFilter.GaussianBlur(radius=1.0)).crop((index, 0, index + 1, height))
    else:
        strip = grey.crop((0, max(0, index - band), width, min(height, index + band)))
        line = grey.crop((0, index, width, index + 1))
        blurred = grey.filter(ImageFilter.GaussianBlur(radius=1.0)).crop((0, index, width, index + 1))
    if strip.size[0] == 0 or strip.size[1] == 0:
        return False
    strip_std = ImageStat.Stat(strip).stddev[0]
    if strip_std < std_threshold:
        return True
    jump = ImageStat.Stat(ImageChops.difference(line, blurred)).mean[0]
    return jump >= contrast_threshold and strip_std > reference_std


def measure_seam(image: Image.Image, grid_size: int, config: dict | None = None) -> SeamReport:
    """对整板做接缝评分。image 必须是已规范化（边长可被 N 整除）的整板。"""
    config = config or load_config()
    seam_cfg = config["seam"]
    band = int(seam_cfg["band_px"])
    epsilon = float(seam_cfg["epsilon"])
    line_cfg = seam_cfg["border_line_detect"]
    min_contrast = float(line_cfg["min_contrast"])
    std_max = float(line_cfg["blank_band_std_max"])

    rgb = image.convert("RGB")
    grey = rgb.convert("L")
    width, height = rgb.size
    tile_w, tile_h = width // grid_size, height // grid_size
    reference_std = ImageStat.Stat(grey).stddev[0]

    boundary_means: list[float] = []
    interior_means: list[float] = []
    border_lines: list[str] = []

    for index in range(1, grid_size):
        x = index * tile_w
        value = _boundary_diff(rgb, "vertical", x, band)
        if value is not None:
            boundary_means.append(value)
        interior_means.extend(_interior_diff(rgb, "vertical", x, band))
        if _is_visible_line(grey, x, "vertical", band=band, std_threshold=std_max,
                            contrast_threshold=min_contrast, reference_std=reference_std):
            border_lines.append(f"v{x}")

    for index in range(1, grid_size):
        y = index * tile_h
        value = _boundary_diff(rgb, "horizontal", y, band)
        if value is not None:
            boundary_means.append(value)
        interior_means.extend(_interior_diff(rgb, "horizontal", y, band))
        if _is_visible_line(grey, y, "horizontal", band=band, std_threshold=std_max,
                            contrast_threshold=min_contrast, reference_std=reference_std):
            border_lines.append(f"h{y}")

    boundary = _mean(boundary_means)
    interior = _mean(interior_means)
    seam_ratio = boundary / (interior + epsilon)
    score = min(1.0, max(0.0, 1.0 - (seam_ratio - 1.0) / 2.0))
    return SeamReport(score=score, method=seam_cfg["method"], boundary_means=boundary_means,
                      interior_means=interior_means, border_lines=border_lines,
                      seam_ratio=seam_ratio)


def classify_seam(report: SeamReport, config: dict | None = None) -> tuple[str, str | None]:
    """返回 (verdict, code)：verdict ∈ pass / risk / fail。"""
    config = config or load_config()
    seam_cfg = config["seam"]
    if report.has_border_line:
        return "fail", "GRID_VALIDATION_FAILED"
    if report.score >= seam_cfg["pass_threshold"]:
        return "pass", None
    if report.score >= seam_cfg["risk_threshold"]:
        return "risk", "GRID_SEAM_RISK"
    return "fail", "GRID_VALIDATION_FAILED"
