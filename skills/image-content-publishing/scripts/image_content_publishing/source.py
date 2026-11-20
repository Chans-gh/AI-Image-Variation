import io
from dataclasses import dataclass
from pathlib import Path

from jsonschema.exceptions import ValidationError
from PIL import Image

from shared.artifacts import file_hash, validate_artifact, verify_delivery
from shared.paths import safe_path

from .contracts import (
    DIRECTION_REGISTRY,
    SHARED_CONTRACTS,
    PublishingError,
    public_payload,
    read_json,
    require,
)

SOURCE_INVALID = "SOURCE_MANIFEST_INVALID"
SOURCE_INCOMPLETE = "SOURCE_ARTIFACT_INCOMPLETE"
SOURCE_ASSET = "SOURCE_ASSET_MISSING"
IMAGE_EXTENSIONS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}
ORDERED_BRANCHES = {"progression", "narrative"}


@dataclass(frozen=True)
class SourceArtifact:
    root: Path
    reference: dict
    assets: dict
    warnings: list

    def inspection(self):
        return {"source": self.reference, "assets": list(self.assets.values()), "warnings": self.warnings}

    def image_bytes(self, asset_id):
        entry = self.assets[asset_id]
        try:
            data = safe_path(self.root, entry["path"]).read_bytes()
        except (OSError, ValueError) as exc:
            raise PublishingError(SOURCE_ASSET, "无法读取源图", asset_id=asset_id) from exc
        require(file_hash(data) == entry["sha256"], SOURCE_ASSET, "源图字节已变化", asset_id=asset_id)
        return data

    def check_manifest(self):
        try:
            sha = file_hash(safe_path(self.root, "manifest.json").read_bytes())
        except (OSError, ValueError) as exc:
            raise PublishingError(SOURCE_INVALID, "无法读取来源清单") from exc
        require(sha == self.reference["image_manifest_sha256"], "SOURCE_REVISION_MISMATCH", "来源清单已变化")


def _source_root(path):
    path = Path(path).absolute()
    return path.parent if path.name == "manifest.json" else path


def _verify_source(root, manifest_bytes):
    try:
        return validate_artifact(
            root, SHARED_CONTRACTS, require_complete=False, manifest_bytes=manifest_bytes
        )
    except (FileNotFoundError, Image.DecompressionBombError, OSError) as exc:
        raise PublishingError(SOURCE_ASSET, "源文件缺失、损坏或超过解码上限") from exc
    except ValidationError as exc:
        field = ".".join(map(str, exc.absolute_path)) or "$"
        raise PublishingError(SOURCE_INVALID, "来源清单字段不符合契约", field=field) from exc
    except (TypeError, KeyError, ValueError) as exc:
        reason = str(exc)
        code = (
            SOURCE_ASSET
            if reason in {"ASSET_INTEGRITY_FAILED", "ASSET_DIMENSIONS_MISMATCH"}
            else SOURCE_INVALID
        )
        raise PublishingError(code, "来源未通过完整性预检", reason=reason) from exc


def _delivery_checked(root):
    try:
        inventory = safe_path(root, "delivery-package.json")
        if not inventory.is_file():
            return False
        data = verify_delivery(root, SHARED_CONTRACTS)
        require(
            data["manifest_path"] == "manifest.json" and data["package_type"] == "image_variations",
            SOURCE_INVALID,
            "来源交付清单与图像产物不匹配",
        )
    except (ValueError, OSError, ValidationError) as exc:
        raise PublishingError(SOURCE_INVALID, "来源交付清单损坏或文件发生变化") from exc
    return True


def _source_evidence(manifest, assets, has_delivery):
    validation = manifest["validation"]
    native = manifest.get("dimension_execution")
    complete = manifest["status"] == "completed"
    warnings = []
    if native:
        # The shared validator checks native geometry for completed artifacts. For a partial
        # sequence, verify the retained frames individually against their recorded native size.
        if not complete:
            records = native.get("frames", [])
            require(
                isinstance(records, list)
                and native.get("dimension_policy") == "provider_native"
                and native.get("requested_tier") == manifest["requested_resolution_tier"]
                and native.get("requested_ratio") == manifest["requested_aspect_ratio"],
                SOURCE_INVALID,
                "部分序列的原生尺寸记录无效",
            )
            require(
                all(isinstance(frame, dict) and type(frame.get("index")) is int for frame in records)
                and len({frame["index"] for frame in records}) == len(records),
                SOURCE_INVALID,
                "部分序列的原生尺寸索引无效",
            )
            frames = {frame["index"]: frame for frame in records}
            for asset in assets:
                frame = frames.get(asset["index"], {})
                require(
                    frame.get("tier_validation", {}).get("status") == "passed"
                    and frame.get("native_output") == {"width": asset["width"], "height": asset["height"]},
                    SOURCE_INVALID,
                    "部分序列的已选帧缺少原生尺寸验证",
                    asset_id=asset["id"],
                )
        return has_delivery, warnings
    if manifest["output_mode"] == "sequence":
        return has_delivery and (not complete or validation.get("sequence_complete") is True), warnings

    edge = min(max(asset["width"], asset["height"]) for asset in assets)
    score = validation.get("seam_score")
    require(edge >= 512, SOURCE_INVALID, "旧版 Grid 单格低于 512 px 硬下限")
    require(validation.get("tile_edge_ok") is not False, SOURCE_INVALID, "旧版 Grid 单格未通过分辨率检查")
    require(
        score is None or (type(score) in {float, int} and 0.60 <= score <= 1),
        SOURCE_INVALID,
        "旧版 Grid 接缝评分未达阈值",
    )
    if edge < 768:
        warnings.append({"code": "TILE_RESOLUTION_RISK", "message": "旧版 Grid 单格长边低于 768 px"})
    if score is not None and score < 0.85:
        warnings.append({"code": "GRID_SEAM_RISK", "message": "旧版 Grid 接缝评分处于警告区间"})
    verified = validation.get("tile_edge_ok") is True and score is not None and has_delivery
    return verified, warnings


