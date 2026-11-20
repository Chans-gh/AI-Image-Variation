import io
import json
import re
from copy import deepcopy
from pathlib import Path
from zipfile import ZipFile

import pytest
from conftest import finish
from image_content_publishing.contracts import PublishingError, json_bytes
from image_content_publishing.covers import make_covers
from image_content_publishing.delivery import build_content, pack_content, validate_content
from image_content_publishing.evidence import validate_evidence
from image_content_publishing.planning import content_id, make_plan
from image_content_publishing.source import assert_source, load_source
from image_variations.adapters.mock import MockProvider
from image_variations.packaging import inventory
from PIL import Image

from shared.artifacts import delivery_inventory, file_hash, file_ref


def source_for(make_job, mode="grid", provider=None, **kwargs):
    state = finish(make_job(mode, **kwargs), provider or MockProvider())
    return Path(state["artifact_path"])


def topic_for(platform, topic_id, ids, *, level="R0"):
    return {
        "platform": platform,
        "topic_id": topic_id,
        "candidate_title": f"画面观察与编辑练习 {topic_id}",
        "angle": "合成画面配色练习",
        "audience": "学习图文编辑的读者",
        "core_thesis": f"从不同配色组合练习安排用图 {topic_id}",
        "outline": ["介绍素材", "比对色块", "收束到编辑提示"],
        "value_proposition": "读者能选择两张构图近似的画面做对比",
        "distinct_from_other_topics": "每篇采用不同的比较方式",
        "selected_asset_ids": ids,
        "asset_plan": [
            {"asset_id": asset_id, "order": i, "reason": "作为对比中的一张"}
            for i, asset_id in enumerate(ids, 1)
        ],
        "research_level": level,
        "research_questions": [] if level == "R0" else ["核验一个外部事实"],
        "quality_scorecard": {
            "asset_fit": 5,
            "value": 5,
            "distinctness": 4,
            "verifiability": None if level == "R0" else 4,
            "platform_fit": 4,
            "total": 3,
            "passed": False,
        },
    }


def request_for(source, topics, intent="generate_single"):
    counts = {}
    for topic in topics:
        platform = topic["platform"]
        counts[platform] = counts.get(platform, 0) + 1
    return {
        "source": source.reference,
        "intent": intent,
        "platform_counts": counts,
        "topics": topics,
        "profiles": [
            {
                "asset_id": asset["id"],
                "source_order": asset["index"],
                "width": asset["width"],
                "height": asset["height"],
                "usable": True,
                "observations": ["纯色背景上有浅色椭圆。"],
                "interpretations": ["统一形状便于并列对比。"],
            }
            for asset in source.assets.values()
        ],
    }


def draft_for(source, topic):
    ids = topic["selected_asset_ids"]
    draft = {"review": {"image_facts": True, "claim_grounding": True, "editorial_quality": True}}
    if topic["platform"] == "xiaohongshu":
        return draft | {
            "body": {
                "title": "同一个椭圆怎么选图",
                "body_with_hashtags": "我会把底色差异最大的两张并排看。\n#配色练习 #图文编辑",
                "hashtags": ["#配色练习", "#图文编辑"],
                "first_comment": "可以先遮住边缘，只比较中间色块的辨识度。",
            }
        }

    blocks = [{"type": "text", "content": "先观察浅色主体在两张图中的位置。"}]
    for order, asset_id in enumerate(ids, 1):
        blocks.append({"type": "image", "asset_id": asset_id})
        blocks.append({"type": "text", "content": f"观察第 {order} 张图片的底色。"})
    asset = source.assets[ids[0]]
    edge = min(asset["width"], asset["height"])
    return draft | {
        "body": {
            "title": "图片与文字的节奏 <script>示例</script>",
            "summary": "图文素材测试。",
            "blocks": blocks,
        },
        "cover": {
            "asset_id": ids[0],
            "sizes": {"wide": [edge, round(edge / 2.35)], "square": [edge, edge]},
        },
    }


@pytest.mark.parametrize(("mode", "count"), [("grid", 9), ("sequence", 16)])
def test_consumes_generated_source_by_index(make_job, mode, count):
    options = {"grid_size": 3} if mode == "grid" else {"image_count": 16, "branch": "progression"}
    root = source_for(make_job, mode, **options)
    source = load_source(root / "manifest.json")
    assert [x["index"] for x in source.assets.values()] == list(range(1, count + 1))
    assert {x["kind"] for x in source.assets.values()} == {"tile" if mode == "grid" else "frame"}
    assert source.reference["image_manifest_sha256"] == file_hash((root / "manifest.json").read_bytes())
    assert not source.reference["validation_unverified"]
    if mode == "sequence":
        assert all(x["phase_label"] for x in source.assets.values())


