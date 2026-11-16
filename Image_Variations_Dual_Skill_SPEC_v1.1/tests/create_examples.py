"""生成 v1.1 契约夹具（正例 + 负例）。

这是**测试夹具生成器**，不是图像生成器、也不是 Skill 运行时。图片都是本地绘制的
合成图形，用于验证 Manifest、哈希、切图、块语法、包清单等数据契约。

用法：
    python tests/create_examples.py
"""
from __future__ import annotations
import hashlib
import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
EX = ROOT / "examples"
T0 = "2026-11-16T01:00:00Z"


# ---------------------------------------------------------------- helpers
def save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def entry(path: Path, root: Path) -> dict:
    return {"path": path.relative_to(root).as_posix(), "sha256": digest(path),
            "bytes": path.stat().st_size}


def write_package(root: Path, package_id: str, package_type: str, manifest_path: str):
    entries = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "delivery-package.json":
            continue
        rel = path.relative_to(root).as_posix()
        if rel.endswith(".png"):
            kind = "cover" if "/covers/" in f"/{rel}" else "image"
        elif rel.endswith((".md", ".html")):
            kind = "text"
        elif "research" in rel:
            kind = "research"
        else:
            kind = "metadata"
        item = entry(path, root)
        item["kind"] = kind
        entries.append(item)
    save_json(root / "delivery-package.json", {
        "package_id": package_id,
        "package_type": package_type,
        "root": root.name,
        "manifest_path": manifest_path,
        "entries": entries,
        "total_files": len(entries),
        "total_bytes": sum(e["bytes"] for e in entries),
        "created_at": T0,
    })