def _selected_assets(manifest, all_assets, subset):
    if manifest["status"] != "completed":
        require(
            manifest["status"] == "partial_failed" and manifest["output_mode"] == "sequence" and bool(subset),
            SOURCE_INCOMPLETE,
            "不完整产物仅可显式选用已完成的序列帧",
        )
    if subset is None:
        return all_assets
    require(
        isinstance(subset, (tuple, list)) and subset and all(isinstance(item, str) for item in subset),
        SOURCE_INCOMPLETE,
        "请提供明确的素材 ID 列表",
    )
    chosen = set(subset)
    selected = [asset for asset in all_assets if asset["id"] in chosen]
    require(
        len(selected) == len(subset),
        SOURCE_ASSET,
        "素材列表包含重复、缺失或未完成的 ID",
    )
    return selected


def _image_card(root, entry):
    try:
        data = safe_path(root, entry["path"]).read_bytes()
        require(file_hash(data) == entry["sha256"], SOURCE_ASSET, "源图字节已变化", asset_id=entry["id"])
        with Image.open(io.BytesIO(data)) as image:
            fmt = image.format
            require(
                fmt in IMAGE_EXTENSIONS
                and getattr(image, "n_frames", 1) == 1
                and image.getexif().get(274, 1) == 1,
                SOURCE_ASSET,
                "源图须为已规范化方向的单张 PNG、JPEG 或 WebP",
                asset_id=entry["id"],
            )
        declared = entry.get("format")
        require(
            declared is None or declared.lower().replace("jpg", "jpeg") == fmt.lower(),
            SOURCE_ASSET,
            "源图格式与清单不符",
            asset_id=entry["id"],
        )
    except (OSError, ValueError) as exc:
        raise PublishingError(SOURCE_ASSET, "无法验证源图格式", asset_id=entry["id"]) from exc
    fields = ("id", "kind", "index", "path", "width", "height", "sha256", "phase_label", "story_context")
    return {k: entry[k] for k in fields if k in entry} | {"format": fmt.lower()}


def load_source(path, *, subset=None, accept_unverified=False, expected_sha=None):
    root = _source_root(path)
    try:
        data = safe_path(root, "manifest.json").read_bytes()
    except (OSError, ValueError) as exc:
        raise PublishingError(SOURCE_INVALID, "无法读取来源清单") from exc
    digest = file_hash(data)
    require(not expected_sha or digest == expected_sha, "SOURCE_REVISION_MISMATCH", "来源清单已变化")
    result = _verify_source(root, data)
    manifest = result["manifest"]
    directions = {entry["key"] for entry in read_json(DIRECTION_REGISTRY)["directions"]}
    require(manifest["direction"] in directions, SOURCE_INVALID, "来源方向未在方向库注册")
    selected = _selected_assets(manifest, result["content_assets"], subset)
    verified, warnings = _source_evidence(manifest, selected, _delivery_checked(root))
    require(
        verified or accept_unverified is True,
        "TILE_RESOLUTION_UNVERIFIED",
        "来源缺少完整验证记录，需要有效产物或用户明确接受风险",
    )
    if not verified:
        warnings.append({"code": "VALIDATION_UNVERIFIED", "message": "来源缺少完整验证记录，已明确接受风险"})
    missing = sorted(
        set(range(1, manifest["image_count"] + 1)) - {a["index"] for a in result["content_assets"]}
    )
    if missing:
        warnings.append(
            {
                "code": "SOURCE_ARTIFACT_INCOMPLETE",
                "message": "来源有缺帧，只用于可独立理解的内容",
                "missing_indices": missing,
            }
        )
    cards = {entry["id"]: _image_card(root, entry) for entry in selected}
    fields = ("task_id", "artifact_revision", "schema_version", "output_mode", "direction", "branch")
    reference = {key: manifest[key] for key in fields} | {
        "image_manifest_sha256": digest,
        "subset_asset_ids": list(cards) if subset is not None else None,
        "validation_unverified": not verified,
        "missing_indices": missing,
        "synthetic_fixture": manifest.get("synthetic_fixture") is True,
    }
    public_payload(reference)
    source = SourceArtifact(root, reference, cards, warnings)
    source.check_manifest()
    return source


def assert_source(path, reference):
    require(
        isinstance(reference, dict)
        and isinstance(reference.get("image_manifest_sha256"), str)
        and len(reference["image_manifest_sha256"]) == 64,
        SOURCE_INVALID,
        "需要 inspect 返回的完整来源引用",
    )
    source = load_source(
        path,
        subset=reference.get("subset_asset_ids"),
        accept_unverified=reference.get("validation_unverified") is True,
        expected_sha=reference["image_manifest_sha256"],
    )
    require(source.reference == reference, "SOURCE_REVISION_MISMATCH", "来源引用与当前清单不一致")
    return source
