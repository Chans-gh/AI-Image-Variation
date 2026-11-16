"""Phase 4 测试：Sequence 计划先行、逐帧提示词、锚点、保槽与续跑。

运行：.venv/bin/python skills/image-variations/tests/test_sequence.py
"""
from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent / "support"))

import paths  # noqa: E402

from iv_skill.anchor import Anchor, build_anchor  # noqa: E402
from iv_skill.config import load_config  # noqa: E402
from iv_skill.prompt_builder import DirectionRegistry, build_frame_plan  # noqa: E402
from iv_skill.provider import ReferenceImage  # noqa: E402
from iv_skill.provider.mock import MockAdapter, MockScenario, default_capability  # noqa: E402
from iv_skill.provider.retry import Budget  # noqa: E402
from iv_skill.sequence import (  # noqa: E402
    SequenceRunner,
    execute_sequence,
    image_similarity,
    plan_sequence,
    text_similarity,
)
from shared.errors import IVError  # noqa: E402
from shared.hashing import sha256_bytes  # noqa: E402

CONFIG = load_config()
REGISTRY = DirectionRegistry()
ANCHOR = Anchor("first_frame", "同一只浅灰陶瓷杯，形状、把手、釉面不变")
FRAME_SIZE = (768, 1024)          # 3:4


