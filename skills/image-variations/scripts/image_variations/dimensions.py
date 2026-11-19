import json
from copy import deepcopy
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

from .errors import VariationError, require
from .imaging import read_image
from .resources import contracts_root

PUBLIC_PARAMETERS = {"size", "image_size", "imageSize", "aspect_ratio", "aspectRatio"}


def plan_dimensions(task, profile, environment="external", endpoint=None):
    """Fail before generation unless this exact model, route and combination has evidence."""
    tier, ratio = task["resolution_tier"], task["aspect_ratio"]
    details = {
        "requested_tier": tier,
        "requested_ratio": ratio,
        "model_id": task.get("model", {}).get("model_id", profile.get("model_id")),
        "supported_options": {
            "tiers": profile.get("supported_native_tiers", []),
            "ratios": profile.get("supported_aspect_ratios", []),
        },
    }

    def check(ok, code, message):
        if not ok:
            raise VariationError(code, message, details=details)

    schema = json.loads((contracts_root() / "capability-profile.schema.json").read_text())
    check(
        not list(Draft202012Validator(schema).iter_errors(profile)),
        "UNVERIFIED_NATIVE_TIER",
        "能力档案结构无效；请依据 capability-profile schema 配置",
    )

    check(
        all(
            isinstance(profile.get(k), str) and profile[k]
            for k in ("provider_type", "model_id", "api_route", "profile_version")
        ),
        "MODEL_CAPABILITY_UNSUPPORTED",
        "请配置精确模型、路由和能力档案版本",
    )
    for key in ("provider_type", "model_id", "api_route"):
        selected = task.get("model", {}).get(key)
        check(
            selected is None or selected == profile[key],
            "MODEL_CAPABILITY_UNSUPPORTED",
            "能力档案与所选模型/路由不一致",
        )
    evidence = profile.get("verification", {})
    check(
        evidence.get("status") in {"verified", "documented", "mock"}
        and bool(evidence.get("verified_at"))
        and bool(evidence.get("source")),
        "UNVERIFIED_NATIVE_TIER",
        "目标原生档位尚未验证；请核验能力档案或显式进行能力试验",
    )
    check(
        (environment == "mock") == (evidence.get("status") == "mock"),
        "UNVERIFIED_NATIVE_TIER",
        "合成测试档案只能供 Mock Provider 使用",
    )
    check(
        profile.get("environment") == environment,
        "MODEL_CAPABILITY_UNSUPPORTED",
        "档案所属宿主/服务来源不一致",
    )
    if environment == "external":
        check(
            bool(endpoint) and endpoint.rstrip("/") == profile.get("base_url", "").rstrip("/"),
            "MODEL_CAPABILITY_UNSUPPORTED",
            "能力档案必须绑定实际选定的服务端点",
        )
        parsed_url = urlsplit(endpoint)
        check(
            parsed_url.scheme == "https"
            and not parsed_url.username
            and not parsed_url.password
            and not parsed_url.query
            and not parsed_url.fragment,
            "INVALID_ENDPOINT",
            "模型端点须为无凭据/令牌的 HTTPS URL",
        )
    check(
        tier in profile.get("supported_native_tiers", []),
        "UNSUPPORTED_RESOLUTION",
        "该型号不支持目标档位；请选择已声明档位",
    )
    check(
        ratio in profile.get("supported_aspect_ratios", []),
        "UNSUPPORTED_RATIO",
        "该型号不能原生表达目标比例；请另行选择支持比例",
    )
    needs_reference = bool(task.get("reference_image_ref")) or task["output_mode"] == "sequence"
    check(
        not needs_reference or profile.get("reference_image_input") is True,
        "REFERENCE_REQUIRED",
        "该任务需要真实图像参考输入；请配置支持参考图的型号",
    )
    if profile["provider_type"] == "openai_image_compatible":
        check(
            profile["api_route"] == "images/generations"
            and (not needs_reference or profile.get("reference_api_route") == "images/edits"),
            "REFERENCE_REQUIRED",
            "请同时验证 Images 生成与真实图片编辑路由",
        )
    if profile["provider_type"] == "gemini_image_compatible":
        check(
            profile["api_route"] in {"generateContent", "interactions"},
            "MODEL_CAPABILITY_UNSUPPORTED",
            "Gemini 档案路由无效",
        )
    combination = profile.get("tier_map", {}).get(tier, {}).get(ratio)
    check(bool(combination), "RESOLUTION_RATIO_COMBINATION_UNSUPPORTED", "该档位与比例无法同时满足")
    params = combination.get("parameters", {})
    check(
        bool(params) and not (set(params) - PUBLIC_PARAMETERS),
        "UNVERIFIED_NATIVE_TIER",
        "档位映射须为可审计的原生尺寸参数",
    )
    check(
        all(isinstance(v, str) and v and v != "auto" for v in params.values()),
        "UNVERIFIED_NATIVE_TIER",
        "auto 或质量参数不能证明原生分辨率档位",
    )
    expected = combination.get("expected_dimensions")
    bounds = combination.get("verified_dimension_range")
    check(bool(expected) or bool(bounds), "UNVERIFIED_NATIVE_TIER", "缺少实际原生像素的验收尺寸或已验证范围")
    mapping = profile.get("mapping_kind")
    check(
        mapping in {"native_tier_parameter", "verified_native_size_mapping"},
        "UNVERIFIED_NATIVE_TIER",
        "缺少原生映射类型",
    )
    if mapping == "verified_native_size_mapping":
        import re

        match = re.fullmatch(r"([1-9]\d*)x([1-9]\d*)", params.get("size", ""))
        check(
            bool(match) and bool(expected) and [int(match[1]), int(match[2])] in expected,
            "UNVERIFIED_NATIVE_TIER",
            "size 映射必须与已验证像素一致",
        )
    else:
        check(
            params.get("image_size", params.get("imageSize")) == tier
            and params.get("aspect_ratio", params.get("aspectRatio")) == ratio,
            "UNVERIFIED_NATIVE_TIER",
            "原生档位/比例参数与请求不一致",
        )
    tolerance = profile.get("output_tolerance_policy", {}).get("ratio_relative", 0.01)
    check(
        type(tolerance) in (float, int) and 0 <= tolerance <= 0.01,
        "UNVERIFIED_NATIVE_TIER",
        "比例验收容差不能大于 1%",
    )
    if expected:
        check(
            isinstance(expected, list)
            and all(
                isinstance(d, list) and len(d) == 2 and all(type(v) is int and 0 < v <= 32768 for v in d)
                for d in expected
            ),
            "UNVERIFIED_NATIVE_TIER",
            "原生像素表无效",
        )
    return deepcopy(
        {
            "dimension_policy": "provider_native",
            "scope": "board" if task["output_mode"] == "grid" else "frame",
            "requested_resolution_tier": tier,
            "requested_aspect_ratio": ratio,
            "provider": profile["provider_type"],
            "model_id": profile["model_id"],
            "api_route": profile["api_route"],
            "reference_api_route": profile.get("reference_api_route"),
            "capability_profile_version": profile["profile_version"],
            "mapping_kind": mapping,
            "provider_parameters": params,
            "expected_native_dimensions": expected,
            "verified_dimension_range": bounds,
            "ratio_tolerance": tolerance,
            "generation_units": 1 if task["output_mode"] == "grid" else task["image_count"],
            "status": "ready",
            "environment": environment,
        }
    )


def validate_native(data, plan, mime=None):
    info = read_image(data, mime=mime)
    w, h = info["width"], info["height"]
    expected, bounds = plan.get("expected_native_dimensions"), plan.get("verified_dimension_range")
    if expected:
        dimension_ok = [w, h] in expected
    elif bounds:
        dimension_ok = (
            bounds["width"][0] <= w <= bounds["width"][1]
            and bounds["height"][0] <= h <= bounds["height"][1]
            and bounds["pixels"][0] <= w * h <= bounds["pixels"][1]
        )
    else:
        dimension_ok = False
    a, b = map(int, plan["requested_aspect_ratio"].split(":"))
    ratio_error = abs((w / h) / (a / b) - 1)
    require(
        dimension_ok and ratio_error <= plan["ratio_tolerance"],
        "NATIVE_RESOLUTION_VALIDATION_FAILED",
        "原始返回图片像素或比例与能力档案冲突",
        actual_native_dimensions={"width": w, "height": h},
        ratio_error=ratio_error,
    )
    return {
        "native_output": {"width": w, "height": h},
        "sha256": info["sha256"],
        "format": info["format"],
        "ratio_error": ratio_error,
        "tier_validation": {"status": "passed", "method": "verified_profile_and_pixels"},
    }
