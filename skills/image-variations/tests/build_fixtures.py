"""Skill 01 夹具生成器：正例（真实合图产物）+ 负例（显式篡改）。

设计原则（与基线 tests/create_examples.py 同性质）：
    - 图片是本地合成，**不是模型输出**，只验证数据契约与几何校验；
    - 正例的宽高、字节数、SHA256 全部由真实文件计算，不手写；
    - 负例只做**一处**可控篡改，用例含义唯一。

用法：
    python skills/image-variations/tests/build_fixtures.py            # 生成/覆盖 fixtures/
    python skills/image-variations/tests/build_fixtures.py --dry-run  # 只打印计划
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "support"))

import paths  # noqa: E402  (完成 sys.path / 环境装配)
from renderer import render_board, render_frame, render_blank, write_image  # noqa: E402

from shared import pkg  # noqa: E402
from shared.hashing import sha256_file  # noqa: E402
from shared.jsonio import write_json  # noqa: E402

FIXTURES = paths.FIXTURES_DIR
CREATED_AT = "2026-11-16T02:00:00Z"
COMPLETED_AT = "2026-11-16T02:03:00Z"


# ----------------------------------------------------------------- 基础构造
def ratio_string(width: int, height: int) -> str:
    frac = Fraction(width, height).limit_denominator(100)
    return f"{frac.numerator}:{frac.denominator}"


def tile_geometry(aspect: str, tile_long_edge: int) -> tuple[int, int]:
    """返回 (tile_w, tile_h)，单格长边精确等于 tile_long_edge 且比例精确等于 aspect。"""
    num, den = (int(x) for x in aspect.split(":"))
    if num >= den:
        return tile_long_edge, max(1, round(tile_long_edge * den / num))
    return max(1, round(tile_long_edge * num / den)), tile_long_edge


def _put(data: dict, flat_key: str, value) -> None:
    """按点号路径写入（或删除，value 为 _DELETE 时）。"""
    keys = flat_key.split(".")
    node = data
    for key in keys[:-1]:
        node = node[key]
    if value is _DELETE:
        node.pop(keys[-1], None)
    else:
        node[keys[-1]] = value


_DELETE = object()


def base_manifest(*, task_id: str, output_mode: str, direction: str, images: list[dict],
                  image_count: int, requested_aspect_ratio: str, actual_aspect_ratio: str,
                  resolution_tier: str, resolution_policy: str, validation: dict,
                  grid_size=None, branch=None, input_type: str = "image_only",
                  extra_warnings: list[dict] | None = None) -> dict:
    manifest = {
        "schema_version": "1.1",
        "artifact_type": "image_variations",
        "task_id": task_id,
        "artifact_revision": "r1",
        "parent_artifact_revision": None,
        "status": "completed",
        "input_type": input_type,
        "output_mode": output_mode,
        "direction": direction,
        "direction_registry_version": "1.0",
        "direction_rules_version": "1.0",
        "branch": branch,
        "grid_size": grid_size,        "image_count": image_count,
        "requested_aspect_ratio": requested_aspect_ratio,
        "actual_aspect_ratio": actual_aspect_ratio,
        "requested_resolution_tier": resolution_tier,
        "requested_resolution_policy": resolution_policy,
        "provider_type": "mock",
        "model_id": "mock-provider-v1",
        "prompt_template_version": "grid-prompt-v1" if output_mode == "grid" else "sequence-prompt-v1",
        "consistency_anchor": {
            "type": "reference_image" if input_type != "text_only" else "first_frame",
            "description": "同一只浅灰陶瓷杯，形状、把手、釉面不变",
        },
        "images": images,
        "validation": validation,
        "execution": {
            "calls_planned": (1 + 2) if output_mode == "grid" else image_count + 2,
            "calls_used": 1 if output_mode == "grid" else image_count,
            "call_budget_limit": None,
            "budget_exceeded": False,
            "cost_estimate": 0.04,
            "cost_currency": "USD",
            "degradations": [],
        },
        "warnings": list(extra_warnings or []),
        "errors": [],
        "retry_counts": {},
        "created_at": CREATED_AT,
        "completed_at": COMPLETED_AT,
    }
    return manifest


def grid_validation(*, tile_edge: int, required: int, seam_score: float,
                    structure: bool = True, attempts: int = 1) -> dict:
    return {
        "asset_integrity_passed": True,
        "grid_structure_passed": structure,
        "aspect_ratio_error_ratio": 0.0,
        "tile_edge_px": tile_edge,
        "tile_edge_min_required_px": required,
        "tile_edge_ok": tile_edge >= required,
        "seam_score": seam_score,
        "seam_check_method": "boundary_gradient_v1",
        "grid_attempts": attempts,
        "checks": ["nxN_structure", "no_border_no_gap", "tile_size_uniform", "crop_box_covers_board"],
    }


def sequence_validation(*, frames: int) -> dict:
    return {
        "asset_integrity_passed": True,
        "sequence_complete": True,
        "aspect_ratio_error_ratio": 0.0,
        "checks": ["frame_count", "frame_index_contiguous", "ratio_uniform"],
    }


# ----------------------------------------------------------------- 具体夹具
def build_valid_grid(root: Path, *, name: str, grid_size: int, aspect: str,
                     tile_long_edge: int, direction: str, resolution_tier: str,
                     policy: str, seam_score: float, degraded: bool = False) -> dict:
    tile_w, tile_h = tile_geometry(aspect, tile_long_edge)
    board_w, board_h = tile_w * grid_size, tile_h * grid_size
    board = render_board(grid_size, tile_w, tile_h, seed=grid_size)
    deliver = root / name
    if deliver.exists():
        shutil.rmtree(deliver)
    write_image(deliver / "board.png", board)
    images = [{
        "id": "board",
        "kind": "board",
        "index": 0,
        "path": "board.png",
        "width": board_w,
        "height": board_h,
        "format": "png",
        "bytes": (deliver / "board.png").stat().st_size,
        "sha256": sha256_file(deliver / "board.png"),
        "status": "completed",
    }]
    for row in range(grid_size):
        for col in range(grid_size):
            index = row * grid_size + col + 1
            tile_path = deliver / "images" / f"{index:02d}.png"
            tile = board.crop((col * tile_w, row * tile_h, (col + 1) * tile_w, (row + 1) * tile_h))
            write_image(tile_path, tile)
            images.append({
                "id": f"tile_{index:02d}",
                "kind": "tile",
                "index": index,
                "row": row + 1,
                "column": col + 1,
                "crop_box": [col * tile_w, row * tile_h, (col + 1) * tile_w, (row + 1) * tile_h],
                "path": f"images/{index:02d}.png",
                "width": tile_w,
                "height": tile_h,
                "format": "png",
                "bytes": tile_path.stat().st_size,
                "sha256": sha256_file(tile_path),
                "status": "completed",
                "prompt_template_id": "grid-prompt-v1",
            })
    warnings = []
    if degraded:
        warnings.append({
            "code": "TILE_RESOLUTION_RISK",
            "message": f"单格长边 {max(tile_w, tile_h)} 低于推荐下限 1024，已按 provider_degraded 记录降档",
            "severity": "warning",
        })
    manifest = base_manifest(
        task_id=name, output_mode="grid", direction=direction, images=images,
        image_count=grid_size * grid_size, requested_aspect_ratio=aspect,
        actual_aspect_ratio=ratio_string(board_w, board_h), resolution_tier=resolution_tier,
        resolution_policy=policy, grid_size=grid_size, branch=None,
        validation=grid_validation(tile_edge=min(tile_w, tile_h), required=1024, seam_score=seam_score),
        extra_warnings=warnings,
    )
    manifest["validation"]["tile_long_edge_px"] = max(tile_w, tile_h)
    manifest["validation"]["tile_long_edge_required_px"] = 1024
    manifest["execution"]["degradations"] = ["provider_degraded:1K"] if degraded else []
    write_json(deliver / "manifest.json", manifest)
    package = pkg.build_manifest(deliver, package_id=f"pkg_{name}", package_type="image_variations")
    pkg.write_manifest(deliver, package)
    return manifest


def build_valid_sequence(root: Path, *, name: str, frame_count: int, aspect: str,
                         frame_long_edge: int, direction: str, branch: str,
                         input_type: str, resolution_tier: str) -> dict:
    num, den = (int(x) for x in aspect.split(":"))
    width, height = num, den
    while max(width, height) < frame_long_edge:
        width, height = width * 2, height * 2
    deliver = root / name
    if deliver.exists():
        shutil.rmtree(deliver)
    images = []
    for index in range(1, frame_count + 1):
        path = deliver / "images" / f"{index:02d}.png"
        write_image(path, render_frame(width, height, index, seed=index))
        images.append({
            "id": f"frame_{index:02d}",
            "kind": "frame",
            "index": index,
            "path": f"images/{index:02d}.png",
            "width": width,
            "height": height,
            "format": "png",
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "status": "completed",
            "phase_label": f"阶段{index}",
            "story_context": f"第 {index} 个关键时刻",
            "prompt_summary": f"{direction}/{branch} 第 {index} 帧变化点",
            "prompt_template_id": "sequence-prompt-v1",
        })
    manifest = base_manifest(
        task_id=name, output_mode="sequence", direction=direction, images=images,
        image_count=frame_count, requested_aspect_ratio=aspect,
        actual_aspect_ratio=ratio_string(width, height), resolution_tier=resolution_tier,
        resolution_policy="tile_min_edge_default" if frame_count != 4 else "tile_min_edge_default",
        grid_size=None, branch=branch, input_type=input_type,
        validation=sequence_validation(frames=frame_count),
    )
    write_json(deliver / "manifest.json", manifest)
    package = pkg.build_manifest(deliver, package_id=f"pkg_{name}", package_type="image_variations")
    pkg.write_manifest(deliver, package)
    return manifest


# ----------------------------------------------------------------- 负例
def _copy_valid(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)


def _rebuild_package(root: Path, package_id: str) -> None:
    package = pkg.build_manifest(root, package_id=package_id, package_type="image_variations")
    pkg.write_manifest(root, package)


def write_case(root: Path, *, name: str, schema: str | None, check: str | None,
               expect_schema_fail: bool | None, codes: list[str], note: str) -> dict:
    spec = {
        "name": name,
        "note": note,
        "schema": schema,
        "check": check,
        "target": "manifest.json",
        "expect": {"codes": codes},
    }
    if schema and expect_schema_fail is not None:
        spec["expect"]["schema_fail"] = expect_schema_fail
    write_json(root / name / "case.json", spec)
    return spec


def build_negatives(root: Path, sources: dict[str, Path]) -> list[str]:
    built: list[str] = []
    grid2 = sources["valid-grid-2x2"]
    grid3 = sources["valid-grid-3x3"]
    seq4 = sources["valid-sequence-4"]
    seq16 = sources["valid-sequence-16"]

    # 1) 输入缺失：schema 层面 required 失败
    name = "invalid-input-empty-missing"
    _copy_valid(grid2, root / name)
    manifest = json.loads((root / name / "manifest.json").read_text(encoding="utf-8"))
    del manifest["input_type"]
    write_json(root / name / "manifest.json", manifest)
    _rebuild_package(root / name, f"pkg_{name}")
    write_case(root, name=name, schema="image-artifact.schema.json", check=None,
               expect_schema_fail=True, codes=[], note="缺 input_type：契约 required 失败")
    built.append(name)

    # 2) 网格规模非法：grid_size=5 违反 enum
    name = "invalid-mode-count"
    _copy_valid(grid2, root / name)
    manifest = json.loads((root / name / "manifest.json").read_text(encoding="utf-8"))
    manifest["grid_size"] = 5
    write_json(root / name / "manifest.json", manifest)
    _rebuild_package(root / name, f"pkg_{name}")
    write_case(root, name=name, schema="image-artifact.schema.json", check=None,
               expect_schema_fail=True, codes=[], note="grid_size=5 不在 2/3/4 枚举内")
    built.append(name)

    # 3) tile 数量与 grid_size² 不一致
    name = "invalid-tile-count-mismatch"
    _copy_valid(grid3, root / name)
    manifest = json.loads((root / name / "manifest.json").read_text(encoding="utf-8"))
    manifest["images"] = [i for i in manifest["images"] if i["index"] != 9]
    write_json(root / name / "manifest.json", manifest)
    _rebuild_package(root / name, f"pkg_{name}")
    write_case(root, name=name, schema="image-artifact.schema.json", check="image_artifact",
               expect_schema_fail=False, codes=["GRID_VALIDATION_FAILED"],
               note="9 格只剩 8 格：image_count 与 tile 数不符（契约层不校验，业务校验器负责）")
    built.append(name)

    # 4) board 缺失（只剩切图）
    name = "invalid-board-missing"
    _copy_valid(grid2, root / name)
    manifest = json.loads((root / name / "manifest.json").read_text(encoding="utf-8"))
    manifest["images"] = [i for i in manifest["images"] if i["kind"] != "board"]
    write_json(root / name / "manifest.json", manifest)
    (root / name / "board.png").unlink()
    _rebuild_package(root / name, f"pkg_{name}")
    write_case(root, name=name, schema="image-artifact.schema.json", check="image_artifact",
               expect_schema_fail=False, codes=["GRID_VALIDATION_FAILED"],
               note="没有 board：不得输出已通过的切图")
    built.append(name)

    # 5) board.index 非 0（命名/索引语义，AC-32）
    name = "invalid-naming-board-index"
    _copy_valid(grid2, root / name)
    manifest = json.loads((root / name / "manifest.json").read_text(encoding="utf-8"))
    for image in manifest["images"]:
        if image["kind"] == "board":
            image["index"] = 1
    write_json(root / name / "manifest.json", manifest)
    _rebuild_package(root / name, f"pkg_{name}")
    write_case(root, name=name, schema="image-artifact.schema.json", check="image_artifact",
               expect_schema_fail=True, codes=["GRID_VALIDATION_FAILED"],
               note="board.index 必须为 0")
    built.append(name)

    # 6) tile 索引不连续
    name = "invalid-naming-tile-index"
    _copy_valid(grid2, root / name)
    manifest = json.loads((root / name / "manifest.json").read_text(encoding="utf-8"))
    for image in manifest["images"]:
        if image["kind"] == "tile" and image["index"] == 4:
            image["index"] = 5
    write_json(root / name / "manifest.json", manifest)
    _rebuild_package(root / name, f"pkg_{name}")
    write_case(root, name=name, schema="image-artifact.schema.json", check="image_artifact",
               expect_schema_fail=False, codes=["GRID_VALIDATION_FAILED"],
               note="tile.index 必须是 1..N² 连续唯一")
    built.append(name)

    # 7) crop_box 与 index 推导的坐标不符
    name = "invalid-crop-box-mismatch"
    _copy_valid(grid2, root / name)
    manifest = json.loads((root / name / "manifest.json").read_text(encoding="utf-8"))
    for image in manifest["images"]:
        if image["kind"] == "tile" and image["index"] == 3:
            image["crop_box"] = [0, 0, image["crop_box"][2], image["crop_box"][3]]
    write_json(root / name / "manifest.json", manifest)
    _rebuild_package(root / name, f"pkg_{name}")
    write_case(root, name=name, schema="image-artifact.schema.json", check="image_artifact",
               expect_schema_fail=False, codes=["GRID_VALIDATION_FAILED"],
               note="crop_box 与行优先坐标不一致：切图不可重叠、不可缺漏")
    built.append(name)

    # 8) 接缝评分过低：结构失败
    name = "invalid-seam-score-low"
    _copy_valid(grid3, root / name)
    manifest = json.loads((root / name / "manifest.json").read_text(encoding="utf-8"))
    manifest["validation"]["seam_score"] = 0.42
    write_json(root / name / "manifest.json", manifest)
    _rebuild_package(root / name, f"pkg_{name}")
    write_case(root, name=name, schema="image-artifact.schema.json", check="image_artifact",
               expect_schema_fail=False, codes=["GRID_VALIDATION_FAILED"],
               note="seam_score<0.60：必须进入重试，不得标记成功")
    built.append(name)

    # 9) 接缝评分落在风险区
    name = "invalid-seam-score-risk"
    _copy_valid(grid3, root / name)
    manifest = json.loads((root / name / "manifest.json").read_text(encoding="utf-8"))
    manifest["validation"]["seam_score"] = 0.72
    manifest["warnings"].append({
        "code": "GRID_SEAM_RISK", "message": "接缝评分落在 0.60–0.85", "severity": "warning",
    })
    write_json(root / name / "manifest.json", manifest)
    _rebuild_package(root / name, f"pkg_{name}")
    write_case(root, name=name, schema="image-artifact.schema.json", check="image_artifact",
               expect_schema_fail=False, codes=["GRID_SEAM_RISK"],
               note="0.60–0.85：可交付但必须标注接缝风险")
    built.append(name)

    # 10) 缺 seam_score（1.1 清单必须带）
    name = "invalid-seam-score-missing"
    _copy_valid(grid3, root / name)
    manifest = json.loads((root / name / "manifest.json").read_text(encoding="utf-8"))
    del manifest["validation"]["seam_score"]
    write_json(root / name / "manifest.json", manifest)
    _rebuild_package(root / name, f"pkg_{name}")
    write_case(root, name=name, schema="image-artifact.schema.json", check="image_artifact",
               expect_schema_fail=False, codes=["GRID_VALIDATION_FAILED"],
               note="缺 seam_score：无法判定无缝，判失败")
    built.append(name)

    # 11) 缺 grid_structure_passed（Grid 条件必填）
    name = "invalid-grid-structure-field"
    _copy_valid(grid3, root / name)
    manifest = json.loads((root / name / "manifest.json").read_text(encoding="utf-8"))
    del manifest["validation"]["grid_structure_passed"]
    write_json(root / name / "manifest.json", manifest)
    _rebuild_package(root / name, f"pkg_{name}")
    write_case(root, name=name, schema="image-artifact.schema.json", check=None,
               expect_schema_fail=True, codes=[],
               note="Grid 模式缺 validation.grid_structure_passed：条件必填失败")
    built.append(name)

    # 12) 单格长边低于硬下限
    name = "invalid-tile-edge-too-low"
    build_valid_grid(root, name=name, grid_size=4, aspect="1:1", tile_long_edge=256,
                     direction="state", resolution_tier="1K", policy="provider_degraded",
                     seam_score=0.97)
    write_case(root, name=name, schema="image-artifact.schema.json", check="image_artifact",
               expect_schema_fail=False, codes=["TILE_RESOLUTION_TOO_LOW"],
               note="4×4 单格 256px：低于硬下限 512，必须阻断（不得静默交付）")
    built.append(name)

    # 13) 单格长边落在风险区（512–768）
    name = "invalid-tile-edge-risk"
    build_valid_grid(root, name=name, grid_size=4, aspect="3:2", tile_long_edge=640,
                     direction="light", resolution_tier="2K", policy="provider_degraded",
                     seam_score=0.95, degraded=True)
    write_case(root, name=name, schema="image-artifact.schema.json", check="image_artifact",
               expect_schema_fail=False, codes=["TILE_RESOLUTION_RISK"],
               note="4×4 单格 640px 长边：落在 512–768 风险区，必须警告且标注降档")
    built.append(name)

    # 14) 分辨率策略非法值
    name = "invalid-resolution-policy"
    _copy_valid(grid3, root / name)
    manifest = json.loads((root / name / "manifest.json").read_text(encoding="utf-8"))
    manifest["requested_resolution_policy"] = "silent_downgrade"
    write_json(root / name / "manifest.json", manifest)
    _rebuild_package(root / name, f"pkg_{name}")
    write_case(root, name=name, schema="image-artifact.schema.json", check=None,
               expect_schema_fail=True, codes=[],
               note="策略枚举外：禁止静默降档口径")
    built.append(name)

    # 15) 序列帧索引不连续且总数不符（缺帧必须保留槽位）
    name = "invalid-sequence-gap"
    _copy_valid(seq16, root / name)
    manifest = json.loads((root / name / "manifest.json").read_text(encoding="utf-8"))
    removed = [i for i in manifest["images"] if i["index"] == 7][0]
    manifest["images"] = [i for i in manifest["images"] if i["index"] != 7]
    (root / name / removed["path"]).unlink()
    write_json(root / name / "manifest.json", manifest)
    _rebuild_package(root / name, f"pkg_{name}")
    write_case(root, name=name, schema="image-artifact.schema.json", check="image_artifact",
               expect_schema_fail=False, codes=["SOURCE_ARTIFACT_INCOMPLETE"],
               note="缺第 7 帧：索引不得前移，必须显式标记不完整（契约层不校验帧数，业务校验器负责）")
    built.append(name)

    # 16) Grid 模式 branch 非 null（模式边界）
    name = "invalid-branch-grid-not-null"
    _copy_valid(grid2, root / name)
    manifest = json.loads((root / name / "manifest.json").read_text(encoding="utf-8"))
    manifest["branch"] = "variants"
    write_json(root / name / "manifest.json", manifest)
    _rebuild_package(root / name, f"pkg_{name}")
    write_case(root, name=name, schema="image-artifact.schema.json", check=None,
               expect_schema_fail=True, codes=[],
               note="Grid 不接收分支：branch 必须为 null")
    built.append(name)

    # 17) 图片被篡改：哈希不符
    name = "invalid-hash-mismatch"
    _copy_valid(grid2, root / name)
    tampered = root / name / "images" / "02.png"
    tampered.write_bytes(tampered.read_bytes() + b"tampered")
    write_case(root, name=name, schema=None, check="image_artifact",
               expect_schema_fail=None, codes=["SOURCE_ASSET_MISSING"],
               note="切图字节被改动：哈希不符必须拒绝消费")
    built.append(name)

    # 18) 包清单未登记新增文件
    name = "invalid-package-undeclared-file"
    _copy_valid(grid2, root / name)
    (root / name / "extra-note.txt").write_text("未被 delivery-package 登记的文件\n", encoding="utf-8")
    write_case(root, name=name, schema=None, check="delivery_package",
               expect_schema_fail=None, codes=["PACKAGE_MANIFEST_INVALID"],
               note="存在未登记文件：清单覆盖率失败")
    built.append(name)

    # 19) 包清单哈希与文件不符
    name = "invalid-package-hash-mismatch"
    _copy_valid(grid2, root / name)
    _rebuild_package(root / name, f"pkg_{name}")
    package = json.loads((root / name / "delivery-package.json").read_text(encoding="utf-8"))
    for entry in package["entries"]:
        if entry["path"] == "board.png":
            entry["sha256"] = "0" * 64
    write_json(root / name / "delivery-package.json", package)
    write_case(root, name=name, schema="delivery-package.schema.json", check="delivery_package",
               expect_schema_fail=False, codes=["PACKAGE_MANIFEST_INVALID"],
               note="清单哈希与实际文件不一致：PACKAGE_MANIFEST_INVALID")
    built.append(name)

    # 20) 路径穿越：manifest 图片路径逃出资产根
    name = "invalid-path-traversal"
    _copy_valid(grid2, root / name)
    manifest = json.loads((root / name / "manifest.json").read_text(encoding="utf-8"))
    for image in manifest["images"]:
        if image["kind"] == "tile" and image["index"] == 2:
            image["path"] = "../../etc/passwd"
    write_json(root / name / "manifest.json", manifest)
    _rebuild_package(root / name, f"pkg_{name}")
    write_case(root, name=name, schema="image-artifact.schema.json", check="image_artifact",
               expect_schema_fail=True, codes=["PATH_TRAVERSAL_BLOCKED"],
               note="图片路径含 ..：PATH_TRAVERSAL_BLOCKED")
    built.append(name)

    # 21) 包清单路径穿越
    name = "invalid-package-traversal"
    _copy_valid(grid2, root / name)
    package = json.loads((root / name / "delivery-package.json").read_text(encoding="utf-8"))
    package["entries"].append({"path": "../outside.png", "kind": "image",
                               "sha256": "a" * 64, "bytes": 10})
    package["total_files"] = len(package["entries"])
    write_json(root / name / "delivery-package.json", package)
    write_case(root, name=name, schema="delivery-package.schema.json", check="delivery_package",
               expect_schema_fail=True, codes=["PATH_TRAVERSAL_BLOCKED"],
               note="清单条目含 ..：路径安全检查失败")
    built.append(name)

    # 22) 多方向：manifest 声明方向不在注册表内
    name = "invalid-direction-multiple"
    _copy_valid(grid3, root / name)
    manifest = json.loads((root / name / "manifest.json").read_text(encoding="utf-8"))
    manifest["direction"] = "light+scene"
    write_json(root / name / "manifest.json", manifest)
    _rebuild_package(root / name, f"pkg_{name}")
    write_case(root, name=name, schema=None, check="direction_registry",
               expect_schema_fail=None, codes=["MULTIPLE_DIRECTIONS"],
               note="方向必须是注册表内单一 key，多方向必须拒绝")
    built.append(name)

    # 23) 能力不足：把"声明缺失"写成执行记录，供 P2 预检读取
    name = "invalid-capability-unsupported"
    _copy_valid(seq4, root / name)
    manifest = json.loads((root / name / "manifest.json").read_text(encoding="utf-8"))
    manifest["errors"] = [{
        "code": "MODEL_CAPABILITY_UNSUPPORTED",
        "message": "所选适配器未声明 tier_to_board_side_px，无法推导档位",
        "recoverable": False,
        "retry_available": False,
    }]
    manifest["status"] = "failed"
    del manifest["actual_aspect_ratio"]
    write_json(root / name / "manifest.json", manifest)
    _rebuild_package(root / name, f"pkg_{name}")
    write_case(root, name=name, schema="image-artifact.schema.json", check="error_registry",
               expect_schema_fail=False, codes=["MODEL_CAPABILITY_UNSUPPORTED"],
               note="能力底线未声明：排产前阻断并指明缺失项")
    built.append(name)

    # 24) 预算超限：不得伪装完整成功
    name = "invalid-budget-exceeded"
    _copy_valid(seq4, root / name)
    manifest = json.loads((root / name / "manifest.json").read_text(encoding="utf-8"))
    manifest["status"] = "partial_failed"
    manifest["execution"]["budget_exceeded"] = True
    manifest["execution"]["call_budget_limit"] = 3
    manifest["execution"]["calls_used"] = 3
    manifest["errors"] = [{
        "code": "BUDGET_EXCEEDED",
        "message": "达到调用预算上限，停止新增调用，建议拆批",
        "recoverable": True,
        "retry_available": False,
    }]
    write_json(root / name / "manifest.json", manifest)
    _rebuild_package(root / name, f"pkg_{name}")
    write_case(root, name=name, schema="image-artifact.schema.json", check="error_registry",
               expect_schema_fail=False, codes=["BUDGET_EXCEEDED"],
               note="超预算必须报码并保留已完成帧")
    built.append(name)

    # 25) 契约主版本不支持
    name = "invalid-schema-major"
    _copy_valid(grid2, root / name)
    manifest = json.loads((root / name / "manifest.json").read_text(encoding="utf-8"))
    manifest["schema_version"] = "2.0"
    write_json(root / name / "manifest.json", manifest)
    _rebuild_package(root / name, f"pkg_{name}")
    write_case(root, name=name, schema="image-artifact.schema.json", check="image_artifact",
               expect_schema_fail=True, codes=["SCHEMA_MAJOR_UNSUPPORTED"],
               note="主版本不支持：明确报错，不猜字段")
    built.append(name)

    return built


def build_all(root: Path, *, dry_run: bool = False) -> dict:
    plan = {
        "valid-grid-2x2": dict(grid_size=2, aspect="1:1", tile_long_edge=512, direction="state",
                               resolution_tier="1K", policy="provider_degraded", seam_score=0.983,
                               degraded=True),
        "valid-grid-3x3": dict(grid_size=3, aspect="3:4", tile_long_edge=768, direction="light",
                               resolution_tier="4K", policy="provider_degraded", seam_score=0.977,
                               degraded=True),
        "valid-grid-4x4": dict(grid_size=4, aspect="1:1", tile_long_edge=1024, direction="scene",
                               resolution_tier="4K", policy="tile_min_edge_default", seam_score=0.991),
        "valid-sequence-4": dict(frame_count=4, aspect="3:4", frame_long_edge=768, direction="light",
                                 branch="progression", input_type="text_only", resolution_tier="1K"),
        "valid-sequence-16": dict(frame_count=16, aspect="3:4", frame_long_edge=768,
                                  direction="narrative", branch="narrative",
                                  input_type="image_plus_text", resolution_tier="1K"),
    }
    if dry_run:
        return {"plan": plan}

    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    sources: dict[str, Path] = {}
    for name, kwargs in plan.items():
        if "grid_size" in kwargs:
            build_valid_grid(root, name=name, **kwargs)
        else:
            build_valid_sequence(root, name=name, **kwargs)
        sources[name] = root / name
    negatives = build_negatives(root, sources)
    manifest = {
        "schema_version": "1.0",
        "generated_by": "skills/image-variations/tests/build_fixtures.py",
        "note": "合成夹具：图片为本地绘制，非模型输出；仅验证契约与几何校验。",
        "valid": sorted(plan),
        "invalid": sorted(negatives),
    }
    write_json(root / "FIXTURES.json", manifest)
    return {"valid": sorted(plan), "invalid": sorted(negatives)}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="生成 Skill 01 契约夹具")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", default=str(FIXTURES))
    args = parser.parse_args(argv)
    result = build_all(Path(args.out), dry_run=args.dry_run)
    if args.dry_run:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"[OK] 正例 {len(result['valid'])} 组: {result['valid']}")
    print(f"[OK] 负例 {len(result['invalid'])} 组: {result['invalid']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