# ------------------------------------------------------------------ 造图
def frame_image(index: int, size: tuple[int, int] = FRAME_SIZE) -> Image.Image:
    """确定性、彼此可区分的合成帧（不是模型输出）。"""
    small = Image.new("L", (24, 24))
    pixels = small.load()
    state = 1000 + index * 7919
    for y in range(24):
        for x in range(24):
            state = (state * 1103515245 + 12345) % (2**31)
            pixels[x, y] = 40 + (state >> 16) % 160
    image = small.resize(size, Image.BICUBIC).convert("RGB")
    draw = ImageDraw.Draw(image)
    radius = size[0] // 6
    centre = (size[0] // 2, size[1] // 2)
    offset = (index * 37) % (size[0] // 3)
    draw.ellipse([centre[0] - radius + offset, centre[1] - radius,
                  centre[0] + radius + offset, centre[1] + radius], outline=(255, 255, 255), width=8)
    return image


def png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class RecordingAdapter(MockAdapter):
    """记录每次 GenerationRequest（含参考图字节），用于验证锚点规则。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.requests = []

    def generate(self, request, **overrides):
        self.requests.append(request)
        return super().generate(request, **overrides)


def make_adapter(images: list[bytes], **overrides) -> RecordingAdapter:
    scenarios = [MockScenario(name=f"frame_{index:02d}", image_bytes=data)
                 for index, data in enumerate(images)]
    return RecordingAdapter(capability=default_capability(**overrides), scenarios=scenarios)


def run_sequence(adapter, *, frame_count=4, input_type="text_only", branch="variants",
                 direction="light", reference=None, anchor=ANCHOR, **overrides):
    options = dict(direction_key=direction, branch=branch, frame_count=frame_count,
                   aspect_ratio="3:4", input_type=input_type, anchor=anchor,
                   reference_image=reference, registry=REGISTRY, config=CONFIG,
                   sleep=lambda _: None)
    options.update(overrides)
    return execute_sequence(adapter=adapter, **options)


# ------------------------------------------------------------------ 计划
class TestPlanning(unittest.TestCase):
    def test_plan_precedes_any_call(self):
        adapter = make_adapter([png_bytes(frame_image(i)) for i in range(1, 5)])
        plan = plan_sequence(registry=REGISTRY, direction_key="light", branch="variants",
                             frame_count=4, aspect_ratio="3:4", resolution_tier="1K",
                             anchor=ANCHOR, config=CONFIG)
        self.assertEqual(adapter.trace, [])
        self.assertEqual(plan.frame_count, 4)
        self.assertEqual(plan.template_version, "sequence-prompt-v1")
        self.assertEqual(plan.identity_anchor, ANCHOR.description)

    def test_all_directions_and_branches_plan(self):
        for key in REGISTRY.keys():
            for branch in REGISTRY.get(key).supported_branches:
                for frame_count in (4, 16):
                    with self.subTest(direction=key, branch=branch, n=frame_count):
                        plan = plan_sequence(registry=REGISTRY, direction_key=key, branch=branch,
                                             frame_count=frame_count, aspect_ratio="3:4",
                                             resolution_tier="1K", anchor=ANCHOR, config=CONFIG)
                        points = plan.change_points()
                        self.assertEqual(len(points), frame_count)
                        self.assertEqual(len(set(points)), frame_count)
                        prompts = [frame.prompt for frame in plan.frames]
                        self.assertEqual(len(set(prompts)), frame_count)
                        limit = CONFIG["sequence"]["near_duplicate_similarity_max"]
                        worst = max(text_similarity(points[a], points[b])
                                    for a in range(frame_count) for b in range(a + 1, frame_count))
                        self.assertLess(worst, limit, f"{key}/{branch} 近重复 {worst:.3f}")

    def test_each_prompt_carries_its_own_change_point(self):
        plan = plan_sequence(registry=REGISTRY, direction_key="light", branch="progression",
                             frame_count=8, aspect_ratio="3:4", resolution_tier="1K",
                             anchor=ANCHOR, config=CONFIG)
        for frame in plan.frames:
            self.assertIn(frame.change_point, frame.prompt)
            self.assertIn(f"[FRAME_PLAN[{frame.index}]]", frame.prompt)
            self.assertIn(frame.phase_label, frame.prompt)

    def test_progression_and_narrative_have_order_context(self):
        for branch in ("progression", "narrative"):
            plan = plan_sequence(registry=REGISTRY, direction_key="light", branch=branch,
                                 frame_count=4, aspect_ratio="3:4", resolution_tier="1K",
                                 anchor=ANCHOR, config=CONFIG)
            self.assertIn("当前为第 2/4 帧", plan.frames[1].prompt)
            self.assertEqual(plan.frames[0].phase_label, "起点")
            self.assertEqual(plan.frames[3].phase_label, "终点")

    def test_variants_have_no_window_order(self):
        plan = plan_sequence(registry=REGISTRY, direction_key="style", branch="variants",
                             frame_count=6, aspect_ratio="1:1", resolution_tier="1K",
                             anchor=ANCHOR, config=CONFIG)
        for frame in plan.frames:
            self.assertIn("不表达时间顺序", frame.prompt)

    def test_frame_count_bounds(self):
        for frame_count in (3, 17, 0):
            with self.subTest(n=frame_count):
                with self.assertRaises(IVError) as ctx:
                    plan_sequence(registry=REGISTRY, direction_key="light", branch="variants",
                                  frame_count=frame_count, aspect_ratio="3:4",
                                  resolution_tier="1K", anchor=ANCHOR, config=CONFIG)
                self.assertEqual(ctx.exception.code, "INVALID_MODE_OR_COUNT")

    def test_branch_must_be_supported_by_direction(self):
        class SingleBranch(DirectionRegistry):
            def __init__(self):
                super().__init__()
                self._dirs["light"] = {**self._dirs["light"],
                                       "branch_defaults": {"default": "variants",
                                                           "supported": ["variants"]}}

        with self.assertRaises(IVError) as ctx:
            plan_sequence(registry=SingleBranch(), direction_key="light", branch="narrative",
                          frame_count=4, aspect_ratio="3:4", resolution_tier="1K",
                          anchor=ANCHOR, config=CONFIG)
        self.assertEqual(ctx.exception.code, "INVALID_MODE_OR_COUNT")

    def test_unknown_direction_blocked(self):
        with self.assertRaises(IVError) as ctx:
            plan_sequence(registry=REGISTRY, direction_key="light+style", branch="variants",
                          frame_count=4, aspect_ratio="3:4", resolution_tier="1K",
                          anchor=ANCHOR, config=CONFIG)
        self.assertEqual(ctx.exception.code, "MULTIPLE_DIRECTIONS")

    def test_near_duplicate_change_points_blocked(self):
        class Duplicated(DirectionRegistry):
            def __init__(self):
                super().__init__()
                self._dirs["light"] = {**self._dirs["light"],
                                       "allowed_changes": ["同一处细节", "同一处细节"]}

        with self.assertRaises(IVError) as ctx:
            plan_sequence(registry=Duplicated(), direction_key="light", branch="variants",
                          frame_count=4, aspect_ratio="3:4", resolution_tier="1K",
                          anchor=ANCHOR, config=CONFIG)
        self.assertEqual(ctx.exception.code, "INVALID_MODE_OR_COUNT")
        self.assertIn("近重复", ctx.exception.message)

    def test_plan_is_json_serialisable(self):
        import json

        plan = plan_sequence(registry=REGISTRY, direction_key="light", branch="variants",
                             frame_count=4, aspect_ratio="3:4", resolution_tier="1K",
                             anchor=ANCHOR, config=CONFIG)
        payload = json.loads(json.dumps(plan.to_dict(), ensure_ascii=False))
        self.assertEqual(len(payload["frames"]), 4)
        self.assertEqual(payload["frames"][0]["index"], 1)


class TestAnchorRules(unittest.TestCase):
    def test_text_only_needs_description(self):
        with self.assertRaises(IVError) as ctx:
            build_anchor(input_type="text_only")
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")

    def test_image_only_needs_path(self):
        with self.assertRaises(IVError) as ctx:
            build_anchor(input_type="image_only")
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")

    def test_text_only_anchor_is_first_frame(self):
        anchor = build_anchor(input_type="text_only", user_description="一只陶瓷杯")
        self.assertEqual(anchor.type, "first_frame")
        self.assertIn("第 1 张图", anchor.prompt_block())

    def test_image_anchor_is_reference(self):
        anchor = build_anchor(input_type="image_only", reference_path="ref.png")
        self.assertEqual(anchor.type, "reference_image")
        self.assertIn("原图", anchor.prompt_block())


# ------------------------------------------------------------------ 生成
class TestSequenceGeneration(unittest.TestCase):
    def test_text_only_anchor_chaining(self):
        images = [png_bytes(frame_image(i)) for i in range(1, 5)]
        adapter = make_adapter(images)
        execution = run_sequence(adapter)
        self.assertEqual(execution.status, "completed")
        self.assertEqual(len(adapter.requests), 4)
        self.assertIsNone(adapter.requests[0].reference_image, "图 1 不带参考图")
        for request in adapter.requests[1:]:
            self.assertEqual(request.reference_image.data, images[0],
                             "图 2–N 必须引用图 1，不得链式引用图 i−1")
        self.assertEqual(execution.anchor_revision, None)

    def test_image_only_every_frame_uses_original(self):
        reference = ReferenceImage(png_bytes(frame_image(99)), "image/png", "ref.png")
        images = [png_bytes(frame_image(i)) for i in range(1, 5)]
        adapter = make_adapter(images)
        execution = run_sequence(adapter, input_type="image_only", reference=reference)
        self.assertEqual(execution.status, "completed")
        for request in adapter.requests:
            self.assertEqual(request.reference_image.data, reference.data)
        self.assertEqual(execution.anchor_revision, sha256_bytes(reference.data))

    def test_frame_records_carry_geometry_and_hash(self):
        images = [png_bytes(frame_image(i)) for i in range(1, 5)]
        execution = run_sequence(make_adapter(images))
        for frame, data in zip(execution.frames, images):
            self.assertEqual(frame.status, "completed")
            self.assertEqual((frame.width, frame.height), FRAME_SIZE)
            self.assertEqual(frame.sha256, sha256_bytes(data))
            self.assertEqual(frame.attempts, 1)
        self.assertTrue(execution.validation()["sequence_complete"])
        self.assertEqual(execution.validation()["failed_indices"], [])

    def test_sixteen_frames_all_distinct_prompts(self):
        images = [png_bytes(frame_image(i)) for i in range(1, 17)]
        adapter = make_adapter(images)
        execution = run_sequence(adapter, frame_count=16)
        self.assertEqual(execution.status, "completed")
        prompts = [request.prompt for request in adapter.requests]
        self.assertEqual(len(set(prompts)), 16)
        self.assertEqual([frame.index for frame in execution.frames], list(range(1, 17)))

    def test_anchor_failure_stops_everything(self):
        scenarios = [MockScenario(status=400, body_text="content policy violation")]
        adapter = make_adapter([])
        adapter.transport.scenarios = scenarios
        with self.assertRaises(IVError) as ctx:
            run_sequence(adapter)
        self.assertEqual(ctx.exception.code, "SEQUENCE_ANCHOR_FAILED")
        self.assertEqual(len(adapter.requests), 1, "图 1 失败后不得继续调用")
        self.assertEqual(ctx.exception.detail["failed_index"], 1)

    def test_anchor_failure_after_retries(self):
        adapter = make_adapter([b"broken"] * 3)
        with self.assertRaises(IVError) as ctx:
            run_sequence(adapter)
        self.assertEqual(ctx.exception.code, "SEQUENCE_ANCHOR_FAILED")
        self.assertEqual(len(adapter.requests),
                         CONFIG["retry"]["frame_max_extra_retries"] + 1)

    def test_frame_failure_keeps_slot(self):
        good = png_bytes(frame_image(1))
        # 帧 1、2 成功；帧 3 的 3 次尝试都返回损坏图；帧 4 成功
        adapter = make_adapter([good, png_bytes(frame_image(2))] + [b"broken"] * 3
                               + [png_bytes(frame_image(4))])
        execution = run_sequence(adapter)
        self.assertEqual(execution.status, "partial_failed")
        self.assertEqual([frame.index for frame in execution.frames], [1, 2, 3, 4])
        self.assertEqual([frame.status for frame in execution.frames],
                         ["completed", "completed", "failed", "completed"])
        failed = execution.frames[2]
        self.assertEqual(failed.error["code"], "IMAGE_VALIDATION_FAILED")
        self.assertEqual(failed.attempts, 3)
        self.assertTrue(failed.error["retry_available"])
        self.assertEqual(execution.validation()["failed_indices"], [3])
        self.assertFalse(execution.validation()["sequence_complete"])
        self.assertTrue(execution.validation()["asset_integrity_passed"])
        self.assertEqual(execution.retry_counts["frame_03"], 2)

    def test_refusal_is_frame_level_not_fatal(self):
        scenarios = [
            MockScenario(image_bytes=png_bytes(frame_image(1))),
            MockScenario(status=400, body_text="content policy violation"),
            MockScenario(image_bytes=png_bytes(frame_image(3))),
            MockScenario(image_bytes=png_bytes(frame_image(4))),
        ]
        adapter = make_adapter([])
        adapter.transport.scenarios = scenarios
        execution = run_sequence(adapter)
        self.assertEqual(execution.status, "partial_failed")
        self.assertEqual(execution.frames[1].error["code"], "MODEL_REFUSAL")
        self.assertEqual(execution.frames[1].attempts, 1, "拒绝不重试")
        self.assertEqual(execution.frames[3].status, "completed", "后续帧继续生成")
        self.assertFalse(execution.frames[1].error["recoverable"] is False)

    def test_auth_failure_aborts_task(self):
        scenarios = [
            MockScenario(image_bytes=png_bytes(frame_image(1))),
            MockScenario(status=401, body_text="unauthorized"),
        ]
        adapter = make_adapter([])
        adapter.transport.scenarios = scenarios
        with self.assertRaises(IVError) as ctx:
            run_sequence(adapter)
        self.assertEqual(ctx.exception.code, "AUTH_FAILED")
        self.assertEqual(len(adapter.requests), 2)

    def test_aspect_mismatch_fails_frame(self):
        wrong = png_bytes(frame_image(2, size=(1024, 1024)))     # 1:1 而非 3:4
        adapter = make_adapter([png_bytes(frame_image(1))] + [wrong] * 3
                               + [png_bytes(frame_image(3)), png_bytes(frame_image(4))])
        execution = run_sequence(adapter)
        self.assertEqual(execution.frames[1].status, "failed")
        self.assertEqual(execution.frames[1].error["code"], "IMAGE_VALIDATION_FAILED")
        self.assertIn("比例", execution.frames[1].error["message"])

    def test_budget_blocks_mid_run(self):
        images = [png_bytes(frame_image(i)) for i in range(1, 5)]
        adapter = make_adapter(images)
        budget = Budget(limit=2, planned=2, cost_per_call=CONFIG["budget"]["cost_per_call"])
        with self.assertRaises(IVError) as ctx:
            run_sequence(adapter, budget=budget)
        self.assertEqual(ctx.exception.code, "BUDGET_EXCEEDED")
        self.assertEqual(len(adapter.requests), 3, "超限即停止新增调用")

    def test_budget_plan_preflight_blocks(self):
        config = dict(CONFIG)
        config["budget"] = {**CONFIG["budget"], "default_call_budget_limit": 3}
        adapter = make_adapter([png_bytes(frame_image(i)) for i in range(1, 5)])
        with self.assertRaises(IVError) as ctx:
            run_sequence(adapter, config=config)
        self.assertEqual(ctx.exception.code, "BUDGET_EXCEEDED")
        self.assertEqual(adapter.requests, [])

    def test_execution_metadata(self):
        adapter = make_adapter([png_bytes(frame_image(i)) for i in range(1, 5)])
        execution = run_sequence(adapter)
        self.assertEqual(execution.budget["calls_used"], 4)
        self.assertEqual(execution.budget["calls_planned"], 6)
        self.assertEqual(execution.plan["tier"], "1K")
        self.assertEqual(execution.plan["branch"], "variants")
        self.assertEqual(execution.anchor["type"], "first_frame")
        self.assertEqual(execution.errors, [])


# ------------------------------------------------------------------ 近重复与续跑
class TestNearDuplicate(unittest.TestCase):
    def test_identical_frame_is_retried_then_flagged(self):
        first = png_bytes(frame_image(1))
        duplicate = png_bytes(frame_image(1))            # 与帧 1 完全相同
        # 帧 1 成功；帧 2 三次尝试都与帧 1 相同；帧 3、4 正常
        adapter = make_adapter([first] + [duplicate] * 3
                               + [png_bytes(frame_image(3)), png_bytes(frame_image(4))])
        execution = run_sequence(adapter)
        frame_two = execution.frames[1]
        self.assertEqual(frame_two.status, "completed")
        self.assertEqual(frame_two.attempts, CONFIG["retry"]["frame_max_extra_retries"] + 1)
        self.assertEqual(frame_two.near_duplicate_of, 1)
        self.assertGreaterEqual(frame_two.similarity_max,
                                CONFIG["sequence"]["near_duplicate_similarity_max"])
        codes = {warning["code"] for warning in execution.warnings}
        self.assertIn("IMAGE_VALIDATION_FAILED", codes)

    def test_distinct_frames_not_flagged(self):
        images = [png_bytes(frame_image(i)) for i in range(1, 6)]
        execution = run_sequence(make_adapter(images), frame_count=5)
        self.assertEqual(execution.status, "completed")
        for frame in execution.frames:
            self.assertIsNone(frame.near_duplicate_of)
            self.assertEqual(frame.attempts, 1)

    def test_similarity_is_symmetric_and_deterministic(self):
        first = png_bytes(frame_image(1))
        second = png_bytes(frame_image(2))
        self.assertEqual(image_similarity(first, first), 1.0)
        self.assertAlmostEqual(image_similarity(first, second), image_similarity(second, first))
        self.assertLess(image_similarity(first, second), 1.0)
        self.assertEqual(text_similarity("光照变化", "光照变化"), 1.0)
        self.assertLess(text_similarity("光照变化", "构图变化"), 1.0)


class TestResume(unittest.TestCase):
    def build_partial(self):
        good = [png_bytes(frame_image(index)) for index in (1, 2, 4)]
        adapter = make_adapter([good[0], good[1]] + [b"broken"] * 3 + [good[2]])
        runner = SequenceRunner(adapter=adapter, registry=REGISTRY, config=CONFIG,
                                sleep=lambda _: None)
        execution = runner.run(direction_key="light", branch="variants", frame_count=4,
                               aspect_ratio="3:4", input_type="text_only", anchor=ANCHOR)
        return runner, execution, good

    def test_resume_only_failed_frames(self):
        runner, execution, good = self.build_partial()
        before = {frame.index: frame.sha256 for frame in execution.frames}
        retry_adapter = make_adapter([png_bytes(frame_image(3))])
        runner.adapter = retry_adapter
        resumed = runner.retry_failed_frames(execution)
        self.assertEqual(resumed.status, "completed")
        self.assertEqual(len(retry_adapter.requests), 1, "只补生成失败帧")
        self.assertEqual(resumed.frames[2].sha256, sha256_bytes(png_bytes(frame_image(3))))
        for index in (1, 2, 4):
            self.assertEqual(resumed.frames[index - 1].sha256, before[index],
                             "成功帧不得被重写")
        self.assertEqual([frame.index for frame in resumed.frames], [1, 2, 3, 4])
        self.assertEqual(resumed.errors, [])
        self.assertTrue(resumed.validation()["sequence_complete"])

    def test_resume_uses_frame_one_anchor(self):
        runner, execution, _ = self.build_partial()
        retry_adapter = make_adapter([png_bytes(frame_image(3))])
        runner.adapter = retry_adapter
        runner.retry_failed_frames(execution)
        reference = retry_adapter.requests[0].reference_image
        self.assertEqual(reference.data, execution.frames[0].image_bytes)

    def test_resume_rejects_changed_reference(self):
        runner, execution, _ = self.build_partial()
        execution.anchor_revision = sha256_bytes(b"original")
        other = ReferenceImage(png_bytes(frame_image(50)), "image/png", "other.png")
        with self.assertRaises(IVError) as ctx:
            runner.retry_failed_frames(execution, reference_image=other)
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")
        self.assertIn("整组重建", ctx.exception.message)

    def test_resume_blocked_when_anchor_frame_failed(self):
        adapter = make_adapter([b"broken"] * 3)
        runner = SequenceRunner(adapter=adapter, registry=REGISTRY, config=CONFIG,
                                sleep=lambda _: None)
        with self.assertRaises(IVError):
            runner.run(direction_key="light", branch="variants", frame_count=4,
                       aspect_ratio="3:4", input_type="text_only", anchor=ANCHOR)
        # 构造一个"帧 1 失败"的执行态，验证续跑被明确阻断
        execution = type("E", (), {})()
        execution.anchor_revision = None
        execution.frames = []
        execution.failed_frames = []
        execution.plan = {"input_type": "text_only", "tier": "1K"}
        execution.warnings = []
        execution.retry_counts = {}
        with self.assertRaises(IVError) as ctx:
            runner.retry_failed_frames(execution)
        self.assertEqual(ctx.exception.code, "SEQUENCE_ANCHOR_FAILED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