@pytest.mark.parametrize("version", ["1.0", "1.1"])
def test_legacy_source_requires_risk_acceptance_without_inventory(make_job, version):
    root = source_for(make_job, "sequence")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_version"] = version
    manifest.pop("dimension_execution")
    manifest_path.write_bytes(json_bytes(manifest))
    (root / "delivery-package.json").unlink()
    with pytest.raises(PublishingError, match="接受风险") as exc:
        load_source(root)
    assert exc.value.code == "TILE_RESOLUTION_UNVERIFIED"
    source = load_source(root, accept_unverified=True)
    assert source.reference["validation_unverified"]
    assert source.warnings[0]["code"] == "VALIDATION_UNVERIFIED"
    inventory(root, "legacy")
    assert not load_source(root).reference["validation_unverified"]


def test_source_mismatch_and_asset_tampering_are_blocked(make_job):
    root = source_for(make_job, grid_size=3)
    source = load_source(root)
    path = root / "manifest.json"
    original = path.read_bytes()
    path.write_bytes(original + b"\n")
    with pytest.raises(PublishingError) as exc:
        assert_source(root, source.reference)
    assert exc.value.code == "SOURCE_REVISION_MISMATCH"
    path.write_bytes(original)
    asset = next(iter(source.assets.values()))
    tile = root / asset["path"]
    tile.write_bytes(tile.read_bytes() + b"tampered")
    with pytest.raises(PublishingError) as exc:
        load_source(root)
    assert exc.value.code == "SOURCE_ASSET_MISSING"


def test_partial_narrative_rejects_grouped_draft_after_explicit_selection(make_job, tmp_path):
    provider = MockProvider({2: [b"bad PNG"] * 3})
    root = source_for(make_job, "sequence", provider, branch="narrative")
    with pytest.raises(PublishingError) as exc:
        load_source(root)
    assert exc.value.code == "SOURCE_ARTIFACT_INCOMPLETE"
    manifest = json.loads((root / "manifest.json").read_text())
    ids = [frame["id"] for frame in manifest["images"]][:2]
    source = load_source(root, subset=ids)
    assert source.reference["missing_indices"] == [2]
    assert [x["index"] for x in source.assets.values()] == [1, 3]
    topic = topic_for("wechat", "gap", ids)
    plan = make_plan(source, request_for(source, [topic]))
    draft = draft_for(source, topic)
    blocks = draft["body"]["blocks"]
    draft["body"]["blocks"] = [
        blocks[0],
        {"type": "image_group", "images": [blocks[1], blocks[3]]},
        blocks[-1],
    ]
    with pytest.raises(PublishingError) as exc:
        build_content(root, plan, {content_id(topic): draft}, tmp_path / "gap")
    assert exc.value.code == "CONTENT_STRUCTURE_INVALID"
    assert not (tmp_path / "gap").exists()


def test_topic_gates_and_exploration_selection(make_job, tmp_path):
    root = source_for(make_job, grid_size=3)
    source = load_source(root)
    ideas = [topic_for("xiaohongshu", key, list(source.assets)[:2]) for key in ("color", "layout")]
    request = request_for(source, ideas, "explore_topics")
    plan = make_plan(source, request)
    assert plan["status"] == "awaiting_selection"
    assert plan["topics"][0]["quality_scorecard"]["total"] == 18
    with pytest.raises(PublishingError) as exc:
        build_content(root, plan, {}, tmp_path / "waiting")
    assert exc.value.code == "TOPIC_SELECTION_REQUIRED" and not (tmp_path / "waiting").exists()
    topic = ideas[1]
    result = build_content(
        root, plan, {content_id(topic): draft_for(source, topic)}, tmp_path / "one", topic_ids=["layout"]
    )
    assert result["selected_topic_ids"] == ["layout"]
    ideas[0]["quality_scorecard"]["value"] = 4
    with pytest.raises(PublishingError) as exc:
        make_plan(source, request)
    assert exc.value.code == "QUALITY_GATE_FAILED"
    ideas[0]["quality_scorecard"]["value"] = 5
    ideas[0]["core_thesis"] = ideas[1]["core_thesis"] + "。"
    with pytest.raises(PublishingError) as exc:
        make_plan(source, request)
    assert exc.value.code == "TOPIC_DUPLICATE"
    request["intent"] = "generate_batch"
    request["platform_counts"]["xiaohongshu"] = 6
    with pytest.raises(PublishingError) as exc:
        make_plan(source, request)
    assert exc.value.code == "BATCH_BUDGET_EXCEEDED"


