"""业务语义校验（JSON Schema 管不到的部分）。

返回统一形态：list[tuple[code, message]]，code 取自 references/error-registry.json。
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from PIL import Image

SUPPORTED_MAJORS = {"1.0", "1.1"}
IMAGE_KINDS = ("tile", "frame")


def load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe_path(root: Path, rel: str) -> Path:
    """解析相对路径并确认仍在 root 内。"""
    if not isinstance(rel, str) or not rel or "\\" in rel or "\x00" in rel:
        raise ValueError(f"PATH_TRAVERSAL_BLOCKED: illegal reference {rel!r}")
    root = Path(root).resolve()
    path = (root / rel).resolve()
    if not str(path).startswith(str(root)) or not path.is_file():
        raise ValueError(f"PATH_TRAVERSAL_BLOCKED: {rel} escapes asset root or missing")
    return path


def _major(version: str) -> str:
    return str(version).split(".")[0]


def check_schema_major(manifest) -> list:
    errs = []
    version = str(manifest.get("schema_version", ""))
    if version not in SUPPORTED_MAJORS:
        errs.append(("SCHEMA_MAJOR_UNSUPPORTED", f"schema_version={version!r} 不受支持"))
    return errs


def check_images_on_disk(folder: Path, manifest) -> list:
    errs = []
    for img in manifest.get("images", []):
        try:
            path = safe_path(folder, img["path"])
        except ValueError as exc:
            errs.append(("PATH_TRAVERSAL_BLOCKED", str(exc)))
            continue
        if sha256(path) != img["sha256"]:
            errs.append(("SOURCE_ASSET_MISSING", f"{img['id']}: sha256 不匹配"))
            continue
        try:
            with Image.open(path) as im:
                if im.size != (img["width"], img["height"]):
                    errs.append(("IMAGE_VALIDATION_FAILED",
                                 f"{img['id']}: 实际 {im.size} != 声明 {(img['width'], img['height'])}"))
        except Exception as exc:  # noqa: BLE001
            errs.append(("IMAGE_VALIDATION_FAILED", f"{img['id']}: 无法解码 {exc}"))
    return errs


def check_grid_structure(folder: Path, manifest) -> list:
    errs = []
    n = manifest.get("grid_size")
    images = manifest.get("images", [])
    boards = [i for i in images if i["kind"] == "board"]
    tiles = sorted([i for i in images if i["kind"] == "tile"], key=lambda x: x["index"])
    if len(boards) != 1:
        return [("GRID_VALIDATION_FAILED", f"必须恰好 1 张 board，实际 {len(boards)}")]
    if boards[0]["index"] != 0:
        errs.append(("GRID_VALIDATION_FAILED", "board.index 必须为 0"))
    if len(tiles) != n * n or manifest.get("image_count") != n * n:
        errs.append(("GRID_VALIDATION_FAILED",
                     f"tile 数 {len(tiles)} 与 grid_size²={n*n} 不一致"))
    if [t["index"] for t in tiles] != list(range(1, len(tiles) + 1)):
        errs.append(("GRID_VALIDATION_FAILED", "tile.index 必须是 1..N² 且连续唯一"))
    if not tiles:
        return errs
    sizes = {(t["width"], t["height"]) for t in tiles}
    if len(sizes) != 1:
        errs.append(("GRID_VALIDATION_FAILED", f"tile 尺寸不一致: {sizes}"))
    board = boards[0]
    tile_w, tile_h = tiles[0]["width"], tiles[0]["height"]
    if board["width"] % n or board["height"] % n:
        errs.append(("GRID_VALIDATION_FAILED", "整板边长不能被 N 整除"))
    if (board["width"] // n, board["height"] // n) != (tile_w, tile_h):
        errs.append(("GRID_VALIDATION_FAILED",
                     f"整板/N={(board['width']//n, board['height']//n)} 与 tile 尺寸 {(tile_w, tile_h)} 不符"))
    for t in tiles:
        row, col = (t["index"] - 1) // n, (t["index"] - 1) % n
        if (t.get("row"), t.get("column")) != (row + 1, col + 1):
            errs.append(("GRID_VALIDATION_FAILED", f"{t['id']}: row/column 与 index 不匹配"))
        expected = (col * tile_w, row * tile_h, (col + 1) * tile_w, (row + 1) * tile_h)
        if tuple(t.get("crop_box", ())) != expected:
            errs.append(("GRID_VALIDATION_FAILED", f"{t['id']}: crop_box {t.get('crop_box')} != {list(expected)}"))
    # 单格长边下限
    tile_edge = min(tile_w, tile_h)
    validation = manifest.get("validation", {})
    if validation.get("tile_edge_px") != tile_edge:
        errs.append(("GRID_VALIDATION_FAILED",
                     f"validation.tile_edge_px={validation.get('tile_edge_px')} 与实际 {tile_edge} 不符"))
    required = validation.get("tile_edge_min_required_px", 1024)
    if tile_edge < 512:
        errs.append(("TILE_RESOLUTION_TOO_LOW", f"单格长边 {tile_edge} <512"))
    elif tile_edge < required:
        errs.append(("TILE_RESOLUTION_RISK", f"单格长边 {tile_edge} < 推荐 {required}"))
    if validation.get("tile_edge_ok") is not (tile_edge >= required):
        errs.append(("GRID_VALIDATION_FAILED", "tile_edge_ok 与实际单格长边不一致"))
    # 接缝评分
    score = validation.get("seam_score")
    if score is None:
        errs.append(("GRID_VALIDATION_FAILED", "缺少 seam_score"))
    elif score < 0.60:
        errs.append(("GRID_VALIDATION_FAILED", f"seam_score={score} <0.60"))
    elif score < 0.85:
        errs.append(("GRID_SEAM_RISK", f"seam_score={score} 落在 0.60–0.85"))
    return errs


def check_sequence_structure(manifest) -> list:
    errs = []
    frames = sorted([i for i in manifest.get("images", []) if i["kind"] == "frame"],
                    key=lambda x: x["index"])
    if len(frames) != manifest.get("image_count"):
        errs.append(("IMAGE_VALIDATION_FAILED", "frame 数与 image_count 不一致"))
    expected = list(range(1, len(frames) + 1))
    if [f["index"] for f in frames] != expected:
        errs.append(("SOURCE_ARTIFACT_INCOMPLETE", f"frame 索引不连续: {[f['index'] for f in frames]}"))
    if manifest.get("branch") not in ("variants", "progression", "narrative"):
        errs.append(("IMAGE_VALIDATION_FAILED", "branch 必须单一且合法"))
    return errs


def content_assets(manifest) -> list:
    """SPEC 03 §1.1 的只读视图。"""
    items = [i for i in manifest.get("images", [])
             if i["kind"] in IMAGE_KINDS and i.get("status") == "completed"]
    return sorted(items, key=lambda x: x["index"])


def check_content_assets_order(manifest) -> list:
    errs = []
    ids = [i["id"] for i in content_assets(manifest)]
    if len(set(ids)) != len(ids):
        errs.append(("SOURCE_MANIFEST_INVALID", "content_assets 出现重复 asset id"))
    if any(i["kind"] == "board" for i in content_assets(manifest)):
        errs.append(("SOURCE_MANIFEST_INVALID", "board 不得进入 content_assets"))
    return errs


def check_blocks(blocks, selected_ids) -> list:
    errs = []
    if not blocks:
        return [("CONTENT_STRUCTURE_INVALID", "blocks 为空")]
    if blocks[0].get("type") != "text":
        errs.append(("CONTENT_STRUCTURE_INVALID", "首块必须是 text"))
    if blocks[-1].get("type") != "text":
        errs.append(("CONTENT_STRUCTURE_INVALID", "末块必须是 text"))
    text_count = 0
    image_blocks = 0
    used = set()
    prev_was_image = False
    for i, block in enumerate(blocks):
        kind = block.get("type")
        if kind == "text":
            text_count += 1
            prev_was_image = False
            if not str(block.get("content", "")).strip():
                errs.append(("CONTENT_STRUCTURE_INVALID", f"第 {i} 块为空文字块"))
        elif kind == "image":
            image_blocks += 1
            if prev_was_image:
                errs.append(("CONTENT_STRUCTURE_INVALID", f"第 {i} 块与上一图片块相邻"))
            prev_was_image = True
            used.add(block.get("asset_id"))
        elif kind == "image_group":
            image_blocks += 1
            if prev_was_image:
                errs.append(("CONTENT_STRUCTURE_INVALID", f"第 {i} 块图集与上一图片块相邻"))
            prev_was_image = True
            group = block.get("images", [])
            if not 2 <= len(group) <= 4:
                errs.append(("CONTENT_STRUCTURE_INVALID", f"image_group 必须 2–4 张，实际 {len(group)}"))
            for item in group:
                used.add(item.get("asset_id"))
        else:
            errs.append(("CONTENT_STRUCTURE_INVALID", f"第 {i} 块类型未知: {kind!r}"))
    if text_count != image_blocks + 1:
        errs.append(("CONTENT_STRUCTURE_INVALID",
                     f"文字块 {text_count} != 图块 {image_blocks} + 1"))
    missing = {x for x in used if x} - set(selected_ids)
    if missing:
        errs.append(("CONTENT_STRUCTURE_INVALID", f"引用了未选中的 asset: {sorted(missing)}"))
    return errs


def check_cover(folder: Path, name: str, cover, selected_ids) -> list:
    errs = []
    try:
        path = safe_path(folder, cover["file"]["path"])
    except ValueError as exc:
        return [("PATH_TRAVERSAL_BLOCKED", str(exc))]
    if sha256(path) != cover["file"]["sha256"]:
        errs.append(("OUTPUT_ASSET_INVALID", f"{name}: 封面哈希不匹配"))
    with Image.open(path) as im:
        if im.size != (cover["actual_width"], cover["actual_height"]):
            errs.append(("OUTPUT_ASSET_INVALID", f"{name}: 封面尺寸与声明不符"))
        num, den = (float(x) for x in cover["target_ratio"].split(":"))
        if abs(im.width / im.height - num / den) > 0.02:
            errs.append(("COVER_FIT_FAILED", f"{name}: 比例偏离 {cover['target_ratio']}"))
    if cover.get("source_asset_id") not in selected_ids:
        errs.append(("COVER_FIT_FAILED", f"{name}: 封面源图不在 selected_asset_ids"))
    factor = cover.get("upscale_factor")
    if factor is not None:
        if factor > 1.5:
            errs.append(("COVER_SOURCE_LOWRES", f"{name}: 放大 {factor}× >1.5×"))
        elif factor > 1.0 and not cover.get("quality_risk"):
            errs.append(("COVER_FIT_FAILED", f"{name}: 放大 {factor}× 但未标 quality_risk"))
    return errs


def check_claim_linkage(item, evidence) -> list:
    errs = []
    if evidence is None:
        if item.get("claim_ids"):
            errs.append(("EVIDENCE_LINK_MISMATCH", f"{item['content_id']}: 有 claim_ids 但缺证据包"))
        return errs
    known = {c["claim_id"] for c in evidence.get("claims", [])}
    declared = set(item.get("claim_ids", []))
    if declared - known:
        errs.append(("EVIDENCE_LINK_MISMATCH", f"{item['content_id']}: 引用未知 claim {sorted(declared - known)}"))
    for claim in evidence.get("claims", []):
        if item["content_id"] in claim.get("used_by_content_ids", []) and claim["claim_id"] not in declared:
            errs.append(("EVIDENCE_LINK_MISMATCH",
                         f"{item['content_id']}: 证据侧声明引用 {claim['claim_id']} 但内容侧未登记"))
    return errs


def check_package_coverage(folder: Path, package) -> list:
    errs = []
    root = Path(folder).resolve()
    declared = {}
    for entry in package.get("entries", []):
        try:
            path = safe_path(root, entry["path"])
        except ValueError as exc:
            errs.append(("PATH_TRAVERSAL_BLOCKED", str(exc)))
            continue
        declared[entry["path"]] = entry
        actual = sha256(path)
        if actual != entry["sha256"]:
            errs.append(("PACKAGE_MANIFEST_INVALID", f"{entry['path']}: 哈希不匹配"))
        if entry.get("bytes") and path.stat().st_size != entry["bytes"]:
            errs.append(("PACKAGE_MANIFEST_INVALID", f"{entry['path']}: 字节数不匹配"))
    on_disk = {p.relative_to(root).as_posix() for p in root.rglob("*")
               if p.is_file() and p.name != "delivery-package.json"}
    undeclared = on_disk - set(declared)
    if undeclared:
        errs.append(("PACKAGE_MANIFEST_INVALID", f"存在未登记文件: {sorted(undeclared)}"))
    if package.get("total_files") != len(declared):
        errs.append(("PACKAGE_MANIFEST_INVALID",
                     f"total_files={package.get('total_files')} != entries={len(declared)}"))
    try:
        safe_path(root, package.get("manifest_path", ""))
    except ValueError as exc:
        errs.append(("PACKAGE_MANIFEST_INVALID", f"manifest_path 不可解析: {exc}"))
    return errs


def check_content_package_images(folder: Path, item, declared=None) -> list:
    """包内 images/ 下所有图片必须登记进 package_images。

    declared 为整包（含其他条目）的登记路径集合；缺省时只用本条目的 package_images。
    """
    errs = []
    own = {f["path"] for f in item.get("package_images", [])}
    allowed = own if declared is None else set(declared)
    for entry in item.get("package_images", []):
        try:
            path = safe_path(folder, entry["path"])
        except ValueError as exc:
            errs.append(("PATH_TRAVERSAL_BLOCKED", str(exc)))
            continue
        if sha256(path) != entry["sha256"]:
            errs.append(("OUTPUT_ASSET_INVALID", f"{entry['path']}: 图片副本哈希不匹配"))
    for path in Path(folder).rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(Path(folder)).as_posix()
        if "/images/" in f"/{rel}" and rel not in allowed:
            errs.append(("OUTPUT_ASSET_INVALID", f"包内图片未登记: {rel}"))
    return errs
