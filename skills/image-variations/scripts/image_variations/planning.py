import json

from .errors import require
from .imaging import sha256
from .prompts import common_context, render_template
from .resources import prompt_template_version, registry


def canonical(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _visible_key(text):
    return "".join(c for c in text.casefold() if c.isalnum())


def compile_plan(task, creative):
    require(isinstance(creative, dict), "INVALID_PLAN", "创意计划须为对象")
    normalized, anchor, plan = [creative.get(k) for k in ("normalized", "anchor", "plan")]
    require(
        all(isinstance(v, dict) for v in (normalized, anchor, plan)),
        "INVALID_PLAN",
        "需要 normalized / anchor / plan 三组宿主模型输出",
    )
    normalized_fields = {
        "normalized_prompt",
        "subject_type",
        "subject",
        "identity_anchor",
        "scene",
        "style",
        "lighting",
        "composition",
        "quality",
        "keep_stable",
        "discarded",
    }
    require(set(normalized) == normalized_fields, "INVALID_PLAN", "归一化输出须完整保留 P1-02 字段")
    require(
        all(isinstance(normalized[k], str) for k in normalized_fields - {"keep_stable", "discarded"})
        and isinstance(normalized["keep_stable"], list)
        and isinstance(normalized["discarded"], list),
        "INVALID_PLAN",
        "归一化输出的文本与列表类型不符",
    )
    require(
        isinstance(anchor.get("identity_anchor"), str) and bool(anchor["identity_anchor"].strip()),
        "INVALID_PLAN",
        "缺少稳定主体锚点",
    )
    for field in ("immutable_identity", "direction_locked", "direction_allowed", "inferred_defaults"):
        require(isinstance(anchor.get(field), list), "INVALID_PLAN", "锚点缺少固定/允许变化分类", field=field)
    require(plan.get("direction") == task["direction"], "INVALID_PLAN", "计划与任务方向不一致")
    grid = task["output_mode"] == "grid"
    items = plan.get("cells" if grid else "frames")
    require(
        isinstance(items, list) and len(items) == task["image_count"], "INVALID_PLAN", "计划条数与任务不一致"
    )
    if grid:
        require(plan.get("grid_size") == task["grid_size"], "INVALID_PLAN", "计划网格规格不一致")
    else:
        require(
            plan.get("branch") == task["branch"] and plan.get("image_count") == task["image_count"],
            "INVALID_PLAN",
            "计划分支或序列张数不一致",
        )
        require(bool(plan.get("sequence_arc")), "INVALID_PLAN", "序列须有变化轴或叙事概况")
    registry_version, directions = registry()
    direction = directions[task["direction"]]
    fields = direction.get("plan_evidence_fields", ())
    changes, visual_targets, distinctive_cues = set(), set(), set()
    for i, item in enumerate(items, 1):
        require(
            isinstance(item, dict) and type(item.get("index")) is int and item["index"] == i,
            "INVALID_PLAN",
            "计划索引须连续、唯一且有序",
        )
        require(
            all(isinstance(item.get(k), str) and item[k].strip() for k in ("change_point", "visual_target")),
            "INVALID_PLAN",
            "每项须有独立变化点与完整画面目标",
        )
        cue = "".join(item["change_point"].lower().split())
        require(cue not in changes, "INVALID_PLAN", "变化点重复")
        changes.add(cue)
        if fields:
            distinctive = item.get("distinctive_cue")
            visible_key = _visible_key(distinctive) if isinstance(distinctive, str) else ""
            require(
                visible_key and visible_key != _visible_key(item["change_point"]),
                "INVALID_PLAN",
                "每项需要可指认的差异证据，不能复写变化名称",
                index=i,
            )
            target = _visible_key(item["visual_target"])
            require(
                visible_key not in distinctive_cues and target and target not in visual_targets,
                "INVALID_PLAN",
                "不同格位或帧重复使用同一画面目标或差异证据",
                index=i,
            )
            distinctive_cues.add(visible_key)
            visual_targets.add(target)
            evidence = item.get("direction_evidence")
            missing = [
                field
                for field in fields
                if not isinstance(evidence, dict)
                or not isinstance(evidence.get(field), str)
                or not _visible_key(evidence[field])
            ]
            require(
                not missing,
                "INVALID_PLAN",
                "缺少此方向的具体计划证据",
                index=i,
                direction=task["direction"],
                missing_fields=missing,
            )
        if grid:
            require(
                (item.get("row"), item.get("column"))
                == (1 + (i - 1) // task["grid_size"], 1 + (i - 1) % task["grid_size"]),
                "INVALID_PLAN",
                "画格坐标须按行优先排列",
            )
        elif task["branch"] in {"progression", "narrative"}:
            require(
                bool(item.get("phase_label")) and bool(item.get("order_context")),
                "INVALID_PLAN",
                "有序分支须记录阶段及前后关系",
            )
    ctx = common_context(task, creative)
    prompts = []
    if grid:
        prompts.append(render_template("P1-05", {**ctx, "GRID_PLAN_JSON": plan}))
    else:
        for item in items:
            prompts.append(
                render_template(
                    "P1-07" if item["index"] == 1 else "P1-08",
                    {
                        **ctx,
                        "SEQUENCE_ARC": plan["sequence_arc"],
                        "FRAME_PLAN_JSON": item,
                        "FRAME_INDEX": item["index"],
                        "ORDER_CONTEXT": item.get("order_context", ""),
                        "ANCHOR_IMAGE": "固定原图附件"
                        if task.get("reference_image_ref")
                        else "通过验证的第1张图片附件",
                    },
                )
            )
        require(len(set(prompts)) == len(prompts), "INVALID_PLAN", "逐帧提示词不能重复")
    result = {
        "creative": creative,
        "prompts": prompts,
        "prompt_template_version": prompt_template_version(),
        "direction_registry_version": registry_version,
        "direction_rules_version": direction["version"],
        "item_evidence_required": bool(fields),
    }
    result["sha256"] = sha256(canonical(result))
    return result
