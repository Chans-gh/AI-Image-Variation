"""产物落盘：`image-artifact` 清单 + 交付包清单 + 可选 ZIP（SPEC 01 §7、packaging.md）。

不变量：
    - 目录布局固定：Grid `board.png` + `images/NN.png`；Sequence 只有 `images/NN.png`；
    - `board.index=0`；tile/frame `index≥1` 连续唯一，**缺帧不前移**（`packaging.md §2`）；
    - 先写图片再算哈希，清单里的每个条目都必须真实存在；
    - 只登记真实存在的文件（部分失败时失败帧不进 `images[]`，由 `errors[]` + `validation.failed_indices` 表达）；
    - 参考图等用户私有附件**不进包**，只写 `"not-exported"`。
"""
from __future__ import annotations

import math
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from shared.errors import IVError
from shared.hashing import sha256_file
from shared.jsonio import write_json
from shared.paths import is_illegal_component
from shared.pkg import (MANIFEST_NAME, build_manifest, export_zip, scan_package_files,
                        verify_manifest, write_manifest)

from .config import load_config
from .executor import GridExecution
from .prompt_builder import DirectionRegistry
from .sequence import SequenceExecution

SCHEMA_VERSION = "1.1"
ARTIFACT_TYPE = "image_variations"
DIRECTION_RULES_VERSION = "1.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ratio_string(width: int, height: int) -> str:
    divisor = math.gcd(width, height) or 1
    return f"{width // divisor}:{height // divisor}"


@dataclass
class ArtifactResult:
    root: Path
    manifest_path: Path
    package_path: Path
    zip_path: Path | None
    manifest: dict
    package: dict

    def to_dict(self) -> dict:
        return {
            "task_id": self.manifest["task_id"],
            "artifact_revision": self.manifest["artifact_revision"],
            "status": self.manifest["status"],
            "artifact_dir": str(self.root),
            "manifest_path": str(self.manifest_path),
            "package_path": str(self.package_path),
            "zip_path": str(self.zip_path) if self.zip_path else None,
            "image_count": self.manifest["image_count"],
            "delivered_images": len(self.manifest["images"]),
            "checksums": {entry["path"]: entry["sha256"] for entry in self.package["entries"]},
        }


def _task_dir(workspace: Path, task_id: str, revision: str) -> Path:
    for value in (task_id, revision):
        if not isinstance(value, str) or not value.strip():
            raise IVError("INVALID_INPUT", "task_id / artifact_revision 不能为空")
        if is_illegal_component(value):
            raise IVError("PATH_TRAVERSAL_BLOCKED",
                          f"task_id / revision 含路径分隔或越界片段: {value!r}")
    return Path(workspace).resolve() / f"iv_{task_id}_{revision}"


def _staging_dir(final: Path) -> Path:
    """产物先写到同级的隐藏暂存目录，全部自检通过后再原子换到最终名字。

    这样"同 revision 重跑"不会留下上一版的孤儿文件（它们会被整体替换掉），
    写到一半失败时旧产物也仍然完整。
    """
    staging = final.parent / f".{final.name}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    return staging


def _commit(staging: Path, final: Path) -> None:
    backup = final.parent / f".{final.name}.previous"
    if backup.exists():
        shutil.rmtree(backup)
    if final.exists():
        os.replace(final, backup)
    try:
        os.replace(staging, final)
    except OSError:
        if backup.exists() and not final.exists():
            os.replace(backup, final)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def _assert_files_match_manifest(root: Path, manifest: dict) -> None:
    """产物目录里的文件必须**恰好**是 images[] + manifest.json。

    这是"产物可验证"的底线：多一个文件＝包里有来源不明的内容（历史残留会被这里抓住），
    少一个＝清单在说谎。
    """
    on_disk = set(scan_package_files(root))
    expected = {image["path"] for image in manifest["images"]} | {"manifest.json"}
    extra, missing = on_disk - expected, expected - on_disk
    if extra or missing:
        raise IVError("PACKAGE_MANIFEST_INVALID",
                      f"产物文件与清单不一致：多余 {sorted(extra)}，缺失 {sorted(missing)}")


