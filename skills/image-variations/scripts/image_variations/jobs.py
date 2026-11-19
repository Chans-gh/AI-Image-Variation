import copy
import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from filelock import FileLock

from shared.paths import safe_path

from .dimensions import plan_dimensions, validate_native
from .errors import VariationError, require
from .imaging import plan_board_geometry, png_bytes, read_image, sha256
from .packaging import now, write_json
from .planning import compile_plan
from .prompts import common_context, render_template
from .request import resolve_request
from .results import finalize_job as _finish

RETRY_CODES = {
    "NATIVE_RESOLUTION_VALIDATION_FAILED",
    "IMAGE_VALIDATION_FAILED",
    "GRID_VALIDATION_FAILED",
    "RATE_LIMITED",
    "PROVIDER_TIMEOUT",
    "PROVIDER_NETWORK_ERROR",
    "PROVIDER_UNAVAILABLE",
}
TERMINAL = {"completed", "partial_failed", "failed"}


def _load(root):
    return json.loads(safe_path(root, "job.json").read_text())


def _save(root, state):
    state["updated_at"] = now()
    write_json(Path(root) / "job.json", state)


def summary(state):
    return {
        "job_id": state["task_id"],
        "artifact_revision": state["artifact_revision"],
        "status": state["status"],
        "completed_count": sum(u["status"] == "completed" for u in state["units"]),
        "total_count": len(state["units"]),
        "calls_used": state["calls_used"],
        "pending_review": state.get("current") is not None,
        "units": [{k: u[k] for k in ("index", "status", "attempts")} for u in state["units"]],
        "errors": state["errors"],
        "artifact_path": state.get("artifact_path"),
    }


def _save_summary(root, state):
    result = summary(state)
    if (
        state["status"] == "completed"
        and state["task"]["recommend_content"]
        and not state.get("recommendation_delivered")
    ):
        result["recommendation"] = {
            "skill": "image-content-publishing",
            "message": "图片已完成，可继续生成公众号文章或小红书笔记。只有用户选择后才启动。",
            "source_task_id": state["task_id"],
            "source_artifact_revision": state["artifact_revision"],
        }
        state["recommendation_delivered"] = True
    _save(root, state)
    return result


def create_job(root, request, creative, profile, output_dir, *, environment="external", endpoint=None):
    root = Path(root)
    require(not root.exists(), "JOB_EXISTS", "任务目录已存在；请使用新目录或恢复接口")
    task = resolve_request(request, creative.get("task_parse"))
    dimensions = plan_dimensions(task, profile, environment, endpoint)
    compiled = compile_plan(task, creative)
    root.mkdir(parents=True, mode=0o700)
    state = {
        "task_id": uuid.uuid4().hex,
        "artifact_revision": "r1",
        "created_at": now(),
        "status": "queued",
        "task": task,
        "profile": profile,
        "dimensions": dimensions,
        "compiled": compiled,
        "output_dir": str(Path(output_dir).resolve()),
        "environment": environment,
        "calls_used": 0,
        "errors": [],
        "current": None,
        "events": [],
        "retry_counts": {},
    }
    state["events"].append({"status": "planning", "at": now()})
    if task["reference_image_ref"]:
        data = png_bytes(read_image(Path(task["reference_image_ref"]).read_bytes(), orient=True)["image"])
        (root / "input").mkdir(mode=0o700)
        (root / "input/reference.png").write_bytes(data)
        state["reference_path"], state["reference_sha256"] = "input/reference.png", sha256(data)
    state["units"] = [
        {"index": i, "status": "queued", "attempts": 0}
        for i in ([0] if task["output_mode"] == "grid" else range(1, task["image_count"] + 1))
    ]
    state["status"] = "generating"
    return _save_summary(root, state)


def get_status(root):
    return summary(_load(root))


def _reference(root, state, index):
    if state.get("reference_path"):
        path, expected = state["reference_path"], state["reference_sha256"]
    elif index > 1:
        first = state["units"][0]
        require(first["status"] == "completed", "SEQUENCE_ANCHOR_FAILED", "图1尚未通过，不能生成后续帧")
        path, expected = first["raw_path"], first["sha256"]
    else:
        return None
    data = safe_path(root, path).read_bytes()
    require(sha256(data) == expected, "ANCHOR_INTEGRITY_FAILED", "固定参考图发生变化；停止本组任务")
    return data


