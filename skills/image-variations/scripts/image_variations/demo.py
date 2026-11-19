import json
from pathlib import Path

from .adapters.mock import MockProvider, fixture_profile
from .jobs import create_job, get_status, review_job, step_job
from .resources import registry


def fixture_creative(task):
    anchor = "同一个合成几何物件，椭圆形主体和固定底色；仅用于软件测试。"
    normalized = {
        "normalized_prompt": anchor,
        "subject_type": "object",
        "subject": "synthetic test object",
        "identity_anchor": anchor,
        "scene": "synthetic fixture",
        "style": "flat shapes",
        "lighting": "uniform",
        "composition": "center",
        "quality": "fixture",
        "keep_stable": ["object identity"],
        "discarded": [],
    }
    anchor_obj = {
        "subject_type": "object",
        "identity_anchor": anchor,
        "immutable_identity": ["ellipse"],
        "direction_locked": [],
        "direction_allowed": [task["direction"]],
        "inferred_defaults": [],
        "visual_world": "synthetic fixtures",
        "source": "text_description",
    }
    items = [
        {
            "index": i,
            "change_point": f"{task['direction']} fixture variation {i}",
            "visual_target": f"Synthetic visible state {i}",
            "phase_label": f"phase {i}",
            "order_context": f"phase {i} of {task['image_count']}",
            "locked_elements": [],
        }
        for i in range(1, task["image_count"] + 1)
    ]
    fields = registry()[1][task["direction"]].get("plan_evidence_fields")
    if fields:
        for item in items:
            item["distinctive_cue"] = f"Synthetic observable shape at position {item['index']}"
            item["direction_evidence"] = {
                field: f"Synthetic {field} evidence at position {item['index']}" for field in fields
            }
    if task["output_mode"] == "grid":
        n = task["grid_size"]
        for item in items:
            item.update(
                row=(item["index"] - 1) // n + 1,
                column=(item["index"] - 1) % n + 1,
                distinctive_cue=item.get("distinctive_cue", item["change_point"]),
                must_preserve=["identity"],
            )
        plan = {"direction": task["direction"], "grid_size": n, "cells": items}
    else:
        plan = {
            "direction": task["direction"],
            "branch": task["branch"],
            "image_count": task["image_count"],
            "sequence_arc": "Synthetic indexed phases",
            "frames": items,
        }
    return {"normalized": normalized, "anchor": anchor_obj, "plan": plan}


def fixture_review(job_root):
    request = json.loads((Path(job_root) / "review-request.json").read_text())
    return {
        k: request[k]
        for k in ("task_id", "artifact_revision", "plan_sha256", "asset_sha256", "index", "attempt")
    } | {
        "result": {
            "pass": True,
            "grid_structure_ok": True,
            "subject_consistency_ok": True,
            "direction_distinctness_ok": True,
            "sequence_order_ok": True,
            "issues": [],
            "retry_suggestion": "",
            "method": "synthetic_fixture_only",
            "item_reviews": [
                {
                    "index": i,
                    "observed": "Synthetic fixture for execution tests",
                    "identity_ok": True,
                    "target_ok": True,
                    "direction_only_ok": True,
                }
                for i in request.get("item_review_indices", [])
            ],
        }
    }


def run_demo(job_root, output_dir, mode="grid", grid_size=3, count=4, branch="variants"):
    from .request import resolve_request

    request = {
        "input": {"user_prompt": "合成图形离线开发测试"},
        "variation": {"direction": "state"},
        "output": {"mode": mode, "package_zip": True},
        "images_only": True,
    }
    if mode == "grid":
        request["output"]["grid_size"] = grid_size
    else:
        request["output"].update(image_count=count, branch=branch)
    task = resolve_request(request)
    create_job(job_root, request, fixture_creative(task), fixture_profile(), output_dir, environment="mock")
    provider = MockProvider()
    while get_status(job_root)["status"] not in {"completed", "partial_failed", "failed"}:
        state = step_job(job_root, provider)
        if state["pending_review"]:
            review_job(job_root, fixture_review(job_root))
    return get_status(job_root)