def _write_png(path: Path, source, *, from_bytes: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if from_bytes:
        path.write_bytes(source)
        return
    source.save(path, format="PNG")


def _entry_for(path: Path, rel: str, **extra) -> dict:
    payload = {
        "path": rel,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    payload.update(extra)
    return payload


def _anchor_block(anchor: dict, reference_sha256: str | None = None) -> dict:
    """锚点写进清单时不得泄露参考图路径（私有附件不进产物包）。

    Sequence 额外落一个参考图 **sha256**（只有哈希，没有图也没有路径）：
    续跑时必须能判断"还是同一张参考图"，否则会把不同锚点的帧混进同一组。
    """
    if anchor.get("type") == "reference_image":
        block = {"type": "input_image", "reference": "not-exported"}
        if reference_sha256:
            block["sha256"] = reference_sha256
        return block
    if anchor.get("type") == "first_frame":
        return {"type": "first_frame", "asset_id": "frame_01"}
    return {"type": "text_description", "description": anchor.get("description", "")}


def _base_manifest(*, task_id: str, revision: str, parent: str | None, status: str, input_type: str,
                   execution_plan: dict, registry: DirectionRegistry, prompt_template_version: str,
                   anchor: dict, execution: dict, warnings: list[dict], errors: list[dict],
                   reference_sha256: str | None = None,
                   validation: dict, created_at: str, completed_at: str,
                   retry_counts: dict | None = None) -> dict:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "task_id": task_id,
        "artifact_revision": revision,
        "parent_artifact_revision": parent,
        "status": status,
        "input_type": input_type,
        "output_mode": execution_plan["output_mode"],
        "direction": execution_plan["direction"],
        "direction_registry_version": registry.version,
        "direction_rules_version": DIRECTION_RULES_VERSION,
        "prompt_template_version": prompt_template_version,
        "image_count": execution_plan["image_count"],
        "requested_aspect_ratio": execution_plan["aspect_ratio"],
        "actual_aspect_ratio": validation.get("actual_aspect_ratio"),
        "requested_resolution_tier": execution_plan["tier"],
        "requested_resolution_policy": execution_plan["tier_policy"],
        "provider_type": execution_plan.get("provider_type"),
        "model_id": execution_plan.get("model_id"),
        "consistency_anchor": _anchor_block(anchor, reference_sha256),
        "images": [],
        "validation": validation,
        "execution": execution,
        "warnings": warnings,
        "errors": errors,
        "created_at": created_at,
        "completed_at": completed_at,
    }
    if retry_counts:
        payload["retry_counts"] = retry_counts
    return payload


def write_grid_artifact(*, workspace: str | Path, task_id: str, execution: GridExecution,
                        anchor: dict, registry: DirectionRegistry, input_type: str,
                        revision: str = "rev1", parent_revision: str | None = None,
                        pack_zip: bool = False, config: dict | None = None) -> ArtifactResult:
    config = config or load_config()
    final = _task_dir(workspace, task_id, revision)
    root = _staging_dir(final)
    created_at = utc_now()
    try:
        return _write_grid_into(root, final, task_id=task_id, revision=revision,
                                execution=execution, anchor=anchor, registry=registry,
                                input_type=input_type, parent_revision=parent_revision,
                                pack_zip=pack_zip, config=config, created_at=created_at)
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise


def _write_grid_into(root: Path, final: Path, *, task_id: str, revision: str, execution, anchor,
                     registry, input_type: str, parent_revision: str | None, pack_zip: bool,
                     config: dict, created_at: str) -> ArtifactResult:
    board_path = root / "board.png"
    _write_png(board_path, execution.board.image)
    images: list[dict] = [_entry_for(board_path, "board.png", id="grid_board", kind="board", index=0,
                                     width=execution.board.width, height=execution.board.height,
                                     format="png", status="completed")]
    cell_plan = execution.prompt_meta.get("cell_plan", [])
    for tile in execution.tiles:
        rel = f"images/{tile.index:02d}.png"
        path = root / rel
        _write_png(path, tile.image)
        summary = f"第 {tile.index} 格：{cell_plan[tile.index - 1]}" if tile.index <= len(cell_plan) \
            else f"第 {tile.index} 格"
        images.append(_entry_for(path, rel, id=f"grid_img_{tile.index:02d}", kind="tile",
                                 index=tile.index, row=tile.row, column=tile.column,
                                 crop_box=list(tile.crop_box), width=tile.size[0],
                                 height=tile.size[1], format="png", status="completed",
                                 prompt_summary=summary))

    validation = dict(execution.result.postprocess.validation)
    validation["actual_aspect_ratio"] = ratio_string(execution.board.width, execution.board.height)
    manifest = _base_manifest(
        task_id=task_id, revision=revision, parent=parent_revision,
        status="completed" if execution.result.verdict in ("pass", "risk") else "failed",
        input_type=input_type, execution_plan=execution.plan,
        registry=registry, prompt_template_version=execution.prompt_meta["template_version"],
        anchor=anchor, execution=execution.budget, warnings=execution.warnings, errors=[],
        validation=validation, created_at=created_at, completed_at=created_at,
        retry_counts={"grid": max(0, execution.result.attempt - 1)})
    manifest["branch"] = None
    manifest["grid_size"] = execution.plan["grid_size"]
    manifest["grid_size_source"] = "user"
    manifest["images"] = images
    return _finalise(root, final, task_id, revision, manifest, pack_zip, config)


def write_sequence_artifact(*, workspace: str | Path, task_id: str, execution: SequenceExecution,
                            anchor: dict, registry: DirectionRegistry, input_type: str,
                            revision: str = "rev1", parent_revision: str | None = None,
                            pack_zip: bool = False, config: dict | None = None) -> ArtifactResult:
    config = config or load_config()
    final = _task_dir(workspace, task_id, revision)
    root = _staging_dir(final)
    created_at = utc_now()
    try:
        return _write_sequence_into(root, final, task_id=task_id, revision=revision,
                                    execution=execution, anchor=anchor, registry=registry,
                                    input_type=input_type, parent_revision=parent_revision,
                                    pack_zip=pack_zip, config=config, created_at=created_at)
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise


def _write_sequence_into(root: Path, final: Path, *, task_id: str, revision: str, execution,
                         anchor, registry, input_type: str, parent_revision: str | None,
                         pack_zip: bool, config: dict, created_at: str) -> ArtifactResult:
    images: list[dict] = []
    for frame in execution.frames:
        if frame.status != "completed" or not frame.image_bytes:
            continue      # 缺帧不进 images[]，但索引不前移
        rel = f"images/{frame.index:02d}.png"
        path = root / rel
        _write_png(path, frame.image_bytes, from_bytes=True)
        entry = _entry_for(path, rel, id=f"frame_{frame.index:02d}", kind="frame",
                           index=frame.index, width=frame.width, height=frame.height,
                           format="png", status="completed",
                           phase_label=frame.phase_label, prompt_summary=frame.change_point,
                           prompt_template_id=execution.sequence_plan.template_version)
        if frame.near_duplicate_of is not None:
            entry["near_duplicate_of"] = f"frame_{frame.near_duplicate_of:02d}"
        images.append(entry)

    validation = dict(execution.validation())
    validation["actual_aspect_ratio"] = (ratio_string(images[0]["width"], images[0]["height"])
                                         if images else None)
    validation["tile_edge_px"] = execution.plan["tile_short_edge_px"]
    validation["tile_edge_min_required_px"] = config["tile_edge_min_px"]
    validation["tile_edge_ok"] = execution.plan["tile_long_edge_px"] >= config["tile_edge_min_px"]
    validation["checks"] = ["frame_index", "frame_count", "aspect_ratio", "asset_integrity"]

    manifest = _base_manifest(
        task_id=task_id, revision=revision, parent=parent_revision, status=execution.status,
        input_type=input_type, execution_plan=execution.plan, registry=registry,
        prompt_template_version=execution.sequence_plan.template_version, anchor=anchor,
        reference_sha256=execution.anchor_revision,
        execution=execution.budget, warnings=execution.warnings, errors=execution.errors,
        validation=validation, created_at=created_at, completed_at=created_at,
        retry_counts=execution.retry_counts)
    manifest["branch"] = execution.plan["branch"]
    manifest["grid_size"] = None
    manifest["images"] = images
    return _finalise(root, final, task_id, revision, manifest, pack_zip, config)


def _finalise(staging: Path, final: Path, task_id: str, revision: str, manifest: dict,
              pack_zip: bool, config: dict) -> ArtifactResult:
    write_json(staging / "manifest.json", manifest)
    _assert_files_match_manifest(staging, manifest)
    package = build_manifest(staging, package_id=f"{task_id}_{revision}",
                             package_type=ARTIFACT_TYPE, root_label=final.name)
    write_manifest(staging, package)
    problems = verify_manifest(staging, package)
    if problems:
        raise IVError(problems[0][0], f"交付包清单自检未通过: {problems}")
    _commit(staging, final)

    zip_path = None
    if pack_zip:
        zip_path = final.parent / f"{final.name}.zip"
        export_zip(final, zip_path, package, limits=config["packaging"])
        package = build_manifest(final, package_id=f"{task_id}_{revision}",
                                 package_type=ARTIFACT_TYPE, root_label=final.name)
    return ArtifactResult(root=final, manifest_path=final / "manifest.json",
                          package_path=final / MANIFEST_NAME, zip_path=zip_path,
                          manifest=manifest, package=package)