def _fail_unit(state, unit, error):
    item = {**error.as_dict(), "index": unit["index"], "attempt": unit["attempts"]}
    state["errors"].append(item)
    unit["error"] = item
    state["retry_counts"][str(unit["index"])] = max(unit["attempts"] - 1, 0)
    if (error.code in RETRY_CODES or error.retryable) and unit["attempts"] <= state["task"][
        "max_extra_retries"
    ]:
        unit["status"] = "queued"
        delay = max(error.retry_after, min(2 ** max(unit["attempts"] - 1, 0), 30))
        unit["retry_not_before"] = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
        unit["requires_retry_confirmation"] = error.code == "PROVIDER_TIMEOUT"
    else:
        unit["status"] = "failed"
        if unit["index"] == 1 and not state.get("reference_path"):
            for other in state["units"][1:]:
                other["status"] = "failed"
                other["error"] = {
                    "code": "SEQUENCE_ANCHOR_FAILED",
                    "index": other["index"],
                    "message": "纯文本首帧失败，未发起后续调用",
                }
    state["current"] = None
    state["status"] = "generating"


def step_job(root, provider, *, confirm_uncertain_retry=False):
    root = Path(root)
    with FileLock(str(root / ".lock"), timeout=2):
        state = _load(root)
        if state["status"] in TERMINAL or state.get("current"):
            return summary(state)
        require(
            not state.get("in_flight"),
            "UNCERTAIN_GENERATION",
            "上次进程在生图期间中断；请先恢复已生成图片，或明确确认未取得结果",
        )
        require(
            provider.environment == state["environment"],
            "MODEL_CAPABILITY_UNSUPPORTED",
            "Provider 来源与任务不一致",
        )
        unit = next((u for u in state["units"] if u["status"] == "queued"), None)
        if unit is None:
            _finish(root, state)
            _save(root, state)
            return summary(state)
        require(
            not unit.get("requires_retry_confirmation") or confirm_uncertain_retry,
            "RETRY_CONFIRMATION_REQUIRED",
            "上次请求超时，可能产生了图片；请核对后显式确认重试",
        )
        if unit.get("retry_not_before"):
            require(
                datetime.now(UTC) >= datetime.fromisoformat(unit["retry_not_before"]),
                "RETRY_BACKOFF_REQUIRED",
                "尚在退避窗口；请稍后重试",
            )
        if state["calls_used"] >= state["task"]["call_budget_limit"]:
            state["errors"].append({"code": "CALL_BUDGET_EXCEEDED", "message": "调用次数已达预算上限"})
            for u in state["units"]:
                if u["status"] == "queued":
                    u["status"] = "failed"
            _finish(root, state)
            _save(root, state)
            return summary(state)
        reference = _reference(root, state, unit["index"])
        unit["attempts"] += 1
        state["calls_used"] += 1
        # Persist intent before any external call. A process crash cannot trigger an automatic duplicate.
        state["in_flight"] = {"index": unit["index"], "attempt": unit["attempts"]}
        _save(root, state)
        prompt_index = 0 if state["task"]["output_mode"] == "grid" else unit["index"] - 1
        prompt = unit.get("revised_prompt", state["compiled"]["prompts"][prompt_index])
        plan = {**state["dimensions"], "unit_index": unit["index"], "grid_size": state["task"]["grid_size"]}
        try:
            generated = provider.generate(prompt, plan, reference)
            _accept_generation(root, state, unit, generated, reference, prompt)
        except VariationError as error:
            _fail_unit(state, unit, error)
        state.pop("in_flight", None)
        if not any(u["status"] in {"queued", "validating"} for u in state["units"]):
            _finish(root, state)
        return _save_summary(root, state)


def _accept_generation(root, state, unit, generated, reference, prompt):
    plan = state["dimensions"]
    raw_path = f"raw/{unit['index']:02}-attempt-{unit['attempts']}.bin"
    raw = safe_path(root, raw_path)
    raw.parent.mkdir(exist_ok=True)
    raw.write_bytes(generated.data)
    validation = validate_native(generated.data, plan, generated.mime)
    extension = "jpg" if validation["format"] == "jpeg" else validation["format"]
    raw_path = f"raw/{unit['index']:02}-attempt-{unit['attempts']}.{extension}"
    raw.rename(safe_path(root, raw_path))
    if state["task"]["output_mode"] == "grid":
        dimensions = validation["native_output"]
        plan_board_geometry(
            dimensions["width"],
            dimensions["height"],
            state["task"]["grid_size"],
            state["task"]["aspect_ratio"],
        )
    state["current"] = {
        "index": unit["index"],
        "attempt": unit["attempts"],
        "raw_path": raw_path,
        "sha256": sha256(generated.data),
        "native": validation,
        "reference_sha256": sha256(reference) if reference else None,
        "sent_prompt_sha256": sha256(prompt.encode()),
        "metadata": {
            k: v
            for k, v in generated.public_metadata.items()
            if k in {"route_used", "tool", "synthetic_fixture", "source", "returned_prompt_sha256"}
        },
    }
    state["status"] = "validating"
    ctx = common_context(state["task"], state["compiled"]["creative"])
    review_indices = _item_review_indices(state)
    qa_prompt = render_template(
        "P1-09",
        {
            **ctx,
            "ACTUAL_OUTPUT_IMAGES": [raw_path],
            "OUTPUT_MODE": state["task"]["output_mode"],
            "GENERATED_PLAN_JSON": state["compiled"]["creative"]["plan"],
            "DETERMINISTIC_VALIDATION_JSON": {**validation, "item_review_indices": review_indices},
        },
    )
    envelope = _review_identity(state)
    write_json(
        root / "review-request.json",
        {
            **envelope,
            "prompt": qa_prompt,
            "item_review_indices": review_indices,
            "image_path": raw_path,
            "reference_path": state.get("reference_path")
            or (state["units"][0].get("raw_path") if unit["index"] > 1 else None),
        },
    )
    unit["status"] = "validating"