def test_mixed_package_failed_item_retry_preserves_success_bytes(make_job, tmp_path):
    root = source_for(make_job, grid_size=3)
    source = load_source(root)
    article = topic_for("wechat", "comparison", list(source.assets)[:4])
    note = topic_for("xiaohongshu", "selection", list(source.assets)[-2:])
    plan = make_plan(source, request_for(source, [article, note], "generate_mixed"))
    invalid_note = draft_for(source, note)
    invalid_note["body"]["hashtags"] = ["#标签"]
    original_root = tmp_path / "r001"
    before = build_content(
        root,
        plan,
        {content_id(article): draft_for(source, article), content_id(note): invalid_note},
        original_root,
    )
    assert before["status"] == "partial"
    assert before["errors"][0]["content_id"] == content_id(note)
    assert before["errors"][0]["code"] == "CONTENT_STRUCTURE_INVALID"
    first_item = before["items"][0]
    image_paths = [record["path"] for record in first_item["package_images"]]
    assert len(image_paths) == 4 and all("grid_board" not in path for path in image_paths)
    html = (original_root / first_item["files"]["article_html"]["path"]).read_text()
    assert "&lt;script&gt;" in html and "<script>" not in html
    new_root = tmp_path / "r002"
    result = build_content(
        root, plan, {content_id(note): draft_for(source, note)}, new_root, previous=original_root
    )
    assert result["status"] == "completed" and result["parent_revision"] == "r001"
    assert result["content_job_id"] == before["content_job_id"] and result["revision"].startswith("r002-")
    assert result["items"][1]["content_revision"] == result["revision"]
    assert result["items"][0] == first_item
    for path in (original_root / content_id(article)).rglob("*"):
        if path.is_file():
            assert path.read_bytes() == (new_root / path.relative_to(original_root)).read_bytes()
    assert validate_content(original_root, root) == before
    assert validate_content(new_root, root) == result
    assert "cover_wide" not in result["items"][1]
    with ZipFile(pack_content(new_root, tmp_path / "result.zip")) as zipped:
        assert "delivery-package.json" in zipped.namelist()
        assert set(zipped.namelist()) == {
            p.relative_to(new_root).as_posix() for p in new_root.rglob("*") if p.is_file()
        }
    tampered = new_root / image_paths[0]
    tampered.write_bytes(tampered.read_bytes() + b"extra")
    with pytest.raises(PublishingError) as exc:
        validate_content(new_root)
    assert exc.value.code == "OUTPUT_ASSET_INVALID"


def test_wechat_interleaves_each_of_sixteen_images_in_all_exports(make_job, tmp_path):
    root = source_for(make_job, "sequence", image_count=16, branch="progression")
    source = load_source(root)
    topic = topic_for("wechat", "sequence", list(source.assets))
    plan = make_plan(source, request_for(source, [topic]))
    out = tmp_path / "sequence"
    draft = draft_for(source, topic)
    draft["body"]["blocks"][0]["content"] += " ![稿件中的示例字样](https://example.org/image.png)"
    result = build_content(root, plan, {content_id(topic): draft}, out)
    item = result["items"][0]
    body = json.loads((out / item["files"]["article_json"]["path"]).read_text())
    expected = ["text", "image"] * 16 + ["text"]
    assert body["blocks_version"] == "1.2"
    assert [block["type"] for block in body["blocks"]] == expected
    html = (out / item["files"]["article_html"]["path"]).read_text()
    chunks = re.findall(r'<div class="text-block">.*?</div>|<figure>.*?</figure>', html, re.DOTALL)
    assert ["image" if chunk.startswith("<figure>") else "text" for chunk in chunks] == expected
    assert all(chunk.count("<img ") == 1 for chunk in chunks[1::2])
    assert all("<p>" in chunk and "<img " not in chunk for chunk in chunks[::2])
    assert html.count("<img ") == 16
    markdown = (out / item["files"]["article_md"]["path"]).read_text()
    blocks = markdown.strip().split("\n\n")[2:-1]
    assert ["image" if block.startswith("![") else "text" for block in blocks] == expected
    assert markdown.count("![") == 16
    assert [record["source_order"] for record in item["package_images"]] == list(range(1, 17))
    assert item["cover_wide"]["source_asset_id"] == item["cover_square"]["source_asset_id"]

    # A self-consistent inventory alone cannot establish the exported layout.
    html_path = item["files"]["article_html"]["path"]
    adjacent_images = html.replace(chunks[2], "", 1).encode("utf-8")
    (out / html_path).write_bytes(adjacent_images)
    item["files"]["article_html"] = file_ref(html_path, adjacent_images)
    (out / "manifest.json").write_bytes(json_bytes(result))
    updated_inventory = delivery_inventory(
        out, "layout-regression", "content_publishing", result["created_at"]
    )
    (out / "delivery-package.json").write_bytes(json_bytes(updated_inventory))
    with pytest.raises(PublishingError, match="导出文件与逐张图文正文不一致"):
        validate_content(out)


