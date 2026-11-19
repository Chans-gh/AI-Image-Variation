import json
import re

from .errors import require
from .resources import REFERENCES, registry

SLOT = re.compile(r"\{\{([A-Z][A-Z_0-9]*)\}\}")


def as_text(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def render_text(template, context):
    missing = set(SLOT.findall(template)) - context.keys()
    require(not missing, "INVALID_PLAN", "提示词缺少必要参数", missing=sorted(missing))
    # One substitution pass: user text never becomes another template.
    return SLOT.sub(lambda m: as_text(context[m[1]]), template)


def render_template(template_id, context):
    require(template_id in {f"P1-{i:02}" for i in range(1, 11)}, "INVALID_PLAN", "未知提示词模板")
    return render_text((REFERENCES / "prompts" / f"{template_id}.txt").read_text(), context)


def common_context(task, creative):
    _, directions = registry()
    ctx = {
        "USER_REQUEST": task["user_prompt"],
        "INPUT_TYPE": task["input_type"],
        "HAS_INPUT_IMAGE": bool(task.get("reference_image_ref")),
        "STRUCTURED_SELECTIONS_JSON": task,
        "MODEL_CAPABILITIES_JSON": {},
        "TASK_PARSE_JSON": creative.get("task_parse", {}),
        "IMAGE_OBSERVATIONS_JSON": creative.get("image_observations", {}),
        "NORMALIZED_VISUAL_JSON": creative["normalized"],
        "IDENTITY_ANCHOR_JSON": creative["anchor"],
        "USER_CONSTRAINTS_JSON": creative.get("user_constraints", {}),
        "DIRECTION_KEY": task["direction"],
        "GRID_SIZE": task["grid_size"],
        "ITEM_COUNT": task["image_count"],
        "BRANCH": task["branch"] or "not_applicable_grid",
        "ASPECT_RATIO": task["aspect_ratio"],
        "RESOLUTION_TIER": task["resolution_tier"],
        "REFERENCE_IMAGE_CONTEXT": "原始参考图已作为实际图像输入附件"
        if task.get("reference_image_ref")
        else "无原图",
    }
    ctx["DIRECTION_RULE"] = render_text(directions[task["direction"]]["text"], ctx)
    ctx["BRANCH_RULE"] = (
        (REFERENCES / "prompts" / f"branch-{task['branch']}.txt").read_text()
        if task["branch"]
        else "不适用；Grid 使用离散画格规则"
    )
    return ctx
