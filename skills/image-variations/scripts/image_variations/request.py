import re
from fractions import Fraction
from pathlib import Path

from .errors import VariationError, require
from .imaging import read_image
from .resources import registry


def ratio(value):
    require(
        isinstance(value, str) and re.fullmatch(r"[1-9]\d*:[1-9]\d*", value),
        "INVALID_INPUT",
        "比例须为正整数宽:高",
    )
    a, b = map(int, value.split(":"))
    f = Fraction(a, b)
    return f"{f.numerator}:{f.denominator}"


def resolve_request(raw, parsed=None):
    """Resolve host TaskParse, with explicit structured selections taking priority."""
    require(isinstance(raw, dict), "INVALID_INPUT", "任务须为 JSON 对象")
    parsed = dict(parsed or {})
    require(isinstance(parsed, dict), "INVALID_INPUT", "TaskParse 须为对象")
    if parsed.get("clarification_needed"):
        raise VariationError(
            "CLARIFICATION_REQUIRED", parsed.get("clarification_question") or "请明确图片输出方式"
        )
    inp, out = raw.get("input", {}), raw.get("output", {})
    var, model = raw.get("variation", {}), raw.get("model", {})
    require(all(isinstance(x, dict) for x in (inp, out, var, model)), "INVALID_INPUT", "参数分组须为对象")
    require(
        not any(k in model for k in ("api_key", "key", "token", "authorization")),
        "INVALID_INPUT",
        "凭据只允许通过本地环境变量或宿主密钥解析器提供",
    )
    text = inp.get("user_prompt", "")
    require(
        isinstance(text, str) and len(text) <= 20000, "INVALID_INPUT", "用户描述须为不超过 20000 字符的文本"
    )
    text = text.strip()
    # Recognize unambiguous structural syntax as a fallback; creative interpretation belongs to P1-01.
    for key, pattern in [
        ("aspect_ratio", r"(?<!\d)([1-9]\d*\s*[:：]\s*[1-9]\d*)(?!\d)"),
        ("resolution_tier", r"(?<!\w)([124]\s*[Kk])(?!\w)"),
    ]:
        found = {re.sub(r"\s", "", v).replace("：", ":").upper() for v in re.findall(pattern, text)}
        if len(found) == 1 and key not in parsed:
            parsed[key] = found.pop()
        elif len(found) > 1 and out.get(key) is None:
            raise VariationError("CLARIFICATION_REQUIRED", "文字描述包含冲突规格，请明确一个值")
    grid_match = re.search(r"([1-9]\d*)\s*[×xX]\s*([1-9]\d*)", text)
    if grid_match and parsed.get("output_mode") is None and out.get("mode") is None:
        require(grid_match[1] == grid_match[2], "INVALID_MODE_OR_COUNT", "只支持正方行列数的 NxN 网格")
        parsed.update(output_mode="grid", grid_size=int(grid_match[1]))
    if "九宫格" in text and parsed.get("output_mode") is None and out.get("mode") is None:
        parsed.update(output_mode="grid", grid_size=3)
    if re.search(r"独立图片|独立出图|连续输出|逐张", text) and parsed.get("output_mode") is None:
        parsed["output_mode"] = "sequence"
        count_match = re.search(r"(\d+)\s*张", text)
        if count_match:
            parsed.setdefault("image_count", int(count_match[1]))
    image = inp.get("reference_image_ref")
    require(not isinstance(image, (list, dict)), "INVALID_INPUT", "仅支持一张参考图")
    require(bool(text or image), "INVALID_INPUT", "请提供图片或有效文本")
    input_type = "image_plus_text" if image and text else "image_only" if image else "text_only"
    reference = None
    if image:
        require(isinstance(image, str), "INVALID_INPUT", "参考图须为本地文件路径")
        path = Path(image).expanduser().resolve()
        require(path.is_file(), "INVALID_INPUT", "参考图不存在")
        reference = read_image(path.read_bytes(), orient=True)
        image = str(path)
    sources, issues = {}, []

    def pick(key, explicit, default):
        if explicit is not None:
            sources[key] = "explicit"
            if parsed.get(key) is not None and parsed[key] != explicit:
                issues.append({"field": key, "code": "STRUCTURED_SELECTION_PREVAILS"})
            return explicit
        if parsed.get(key) is not None:
            sources[key] = parsed.get("field_sources", {}).get(key, "inferred")
            return parsed[key]
        sources[key] = "proposed_default"
        return default

    if out.get("mode") is None and parsed.get("output_mode") is None:
        require(
            not re.search(r"(?:\d+|[四五六七八九十]+)\s*张", text),
            "CLARIFICATION_REQUIRED",
            "需要网格作品板，还是逐张独立图片？",
        )
    mode = pick("output_mode", out.get("mode"), "grid")
    require(
        isinstance(mode, str) and mode in {"grid", "sequence"},
        "INVALID_MODE_OR_COUNT",
        "模式仅支持 grid / sequence",
    )
    _, directions = registry()
    direction = pick("direction", var.get("direction"), "state")
    require(not isinstance(direction, list), "MULTIPLE_DIRECTIONS", "单次任务只选择一个变化方向")
    require(isinstance(direction, str) and direction in directions, "INVALID_INPUT", "未知变化方向")
    if mode == "grid":
        require(
            out.get("image_count") is None and out.get("branch") is None,
            "INVALID_MODE_OR_COUNT",
            "Grid 的数量由网格推导，不能设置分支或独立张数",
        )
        require(parsed.get("branch") is None, "INVALID_MODE_OR_COUNT", "Grid 不接收序列分支")
        size = pick("grid_size", out.get("grid_size"), 3)
        require(
            type(size) is int and size in (2, 3, 4), "INVALID_MODE_OR_COUNT", "网格只能为 2×2 / 3×3 / 4×4"
        )
        count, branch, default_tier = size * size, None, {2: "1K", 3: "2K", 4: "4K"}[size]
    else:
        require(
            out.get("grid_size") is None and parsed.get("grid_size") is None,
            "INVALID_MODE_OR_COUNT",
            "Sequence 不设置 grid_size",
        )
        size = None
        count = pick("image_count", out.get("image_count"), 4)
        require(type(count) is int and 4 <= count <= 16, "INVALID_MODE_OR_COUNT", "序列张数须为 4–16 的整数")
        branch = pick("branch", out.get("branch"), directions[direction]["branch_default"])
        require(
            branch in ("variants", "progression", "narrative"), "INVALID_INPUT", "序列只能选择一个有效分支"
        )
        default_tier = "1K"
    inherited = f"{reference['width']}:{reference['height']}" if reference else "3:4"
    requested_ratio = pick("aspect_ratio", out.get("aspect_ratio"), inherited)
    if requested_ratio == "inherited":
        requested_ratio = inherited
    if sources["aspect_ratio"] == "proposed_default":
        sources["aspect_ratio"] = "inherited" if image else "confirmed_default"
    tier = pick("resolution_tier", out.get("resolution_tier"), default_tier)
    if sources["resolution_tier"] == "proposed_default":
        sources["resolution_tier"] = "confirmed_default"
    require(tier in ("1K", "2K", "4K"), "INVALID_INPUT", "分辨率档位须为 1K / 2K / 4K")
    retries = raw.get("max_extra_retries", 2)
    require(type(retries) is int and 0 <= retries <= 2, "INVALID_INPUT", "额外重试次数支持 0–2")
    budget = raw.get("call_budget_limit", count * (retries + 1) if mode == "sequence" else retries + 1)
    require(
        type(budget) is int and budget >= (count if mode == "sequence" else 1),
        "INVALID_INPUT",
        "调用预算小于完成任务所需的最少调用数",
    )
    return {
        "input_type": input_type,
        "user_prompt": text,
        "reference_image_ref": image,
        "output_mode": mode,
        "grid_size": size,
        "image_count": count,
        "direction": direction,
        "branch": branch,
        "aspect_ratio": ratio(requested_ratio),
        "resolution_tier": tier,
        "model": model,
        "field_sources": sources,
        "issues": issues,
        "max_extra_retries": retries,
        "call_budget_limit": budget,
        "package_zip": bool(out.get("package_zip", False)),
        "recommend_content": not raw.get("images_only", False),
    }