@pytest.mark.parametrize("invalid", ["image_group", "adjacent", "caption_only"])
def test_wechat_rejects_images_without_intervening_body_text(make_job, tmp_path, invalid):
    root = source_for(make_job, grid_size=2)
    source = load_source(root)
    topic = topic_for("wechat", "comparison", list(source.assets)[:2])
    plan = make_plan(source, request_for(source, [topic]))
    draft = draft_for(source, topic)
    blocks = draft["body"]["blocks"]
    if invalid == "image_group":
        draft["body"]["blocks"] = [
            blocks[0],
            {"type": "image_group", "images": [blocks[1], blocks[3]], "caption": "两张图。"},
            blocks[-1],
        ]
    elif invalid == "adjacent":
        blocks.pop(2)
    else:
        blocks[1]["caption"] = "这行是图注。"
        blocks[2]["content"] = " \t "
    with pytest.raises(PublishingError) as exc:
        build_content(root, plan, {content_id(topic): draft}, tmp_path / "invalid")
    assert exc.value.code == "CONTENT_STRUCTURE_INVALID"
    assert not (tmp_path / "invalid").exists()


def test_cover_crop_contains_subject_and_respects_upscale_limit():
    image = Image.new("RGB", (800, 800), "navy")
    with io.BytesIO() as stream:
        image.save(stream, format="PNG")
        data = stream.getvalue()
    covers = make_covers(data, {"id": "a"}, {"subject_box": [150, 370, 650, 430]})
    wide, wide_bytes = covers["wide"]
    square = covers["square"][0]
    assert wide["fit_method"] == "crop" and wide["quality_risk"]
    assert not square["quality_risk"] and wide["source_asset_id"] == square["source_asset_id"]
    assert wide["crop_box"][1] <= 370 and wide["crop_box"][3] >= 430
    with Image.open(io.BytesIO(wide_bytes)) as output:
        assert output.size == (1063, 452)
    full = make_covers(data, {"id": "a"}, {})
    assert full["wide"][0]["fit_method"] == "contain_pad"
    with io.BytesIO() as stream:
        Image.new("RGB", (500, 1200), "navy").save(stream, format="PNG")
        portrait = stream.getvalue()
    portrait_covers = make_covers(portrait, {"id": "portrait"}, {"subject_box": [180, 520, 320, 620]})
    assert portrait_covers["wide"][0]["fit_method"] == "contain_pad"
    with pytest.raises(PublishingError) as exc:
        make_covers(data, {"id": "a"}, {"sizes": {"wide": [1600, 681]}})
    assert exc.value.code == "COVER_SOURCE_LOWRES"


def evidence_for(level, item_id):
    # Explicitly synthetic metadata for validating the evidence format.
    return {
        "research_level": level,
        "status": "verified",
        "search_performed": True,
        "search_provider": "test fixture",
        "tools": ["test_search"],
        "queries": ["synthetic evidence example"],
        "searched_at": "2026-11-19T00:00:00Z",
        "claims": [
            {
                "claim_id": "claim-one",
                "claim": "Synthetic claim used to test the evidence schema.",
                "source_type": "primary",
                "support_status": "verified",
                "source_title": "Synthetic source example",
                "source_url": "https://example.org/fixture",
                "retrieved_at": "2026-11-19T00:00:00Z",
                "used_by_content_ids": [item_id],
            }
        ],
    }


def test_research_records_and_bidirectional_claim_links(make_job, tmp_path):
    source = load_source(source_for(make_job))
    topic = topic_for("xiaohongshu", "research", list(source.assets)[:2], level="R1")
    item_id = content_id(topic)
    plan = make_plan(source, request_for(source, [topic]))
    pack = evidence_for("R1", item_id)
    draft = draft_for(source, topic) | {"claim_ids": ["claim-one"], "evidence": pack}
    out = tmp_path / "research"
    result = build_content(source.root, plan, {item_id: draft}, out)
    item = result["items"][0]
    assert item["claim_ids"] == ["claim-one"]
    assert json.loads((out / item["research"]["evidence_file"]["path"]).read_text()) == pack
    assert validate_content(out, source.root) == result
    unrelated = deepcopy(pack)
    unrelated["claims"][0]["used_by_content_ids"] = []
    with pytest.raises(PublishingError) as exc:
        validate_evidence("R1", item_id, ["claim-one"], unrelated)
    assert exc.value.code == "EVIDENCE_LINK_MISMATCH"
    invented = deepcopy(pack)
    invented["claims"][0]["source_type"] = "image_observation"
    with pytest.raises(PublishingError) as exc:
        validate_evidence("R1", item_id, ["claim-one"], invented)
    assert exc.value.code == "EVIDENCE_INSUFFICIENT"
    with pytest.raises(PublishingError) as exc:
        validate_evidence("R2", item_id, [])
    assert exc.value.code == "RESEARCH_UNAVAILABLE"
