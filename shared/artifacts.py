import hashlib
import io
import json
from fractions import Fraction
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from PIL import Image

from .paths import safe_path


def file_hash(data):
    return hashlib.sha256(data).hexdigest()


def content_assets(manifest):
    """Read-only derived view; boards never enter the publishing asset list."""
    selected = [
        dict(x) for x in manifest["images"] if x["kind"] in {"tile", "frame"} and x["status"] == "completed"
    ]
    indices = [x["index"] for x in selected]
    if len(set(indices)) != len(indices) or any(type(i) is not int or i < 1 for i in indices):
        raise ValueError("ARTIFACT_INDEX_INVALID")
    return sorted(selected, key=lambda x: x["index"])


def validate_artifact(root, contracts=None, require_complete=True):
    root = Path(root)
    manifest = json.loads(safe_path(root, "manifest.json").read_text())
    version = manifest.get("schema_version")
    if version not in {"1.0", "1.1"}:
        raise ValueError("UNSUPPORTED_SCHEMA_VERSION")
    contracts = Path(contracts or Path(__file__).resolve().parents[1] / "contracts")
    schema = json.loads((contracts / f"image-artifact-{version}.schema.json").read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    completed = manifest["status"] == "completed"
    if require_complete and not completed:
        raise ValueError("ARTIFACT_INCOMPLETE")
    if completed and not manifest.get("actual_aspect_ratio"):
        raise ValueError("ARTIFACT_RATIO_MISSING")
    images, ids, indices = manifest["images"], set(), set()
    decoded = {}
    for entry in images:
        asset_id = entry["id"]
        key = entry["kind"], entry["index"]
        if asset_id in ids or key in indices:
            raise ValueError("ARTIFACT_INDEX_INVALID")
        ids.add(asset_id)
        indices.add(key)
        data = safe_path(root, entry["path"]).read_bytes()
        if file_hash(data) != entry["sha256"] or ("bytes" in entry and len(data) != entry["bytes"]):
            raise ValueError("ASSET_INTEGRITY_FAILED")
        with Image.open(io.BytesIO(data)) as im:
            if im.width * im.height > 64_000_000:
                raise ValueError("ASSET_TOO_LARGE")
            im.load()
            if im.size != (entry["width"], entry["height"]):
                raise ValueError("ASSET_DIMENSIONS_MISMATCH")
            decoded[asset_id] = im.convert("RGBA")
        if entry["status"] == "completed":
            target = Fraction(manifest["requested_aspect_ratio"].replace(":", "/"))
            if abs((entry["width"] / entry["height"]) / float(target) - 1) > 0.01:
                raise ValueError("ASSET_RATIO_MISMATCH")
    assets = content_assets(manifest)
    expected_indices = set(range(1, manifest["image_count"] + 1))
    if completed and {x["index"] for x in assets} != expected_indices:
        raise ValueError("ARTIFACT_COUNT_MISMATCH")
    if manifest["output_mode"] == "grid":
        n = manifest["grid_size"]
        if manifest["image_count"] != n * n or any(x["kind"] == "frame" for x in images):
            raise ValueError("GRID_GEOMETRY_INVALID")
        boards = [x for x in images if x["kind"] == "board"]
        if len(boards) != 1 or boards[0]["index"] != 0:
            raise ValueError("GRID_GEOMETRY_INVALID")
        board = boards[0]
        if board["width"] % n or board["height"] % n:
            raise ValueError("GRID_GEOMETRY_INVALID")
        tw, th = board["width"] // n, board["height"] // n
        for tile in assets:
            row, col = divmod(tile["index"] - 1, n)
            box = [col * tw, row * th, (col + 1) * tw, (row + 1) * th]
            if (
                tile.get("crop_box") != box
                or (tile["width"], tile["height"]) != (tw, th)
                or (tile.get("row"), tile.get("column")) != (row + 1, col + 1)
            ):
                raise ValueError("GRID_GEOMETRY_INVALID")
            if decoded[board["id"]].crop(tuple(box)).tobytes() != decoded[tile["id"]].tobytes():
                raise ValueError("GRID_CROP_MISMATCH")
        if completed and manifest["validation"].get("grid_structure_passed") is not True:
            raise ValueError("GRID_VALIDATION_UNVERIFIED")
    else:
        if any(x["kind"] != "frame" for x in images):
            raise ValueError("SEQUENCE_INVALID")
        if manifest["branch"] in {"progression", "narrative"} and any(
            not (x.get("phase_label") or x.get("story_context")) for x in assets
        ):
            raise ValueError("SEQUENCE_ORDER_UNVERIFIED")
        if any(x["index"] not in expected_indices for x in images):
            raise ValueError("ARTIFACT_INDEX_INVALID")
    dimension = manifest.get("dimension_execution")
    if dimension and completed:
        if dimension.get("dimension_policy") != "provider_native":
            raise ValueError("NATIVE_RESOLUTION_UNVERIFIED")
        records = [dimension] if manifest["output_mode"] == "grid" else dimension.get("frames", [])
        if not records or any(x.get("tier_validation", {}).get("status") != "passed" for x in records):
            raise ValueError("NATIVE_RESOLUTION_UNVERIFIED")
        if (
            dimension.get("requested_tier") != manifest["requested_resolution_tier"]
            or dimension.get("requested_ratio") != manifest["requested_aspect_ratio"]
        ):
            raise ValueError("NATIVE_RESOLUTION_METADATA_MISMATCH")
        if manifest["output_mode"] == "grid":
            board = next(x for x in images if x["kind"] == "board")
            if dimension.get("effective_output") != {"width": board["width"], "height": board["height"]}:
                raise ValueError("NATIVE_RESOLUTION_METADATA_MISMATCH")
            if dimension.get("native_file"):
                data = safe_path(root, dimension["native_file"]).read_bytes()
                if file_hash(data) != dimension.get("native_file_sha256"):
                    raise ValueError("ASSET_INTEGRITY_FAILED")
                with Image.open(io.BytesIO(data)) as native:
                    native_size = {"width": native.width, "height": native.height}
                if native_size != dimension.get("native_output"):
                    raise ValueError("NATIVE_RESOLUTION_METADATA_MISMATCH")
        else:
            frames = dimension["frames"]
            if {f["index"] for f in frames} != {x["index"] for x in assets} or len(frames) != len(assets):
                raise ValueError("NATIVE_RESOLUTION_METADATA_MISMATCH")
            for asset in assets:
                frame = next(f for f in frames if f["index"] == asset["index"])
                if frame.get("native_output") != {"width": asset["width"], "height": asset["height"]}:
                    raise ValueError("NATIVE_RESOLUTION_METADATA_MISMATCH")
    if completed and manifest["validation"].get("asset_integrity_passed") is not True:
        raise ValueError("ASSET_INTEGRITY_FAILED")
    # Legacy manifests are readable, but absence of dimension evidence stays explicit.
    return {
        "manifest": manifest,
        "content_assets": assets,
        "native_resolution_verified": bool(dimension),
        "validation_unverified": dimension is None,
    }


def verify_delivery(root, contracts=None):
    root = Path(root)
    inventory = json.loads(safe_path(root, "delivery-package.json").read_text())
    contracts = Path(contracts or Path(__file__).resolve().parents[1] / "contracts")
    Draft202012Validator(json.loads((contracts / "delivery-package.schema.json").read_text())).validate(
        inventory
    )
    entries = inventory["entries"]
    names = [entry["path"] for entry in entries]
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    # The inventory excludes only itself; a file cannot contain its own cryptographic hash.
    if len(set(names)) != len(names) or set(names) != actual - {"delivery-package.json"}:
        raise ValueError("PACKAGE_INVENTORY_MISMATCH")
    total = 0
    for entry in entries:
        data = safe_path(root, entry["path"]).read_bytes()
        if len(data) != entry["bytes"] or file_hash(data) != entry["sha256"]:
            raise ValueError("PACKAGE_INTEGRITY_FAILED")
        total += len(data)
    if inventory["total_files"] != len(entries) or inventory.get("total_bytes") != total:
        raise ValueError("PACKAGE_INVENTORY_MISMATCH")
    return inventory
