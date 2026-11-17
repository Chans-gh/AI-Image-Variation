#!/usr/bin/env python3
"""12 方向跑测台：批量跑 Grid / Sequence，把**每一个方向的每一次调用**落盘存档。

为什么要有它（用户诉求）：
    1. 所有测试结果都要保存，供后续做 pages 页；
    2. 所有提示词都要能追溯（存原文 + sha256）；
    3. 默认走青水 image2.5，参考图类任务走 gemini-3.1。

产物布局（`--out` 目录下）：
    campaign.json              整批汇总（含算力/调用数与每个 cell 的状态）
    index.json                 给 pages 用的索引（图片相对路径 + 尺寸 + 提示词路径）
    ledger.jsonl               一行一个 cell（方向×模式），内含 images[] 明细
    <cell_id>/task.json        该 cell 实际使用的任务描述（原样）
    <cell_id>/prompts/*.md     真正发给模型的提示词原文（Grid=整板，Sequence=逐帧）
    <cell_id>/result.json      流水线返回的 JSON（已去掉 prompts，提示词只在上面）
    <cell_id>/manifest.json    技能产出的正式产物清单
    <cell_id>/board.png、images/*.png  技能产出的图

用法：
    # 先看计划与算力预估，不花算力
    python scripts/run_matrix.py --out workspace/runs/2026-11-17 --dry-run

    # 跑一个方向一个模式（1 轮）
    python scripts/run_matrix.py --out workspace/runs/r1 --directions light --modes grid

    # 跑全部 12 方向
    python scripts/run_matrix.py --out workspace/runs/all --directions all --modes grid,sequence \\
        --subject-file workspace/qingshui/subject.txt --max-calls 80
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS.parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_task as cli  # noqa: E402  （复用同一套引导与部署错误处理）

DEFAULT_SUBJECT = ("一位穿黑色长袖深V连衣裙、黑色丝袜与黑色穆勒高跟鞋的年轻女性，"
                   "坐在城市街边水泥台阶上侧头看向左侧，米色混凝土墙与棕色金属百叶格栅，"
                   "黄色路缘线，低饱和灰褐色调，竖构图时尚街拍")

# 模型策略（用户 2026-11-17 决定）：纯文本默认 image2.5；带参考图必须 gemini-3.1，
# 因为实测 image2.5 的图片编辑通路 3/3 失败（AIMS [502]），档案里已声明不支持参考图。
PROFILE_TEXT_ONLY = "qingshui_image2.5"
PROFILE_IMAGE_ONLY = "qingshui_gemini3.1"


def profile_model_id(profile_name: str | None) -> str | None:
    """能力档案里的真实模型名：老产物 manifest 的 model_id 是空串，用档案回填并标明来源。"""
    if not profile_name:
        return None
    path = Path(__file__).resolve().parent.parent / "config" / "provider-profiles.json"
    try:
        profiles = json.loads(path.read_text(encoding="utf-8"))["profiles"]
    except (OSError, KeyError, ValueError):
        return None
    return (profiles.get(profile_name) or {}).get("model_id")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def direction_keys() -> list[str]:
    from iv_skill.prompt_builder import DirectionRegistry

    return list(DirectionRegistry().keys())


def build_spec(*, cell_id: str, mode: str, direction: str, args, profile: str,
               subject: str) -> dict:
    spec: dict = {
        "task_id": cell_id,
        "output_mode": mode,
        "direction": direction,
        "input_type": args.input_type,
        "subject": subject,
        "aspect_ratio": args.aspect_ratio,
        "capture_prompt": True,
        "pack_zip": True,
        "provider": {
            "type": "qingshui_image_compatible",
            "capability_profile": profile,
            "api_key_secret_ref": "env:QINGSHUI_API_KEY",
        },
    }
    if args.resolution_tier:
        spec["resolution_tier"] = args.resolution_tier
    if mode == "grid":
        spec["grid_size"] = args.grid_size
    else:
        spec["image_count"] = args.frames
        spec["branch"] = args.branch
    if args.input_type == "image_only":
        spec["reference_image"] = str(Path(args.reference).resolve())
    return spec


def planned_calls(mode: str, args) -> int:
    return 1 if mode == "grid" else args.frames


def minimal_calls(mode: str, args) -> int:
    """最少要花的算力：Grid 至少 1 张整板，Sequence 至少帧数张。"""
    return planned_calls(mode, args)


def cell_images(artifact_dir: Path, out_dir: Path, manifest: dict) -> list[dict]:
    """以 manifest.json 为唯一权威来源（它才是契约产物），绝不猜路径。"""
    entries = []
    for image in manifest.get("images", []):
        rel = image.get("path")
        if not rel:
            continue
        path = artifact_dir / rel
        entries.append({
            "asset_id": image.get("id"),
            "kind": image.get("kind"),
            "index": image.get("index"),
            "row": image.get("row"),
            "column": image.get("column"),
            "rel_path": path.relative_to(out_dir).as_posix(),
            "width": image.get("width"),
            "height": image.get("height"),
            "bytes": image.get("bytes"),
            "sha256": image.get("sha256"),
            "format": image.get("format"),
            "status": image.get("status"),
            "exists": path.is_file(),
        })
    return entries


def run_cell(*, cell_id: str, spec: dict, out_dir: Path, args) -> dict:
    cell_dir = out_dir / cell_id
    cell_dir.mkdir(parents=True, exist_ok=True)
    (cell_dir / "task.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8")
    started = time.monotonic()
    payload: dict
    try:
        payload = cli.run_task(spec, workspace=out_dir)
    except Exception as exc:  # noqa: BLE001 —— 任何失败都要存档，不能中断整批
        elapsed = round(time.monotonic() - started, 2)
        code = getattr(exc, "code", type(exc).__name__)
        detail = getattr(exc, "detail", None)
        message = getattr(exc, "message", str(exc))
        result = {
            "ok": False,
            "generated_at": iso_now(),
            "elapsed_sec": elapsed,
            "error": {"code": code, "message": message, "detail": detail},
            "traceback": traceback.format_exc() if not hasattr(exc, "code") else None,
        }
        (cell_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                              encoding="utf-8")
        return {
            "cell_id": cell_id, "ok": False, "status": "failed", "error_code": code,
            "error_message": message, "elapsed_sec": elapsed, "calls_used": None,
            "asset_dir": cell_id, "images": [], "warnings": [],
            "prompt_files": [], "result_path": f"{cell_id}/result.json",
        }

    elapsed = round(time.monotonic() - started, 2)
    prompts = payload.pop("prompts", {}) or {}
    prompt_dir = cell_dir / "prompts"
    prompt_dir.mkdir(exist_ok=True)
    prompt_files = []
    prompt_hashes = {}
    for key, text in prompts.items():
        name = f"{key}.md"
        (prompt_dir / name).write_text(text, encoding="utf-8")
        prompt_files.append(f"{cell_id}/prompts/{name}")
        prompt_hashes[key] = {"sha256": sha256_text(text), "chars": len(text),
                              "rel_path": f"{cell_id}/prompts/{name}"}
    payload["elapsed_sec"] = elapsed
    (cell_dir / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
    return record_from_payload(cell_id=cell_id, spec=spec, payload=payload, out_dir=out_dir,
                               elapsed=elapsed, prompt_files=prompt_files,
                               prompt_hashes=prompt_hashes)


def record_from_payload(*, cell_id: str, spec: dict, payload: dict, out_dir: Path,
                        elapsed: float | None, prompt_files: list[str],
                        prompt_hashes: dict) -> dict:
    """把一次成功执行整理成台账记录；manifest.json 是 model_id/尺寸/校验的唯一权威来源。"""
    manifest_path = payload.get("manifest_path")
    manifest = {}
    artifact_dir = None
    if manifest_path:
        artifact_dir = Path(manifest_path).resolve().parent
        try:
            manifest = json.loads(artifact_dir.joinpath("manifest.json").read_text(encoding="utf-8"))
        except OSError:
            manifest = {}
    validation = manifest.get("validation") or {}
    images = cell_images(artifact_dir, out_dir, manifest) if artifact_dir else []
    experiment = manifest.get("experiment") or {}
    zip_path = payload.get("zip_path")
    profile_name = (spec.get("provider") or {}).get("capability_profile")
    recorded_model = manifest.get("model_id") or payload.get("model_id")
    return {
        "cell_id": cell_id,
        "ok": bool(payload.get("ok")),
        "status": manifest.get("status") or payload.get("status"),
        "output_mode": manifest.get("output_mode") or payload.get("output_mode"),
        "direction": manifest.get("direction") or spec.get("direction"),
        "input_type": manifest.get("input_type") or spec.get("input_type"),
        "model_id": recorded_model or profile_model_id(profile_name),
        "model_id_source": "manifest" if recorded_model else "capability_profile(回填)",
        "provider_type": manifest.get("provider_type"),
        "provider_profile": (spec.get("provider") or {}).get("capability_profile"),
        "tier": manifest.get("requested_resolution_tier") or payload.get("tier"),
        "grid_size": manifest.get("grid_size"),
        "frame_count": manifest.get("image_count"),
        "branch": manifest.get("branch"),
        "aspect_ratio": manifest.get("requested_aspect_ratio"),
        "actual_aspect_ratio": manifest.get("actual_aspect_ratio"),
        "elapsed_sec": elapsed,
        "calls_used": (manifest.get("execution") or {}).get("calls_used") or payload.get("calls_used"),
        "cost_estimate": (manifest.get("execution") or {}).get("cost_estimate"),
        "seam_score": validation.get("seam_score"),
        "seam_method": validation.get("seam_check_method"),
        "seam_gate_mode": validation.get("seam_gate_mode"),
        "tile_edge_px": validation.get("tile_edge_px"),
        "tile_edge_ok": validation.get("tile_edge_ok"),
        "grid_attempts": validation.get("grid_attempts"),
        "completed_count": validation.get("completed_count"),
        "failed_indices": validation.get("failed_indices"),
        "avg_similarity": experiment.get("avg_similarity"),
        "warnings": manifest.get("warnings") or payload.get("warnings") or [],
        "errors": manifest.get("errors") or payload.get("errors") or [],
        "prompt_hashes": prompt_hashes,
        "prompt_files": prompt_files,
        "images": images,
        "artifact_dir": str(artifact_dir.relative_to(out_dir)) if artifact_dir else None,
        "manifest_path": (str(artifact_dir.relative_to(out_dir)) + "/manifest.json")
                         if artifact_dir else None,
        "result_path": f"{cell_id}/result.json",
        "zip_path": str(Path(zip_path).resolve().relative_to(out_dir)) if zip_path else None,
        "generated_at": iso_now(),
    }


def reindex(out_dir: Path, args) -> int:
    """不调用模型，按已落盘的 result.json + manifest.json 重建台账（补正字段用）。"""
    records = []
    for result_file in sorted(out_dir.glob("*/result.json")):
        cell_id = result_file.parent.name
        payload = json.loads(result_file.read_text(encoding="utf-8"))
        spec_file = result_file.parent / "task.json"
        spec = json.loads(spec_file.read_text(encoding="utf-8")) if spec_file.is_file() else {}
        prompt_dir = result_file.parent / "prompts"
        prompt_files = sorted(str(path.relative_to(out_dir).as_posix())
                              for path in prompt_dir.glob("*.md"))
        prompt_hashes = {}
        for rel in prompt_files:
            text = (out_dir / rel).read_text(encoding="utf-8")
            key = Path(rel).stem
            prompt_hashes[key] = {"sha256": sha256_text(text), "chars": len(text), "rel_path": rel}
        if payload.get("ok"):
            record = record_from_payload(cell_id=cell_id, spec=spec, payload=payload,
                                         out_dir=out_dir,
                                         elapsed=payload.get("elapsed_sec"),
                                         prompt_files=prompt_files, prompt_hashes=prompt_hashes)
        else:
            error = payload.get("error") or {}
            record = {"cell_id": cell_id, "ok": False, "status": "failed",
                      "output_mode": spec.get("output_mode"), "direction": spec.get("direction"),
                      "input_type": spec.get("input_type"),
                      "provider_profile": (spec.get("provider") or {}).get("capability_profile"),
                      "error_code": error.get("code"), "error_message": error.get("message"),
                      "elapsed_sec": payload.get("elapsed_sec"), "calls_used": None,
                      "artifact_dir": None, "images": [], "warnings": [], "errors": [error],
                      "prompt_files": prompt_files, "prompt_hashes": prompt_hashes,
                      "result_path": f"{cell_id}/result.json", "generated_at": iso_now()}
        records.append(record)

    ledger_path = out_dir / "ledger.jsonl"
    with ledger_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    write_campaign(out_dir, records, [], args, calls_used=None)
    print(json.dumps({"ok": True, "reindexed": len(records),
                      "ok_cells": sum(1 for r in records if r["ok"]),
                      "ocr": None, "ledger": str(ledger_path)}, ensure_ascii=False, indent=2))
    return 0


def write_campaign(out_dir: Path, records: list[dict], skipped: list[dict], args,
                   *, calls_used: int | None, directions: list[str] | None = None,
                   modes: list[str] | None = None, planned: int | None = None) -> None:
    """写 campaign.json 与 index.json：这两份是给 pages 页直接用的。"""
    directions = directions or sorted({r.get("direction") for r in records if r.get("direction")})
    modes = modes or sorted({r.get("output_mode") for r in records if r.get("output_mode")})
    campaign = {
        "ok": True,
        "campaign": out_dir.name,
        "generated_at": iso_now(),
        "provider_profile": next((r.get("provider_profile") for r in records
                                  if r.get("provider_profile")),
                                 PROFILE_IMAGE_ONLY if args.input_type == "image_only"
                                 else PROFILE_TEXT_ONLY),
        "input_type": args.input_type,
        "aspect_ratio": args.aspect_ratio,
        "grid_size": args.grid_size,
        "frames": args.frames if "sequence" in modes else None,
        "branch": args.branch if "sequence" in modes else None,
        "directions": list(directions),
        "modes": list(modes),
        "calls_used": calls_used if calls_used is not None else sum(
            r.get("calls_used") or 0 for r in records),
        "max_calls": args.max_calls,
        "cells_planned": planned if planned is not None else len(records) + len(skipped),
        "cells_ran": len(records),
        "cells_ok": sum(1 for record in records if record["ok"]),
        "cells_failed": sum(1 for record in records if not record["ok"]),
        "skipped": skipped,
        "records": records,
        "ledger": "ledger.jsonl",
    }
    (out_dir / "campaign.json").write_text(
        json.dumps(campaign, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "index.json").write_text(
        json.dumps({"ok": True, "campaign": campaign["campaign"],
                    "generated_at": campaign["generated_at"],
                    "provider_profile": campaign["provider_profile"],
                    "input_type": campaign["input_type"],
                    "cells": [{"cell_id": record["cell_id"], "direction": record.get("direction"),
                               "output_mode": record.get("output_mode"),
                               "status": record.get("status"), "ok": record["ok"],
                               "error_code": record.get("error_code"),
                               "error_message": record.get("error_message"),
                               "model_id": record.get("model_id"),
                               "provider_profile": record.get("provider_profile"),
                               "seam_score": record.get("seam_score"),
                               "seam_gate_mode": record.get("seam_gate_mode"),
                               "tile_edge_px": record.get("tile_edge_px"),
                               "avg_similarity": record.get("avg_similarity"),
                               "warnings": [w.get("code") for w in record.get("warnings") or []],
                               "prompt_files": record.get("prompt_files"),
                               "prompt_hashes": record.get("prompt_hashes"),
                               "images": record.get("images"),
                               "artifact_dir": record.get("artifact_dir"),
                               "manifest_path": record.get("manifest_path"),
                               "result_path": record.get("result_path"),
                               "zip_path": record.get("zip_path"),
                               "elapsed_sec": record.get("elapsed_sec"),
                               "calls_used": record.get("calls_used")}
                              for record in records]},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in campaign.items() if k != "records"},
                     ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="12 方向跑测台（Grid / Sequence）")
    parser.add_argument("--out", required=True, help="存档根目录")
    parser.add_argument("--directions", default="all", help="all 或逗号分隔的方向 key")
    parser.add_argument("--modes", default="grid,sequence", help="grid / sequence（逗号分隔）")
    parser.add_argument("--grid-size", type=int, default=2, choices=[2, 3, 4])
    parser.add_argument("--frames", type=int, default=4, help="Sequence 帧数（4–16）")
    parser.add_argument("--branch", default="progression", help="Sequence 分支")
    parser.add_argument("--aspect-ratio", default="1:1")
    parser.add_argument("--resolution-tier", default=None, help="留空则由技能按能力推导")
    parser.add_argument("--input-type", default="text_only", choices=["text_only", "image_only"])
    parser.add_argument("--reference", default=None, help="image_only 时的参考图路径")
    parser.add_argument("--subject", default=None, help="主体锚点文本")
    parser.add_argument("--subject-file", default=None, help="主体锚点文本文件（优先于 --subject）")
    parser.add_argument("--max-calls", type=int, default=120, help="算力硬上限（张）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不调用模型")
    parser.add_argument("--reindex", action="store_true",
                        help="不调用模型，按已落盘的 result.json/manifest.json 重建台账")
    parser.add_argument("--force", action="store_true", help="重跑已成功的 cell（默认跳过，保护算力）")
    parser.add_argument("--rerun-failed", action="store_true", help="只重跑失败的 cell")
    args = parser.parse_args(argv)

    if cli.DEPLOYMENT_ERROR is not None or cli.IMPORT_ERROR is not None:
        problem = cli.DEPLOYMENT_ERROR or {"stage": "import", "message": str(cli.IMPORT_ERROR)}
        print(json.dumps({"ok": False, "deployment_error": problem}, ensure_ascii=False, indent=2))
        return 4

    subject = args.subject
    if args.subject_file:
        subject = Path(args.subject_file).read_text(encoding="utf-8").strip()
    subject = subject or DEFAULT_SUBJECT

    if args.input_type == "image_only" and not args.reference:
        print(json.dumps({"ok": False, "error": {"code": "REFERENCE_REQUIRED",
                                                 "message": "image_only 必须给 --reference"}},
                         ensure_ascii=False, indent=2))
        return 3

    if args.reindex:
        return reindex(Path(args.out).resolve(), args)

    known = direction_keys()
    directions = known if args.directions == "all" else [
        item.strip() for item in args.directions.split(",") if item.strip()]
    unknown = [item for item in directions if item not in known]
    if unknown:
        print(json.dumps({"ok": False, "error": {"code": "INVALID_INPUT",
                                                 "message": f"未知方向 {unknown}，可用：{known}"}},
                         ensure_ascii=False, indent=2))
        return 3
    modes = [item.strip() for item in args.modes.split(",") if item.strip()]

    profile = PROFILE_IMAGE_ONLY if args.input_type == "image_only" else PROFILE_TEXT_ONLY
    plan = [(mode, direction) for direction in directions for mode in modes]

    if args.dry_run:
        rows = [{"cell_id": f"{mode}-{direction}", "mode": mode, "direction": direction,
                 "planned_calls": minimal_calls(mode, args)} for mode, direction in plan]
        print(json.dumps({
            "ok": True, "dry_run": True, "provider_profile": profile,
            "input_type": args.input_type, "directions": len(directions), "modes": modes,
            "cells": len(rows), "planned_calls_total": sum(row["planned_calls"] for row in rows),
            "max_calls": args.max_calls, "aspect_ratio": args.aspect_ratio,
            "grid_size": args.grid_size, "frames": args.frames if "sequence" in modes else None,
            "rows": rows,
        }, ensure_ascii=False, indent=2))
        return 0

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = out_dir / "ledger.jsonl"
    budget = args.max_calls
    spent = 0
    records: list[dict] = []
    skipped: list[dict] = []

    for mode, direction in plan:
        cell_id = f"{mode}-{direction}"
        existing = out_dir / cell_id / "result.json"
        if existing.is_file() and not args.force:
            previous = json.loads(existing.read_text(encoding="utf-8"))
            if args.rerun_failed and not previous.get("ok"):
                pass
            elif previous.get("ok"):
                skipped.append({"cell_id": cell_id, "reason": "已成功（--force 可重跑）"})
                continue
        if spent + minimal_calls(mode, args) > budget:
            skipped.append({"cell_id": cell_id,
                            "reason": f"算力上限：已用 {spent}，本次至少需 "
                                      f"{minimal_calls(mode, args)}，上限 {budget}"})
            continue

        spec = build_spec(cell_id=cell_id, mode=mode, direction=direction, args=args,
                          profile=profile, subject=subject)
        record = run_cell(cell_id=cell_id, spec=spec, out_dir=out_dir, args=args)
        record["planned_calls"] = minimal_calls(mode, args)
        records.append(record)
        calls = record.get("calls_used") or 0
        spent += calls
        with ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"[{len(records)}/{len(plan)}] {cell_id}: ok={record['ok']} "
              f"status={record.get('status')} calls={calls} "
              f"{record.get('error_code') or ''} {record['elapsed_sec']}s", flush=True)

    write_campaign(out_dir, records, skipped, args, calls_used=spent,
                   directions=directions, modes=modes, planned=len(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
