import io
from fractions import Fraction

from PIL import Image, ImageOps

from .contracts import platform_config, require


def _cover_size(preset, requested):
    horizontal, vertical = map(Fraction, preset["target_ratio"].split(":"))
    ratio = horizontal / vertical
    recommended = preset["product_recommendation"]
    default = recommended.get("sizes_px")
    if default:
        default = default[0]
    else:
        width = recommended["long_edge_px"]
        default = [width, round(width / ratio)]
    size = default if requested is None else requested
    require(
        isinstance(size, list) and len(size) == 2 and all(type(v) is int and 1 <= v <= 4096 for v in size),
        "COVER_FIT_FAILED",
        "封面需要有效的目标宽高",
    )
    width, height = size
    require(abs(height - width / ratio) <= 0.5, "COVER_FIT_FAILED", "封面尺寸与指定比例不符")
    return width, height


def _subject_box(subject, width, height):
    if subject is None:
        return (0, 0, width, height)
    require(
        isinstance(subject, list) and len(subject) == 4 and all(type(x) is int for x in subject),
        "COVER_FIT_FAILED",
        "主体安全区需提供四个像素坐标",
    )
    left, top, right, bottom = subject
    require(
        0 <= left < right <= width and 0 <= top < bottom <= height,
        "COVER_FIT_FAILED",
        "主体安全区超出源图",
    )
    return tuple(subject)


def _crop_box(size, target, subject):
    width, height = size
    ratio = Fraction(*target)
    crop_width = min(width, max(1, round(height * ratio)))
    crop_height = min(height, max(1, round(width / ratio)))
    left, top, right, bottom = subject
    if right - left > crop_width or bottom - top > crop_height:
        return None
    x = min(max(0, round((left + right - crop_width) / 2)), width - crop_width)
    y = min(max(0, round((top + bottom - crop_height) / 2)), height - crop_height)
    return (x, y, x + crop_width, y + crop_height)


def make_covers(data, asset, proposal):
    config = platform_config()
    maximum = config["global"]["max_cover_upscale_factor"]
    sizes = proposal.get("sizes", {})
    require(
        isinstance(sizes, dict) and sizes.keys() <= {"wide", "square"},
        "COVER_FIT_FAILED",
        "封面尺寸仅接受 wide 和 square",
    )
    result = {}
    with Image.open(io.BytesIO(data)) as source:
        image = source.convert("RGBA")
    subject = _subject_box(proposal.get("subject_box"), *image.size)
    for name, preset in config["platforms"]["wechat"]["covers"].items():
        target = _cover_size(preset, sizes.get(name))
        crop = _crop_box(image.size, target, subject)
        if crop and max(target) > maximum * max(crop[2] - crop[0], crop[3] - crop[1]):
            crop = None
        box = crop or (0, 0, *image.size)
        content = image.crop(box)
        resize_factor = min(target[0] / content.width, target[1] / content.height)
        upscale_factor = max(1.0, max(target) / max(content.size))
        require(
            upscale_factor <= maximum,
            "COVER_SOURCE_LOWRES",
            "裁切后源图无法满足封面尺寸",
            asset_id=asset["id"],
            cover=name,
            upscale_factor=round(upscale_factor, 6),
        )
        scaled = ImageOps.contain(content, target, Image.Resampling.LANCZOS)
        output = Image.new("RGB", target, "#f4f1ee")
        output.paste(scaled, ((target[0] - scaled.width) // 2, (target[1] - scaled.height) // 2), scaled)
        with io.BytesIO() as buffer:
            output.save(buffer, format="PNG")
            data = buffer.getvalue()
        result[name] = (
            {
                "source_asset_id": asset["id"],
                "source_asset_long_edge_px": max(image.size),
                "subject_box": list(subject),
                "crop_box": list(box),
                "crop_long_edge_px": max(content.size),
                "target_ratio": preset["target_ratio"],
                "actual_width": target[0],
                "actual_height": target[1],
                "fit_method": "crop" if crop else "contain_pad",
                "resize_scale_factor": round(resize_factor, 8),
                "upscale_factor": round(upscale_factor, 8),
                "quality_risk": upscale_factor > 1.0,
            },
            data,
        )
    return result
