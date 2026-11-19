import hashlib
import io
import warnings

from PIL import Image, ImageOps

from .errors import VariationError, require

MAX_BYTES = 80 * 1024 * 1024
MAX_PIXELS = 64_000_000
FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_image(data, *, orient=False, mime=None):
    require(
        isinstance(data, bytes) and 0 < len(data) <= MAX_BYTES,
        "IMAGE_VALIDATION_FAILED",
        "图片为空或超出大小限制",
    )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as src:
                fmt = src.format
                require(fmt in FORMATS, "IMAGE_VALIDATION_FAILED", "图片格式须为 PNG / JPEG / WebP")
                require(src.width * src.height <= MAX_PIXELS, "IMAGE_VALIDATION_FAILED", "图片像素数超出限制")
                require(
                    mime is None or mime.split(";")[0].lower() == FORMATS[fmt],
                    "IMAGE_VALIDATION_FAILED",
                    "响应 MIME 与真实图片格式不符",
                )
                src.load()
                result = ImageOps.exif_transpose(src).copy() if orient else src.copy()
    except VariationError:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise VariationError("IMAGE_VALIDATION_FAILED", "图片无法解码") from None
    return {
        "image": result,
        "width": result.width,
        "height": result.height,
        "format": fmt.lower(),
        "mime": FORMATS[fmt],
        "sha256": sha256(data),
        "bytes": len(data),
    }


def png_bytes(image):
    out = io.BytesIO()
    # Remove EXIF/private metadata from derived images.
    clean = Image.new("RGBA" if "A" in image.getbands() else "RGB", image.size)
    clean.paste(image)
    clean.save(out, format="PNG")
    return out.getvalue()


def plan_board_geometry(w, h, n, target_ratio, tolerance=0.01):
    new_w, new_h = max(n, int(w / n + 0.5) * n), max(n, int(h / n + 0.5) * n)
    require(
        abs(new_w - w) / w <= tolerance and abs(new_h - h) / h <= tolerance,
        "GRID_VALIDATION_FAILED",
        "画板边长无法在允许容差内等分",
    )
    a, b = map(int, target_ratio.split(":"))
    require(
        abs((new_w / new_h) / (a / b) - 1) <= tolerance, "GRID_VALIDATION_FAILED", "几何对齐后比例超出容差"
    )
    return new_w, new_h


def split_board(data, n, target_ratio, tolerance=0.01):
    src = read_image(data)["image"]
    w, h = src.size
    new_w, new_h = plan_board_geometry(w, h, n, target_ratio, tolerance)
    if (new_w, new_h) != (w, h):
        src = src.resize((new_w, new_h), Image.Resampling.LANCZOS)
    tw, th = new_w // n, new_h // n
    tiles = []
    for i in range(n * n):
        row, col = divmod(i, n)
        box = (col * tw, row * th, (col + 1) * tw, (row + 1) * th)
        tiles.append(
            {
                "index": i + 1,
                "row": row + 1,
                "column": col + 1,
                "crop_box": list(box),
                "data": png_bytes(src.crop(box)),
            }
        )
    return (
        png_bytes(src),
        tiles,
        {
            "type": "minor_board_resample",
            "performed": (new_w, new_h) != (w, h),
            "original_dimensions": {"width": w, "height": h},
            "effective_dimensions": {"width": new_w, "height": new_h},
            "delta_width": new_w - w,
            "delta_height": new_h - h,
            "method": "lanczos" if (new_w, new_h) != (w, h) else "none",
        },
    )
