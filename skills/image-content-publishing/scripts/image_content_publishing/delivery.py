import shutil
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

from filelock import FileLock
from jsonschema.exceptions import ValidationError
from PIL import Image

from shared.artifacts import delivery_inventory, file_ref, verify_delivery
from shared.paths import safe_path

from .contracts import (
    SHARED_CONTRACTS,
    PublishingError,
    check_schema,
    json_bytes,
    nonblank,
    now,
    platform_config,
    public_payload,
    read_json,
    require,
)
from .covers import make_covers
from .evidence import validate_evidence
from .planning import check_selection, content_id, make_plan, score_topic, selected_topics
from .render import ARTICLE_BLOCKS_VERSION, article_views, note_views, validate_article, validate_note
from .source import IMAGE_EXTENSIONS, assert_source

DELIVERY = "delivery-package.json"
PLAN = "plan.json"
INVALID = "OUTPUT_ASSET_INVALID"
REVIEW_FIELDS = ("image_facts", "claim_grounding", "editorial_quality")


def _write(folder, path, data):
    file = safe_path(folder, path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(data)
    return file_ref(path, data)


def _under_item(item_id, ref):
    return ref | {"path": f"{item_id}/{ref['path']}"}


def _body(source, topic, item_id, draft):
    code = "CONTENT_GENERATION_FAILED"
    require(isinstance(draft, dict), code, "缺少本篇成稿")
    public_payload(draft)
    review = draft.get("review")
    require(
        isinstance(review, dict) and all(review.get(key) is True for key in REVIEW_FIELDS),
        code,
        "宿主需要完成素材事实、断言和文案审核",
    )
    claim_ids = draft.get("claim_ids", [])
    evidence = validate_evidence(topic["research_level"], item_id, claim_ids, draft.get("evidence"))
    body = draft.get("body")
    require(isinstance(body, dict), code, "缺少结构化正文")
    disclosure = platform_config()["platforms"]["xiaohongshu"]["ai_disclosure_suggested"].split("，")[0]
    metadata = {"topic_id": topic["topic_id"], "claim_ids": claim_ids}
    if topic["platform"] == "wechat":
        require(
            body.get("blocks_version", ARTICLE_BLOCKS_VERSION) in ("1.1", ARTICLE_BLOCKS_VERSION),
            "CONTENT_STRUCTURE_INVALID",
            "公众号正文字段版本无效",
        )
        metadata["content_id"] = item_id
        body = body | {"blocks_version": ARTICLE_BLOCKS_VERSION}
    else:
        metadata["selected_asset_ids"] = topic["selected_asset_ids"]
    require(
        all(body.get(field, value) == value for field, value in metadata.items()),
        "CONTENT_STRUCTURE_INVALID",
        "正文元数据与选题或证据包不符",
    )
    body = {"ai_disclosure": disclosure, **body, **metadata}
    require(nonblank(body["ai_disclosure"]), "CONTENT_STRUCTURE_INVALID", "需填写 AI 图片说明")
    ids = topic["selected_asset_ids"]
    if topic["platform"] == "wechat":
        validate_article(body, ids, source.assets, source.reference["branch"])
    else:
        require("cover" not in draft, "CONTENT_STRUCTURE_INVALID", "小红书没有独立封面字段")
        validate_note(body, ids)
    return body, evidence


def _cover_files(folder, item_id, source, profiles, proposal):
    code = "COVER_FIT_FAILED"
    require(isinstance(proposal, dict), code, "公众号需要指定同一张双封面源图")
    asset_id = proposal.get("asset_id")
    require(isinstance(asset_id, str) and asset_id in source.assets, code, "封面源图无效")
    profile = profiles[asset_id]
    require(
        profile["usable"]
        and profile.get("cover_suitability") != "unsuitable"
        and profile["quality"].get("cover_ready") is not False,
        code,
        "素材画像判定封面源图不可用",
    )
    covers = make_covers(source.image_bytes(asset_id), source.assets[asset_id], proposal)
    return {
        f"cover_{kind}": meta
        | {"file": _under_item(item_id, _write(folder, f"covers/cover-{kind}.png", data))}
        for kind, (meta, data) in covers.items()
    }


def _assemble_item(folder, source, profiles, topic, draft, revision):
    item_id = content_id(topic)
    body, evidence = _body(source, topic, item_id, draft)
    wechat = topic["platform"] == "wechat"
    covers = _cover_files(folder, item_id, source, profiles, draft.get("cover")) if wechat else {}
    used_topic = topic | {"status": "used", "content_ids": [item_id]}
    topic_ref = _under_item(item_id, _write(folder, "topic-plan.json", json_bytes(used_topic)))
    ids = topic["selected_asset_ids"]
    profile_ids = list(dict.fromkeys(ids + [cover["source_asset_id"] for cover in covers.values()]))
    profile = {"asset_ids": profile_ids, "profiles": [profiles[asset_id] for asset_id in profile_ids]}
    profile_ref = _under_item(item_id, _write(folder, "profile.json", json_bytes(profile)))

    paths, image_refs = {}, []
    extensions = {fmt.lower(): ext for fmt, ext in IMAGE_EXTENSIONS.items()}
    for order, asset_id in enumerate(ids, 1):
        asset = source.assets[asset_id]
        relative = f"images/image-{order:02}.{extensions[asset['format']]}"
        ref = _under_item(item_id, _write(folder, relative, source.image_bytes(asset_id)))
        paths[asset_id] = relative
        image_refs.append(ref | {"source_asset_id": asset_id, "source_order": asset["index"]})

    stem = "article" if wechat else "note"
    files = {f"{stem}_json": _under_item(item_id, _write(folder, f"{stem}.json", json_bytes(body)))}
    views = article_views(body, paths) if wechat else note_views(body)
    for key, (name, text) in views.items():
        files[key] = _under_item(item_id, _write(folder, name, text.encode("utf-8")))
    research = {
        "level": evidence["research_level"],
        "status": evidence["status"],
        "search_performed": evidence["search_performed"],
        "queries": evidence.get("queries", []),
    }
    if evidence["search_performed"]:
        research["evidence_file"] = _under_item(
            item_id, _write(folder, "research/evidence.json", json_bytes(evidence))
        )
    return {
        "content_id": item_id,
        "content_revision": revision,
        "platform": topic["platform"],
        "topic_id": topic["topic_id"],
        **({"theme_id": topic["theme_id"]} if "theme_id" in topic else {}),
        "topic_plan_file": topic_ref,
        "profile_file": profile_ref,
        "status": "completed",
        "selected_asset_ids": ids,
        "claim_ids": body["claim_ids"],
        "research_level": topic["research_level"],
        "research_status": evidence["status"],
        "research": research,
        "files": files,
        "package_images": image_refs,
        **covers,
        "validation": {
            "passed": True,
            "checks": [
                "host_editorial_review",
                "evidence_links",
                "platform_structure",
                "asset_references",
                "topic_gate",
            ],
            "scorecard": topic["quality_scorecard"],
        },
    }


def _read_verified(root, ref):
    path = ref["path"]
    data = safe_path(root, path).read_bytes()
    expected = file_ref(path, data)
    require(all(ref.get(k) == v for k, v in expected.items()), INVALID, "交付引用与文件不一致", path=path)
    return data


def _check_cover(root, cover, profiles, ref, name):
    path = ref(cover["file"], f"covers/cover-{name}.png")
    with Image.open(path) as image:
        image.load()
        target = (cover["actual_width"], cover["actual_height"])
        require(image.format == "PNG" and image.size == target, INVALID, "封面图尺寸或格式不符")
    profile = profiles.get(cover["source_asset_id"])
    require(profile is not None, INVALID, "封面缺少源图画像")
    width, height = profile["width"], profile["height"]
    x1, y1, x2, y2 = cover["crop_box"]
    left, top, right, bottom = cover["subject_box"]
    require(
        0 <= x1 <= left < right <= x2 <= width and 0 <= y1 <= top < bottom <= y2 <= height,
        INVALID,
        "封面裁切超出源图或切断主体安全区",
    )
    factor = max(1.0, max(target) / max(x2 - x1, y2 - y1))
    config = platform_config()
    ratio = config["platforms"]["wechat"]["covers"][name]["target_ratio"]
    w, h = map(float, ratio.split(":"))
    require(
        cover["target_ratio"] == ratio
        and abs(target[1] - target[0] * h / w) <= 0.5
        and factor <= config["global"]["max_cover_upscale_factor"]
        and cover["source_asset_long_edge_px"] == max(width, height)
        and abs(cover["upscale_factor"] - factor) <= 1e-8
        and cover["quality_risk"] == (factor > 1.0),
        INVALID,
        "封面比例或放大记录不符",
    )


def _validate_item(root, item, topic, plan, seen, source):
    item_id = content_id(topic)

    def ref(record, suffix):
        path = f"{item_id}/{suffix}"
        require(record["path"] == path and path not in seen, INVALID, "内容文件引用冲突")
        _read_verified(root, record)
        seen.add(path)
        return safe_path(root, path)

    require(
        item["content_id"] == item_id
        and item["topic_id"] == topic["topic_id"]
        and item["platform"] == topic["platform"]
        and item["research_level"] == topic["research_level"]
        and item["selected_asset_ids"] == topic["selected_asset_ids"]
        and item["status"] == "completed",
        INVALID,
        "交付项目与选题不一致",
    )
    used_topic = read_json(ref(item["topic_plan_file"], "topic-plan.json"))
    require(
        used_topic == topic | {"status": "used", "content_ids": [item_id]}, INVALID, "选题文件与计划不一致"
    )
    require(
        score_topic(used_topic, len(plan["topics"]) > 1) == item["validation"]["scorecard"],
        INVALID,
        "选题评分卡不一致",
    )
    profile_file = read_json(ref(item["profile_file"], "profile.json"))
    profiles = {p["asset_id"]: p for p in plan["profiles"]}
    ids, branch = item["selected_asset_ids"], plan["source"]["branch"]
    assets = {asset_id: {"index": p["source_order"]} for asset_id, p in profiles.items()}
    check_selection(ids, assets, branch, code=INVALID)
    require(len(ids) == len(item["package_images"]), INVALID, "正文图片副本数量不符")
    image_paths = {}
    for order, (asset_id, package_image) in enumerate(zip(ids, item["package_images"]), 1):
        ext = Path(package_image["path"]).suffix
        relative = f"images/image-{order:02}{ext}"
        path = ref(package_image, relative)
        image_paths[asset_id] = relative
        profile = profiles[asset_id]
        with Image.open(path) as image:
            image.load()
            require(
                IMAGE_EXTENSIONS.get(image.format) is not None
                and ext == "." + IMAGE_EXTENSIONS[image.format]
                and image.size == (profile["width"], profile["height"])
                and package_image.get("source_asset_id") == asset_id
                and package_image.get("source_order") == profile["source_order"],
                INVALID,
                "正文图片副本与索引不符",
            )
        if source:
            require(
                package_image["sha256"] == source.assets[asset_id]["sha256"], INVALID, "图片副本与来源不符"
            )

    research = item["research"]
    evidence_file = research.get("evidence_file")
    evidence = read_json(ref(evidence_file, "research/evidence.json")) if evidence_file else None
    pack = validate_evidence(item["research_level"], item_id, item["claim_ids"], evidence)
    require(
        pack["status"] == item["research_status"] == research["status"]
        and pack["search_performed"] == research["search_performed"]
        and pack.get("queries", []) == research["queries"]
        and pack["research_level"] == research["level"],
        INVALID,
        "研究元数据不一致",
    )
    files = item["files"]
    profile_ids = list(ids)
    if item["platform"] == "wechat":
        article = read_json(ref(files["article_json"], "article.json"))
        validate_article(article, ids, assets, branch, allow_legacy=True)
        if article.get("blocks_version") == ARTICLE_BLOCKS_VERSION:
            for key, (filename, expected) in article_views(article, image_paths).items():
                require(
                    ref(files[key], filename).read_bytes() == expected.encode("utf-8"),
                    INVALID,
                    "公众号导出文件与逐张图文正文不一致",
                )
        else:
            ref(files["article_md"], "article.md")
            ref(files["article_html"], "article.html")
        body = article
        require(article.get("content_id") == item_id, INVALID, "文章内容 ID 不一致")
        wide, square = item["cover_wide"], item["cover_square"]
        require(wide["source_asset_id"] == square["source_asset_id"], INVALID, "双封面源图不一致")
        require(wide["subject_box"] == square["subject_box"], INVALID, "双封面主体标注不一致")
        _check_cover(root, wide, profiles, ref, "wide")
        _check_cover(root, square, profiles, ref, "square")
        profile_ids = list(dict.fromkeys(profile_ids + [wide["source_asset_id"]]))
    else:
        body = read_json(ref(files["note_json"], "note.json"))
        ref(files["note_md"], "note.md")
        validate_note(body, ids)
    require(
        body.get("topic_id") == topic["topic_id"]
        and body.get("claim_ids") == item["claim_ids"]
        and nonblank(body.get("ai_disclosure"))
        and profile_file
        == {"asset_ids": profile_ids, "profiles": [profiles[asset_id] for asset_id in profile_ids]},
        INVALID,
        "正文、画像、选题或断言关联不符",
    )
    public_payload(body)


def _validate_content(root, source_path=None, *, inventory_bytes=None):
    inventory = verify_delivery(root, SHARED_CONTRACTS, inventory_bytes=inventory_bytes)
    require(
        inventory["package_type"] == "content_publishing" and inventory["manifest_path"] == "manifest.json",
        INVALID,
        "交付清单类型不符",
    )
    manifest = read_json(safe_path(root, "manifest.json"))
    check_schema("content-artifact", manifest, INVALID)
    public_payload(manifest)
    require(
        manifest["delivery_package_path"] == DELIVERY and manifest["plan_file"]["path"] == PLAN,
        INVALID,
        "素材包缺少计划或清单",
    )
    plan = read_json(safe_path(root, PLAN))
    public_payload(plan)
    _read_verified(root, manifest["plan_file"])
    source = assert_source(source_path, plan["source"]) if source_path else None
    if source:
        require(make_plan(source, plan) == plan, INVALID, "交付计划与来源不匹配")
    wanted = manifest["selected_topic_ids"]
    topics = selected_topics(plan, wanted if plan["intent"] == "explore_topics" else None)
    require([topic["topic_id"] for topic in topics] == wanted, INVALID, "交付选题与计划不一致")
    jobs = {content_id(topic): topic for topic in topics}
    items = {item["content_id"]: item for item in manifest["items"]}
    errors = {item["content_id"]: item for item in manifest["errors"]}
    require(
        len(items) == len(manifest["items"])
        and len(errors) == len(manifest["errors"])
        and not (items.keys() & errors.keys())
        and items.keys() | errors.keys() == jobs.keys()
        and manifest["status"] == ("partial" if errors else "completed"),
        INVALID,
        "交付篇数、状态与选题计划不符",
    )
    seen = {"manifest.json", PLAN}
    selected = set()
    for item_id, item in items.items():
        _validate_item(root, item, jobs[item_id], plan, seen, source)
        selected.update(item["selected_asset_ids"])
    original_order = [profile["asset_id"] for profile in plan["profiles"]]
    # The packed file list and manifest cross-references cover exactly the same payload.
    require(
        (plan["source"] | {"selected_asset_ids": [i for i in original_order if i in selected]})
        == manifest["source"]
        and {entry["path"] for entry in inventory["entries"]} == seen,
        INVALID,
        "交付中含有未登记文件或来源引用不一致",
    )
    return manifest, inventory


def _verified_content(root, source_path=None, *, inventory_bytes=None):
    try:
        return _validate_content(Path(root), source_path, inventory_bytes=inventory_bytes)
    except (OSError, ValueError, KeyError, TypeError, ValidationError, Image.DecompressionBombError) as exc:
        raise PublishingError(INVALID, "素材包缺失、损坏或不符合契约") from exc


def validate_content(root, source_path=None):
    return _verified_content(root, source_path)[0]


def _new_target(output, source, previous):
    output = Path(output).absolute()
    protected = [source.root]
    if previous:
        protected.append(previous)
    require(
        not output.exists()
        and not output.is_symlink()
        and all(not output.resolve().is_relative_to(path.resolve()) for path in protected),
        "ARTIFACT_EXISTS",
        "请选择新目录，且须位于来源和已交付产物之外",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def build_content(source_path, plan, drafts, output, *, topic_ids=None, previous=None):
    require(isinstance(plan, dict), "TOPIC_PLAN_INVALID", "需要经过校验的计划对象")
    source = assert_source(source_path, plan.get("source"))
    require(make_plan(source, plan) == plan, "TOPIC_PLAN_INVALID", "请先通过 plan 生成计划")
    require(isinstance(drafts, dict), "INVALID_INPUT", "成稿输入须为按 content_id 索引的对象")
    previous = Path(previous).absolute() if previous else None
    before = validate_content(previous) if previous else None
    if before:
        require(
            read_json(safe_path(previous, PLAN)) == plan,
            "SOURCE_REVISION_MISMATCH",
            "补做时须沿用原任务的来源与计划",
        )
        if plan["intent"] == "explore_topics" and not topic_ids:
            topic_ids = before["selected_topic_ids"]
    topics = selected_topics(plan, topic_ids)
    selected_ids = [t["topic_id"] for t in topics]
    available = {content_id(topic) for topic in topics}
    if before:
        require(before["selected_topic_ids"] == selected_ids, "TOPIC_PLAN_INVALID", "补做时不能更换选题")
        available = {error["content_id"] for error in before["errors"]}
        require(bool(drafts), "INVALID_INPUT", "补做至少需要一份失败项的新成稿")
    require(drafts.keys() <= available, "INVALID_INPUT", "成稿包含计划之外或已完成的内容项")
    output = _new_target(output, source, previous)
    profiles = {profile["asset_id"]: profile for profile in plan["profiles"]}
    items = {item["content_id"]: item for item in before["items"]} if before else {}
    errors = {error["content_id"]: error for error in before["errors"]} if before else {}
    started = before["created_at"] if before else now()
    job_id = before["content_job_id"] if before else "cp-" + uuid.uuid4().hex
    generation = int(before["revision"].split("-")[0][1:]) + 1 if before else 1
    revision = f"r{generation:03}-{uuid.uuid4().hex[:12]}" if before else "r001"
    image_ops = 0
    with TemporaryDirectory(prefix=".publishing-", dir=output.parent) as stage:
        root = Path(stage)
        for item_id in items:
            shutil.copytree(safe_path(previous, item_id), safe_path(root, item_id))
        for topic in topics:
            item_id = content_id(topic)
            if item_id in items or (before and item_id not in drafts):
                continue
            folder = safe_path(root, item_id)
            try:
                items[item_id] = _assemble_item(
                    folder, source, profiles, topic, drafts.get(item_id), revision
                )
            except PublishingError as exc:
                if folder.exists():
                    shutil.rmtree(folder)
                if exc.code.startswith("SOURCE_"):
                    raise
                errors[item_id] = exc.as_dict() | {"content_id": item_id, "topic_id": topic["topic_id"]}
            else:
                image_ops += 2 if topic["platform"] == "wechat" else 0
                errors.pop(item_id, None)
        if not items and len(errors) == 1:
            error = next(iter(errors.values()))
            raise PublishingError(error["code"], error["message"], errors=list(errors.values()))
        require(items, "CONTENT_GENERATION_FAILED", "本批所有稿件均未通过", errors=list(errors.values()))
        source.check_manifest()
        ordered = [items[content_id(t)] for t in topics if content_id(t) in items]
        used = {asset_id for item in ordered for asset_id in item["selected_asset_ids"]}
        warnings = list(source.warnings)
        for item in ordered:
            for kind in ("cover_wide", "cover_square"):
                if item.get(kind, {}).get("quality_risk"):
                    warnings.append(
                        {
                            "code": "COVER_QUALITY_RISK",
                            "message": "封面目标长边超过裁切源长边，原始细节有限",
                            "content_id": item["content_id"],
                            "cover": kind,
                        }
                    )
        manifest = {
            "schema_version": "1.1",
            "artifact_type": "content_publishing",
            "content_job_id": job_id,
            "revision": revision,
            "parent_revision": before["revision"] if before else None,
            "source": source.reference | {"selected_asset_ids": [i for i in source.assets if i in used]},
            "status": "partial" if errors else "completed",
            "selected_topic_ids": selected_ids,
            "batch": {
                "batch_id": job_id,
                "planned_item_count": len(topics),
                "platforms": list(dict.fromkeys(t["platform"] for t in topics)),
                "dedup_summary": "选题通过评分和重复项检查",
            },
            "items": ordered,
            "plan_file": _write(root, PLAN, json_bytes(plan)),
            "delivery_package_path": DELIVERY,
            "execution": {"image_ops_used": image_ops, "budget_exceeded": False},
            "warnings": warnings,
            "errors": list(errors.values()),
            "created_at": started,
            "completed_at": now(),
        }
        _write(root, "manifest.json", json_bytes(manifest))
        inventory = delivery_inventory(root, f"{job_id}-{revision}", "content_publishing", now())
        _write(root, DELIVERY, json_bytes(inventory))
        validate_content(root)
        with FileLock(str(output) + ".lock", timeout=10):
            _new_target(output, source, previous)
            source.check_manifest()
            root.rename(output)
    return manifest


def pack_content(root, target):
    root, target = Path(root).absolute(), Path(target).absolute()
    require(not target.resolve().is_relative_to(root.resolve()), INVALID, "ZIP 须保存到素材包目录外")
    target.parent.mkdir(parents=True, exist_ok=True)
    inventory_bytes = safe_path(root, DELIVERY).read_bytes()
    _, inventory = _verified_content(root, inventory_bytes=inventory_bytes)
    require(not target.exists() and not target.is_symlink(), "ARTIFACT_EXISTS", "ZIP 目标已存在")
    with TemporaryDirectory(prefix=".publishing-zip-", dir=target.parent) as stage:
        archive = Path(stage) / "content.zip"
        with ZipFile(archive, "x", ZIP_DEFLATED) as bundle:
            for entry in inventory["entries"]:
                bundle.writestr(entry["path"], _read_verified(root, entry))
            bundle.writestr(DELIVERY, inventory_bytes)
        with FileLock(str(target) + ".lock", timeout=10):
            require(not target.exists() and not target.is_symlink(), "ARTIFACT_EXISTS", "ZIP 目标已存在")
            archive.rename(target)
    return target