def synthetic_tile(color: str, size=(1024, 1024), variant: int = 0) -> Image.Image:
    canvas = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(canvas)
    w, h = size
    draw.ellipse((w * 0.30, h * 0.26, w * 0.70, h * 0.62), fill="#F5F5F0", outline="#35434D",
                 width=max(4, w // 120))
    for i in range(variant + 1):
        draw.line((w * 0.34, h * 0.72 + i * 14, w * 0.66, h * 0.72 + i * 14),
                  fill="#35434D", width=max(3, w // 160))
    return canvas


# ---------------------------------------------------------------- grid
def build_grid() -> Path:
    root = EX / "valid-grid-2x2"
    if root.exists():
        shutil.rmtree(root)
    (root / "images").mkdir(parents=True)
    n = 2
    tile = 1024
    board = Image.new("RGB", (tile * n, tile * n))
    colors = ["#94B5D4", "#A3CDBB", "#D7B99F", "#D9CEDC"]
    images = [{
        "id": "grid_board", "kind": "board", "index": 0, "path": "board.png",
        "width": tile * n, "height": tile * n, "format": "png", "status": "completed",
    }]
    for i, color in enumerate(colors, start=1):
        canvas = synthetic_tile(color, (tile, tile), variant=i % 3)
        x, y = ((i - 1) % n) * tile, ((i - 1) // n) * tile
        board.paste(canvas, (x, y))
        canvas.save(root / "images" / f"{i:02d}.png")
        images.append({
            "id": f"grid_img_{i:02d}", "kind": "tile", "index": i,
            "row": (i - 1) // n + 1, "column": (i - 1) % n + 1,
            "crop_box": [x, y, x + tile, y + tile],
            "path": f"images/{i:02d}.png", "width": tile, "height": tile,
            "format": "png", "status": "completed",
            "prompt_summary": f"第 {i} 格：独立光照方案",
        })
    board.save(root / "board.png")
    images[0]["sha256"] = digest(root / "board.png")
    images[0]["bytes"] = (root / "board.png").stat().st_size
    for img in images[1:]:
        p = root / img["path"]
        img["sha256"] = digest(p)
        img["bytes"] = p.stat().st_size
    manifest = {
        "schema_version": "1.1", "artifact_type": "image_variations",
        "task_id": "iv_grid_sample_001", "artifact_revision": "rev1",
        "parent_artifact_revision": None,
        "status": "completed", "input_type": "image_plus_text", "output_mode": "grid",
        "direction": "light", "direction_registry_version": "1.0",
        "branch": None, "grid_size": n, "grid_size_source": "user",
        "image_count": n * n, "requested_aspect_ratio": "1:1", "actual_aspect_ratio": "1:1",
        "requested_resolution_tier": "2K", "requested_resolution_policy": "tile_min_edge_default",
        "provider_type": "mock_fixture", "model_id": "synthetic-test-only",
        "direction_rules_version": "1.0", "prompt_template_version": "grid-prompt-v1",
        "consistency_anchor": {"type": "input_image", "reference": "not-exported"},
        "images": images,
        "validation": {
            "asset_integrity_passed": True, "grid_structure_passed": True,
            "aspect_ratio_error_ratio": 0.0,
            "tile_edge_px": tile, "tile_edge_min_required_px": 1024, "tile_edge_ok": True,
            "seam_score": 0.93, "seam_check_method": "boundary_gradient_v1", "grid_attempts": 1,
            "checks": ["grid_structure", "tile_edges", "seam", "asset_integrity"],
        },
        "execution": {"calls_planned": 1, "calls_used": 1, "call_budget_limit": 3,
                      "budget_exceeded": False, "cost_estimate": None, "degradations": []},
        "warnings": [],
        "created_at": T0, "completed_at": T0,
    }
    save_json(root / "manifest.json", manifest)
    write_package(root, "iv_grid_sample_001_rev1", "image_variations", "manifest.json")
    return root


# ---------------------------------------------------------------- sequence
def build_sequence() -> Path:
    root = EX / "valid-sequence-4"
    if root.exists():
        shutil.rmtree(root)
    (root / "images").mkdir(parents=True)
    colors = ["#95A4C8", "#AAC4D3", "#CAD7B8", "#DEC18D"]
    images = []
    for i, color in enumerate(colors, start=1):
        canvas = synthetic_tile(color, (768, 1024), variant=i)
        p = root / "images" / f"{i:02d}.png"
        canvas.save(p)
        images.append({
            "id": f"frame_{i:02d}", "kind": "frame", "index": i, "path": f"images/{i:02d}.png",
            "width": 768, "height": 1024, "format": "png", "status": "completed",
            "sha256": digest(p), "bytes": p.stat().st_size,
            "phase_label": f"阶段{i}", "prompt_summary": f"同一主体在阶段{i}的光照差异",
            "prompt_template_id": "sequence-prompt-v1",
        })
    manifest = {
        "schema_version": "1.1", "artifact_type": "image_variations",
        "task_id": "iv_seq_sample_001", "artifact_revision": "rev1",
        "parent_artifact_revision": None,
        "status": "completed", "input_type": "text_only", "output_mode": "sequence",
        "direction": "light", "direction_registry_version": "1.0",
        "branch": "progression", "grid_size": None, "image_count": 4,
        "requested_aspect_ratio": "3:4", "actual_aspect_ratio": "3:4",
        "requested_resolution_tier": "1K", "requested_resolution_policy": "tile_min_edge_default",
        "provider_type": "mock_fixture", "model_id": "synthetic-test-only",
        "direction_rules_version": "1.0", "prompt_template_version": "sequence-prompt-v1",
        "consistency_anchor": {"type": "first_frame", "asset_id": "frame_01"},
        "images": images,
        "validation": {"asset_integrity_passed": True, "sequence_complete": True,
                       "tile_edge_px": 768, "tile_edge_min_required_px": 1024,
                       "tile_edge_ok": False, "checks": ["frame_index", "asset_integrity"]},
        "execution": {"calls_planned": 4, "calls_used": 4, "call_budget_limit": 10,
                      "budget_exceeded": False, "degradations": []},
        "warnings": [{"code": "TILE_RESOLUTION_RISK", "severity": "warning",
                      "message": "768×1024 单帧长边低于推荐 1024，仅用于夹具演示风险记录"}],
        "created_at": T0, "completed_at": T0,
    }
    save_json(root / "manifest.json", manifest)
    write_package(root, "iv_seq_sample_001_rev1", "image_variations", "manifest.json")
    return root


# ---------------------------------------------------------------- content from grid
def topic_plan(topic_id, platform, title, theme_id, distinct):
    return {
        "topic_id": topic_id, "theme_id": theme_id, "batch_id": "batch_001",
        "platform": platform, "candidate_title": title, "angle": "视觉观察",
        "audience": "对图像创作感兴趣的读者",
        "core_thesis": "同一主体可以因光影与配色呈现不同视觉氛围",
        "outline": ["开篇：说明素材来源", "主体：对比不同光的视觉表现", "结尾：给出可继续观察的方向"],
        "selected_asset_ids": ["grid_img_01", "grid_img_02", "grid_img_03", "grid_img_04"],
        "asset_plan": [
            {"asset_id": f"grid_img_{i:02d}", "order": i, "reason": "同源不同光照"}
            for i in range(1, 5)
        ],
        "research_level": "R0", "research_questions": [],
        "value_proposition": "提供可复用的画面观察角度",
        "distinct_from_other_topics": distinct,
        "distinct_from_topic_ids": [],
        "dedup_key": f"{theme_id}:{platform}",
        "status": "used",
        "content_ids": [f"{platform}_001"],
        "quality_scorecard": {"asset_fit": 5, "value": 4, "distinctness": 4,
                              "verifiability": None, "platform_fit": 4, "total": 17,
                              "passed": False,
                              "notes": "夹具示例：总分 17/25 未达标，仅用于演示评分卡结构落盘"},
    }


def profile(items):
    return {
        "asset_ids": items,
        "profiles": [
            {
                "asset_id": f"grid_img_{i:02d}", "source_order": i, "source_kind": "tile",
                "width": 1024, "height": 1024, "aspect_ratio": "1:1", "usable": True,
                "quality": {"issues": [], "long_edge_px": 1024, "cover_ready": True, "body_ready": True},
                "subject": "合成测试图形", "scene": "纯色背景",
                "visual_style": "扁平图形", "highlights": ["对比清晰的色块"],
                "mood": ["中性"], "differences_from_others": "配色与线条数量不同",
                "near_duplicate_of": None,
                "cover_suitability": "acceptable", "body_suitability": "acceptable",
                "observations": ["画面为纯色底上的圆形与横线", "四张图配色不同"],
                "interpretations": ["用于演示光影方向差异"],
            }
            for i in range(1, 5)
        ],
    }


def build_content_from_grid(grid_root: Path) -> Path:
    root = EX / "valid-content-job"
    if root.exists():
        shutil.rmtree(root)
    wc = root / "wechat-article-001"
    xc = root / "xiaohongshu-note-001"
    (wc / "covers").mkdir(parents=True)
    (wc / "images").mkdir(parents=True)
    (xc / "images").mkdir(parents=True)
    (root / "research").mkdir(parents=True)

    # 正文图片副本
    for i in range(1, 5):
        shutil.copy2(grid_root / "images" / f"{i:02d}.png", wc / "images" / f"image-{i:02d}.png")
        shutil.copy2(grid_root / "images" / f"{i:02d}.png", xc / "images" / f"image-{i:02d}.png")

    # 封面：同源（grid_img_01），不放大
    src = Image.open(grid_root / "images" / "01.png")
    wide = src.crop((0, 294, 1024, 730)).resize((1024, 436))
    wide.save(wc / "covers" / "cover-wide.png")
    square = src.resize((1024, 1024))
    square.save(wc / "covers" / "cover-square.png")

    article = {
        "title": "同一主体的四种光影表达", "summary": "用合成素材观察不同画面氛围。",
        "topic_id": "topic_01", "content_id": "wechat_001", "language": "zh-CN",
        "blocks_version": "1.1",
        "blocks": [
            {"type": "text", "heading": "开篇", "level": "h2",
             "content": "这是一份合成测试示例，不是真实拍摄结论。我们从四张同源画面开始观察。"},
            {"type": "image", "asset_id": "grid_img_01", "alt": "示例图片一", "caption": "示例图一"},
            {"type": "text", "content": "第二组把三张画面放在一起看，差异主要体现在配色与线条密度上。"},
            {"type": "image_group", "caption": "三张同源画面的并列观察",
             "images": [{"asset_id": "grid_img_02", "alt": "示例图片二"},
                        {"asset_id": "grid_img_03", "alt": "示例图片三"},
                        {"asset_id": "grid_img_04", "alt": "示例图片四"}]},
            {"type": "text", "content": "以上仅用于验证「文字—图片—文字—图集—文字」的结构，不代表真实研究结论。"},
        ],
    }
    save_json(wc / "article.json", article)
    (wc / "article.md").write_text(
        "# " + article["title"] + "\n\n" + article["blocks"][0]["content"]
        + "\n\n![示例图一](images/image-01.png)\n\n" + article["blocks"][2]["content"]
        + "\n\n![示例图二](images/image-02.png)\n![示例图三](images/image-03.png)\n![示例图四](images/image-04.png)\n\n"
        + article["blocks"][4]["content"] + "\n", encoding="utf-8")
    (wc / "article.html").write_text(
        "<!doctype html><html lang=\"zh-CN\"><meta charset=\"UTF-8\"><title>"
        + article["title"] + "</title><body style=\"max-width:700px;margin:30px auto;font:17px/1.9 sans-serif\">"
        + "<h1>" + article["title"] + "</h1><p>" + article["blocks"][0]["content"] + "</p>"
        + "<img style=\"max-width:100%\" src=\"images/image-01.png\">"
        + "<p>" + article["blocks"][2]["content"] + "</p>"
        + "<img style=\"max-width:32%\" src=\"images/image-02.png\">"
        + "<img style=\"max-width:32%\" src=\"images/image-03.png\">"
        + "<img style=\"max-width:32%\" src=\"images/image-04.png\">"
        + "<p>" + article["blocks"][4]["content"] + "</p></body></html>\n", encoding="utf-8")
    save_json(wc / "topic-plan.json", topic_plan("topic_01", "wechat", article["title"], "theme_01",
                                                 "更完整的图文展开"))
    save_json(wc / "profile.json", profile(["grid_img_01", "grid_img_02", "grid_img_03", "grid_img_04"]))

    note = {
        "title": "用四张示例图观察光影变化",
        "body_with_hashtags": "同一主体在不同画面中的氛围变化，可以用来练习观察构图和光线。这里是合成测试素材，不是真实拍摄。\n#视觉练习 #AI图像创作",
        "first_comment": "你更喜欢哪一种示意配色？欢迎聊聊。",
        "selected_asset_ids": ["grid_img_01", "grid_img_02", "grid_img_03", "grid_img_04"],
        "topic_id": "topic_02",
        "hashtags": ["#视觉练习", "#AI图像创作"],
        "claim_ids": [],
        "ai_disclosure": "画面为 AI 生成示意，非真实拍摄",
    }
    save_json(xc / "note.json", note)
    (xc / "note.md").write_text("# " + note["title"] + "\n\n" + note["body_with_hashtags"]
                                + "\n\n**首条评论**：" + note["first_comment"] + "\n", encoding="utf-8")
    save_json(xc / "topic-plan.json", topic_plan("topic_02", "xiaohongshu", note["title"], "theme_02",
                                                 "更轻量的清单式表达"))
    save_json(xc / "profile.json", profile(["grid_img_01", "grid_img_02", "grid_img_03", "grid_img_04"]))

    save_json(root / "research" / "evidence.json", {
        "research_level": "R0", "search_performed": False, "status": "not_required",
        "search_provider": None, "tools": [], "queries": [], "searched_at": None,
        "notes": "R0 示例：无外部研究，claims 为空。", "claims": [],
    })

    manifest = {
        "schema_version": "1.1", "artifact_type": "content_publishing",
        "content_job_id": "cp_sample_001", "revision": "rev1", "parent_revision": None,
        "source": {"task_id": "iv_grid_sample_001", "artifact_revision": "rev1",
                   "schema_version": "1.1", "output_mode": "grid", "direction": "light",
                   "branch": None,
                   "selected_asset_ids": ["grid_img_01", "grid_img_02", "grid_img_03", "grid_img_04"]},
        "status": "completed",
        "batch": {"batch_id": "batch_001", "planned_item_count": 2,
                  "platforms": ["wechat", "xiaohongshu"],
                  "dedup_summary": "两篇主题不同：一篇完整图文展开，一篇清单式观察"},
        "items": [
            {
                "content_id": "wechat_001", "content_revision": "rev1", "platform": "wechat",
                "topic_id": "topic_01", "theme_id": "theme_01",
                "topic_plan_file": entry(wc / "topic-plan.json", root),
                "status": "completed",
                "selected_asset_ids": ["grid_img_01", "grid_img_02", "grid_img_03", "grid_img_04"],
                "claim_ids": [], "research_level": "R0", "research_status": "not_required",
                "research": {"level": "R0", "status": "not_required", "search_performed": False,
                             "queries": []},
                "files": {
                    "article_json": entry(wc / "article.json", root),
                    "article_md": entry(wc / "article.md", root),
                    "article_html": entry(wc / "article.html", root),
                },
                "package_images": [entry(wc / "images" / f"image-{i:02d}.png", root) for i in range(1, 5)],
                "cover_wide": {"file": entry(wc / "covers" / "cover-wide.png", root),
                               "source_asset_id": "grid_img_01",
                               "source_asset_long_edge_px": 1024,
                               "crop_box": [0, 294, 1024, 730],
                               "target_ratio": "2.35:1", "actual_width": 1024, "actual_height": 436,
                               "fit_method": "crop", "upscale_factor": 1.0, "quality_risk": False},
                "cover_square": {"file": entry(wc / "covers" / "cover-square.png", root),
                                 "source_asset_id": "grid_img_01",
                                 "source_asset_long_edge_px": 1024,
                                 "crop_box": [0, 0, 1024, 1024],
                                 "target_ratio": "1:1", "actual_width": 1024, "actual_height": 1024,
                                 "fit_method": "crop", "upscale_factor": 1.0, "quality_risk": False},
                "profile_file": entry(wc / "profile.json", root),
                "validation": {"passed": True,
                               "checks": ["block_grammar", "cover_ratios", "cover_source",
                                          "asset_refs", "package_images"],
                               "scorecard": {"asset_fit": 5, "value": 4, "distinctness": 4,
                                             "verifiability": None, "platform_fit": 4}},
            },
            {
                "content_id": "xhs_001", "content_revision": "rev1", "platform": "xiaohongshu",
                "topic_id": "topic_02", "theme_id": "theme_02",
                "topic_plan_file": entry(xc / "topic-plan.json", root),
                "status": "completed",
                "selected_asset_ids": ["grid_img_01", "grid_img_02", "grid_img_03", "grid_img_04"],
                "claim_ids": [], "research_level": "R0", "research_status": "not_required",
                "research": {"level": "R0", "status": "not_required", "search_performed": False},
                "files": {"note_json": entry(xc / "note.json", root),
                          "note_md": entry(xc / "note.md", root)},
                "package_images": [entry(xc / "images" / f"image-{i:02d}.png", root) for i in range(1, 5)],
                "profile_file": entry(xc / "profile.json", root),
                "validation": {"passed": True,
                               "checks": ["title_body_tags_comment", "hashtags_in_body",
                                          "asset_refs", "package_images"]},
            },
        ],
        "execution": {"text_calls_used": 2, "image_ops_used": 2, "search_calls_used": 0,
                      "call_budget_limit": 20, "budget_exceeded": False},
        "warnings": [],
        "created_at": T0, "completed_at": T0,
    }
    save_json(root / "manifest.json", manifest)
    write_package(root, "cp_sample_001_rev1", "content_publishing", "manifest.json")
    save_json(root / "manifest.json", manifest)  # 保持 manifest 与包清单解耦（原始草案顺序）
    return root


# ---------------------------------------------------------------- content from sequence
def build_content_from_sequence(seq_root: Path) -> Path:
    root = EX / "valid-content-from-sequence"
    if root.exists():
        shutil.rmtree(root)
    xc = root / "xiaohongshu-note-001"
    (xc / "images").mkdir(parents=True)
    for i in range(1, 5):
        shutil.copy2(seq_root / "images" / f"{i:02d}.png", xc / "images" / f"image-{i:02d}.png")
    note = {
        "title": "四张渐进示例看光线推进",
        "body_with_hashtags": "同一主体的四个阶段示例，用来看光线如何一步步推进。合成素材，非真实拍摄。\n#光影观察 #渐进变化",
        "first_comment": "你会在第几张停下来？",
        "selected_asset_ids": [f"frame_{i:02d}" for i in range(1, 5)],
        "topic_id": "topic_03", "hashtags": ["#光影观察", "#渐进变化"],
        "claim_ids": [], "ai_disclosure": "画面为 AI 生成示意，非真实拍摄",
    }
    save_json(xc / "note.json", note)
    (xc / "note.md").write_text("# " + note["title"] + "\n\n" + note["body_with_hashtags"]
                                + "\n\n**首条评论**：" + note["first_comment"] + "\n", encoding="utf-8")
    plan = topic_plan("topic_03", "xiaohongshu", note["title"], "theme_03", "以阶段顺序为核心卖点")
    plan["selected_asset_ids"] = [f"frame_{i:02d}" for i in range(1, 5)]
    plan["asset_plan"] = [{"asset_id": f"frame_{i:02d}", "order": i,
                           "reason": "progression 原序不可颠倒"} for i in range(1, 5)]
    plan["content_ids"] = ["xhs_002"]
    save_json(xc / "topic-plan.json", plan)
    save_json(xc / "profile.json", profile(["frame_01", "frame_02", "frame_03", "frame_04"]))
    manifest = {
        "schema_version": "1.1", "artifact_type": "content_publishing",
        "content_job_id": "cp_sample_002", "revision": "rev1", "parent_revision": None,
        "source": {"task_id": "iv_seq_sample_001", "artifact_revision": "rev1",
                   "schema_version": "1.1", "output_mode": "sequence", "direction": "light",
                   "branch": "progression",
                   "selected_asset_ids": [f"frame_{i:02d}" for i in range(1, 5)]},
        "status": "completed",
        "batch": {"batch_id": "batch_002", "planned_item_count": 1, "platforms": ["xiaohongshu"],
                  "dedup_summary": "单篇，来自 Sequence 源，保留阶段顺序"},
        "items": [{
            "content_id": "xhs_002", "content_revision": "rev1", "platform": "xiaohongshu",
            "topic_id": "topic_03", "theme_id": "theme_03",
            "topic_plan_file": entry(xc / "topic-plan.json", root),
            "status": "completed",
            "selected_asset_ids": [f"frame_{i:02d}" for i in range(1, 5)],
            "claim_ids": [], "research_level": "R0", "research_status": "not_required",
            "files": {"note_json": entry(xc / "note.json", root),
                      "note_md": entry(xc / "note.md", root)},
            "package_images": [entry(xc / "images" / f"image-{i:02d}.png", root) for i in range(1, 5)],
            "profile_file": entry(xc / "profile.json", root),
            "validation": {"passed": True, "checks": ["sequence_order", "asset_refs", "package_images"],
                           "failed_checks": []},
        }],
        "execution": {"text_calls_used": 1, "image_ops_used": 0, "search_calls_used": 0,
                      "call_budget_limit": 10, "budget_exceeded": False},
        "warnings": [],
        "created_at": T0, "completed_at": T0,
    }
    save_json(root / "manifest.json", manifest)
    write_package(root, "cp_sample_002_rev1", "content_publishing", "manifest.json")
    return root


# ---------------------------------------------------------------- negatives
def case(root: Path, _name: str, payload: dict):
    save_json(Path(root) / "case.json", payload)


def build_negatives(grid_root: Path, content_root: Path):
    inv = EX / "invalid"
    if inv.exists():
        shutil.rmtree(inv)
    inv.mkdir(parents=True)

    # 1) Grid crop_box 与 index 不匹配
    src = grid_root
    dst = inv / "grid-crop-mismatch"
    shutil.copytree(src, dst)
    m = load(dst / "manifest.json")
    m["images"][2]["crop_box"] = [0, 0, 1024, 1024]
    save_json(dst / "manifest.json", m)
    case(dst, "case.json", {"check": "image_artifact", "target": "manifest.json",
                            "schema": "image-artifact.schema.json",
                            "expect": {"schema_fail": False, "codes": ["GRID_VALIDATION_FAILED"]}})

    # 2) 哈希被篡改
    dst = inv / "hash-mismatch"
    shutil.copytree(src, dst)
    m = load(dst / "manifest.json")
    m["images"][1]["sha256"] = "0" * 64
    save_json(dst / "manifest.json", m)
    case(dst, "case.json", {"check": "image_artifact", "target": "manifest.json",
                            "schema": "image-artifact.schema.json",
                            "expect": {"schema_fail": False, "codes": ["SOURCE_ASSET_MISSING"]}})

    # 3) 路径穿越
    dst = inv / "path-traversal"
    shutil.copytree(src, dst)
    m = load(dst / "manifest.json")
    m["images"][1]["path"] = "../outside.png"
    save_json(dst / "manifest.json", m)
    case(dst, "case.json", {"check": "image_artifact", "target": "manifest.json",
                            "schema": "image-artifact.schema.json",
                            "expect": {"schema_fail": True, "codes": ["PATH_TRAVERSAL_BLOCKED"]}})

    # 4) 主版本不支持
    dst = inv / "schema-major"
    shutil.copytree(src, dst)
    m = load(dst / "manifest.json")
    m["schema_version"] = "2.0"
    save_json(dst / "manifest.json", m)
    case(dst, "case.json", {"check": "image_artifact", "target": "manifest.json",
                            "schema": "image-artifact.schema.json",
                            "expect": {"schema_fail": True, "codes": ["SCHEMA_MAJOR_UNSUPPORTED"]}})

    # 5) Sequence 索引缺口
    dst = inv / "sequence-gap"
    shutil.copytree(EX / "valid-sequence-4", dst)
    m = load(dst / "manifest.json")
    m["images"][2]["index"] = 4
    m["images"][3]["index"] = 5
    save_json(dst / "manifest.json", m)
    case(dst, "case.json", {"check": "image_artifact", "target": "manifest.json",
                            "schema": "image-artifact.schema.json",
                            "expect": {"schema_fail": False,
                                       "codes": ["SOURCE_ARTIFACT_INCOMPLETE"]}})

    # 6) 正文块结构错误（相邻图片块 + 文字块数不符）
    dst = inv / "content-alternation"
    shutil.copytree(content_root, dst)
    art = load(dst / "wechat-article-001" / "article.json")
    art["blocks"] = [art["blocks"][0], art["blocks"][1],
                     {"type": "image", "asset_id": "grid_img_02"}]
    save_json(dst / "wechat-article-001" / "article.json", art)
    case(dst, "case.json", {"check": "article_body",
                            "target": "wechat-article-001/article.json",
                            "schema": "article-body.schema.json",
                            "expect": {"schema_fail": False,
                                       "codes": ["CONTENT_STRUCTURE_INVALID"]}})

    # 7) 小红书条目携带封面（A1 回归）
    dst = inv / "xhs-with-cover"
    shutil.copytree(content_root, dst)
    m = load(dst / "manifest.json")
    for item in m["items"]:
        if item["platform"] == "xiaohongshu":
            item["cover_wide"] = m["items"][0]["cover_wide"]
    save_json(dst / "manifest.json", m)
    case(dst, "case.json", {"check": "content_artifact", "target": "manifest.json",
                            "schema": "content-artifact.schema.json",
                            "expect": {"schema_fail": True, "codes": []}})

    # 8) 包清单覆盖不全
    dst = inv / "package-coverage"
    shutil.copytree(grid_root, dst)
    pkg = load(dst / "delivery-package.json")
    pkg["entries"] = [e for e in pkg["entries"] if not e["path"].endswith("04.png")]
    pkg["total_files"] = len(pkg["entries"])
    save_json(dst / "delivery-package.json", pkg)
    case(dst, "case.json", {"check": "delivery_package", "target": "delivery-package.json",
                            "schema": "delivery-package.schema.json",
                            "expect": {"schema_fail": False,
                                       "codes": ["PACKAGE_MANIFEST_INVALID"]}})

    # 9) 封面放大幅度超限
    dst = inv / "cover-upscale"
    shutil.copytree(content_root, dst)
    m = load(dst / "manifest.json")
    for item in m["items"]:
        if item["platform"] == "wechat":
            item["cover_wide"]["upscale_factor"] = 2.0
            item["cover_wide"]["quality_risk"] = True
    save_json(dst / "manifest.json", m)
    case(dst, "case.json", {"check": "content_artifact", "target": "manifest.json",
                            "schema": "content-artifact.schema.json",
                            "expect": {"schema_fail": False, "codes": ["COVER_SOURCE_LOWRES"]}})

    # 10) 事实链双向不一致
    dst = inv / "evidence-link-mismatch"
    shutil.copytree(content_root, dst)
    m = load(dst / "manifest.json")
    m["items"][0]["claim_ids"] = ["claim_missing"]
    save_json(dst / "manifest.json", m)
    case(dst, "case.json", {"check": "content_artifact", "target": "manifest.json",
                            "schema": "content-artifact.schema.json",
                            "expect": {"schema_fail": False, "codes": ["EVIDENCE_LINK_MISMATCH"]}})
    return inv


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- root examples
def build_root_examples():
    save_json(EX / "topic-plan.example.json", topic_plan(
        "topic_01", "wechat", "同一主体的四种光影表达", "theme_01", "更完整的图文展开"))
    save_json(EX / "research-evidence-R1.example.json", {
        "research_level": "R1", "search_performed": True, "status": "partially_verified",
        "search_provider": "example-search", "tools": ["web_search"],
        "queries": ["光影方向 对 产品摄影 的影响"],
        "searched_at": T0,
        "notes": "示例数据，非真实检索结果；用于演示 R1 证据结构与字段。",
        "claims": [{
            "claim_id": "claim_001",
            "claim": "侧光比正面光更容易表现材质表面纹理",
            "support_status": "partial", "source_type": "secondary",
            "source_title": "示例来源标题（占位）",
            "source_url": "https://example.invalid/lighting-texture",
            "publisher": "示例出版方", "published_at": "2025-06-01",
            "retrieved_at": T0, "verified_at": T0,
            "used_by_content_ids": ["wechat_001"],
        }],
    })
    (EX / "README.md").write_text("""# 契约示例（合成测试夹具，v1.1）

图片由本地绘图程序生成，**不是 AI 生图模型输出**，也不代表真实平台封面质量。用于验证 Manifest、
哈希、网格切分、块语法、封面同源比例、包清单与负例拒绝逻辑。

## 正例

- `valid-grid-2x2/`：2048×2048 整板 + 4 张 1024×1024 Tile（单格长边达标），含 `delivery-package.json`。
- `valid-sequence-4/`：4 张 768×1024 帧；纯文本默认 3:4；图 1 锚点；演示 `TILE_RESOLUTION_RISK` 警告记录。
- `valid-content-job/`：Grid 源 → 1 篇公众号（含 `image_group` 图集块 + 同源双封面 + 选题/画像 + 包清单）
  + 1 篇小红书。
- `valid-content-from-sequence/`：Sequence 源 → 1 篇小红书，保留阶段顺序（对应 INT-22）。
- `topic-plan.example.json` / `research-evidence-R1.example.json`：选题与 R1 证据结构示例。

## 负例（`invalid/`）

每组含 `case.json` 声明期望结果（`schema_fail` 与/或错误码）：

| 目录 | 期望 |
|---|---|
| `grid-crop-mismatch/` | `GRID_VALIDATION_FAILED` |
| `hash-mismatch/` | `SOURCE_ASSET_MISSING` |
| `path-traversal/` | schema 失败 + `PATH_TRAVERSAL_BLOCKED` |
| `schema-major/` | schema 失败 + `SCHEMA_MAJOR_UNSUPPORTED` |
| `sequence-gap/` | `SOURCE_ARTIFACT_INCOMPLETE` |
| `content-alternation/` | `CONTENT_STRUCTURE_INVALID` |
| `xhs-with-cover/` | schema 失败（A1 回归） |
| `package-coverage/` | `PACKAGE_MANIFEST_INVALID` |
| `cover-upscale/` | `COVER_SOURCE_LOWRES`（放大 2.0×） |
| `evidence-link-mismatch/` | `EVIDENCE_LINK_MISMATCH` |

## 运行校验

```bash
python tests/validate_example_contracts.py   # 正例 + 负例（24 项）
python tests/test_negative_fixtures.py       # 只跑负例
python tests/mock_provider.py --selftest     # Mock Provider 错误映射
python tests/build_direction_docs.py         # 由 registry 重新生成方向规则正文
```

依赖：Python 3.10+ 与 `Pillow`（JSON Schema 由包内 `tests/schema_lite.py` 提供，无需 `jsonschema`）。
""", encoding="utf-8")


def main():
    grid = build_grid()
    seq = build_sequence()
    content = build_content_from_grid(grid)
    build_content_from_sequence(seq)
    build_negatives(grid, content)
    build_root_examples()
    total = sum(1 for _ in EX.rglob("*") if _.is_file())
    print(f"wrote fixtures: {total} files under examples/")


if __name__ == "__main__":
    main()
