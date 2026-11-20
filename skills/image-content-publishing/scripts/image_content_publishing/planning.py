import re
import unicodedata
from fractions import Fraction

from .contracts import check_schema, nonblank, platform_config, public_payload, require
from .source import ORDERED_BRANCHES

PLATFORMS = {"wechat", "xiaohongshu"}
INTENTS = {"explore_topics", "generate_single", "generate_batch", "generate_mixed"}
SCORE_FIELDS = ("asset_fit", "value", "distinctness", "verifiability", "platform_fit")
TOPIC_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")


def normalized_text(text):
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold() if c.isalnum())


def content_id(topic):
    return f"{topic['platform']}-{topic['topic_id']}"


def check_selection(asset_ids, assets, branch, *, consecutive=False, code="CONTENT_STRUCTURE_INVALID"):
    require(
        isinstance(asset_ids, list)
        and asset_ids
        and all(isinstance(x, str) for x in asset_ids)
        and len(set(asset_ids)) == len(asset_ids)
        and all(x in assets for x in asset_ids),
        code,
        "素材必须是来源中的有效独立图片，且不能重复",
    )
    indices = [assets[asset_id]["index"] for asset_id in asset_ids]
    if consecutive or branch in ORDERED_BRANCHES:
        require(indices == sorted(indices), code, "图片顺序与来源索引不符")
    if consecutive and branch in ORDERED_BRANCHES:
        require(indices == list(range(indices[0], indices[-1] + 1)), code, "图集不能跨越缺失或跳过的序列阶段")


def _profiles(source, inputs):
    code = "ASSET_PROFILE_INVALID"
    require(isinstance(inputs, list), code, "profiles 须为素材画像列表")
    profiles = {}
    for profile in inputs:
        check_schema("asset-profile", profile, code)
        asset_id = profile["asset_id"]
        require(asset_id in source.assets and asset_id not in profiles, code, "素材画像 ID 缺失或重复")
        asset = source.assets[asset_id]
        size = Fraction(asset["width"], asset["height"])
        metadata = {
            "source_order": asset["index"],
            "source_kind": asset["kind"],
            "width": asset["width"],
            "height": asset["height"],
            "aspect_ratio": f"{size.numerator}:{size.denominator}",
        }
        for key, expected in metadata.items():
            require(profile.get(key, expected) == expected, code, "素材画像与源文件元数据不符", field=key)
        require(
            all(nonblank(x) for key in ("observations", "interpretations") for x in profile[key])
            and (
                bool(profile["observations"])
                if profile["usable"]
                else nonblank(profile.get("unusable_reason"))
            ),
            code,
            "可用素材需要可见观察，不可用素材需要说明原因",
        )
        quality = profile.get("quality", {})
        long_edge = max(asset["width"], asset["height"])
        require(quality.get("long_edge_px", long_edge) == long_edge, code, "画像长边与源图不符")
        duplicate = profile.get("near_duplicate_of")
        require(
            duplicate is None or (duplicate in source.assets and duplicate != asset_id),
            code,
            "近似图必须引用另一张有效图片",
        )
        profiles[asset_id] = profile | metadata | {"quality": quality | {"long_edge_px": long_edge}}
    require(profiles.keys() == source.assets.keys(), code, "每张可选源图都需要画像")
    return [profiles[asset_id] for asset_id in source.assets]


def score_topic(topic, multi):
    code = "QUALITY_GATE_FAILED"
    score = topic.get("quality_scorecard", {})
    require(all(field in score for field in SCORE_FIELDS), code, "评分卡必须记录所有维度")
    r0 = topic["research_level"] == "R0"
    verify_score = score["verifiability"]
    require(
        verify_score is None if r0 else verify_score is not None and verify_score >= 3,
        code,
        "R0 可证性记为空，R1/R2 可证性至少 3 分",
    )
    applicable = [score[key] for key in SCORE_FIELDS if score[key] is not None]
    total = sum(applicable)
    require(
        total >= 18 and min(applicable) >= 2 and (not multi or score["distinctness"] >= 3),
        code,
        "选题评分未达到写作门槛",
        topic_id=topic["topic_id"],
        total=total,
    )
    return score | {"total": total, "possible_total": 20 if r0 else 25, "passed": True}