def _review_identity(state):
    return {
        "task_id": state["task_id"],
        "artifact_revision": state["artifact_revision"],
        "plan_sha256": state["compiled"]["sha256"],
        "asset_sha256": state["current"]["sha256"],
        "index": state["current"]["index"],
        "attempt": state["current"]["attempt"],
    }


def _item_review_indices(state):
    if not state["compiled"].get("item_evidence_required"):
        return []
    if state["task"]["output_mode"] == "grid":
        return list(range(1, state["task"]["image_count"] + 1))
    return [state["current"]["index"]]


def _item_reviews_pass(result, indices):
    if not indices:
        return True
    items = result.get("item_reviews")
    require(
        isinstance(items, list) and len(items) == len(indices),
        "INVALID_REVIEW",
        "逐格或逐帧审核需要覆盖所有送审索引",
        expected_indices=indices,
    )
    fields = ("identity_ok", "target_ok", "direction_only_ok")
    reviewed = set()
    for item in items:
        require(
            isinstance(item, dict)
            and type(item.get("index")) is int
            and item["index"] in indices
            and item["index"] not in reviewed
            and isinstance(item.get("observed"), str)
            and bool(item["observed"].strip())
            and all(field in item and (item[field] is None or type(item[field]) is bool) for field in fields),
            "INVALID_REVIEW",
            "逐项审核需要唯一索引、可见证据与三个判断",
            expected_indices=indices,
        )
        reviewed.add(item["index"])
    return all(item[field] is True for item in items for field in fields)


def review_job(root, report):
    root = Path(root)
    with FileLock(str(root / ".lock"), timeout=2):
        state = _load(root)
        require(state.get("current") is not None, "INVALID_REVIEW", "没有等待检查的图片")
        require(
            all(report.get(k) == v for k, v in _review_identity(state).items()),
            "STALE_REVIEW",
            "检查报告与当前图片、尝试次数或计划不一致",
        )
        result = report.get("result")
        require(
            isinstance(result, dict)
            and type(result.get("pass")) is bool
            and isinstance(result.get("issues"), list)
            and all(isinstance(issue, dict) for issue in result["issues"]),
            "INVALID_REVIEW",
            "检查报告须为 P1-09 JSON",
        )
        current = state["current"]
        data = safe_path(root, current["raw_path"]).read_bytes()
        require(sha256(data) == current["sha256"], "ASSET_INTEGRITY_FAILED", "待检查图片被修改")
        validate_native(data, state["dimensions"])
        task = state["task"]
        evidence_passed = _item_reviews_pass(result, _item_review_indices(state))
        passed = (
            evidence_passed
            and result["pass"] is True
            and result.get("subject_consistency_ok") is True
            and result.get("direction_distinctness_ok") is True
            and not any(x.get("severity") in {"critical", "major"} for x in result["issues"])
        )
        if task["output_mode"] == "grid":
            passed = passed and result.get("grid_structure_ok") is True
        elif task["branch"] != "variants":
            passed = passed and result.get("sequence_order_ok") is True
        unit = next(u for u in state["units"] if u["index"] == current["index"])
        if passed:
            unit.update({**current, "status": "completed", "review": result})
            state["current"], state["status"] = None, "generating"
        else:
            unit["last_review"] = result
            _fail_unit(
                state,
                unit,
                VariationError(
                    "GRID_VALIDATION_FAILED" if task["output_mode"] == "grid" else "IMAGE_VALIDATION_FAILED",
                    "视觉检查未通过",
                ),
            )
        if not any(u["status"] == "queued" for u in state["units"]):
            _finish(root, state)
        return _save_summary(root, state)


