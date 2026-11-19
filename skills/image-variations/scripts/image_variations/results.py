"""Finalize immutable, validated image artifacts and their public inventory."""

import uuid
from pathlib import Path

from shared.artifacts import validate_artifact
from shared.paths import safe_path

from .dimensions import validate_native
from .errors import require
from .imaging import read_image, sha256, split_board
from .packaging import inventory, now, package_artifact, write_json
from .request import ratio
from .resources import contracts_root


def _asset(root, path, data, kind, index, task_id, **extra):
    target = safe_path(root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    info = read_image(data)
    return {
        "id": f"{task_id}-{kind}-{index:02}",
        "kind": kind,
        "index": index,
        "path": path,
        **{k: info[k] for k in ("width", "height", "format", "sha256", "bytes")},
        "status": "completed",
        **extra,
    }


def finalize_job(root, state):
    task = state["task"]
    passed = [u for u in state["units"] if u["status"] == "completed"]
    state["completed_at"] = now()
    if not passed:
        state["status"] = "failed"
        return
    state["status"] = "postprocessing"
    final_dest = Path(state["output_dir"]) / state["task_id"] / state["artifact_revision"]
    require(not final_dest.exists(), "ARTIFACT_EXISTS", "禁止覆盖已交付的产物修订")
    dest = final_dest.with_name(f".{state['artifact_revision']}-{uuid.uuid4().hex}.staging")
    for unit in passed:
        data = safe_path(root, unit["raw_path"]).read_bytes()
        require(sha256(data) == unit["sha256"], "ASSET_INTEGRITY_FAILED", "已通过的原始图片发生变化")
        validate_native(data, state["dimensions"])
    board_result = None
    if task["output_mode"] == "grid":
        board_result = split_board(
            safe_path(root, passed[0]["raw_path"]).read_bytes(), task["grid_size"], task["aspect_ratio"]
        )
    dest.mkdir(parents=True)
    images, dimensions = (
        [],
        {
            "dimension_policy": "provider_native",
            "requested_tier": task["resolution_tier"],
            "requested_ratio": task["aspect_ratio"],
            "mapping_kind": state["dimensions"]["mapping_kind"],
            "capability_profile_version": state["dimensions"]["capability_profile_version"],
            "provider_request_public": state["dimensions"]["provider_parameters"],
            "api_route": state["dimensions"]["api_route"],
            "model_id": state["dimensions"]["model_id"],
        },
    )
    if task["output_mode"] == "grid":
        unit = passed[0]
        data = safe_path(root, unit["raw_path"]).read_bytes()
        board, tiles, geometry = board_result
        ext = "jpg" if unit["native"]["format"] == "jpeg" else unit["native"]["format"]
        native_file = f"native/board.{ext}"
        (dest / "native").mkdir()
        (dest / native_file).write_bytes(data)
        images.append(_asset(dest, "grid_board.png", board, "board", 0, state["task_id"]))
        for tile in tiles:
            images.append(
                _asset(
                    dest,
                    f"tiles/{tile['index']:02}.png",
                    tile["data"],
                    "tile",
                    tile["index"],
                    state["task_id"],
                    **{k: tile[k] for k in ("row", "column", "crop_box")},
                )
            )
        dimensions.update(
            {
                **unit["native"],
                "effective_output": geometry["effective_dimensions"],
                "geometry_adjustment": geometry,
                "native_file": native_file,
                "native_file_sha256": sha256(data),
                "api_route_used": unit.get("metadata", {}).get(
                    "route_used", state["dimensions"]["api_route"]
                ),
            }
        )
    else:
        frames = []
        for unit in passed:
            data = safe_path(root, unit["raw_path"]).read_bytes()
            frame = state["compiled"]["creative"]["plan"]["frames"][unit["index"] - 1]
            ext = "jpg" if unit["native"]["format"] == "jpeg" else unit["native"]["format"]
            images.append(
                _asset(
                    dest,
                    f"sequence/{unit['index']:02}.{ext}",
                    data,
                    "frame",
                    unit["index"],
                    state["task_id"],
                    phase_label=frame.get("phase_label", ""),
                    story_context=frame.get("order_context", ""),
                    reference_sha256=unit["reference_sha256"],
                    sent_prompt_sha256=unit["sent_prompt_sha256"],
                )
            )
            frames.append(
                {
                    "index": unit["index"],
                    **unit["native"],
                    "effective_output": unit["native"]["native_output"],
                    "geometry_adjustment": {"performed": False},
                    "api_route_used": unit.get("metadata", {}).get(
                        "route_used", state["dimensions"]["api_route"]
                    ),
                }
            )
        dimensions["frames"] = frames
    final_status = "completed" if len(passed) == len(state["units"]) else "partial_failed"
    manifest = {
        "schema_version": "1.0",
        "artifact_type": "image_variations",
        "task_id": state["task_id"],
        "artifact_revision": state["artifact_revision"],
        "parent_artifact_revision": state.get("parent_artifact_revision"),
        "status": final_status,
        "input_type": task["input_type"],
        "output_mode": task["output_mode"],
        "direction": task["direction"],
        "branch": task["branch"],
        "grid_size": task["grid_size"],
        "image_count": task["image_count"],
        "requested_aspect_ratio": task["aspect_ratio"],
        "actual_aspect_ratio": ratio(f"{images[0]['width']}:{images[0]['height']}"),
        "requested_resolution_tier": task["resolution_tier"],
        "provider_type": state["dimensions"]["provider"],
        "model_id": state["dimensions"]["model_id"],
        "direction_registry_version": state["compiled"].get("direction_registry_version", "1.3.0"),
        "direction_rules_version": state["compiled"].get("direction_rules_version", "1.3.0"),
        "prompt_template_version": state["compiled"]["prompt_template_version"],
        "prompt_provenance": [
            {
                "index": unit["index"],
                "template_version": state["compiled"]["prompt_template_version"],
                "sent_prompt_sha256": unit["sent_prompt_sha256"],
                "returned_prompt_sha256": unit.get("metadata", {}).get("returned_prompt_sha256"),
                "provider_rewrite_status": "available_hash"
                if unit.get("metadata", {}).get("returned_prompt_sha256")
                else "not_exposed",
                "locally_repaired": "revised_prompt" in unit,
            }
            for unit in passed
        ],
        "created_at": state["created_at"],
        "completed_at": state["completed_at"],
        "images": images,
        "dimension_execution": dimensions,
        "failed_frames": [
            {"index": u["index"], "error": u.get("error")} for u in state["units"] if u["status"] == "failed"
        ],
        "errors": state["errors"],
        "retry_counts": state["retry_counts"],
        "field_sources": task["field_sources"],
        "issues": task["issues"],
        "consistency_anchor": {
            "source": "input_image"
            if state.get("reference_path")
            else "frame_01"
            if task["output_mode"] == "sequence"
            else "text_description",
            "sha256": state.get("reference_sha256")
            or (passed[0]["sha256"] if task["output_mode"] == "sequence" else None),
        },
        "validation": {
            "asset_integrity_passed": True,
            "grid_structure_passed": task["output_mode"] == "grid",
            "sequence_complete": task["output_mode"] == "sequence" and final_status == "completed",
            "visual_check_method": "synthetic_fixture"
            if state["environment"] == "mock"
            else "host_multimodal_P1-09",
        },
        "execution": {
            "calls_planned": state["dimensions"]["generation_units"],
            "calls_used": state["calls_used"],
            "call_budget_limit": task["call_budget_limit"],
            "cost_estimate": None,
            "budget_exceeded": any(e["code"] == "CALL_BUDGET_EXCEEDED" for e in state["errors"]),
            "degradations": [],
        },
        "synthetic_fixture": state["environment"] == "mock",
    }
    write_json(dest / "manifest.json", manifest)
    validate_artifact(dest, contracts_root(), require_complete=False)
    inventory(dest, state["task_id"] + "-" + state["artifact_revision"])
    dest.rename(final_dest)
    if task["package_zip"]:
        package_artifact(final_dest, final_dest.with_suffix(".zip"))
    state["status"], state["artifact_path"] = final_status, str(final_dest)