def _validate_topics(source, topics, profiles, counts, intent):
    code = "TOPIC_PLAN_INVALID"
    require(isinstance(topics, list) and len(topics) == sum(counts.values()), code, "选题数量与平台计划不符")
    multi = len(topics) > 1
    board = {}
    for topic in topics:
        check_schema("topic-plan", topic, code)
        topic_id = topic["topic_id"]
        require(TOPIC_ID.fullmatch(topic_id) and topic_id not in board, code, "选题 ID 无效或重复")
        for key in ("candidate_title", "angle", "audience", "core_thesis", "value_proposition"):
            require(nonblank(topic.get(key)), code, "选题字段不得为空", field=key)
        require(all(nonblank(point) for point in topic["outline"]), code, "提纲不得有空段")
        require(
            not multi or nonblank(topic.get("distinct_from_other_topics")),
            code,
            "多选题需要说明内容差异",
        )
        research_questions = topic.get("research_questions", [])
        require(
            all(nonblank(question) for question in research_questions)
            and (bool(research_questions) == (topic["research_level"] != "R0")),
            code,
            "研究级别与待核验问题不一致",
        )
        ids = topic["selected_asset_ids"]
        check_selection(ids, source.assets, source.reference["branch"], code=code)
        require(
            all(
                profiles[i]["usable"]
                and profiles[i].get("body_suitability") != "unsuitable"
                and profiles[i]["quality"].get("body_ready") is not False
                for i in ids
            ),
            code,
            "选题使用了画像中不可用的正文素材",
        )
        asset_plan = topic.get("asset_plan", [])
        require(
            [item["asset_id"] for item in asset_plan] == ids
            and [item["order"] for item in asset_plan] == list(range(1, len(ids) + 1))
            and all(nonblank(item.get("reason")) for item in asset_plan),
            code,
            "选图计划需与选图顺序一致，并逐张说明理由",
        )
        board[topic_id] = topic | {
            "quality_scorecard": score_topic(topic, multi),
            "status": "candidate" if intent == "explore_topics" else "selected",
        }
    require(
        {p: sum(t["platform"] == p for t in board.values()) for p in counts} == counts,
        code,
        "选题平台配额与请求不符",
    )
    for field in ("candidate_title", "core_thesis", "dedup_key"):
        keys = [normalized_text(topic[field]) for topic in board.values() if nonblank(topic.get(field))]
        require(all(keys) and len(keys) == len(set(keys)), "TOPIC_DUPLICATE", "同批选题重复", field=field)
    return list(board.values())


def make_plan(source, request):
    code = "TOPIC_PLAN_INVALID"
    require(isinstance(request, dict), code, "策划输入须为 JSON 对象")
    intent, counts = request.get("intent"), request.get("platform_counts")
    require(isinstance(intent, str) and intent in INTENTS, code, "需要明确的内容意图")
    require(
        isinstance(counts, dict) and counts and counts.keys() <= PLATFORMS,
        "PLATFORM_REQUIRED",
        "需要明确公众号或小红书",
    )
    require(all(type(n) is int and n >= 1 for n in counts.values()), code, "平台篇数须为正整数")
    total = sum(counts.values())
    if intent != "explore_topics":
        limit = platform_config()["global"]["batch_default_max_items"]
        require(
            total <= limit,
            "BATCH_BUDGET_EXCEEDED",
            "超过默认单批篇数，请确认拆批",
            requested=total,
            limit=limit,
        )
        valid_route = {
            "generate_single": total == 1,
            "generate_batch": len(counts) == 1 and total > 1,
            "generate_mixed": len(counts) == 2,
        }
        require(valid_route[intent], code, "内容意图与平台、篇数不一致")
    require(request.get("source") == source.reference, "SOURCE_REVISION_MISMATCH", "策划引用与来源不一致")
    profiles = _profiles(source, request.get("profiles"))
    topics = _validate_topics(
        source, request.get("topics"), {p["asset_id"]: p for p in profiles}, counts, intent
    )
    plan = {
        "schema_version": "1.0",
        "source": source.reference,
        "intent": intent,
        "status": "awaiting_selection" if intent == "explore_topics" else "selected",
        "platform_counts": counts,
        "profiles": profiles,
        "topics": topics,
        "warnings": source.warnings,
    }
    public_payload(plan)
    return plan


def selected_topics(plan, topic_ids):
    if plan["intent"] != "explore_topics":
        require(not topic_ids, "TOPIC_PLAN_INVALID", "直接生成按原计划执行")
        return plan["topics"]
    code = "TOPIC_SELECTION_REQUIRED"
    require(topic_ids, code, "选题探索已完成，请选择要写的选题")
    topics = [topic for topic in plan["topics"] if topic["topic_id"] in topic_ids]
    require(len(topics) == len(topic_ids), code, "选题 ID 重复或不在候选列表中")
    limit = platform_config()["global"]["batch_default_max_items"]
    require(len(topics) <= limit, "BATCH_BUDGET_EXCEEDED", "超过默认单批篇数，请确认拆批", limit=limit)
    return topics