def repair_prompt(root, repair):
    """Host P1-10 result: only the failed unit changes; model/anchor/parameters stay in code."""
    with FileLock(str(Path(root) / ".lock"), timeout=2):
        state = _load(root)
        require(
            state.get("current") is None and state["status"] == "generating",
            "INVALID_PLAN",
            "当前不能修复提示词",
        )
        unit = next((u for u in state["units"] if u["status"] == "queued" and u.get("last_review")), None)
        require(unit is not None, "INVALID_PLAN", "没有可局部修复的失败单元")
        prompt = repair.get("revised_prompt")
        require(
            isinstance(prompt, str)
            and 0 < len(prompt) <= 100000
            and isinstance(repair.get("fix_focus"), list)
            and isinstance(repair.get("unchanged_constraints"), list),
            "INVALID_PLAN",
            "修复须符合 P1-10 输出结构",
        )
        ctx = common_context(state["task"], state["compiled"]["creative"])
        require(
            ctx["DIRECTION_RULE"] in prompt
            and state["compiled"]["creative"]["anchor"]["identity_anchor"] in prompt,
            "INVALID_PLAN",
            "局部修复必须保留方向全文与主体锚点",
        )
        unit["revised_prompt"] = prompt
        return _save_summary(root, state)


def resume_job(source, target, failed_indices=None):
    source, target = Path(source), Path(target)
    with FileLock(str(source / ".lock"), timeout=2):
        state = copy.deepcopy(_load(source))
        require(
            state["task"]["output_mode"] == "sequence" and state["status"] in {"failed", "partial_failed"},
            "INVALID_INPUT",
            "只可对终止后的失败序列创建补生成修订",
        )
        require(not target.exists(), "JOB_EXISTS", "恢复目标须为新目录")
        failed = {u["index"] for u in state["units"] if u["status"] == "failed"}
        chosen = set(failed_indices) if failed_indices is not None else failed
        require(chosen and chosen <= failed, "INVALID_INPUT", "只能补生成失败索引，不可改写成功帧")
        if 1 in chosen and not state.get("reference_path"):
            require(
                not any(u["status"] == "completed" for u in state["units"]),
                "ANCHOR_REVISION_REQUIRED",
                "重建图1需要整组重建，不能沿用旧锚点帧",
            )
            chosen = failed
        previous = state["artifact_revision"]
        state["parent_artifact_revision"] = previous
        state["artifact_revision"] = f"r{int(previous[1:]) + 1}"
        target.mkdir(parents=True, mode=0o700)
        for name in ("raw", "input"):
            if (source / name).exists():
                shutil.copytree(source / name, target / name)
        for unit in state["units"]:
            if unit["index"] in chosen:
                unit["previous_attempts"] = unit["attempts"]
                unit["attempts"], unit["status"] = 0, "queued"
                for key in ("retry_not_before", "requires_retry_confirmation"):
                    unit.pop(key, None)
        state["task"]["call_budget_limit"] = state["calls_used"] + len(chosen) * (
            state["task"]["max_extra_retries"] + 1
        )
        state["status"], state["current"] = "generating", None
        for key in ("completed_at", "artifact_path", "in_flight"):
            state.pop(key, None)
        _save(target, state)
        return summary(state)


def recover_interrupted(root, *, image_path=None, confirm_no_result=False):
    from .adapters.base import GeneratedImage

    root = Path(root)
    with FileLock(str(root / ".lock"), timeout=2):
        state = _load(root)
        require(bool(state.get("in_flight")), "INVALID_INPUT", "没有中断中的调用")
        require(
            bool(image_path) != bool(confirm_no_result),
            "RETRY_CONFIRMATION_REQUIRED",
            "请导入上次调用的实际图片，或核对后明确确认未取得结果",
        )
        unit = next(u for u in state["units"] if u["index"] == state["in_flight"]["index"])
        if image_path:
            reference = _reference(root, state, unit["index"])
            index = 0 if state["task"]["output_mode"] == "grid" else unit["index"] - 1
            prompt = unit.get("revised_prompt", state["compiled"]["prompts"][index])
            generated = GeneratedImage(
                Path(image_path).read_bytes(), public_metadata={"source": "interrupted_call_recovered"}
            )
            try:
                _accept_generation(root, state, unit, generated, reference, prompt)
            except VariationError as error:
                _fail_unit(state, unit, error)
        else:
            _fail_unit(state, unit, VariationError("PROVIDER_TIMEOUT", "上次调用中断，用户确认未取得结果"))
            unit["requires_retry_confirmation"] = False
        state.pop("in_flight")
        if not any(u["status"] in {"queued", "validating"} for u in state["units"]):
            _finish(root, state)
        return _save_summary(root, state)
