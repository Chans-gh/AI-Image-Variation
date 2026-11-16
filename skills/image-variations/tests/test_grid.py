"""Phase 3 测试：Grid 一次成板、接缝评分、确定性切图、重试预算。

运行：.venv/bin/python skills/image-variations/tests/test_grid.py
"""
from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent / "support"))

import paths  # noqa: E402

from iv_skill.config import load_config  # noqa: E402
from iv_skill.executor import execute_grid  # noqa: E402
from iv_skill.grid import (  # noqa: E402
    check_tile_crop_coverage,
    decode_board,
    normalize_board,
    postprocess,
    split_tiles,
    tile_edge_metrics,
)
from iv_skill.prompt_builder import DirectionRegistry, build_cell_plan, build_grid_prompt  # noqa: E402
from iv_skill.provider import ReferenceImage  # noqa: E402
from iv_skill.provider.mock import (  # noqa: E402
    MockAdapter,
    MockScenario,
    default_capability,
    fake_png,
)
from iv_skill.provider.retry import Budget  # noqa: E402
from iv_skill.seam import classify_seam, measure_seam  # noqa: E402
from shared.errors import IVError  # noqa: E402

CONFIG = load_config()
REGISTRY = DirectionRegistry()


# ------------------------------------------------------------------ 造图工具
# 这些图只用于算法回归，**不是**模型输出，也不代表真实画质。
def smooth_board(grid_size: int, tile: int, seed: int = 7) -> Image.Image:
    """无缝整板：低分辨率平滑场双三次放大，跨格线连续。"""
    side = tile * grid_size
    small = Image.new("L", (24, 24))
    pixels = small.load()
    state = seed
    for y in range(24):
        for x in range(24):
            state = (state * 1103515245 + 12345) % (2**31)
            pixels[x, y] = 40 + (state >> 16) % 160
    return small.resize((side, side), Image.BICUBIC).convert("RGB")


def faint_seam_board(grid_size: int, tile: int, delta: int = 1) -> Image.Image:
    """弱接缝整板：奇格亮度差 1 级，用于验证 0.60–0.85 风险带。"""
    board = smooth_board(grid_size, tile)
    for row in range(grid_size):
        for col in range(grid_size):
            if (row + col) % 2 == 0:
                continue
            box = (col * tile, row * tile, (col + 1) * tile, (row + 1) * tile)
            board.paste(board.crop(box).point(lambda value: min(255, value + delta)), box)
    return board


def hard_seam_board(grid_size: int, tile: int) -> Image.Image:
    """硬接缝整板：每格独立底色与纹理，跨格明显跳变。"""
    board = Image.new("RGB", (tile * grid_size, tile * grid_size))
    for row in range(grid_size):
        for col in range(grid_size):
            index = row * grid_size + col + 1
            color = 20 + (index * 37) % 200
            cell = Image.new("RGB", (tile, tile), (color, (color * 3) % 256, (color * 7) % 256))
            draw = ImageDraw.Draw(cell)
            step = max(8, tile // 8)
            for offset in range(0, tile * 2, step):
                draw.line([(offset + index, 0), (0, offset + index)], fill=(255, 255, 255), width=1)
            board.paste(cell, (col * tile, row * tile))
    return board


def border_line_board(grid_size: int, tile: int) -> Image.Image:
    """带白色框线的整板：应被框线检测直接判失败。"""
    image = smooth_board(grid_size, tile)
    draw = ImageDraw.Draw(image)
    for index in range(1, grid_size):
        draw.line([(index * tile, 0), (index * tile, image.height)], fill=(255, 255, 255), width=2)
        draw.line([(0, index * tile), (image.width, index * tile)], fill=(255, 255, 255), width=2)
    return image


def flat_board(grid_size: int, tile: int) -> Image.Image:
    return Image.new("RGB", (tile * grid_size, tile * grid_size), (128, 128, 128))


def png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def mock_adapter(images: list[bytes], **overrides) -> MockAdapter:
    scenarios = [MockScenario(name=f"board_{index}", image_bytes=data)
                 for index, data in enumerate(images)]
    return MockAdapter(capability=default_capability(**overrides), scenarios=scenarios)


# ------------------------------------------------------------------ 接缝评分
class TestSeamScore(unittest.TestCase):
    def test_smooth_board_scores_pass(self):
        for grid_size in (2, 3, 4):
            with self.subTest(grid_size=grid_size):
                report = measure_seam(smooth_board(grid_size, 384), grid_size, CONFIG)
                verdict, code = classify_seam(report, CONFIG)
                self.assertGreaterEqual(report.score, CONFIG["seam"]["pass_threshold"],
                                        f"score={report.score:.4f}")
                self.assertEqual(verdict, "pass")
                self.assertIsNone(code)
                self.assertFalse(report.has_border_line)

    def test_faint_seam_lands_in_risk_band(self):
        """D-24 的 0.60–0.85 风险带必须真的可达，否则分级形同虚设。"""
        for grid_size in (2, 3, 4):
            with self.subTest(grid_size=grid_size):
                report = measure_seam(faint_seam_board(grid_size, 384, delta=3), grid_size, CONFIG)
                verdict, code = classify_seam(report, CONFIG)
                self.assertEqual(verdict, "risk", f"score={report.score:.4f}")
                self.assertEqual(code, "GRID_SEAM_RISK")

    def test_score_decreases_with_seam_strength(self):
        scores = [measure_seam(faint_seam_board(3, 384, delta=delta), 3, CONFIG).score
                  for delta in (0, 1, 2, 3, 4)]
        self.assertEqual(scores, sorted(scores, reverse=True), scores)
        self.assertGreaterEqual(scores[0], CONFIG["seam"]["pass_threshold"])
        self.assertLess(scores[-1], CONFIG["seam"]["pass_threshold"])

    def test_seam_ratio_reported(self):
        report = measure_seam(smooth_board(3, 384), 3, CONFIG)
        payload = report.to_validation()
        self.assertLess(abs(payload["seam_ratio"] - report.seam_ratio), 1e-6)
        self.assertGreater(report.seam_ratio, 0)

    def test_hard_seam_fails(self):
        for grid_size in (2, 3):
            with self.subTest(grid_size=grid_size):
                report = measure_seam(hard_seam_board(grid_size, 384), grid_size, CONFIG)
                verdict, code = classify_seam(report, CONFIG)
                self.assertEqual(verdict, "fail", f"score={report.score:.4f}")
                self.assertEqual(code, "GRID_VALIDATION_FAILED")

    def test_solid_color_boards_flagged_as_blank_band(self):
        """纯色板没有任何内容，边界即纯色带 → 判有可见缝，不冒充无缝。"""
        report = measure_seam(flat_board(3, 256), 3, CONFIG)
        verdict, code = classify_seam(report, CONFIG)
        self.assertTrue(report.has_border_line)
        self.assertEqual(verdict, "fail")
        self.assertEqual(code, "GRID_VALIDATION_FAILED")

    def test_border_line_detected(self):
        report = measure_seam(border_line_board(3, 384), 3, CONFIG)
        verdict, _ = classify_seam(report, CONFIG)
        self.assertTrue(report.has_border_line, report.border_lines)
        self.assertEqual(verdict, "fail")

    def test_border_line_beats_score(self):
        """即使分数很高，只要检测到框线也判失败。"""
        report = measure_seam(border_line_board(2, 384), 2, CONFIG)
        self.assertTrue(report.has_border_line)
        self.assertEqual(classify_seam(report, CONFIG), ("fail", "GRID_VALIDATION_FAILED"))

    def test_score_is_deterministic(self):
        board = smooth_board(3, 128)
        self.assertEqual(measure_seam(board, 3, CONFIG).score,
                         measure_seam(board, 3, CONFIG).score)

    def test_thresholds_come_from_config(self):
        report = measure_seam(smooth_board(2, 128), 2, CONFIG)
        strict = dict(CONFIG)
        strict["seam"] = {**CONFIG["seam"], "pass_threshold": 1.01, "risk_threshold": 0.0}
        verdict, code = classify_seam(report, strict)
        self.assertEqual(verdict, "risk")
        self.assertEqual(code, "GRID_SEAM_RISK")

    def test_report_serialisable(self):
        payload = measure_seam(smooth_board(2, 96), 2, CONFIG).to_validation()
        self.assertEqual(payload["seam_check_method"], CONFIG["seam"]["method"])
        self.assertIn("seam_score", payload)
        json.dumps(payload)


# ------------------------------------------------------------------ 切图
class TestSplitAndNormalize(unittest.TestCase):
    def test_split_is_exact_and_disjoint(self):
        board = smooth_board(3, 128)
        tiles = split_tiles(board, 3)
        self.assertEqual(len(tiles), 9)
        self.assertEqual([t.index for t in tiles], list(range(1, 10)))
        self.assertEqual(tiles[0].crop_box, (0, 0, 128, 128))
        self.assertEqual(tiles[8].crop_box, (256, 256, 384, 384))
        self.assertEqual({t.size for t in tiles}, {(128, 128)})
        check_tile_crop_coverage(tiles, board.size, 3)

    def test_tiles_reconstruct_board(self):
        board = smooth_board(3, 64)
        rebuilt = Image.new("RGB", board.size)
        for tile in split_tiles(board, 3):
            rebuilt.paste(tile.image, tile.crop_box[:2])
        self.assertEqual(list(rebuilt.getdata()), list(board.convert("RGB").getdata()))

    def test_coverage_check_catches_tampering(self):
        tiles = split_tiles(smooth_board(2, 64), 2)
        tiles[0].crop_box = (0, 0, 32, 32)
        with self.assertRaises(IVError) as ctx:
            check_tile_crop_coverage(tiles, (128, 128), 2)
        self.assertEqual(ctx.exception.code, "GRID_VALIDATION_FAILED")

    def test_normalize_aligns_within_tolerance(self):
        normalized = normalize_board(Image.new("RGB", (1000, 1000)), 4, CONFIG)
        self.assertEqual((normalized.width, normalized.height), (1000, 1000))
        normalized = normalize_board(Image.new("RGB", (1002, 1002)), 4, CONFIG)
        self.assertEqual((normalized.width, normalized.height), (1000, 1000))
        self.assertEqual(normalized.alignment_loss_px, 1002 * 1002 - 1000 * 1000)

    def test_normalize_rejects_excessive_loss(self):
        with self.assertRaises(IVError) as ctx:
            normalize_board(Image.new("RGB", (130, 130)), 4, CONFIG)
        self.assertEqual(ctx.exception.code, "GRID_VALIDATION_FAILED")

    def test_ratio_check_rejects_mismatch(self):
        with self.assertRaises(IVError) as ctx:
            postprocess(png(Image.new("RGB", (2048, 1024))), grid_size=2,
                        requested_aspect_ratio="1:1", config=CONFIG)
        self.assertEqual(ctx.exception.code, "GRID_VALIDATION_FAILED")

    def test_tile_edge_grading(self):
        ok = tile_edge_metrics((1024, 1024), CONFIG)
        self.assertTrue(ok["tile_edge_ok"])
        self.assertEqual(ok["warnings"], [])
        risk = tile_edge_metrics((600, 600), CONFIG)
        self.assertFalse(risk["tile_edge_ok"])
        self.assertEqual(risk["warnings"][0]["code"], "TILE_RESOLUTION_RISK")
        with self.assertRaises(IVError) as ctx:
            tile_edge_metrics((256, 256), CONFIG)
        self.assertEqual(ctx.exception.code, "TILE_RESOLUTION_TOO_LOW")

    def test_postprocess_validates_and_splits(self):
        processed = postprocess(png(smooth_board(3, 1024)), grid_size=3,
                                requested_aspect_ratio="1:1", config=CONFIG)
        self.assertEqual(len(processed.tiles), 9)
        self.assertEqual(processed.tile_size, (1024, 1024))
        self.assertTrue(processed.validation["tile_edge_ok"])
        self.assertTrue(processed.validation["grid_structure_passed"])
        self.assertIn("crop_box_covers_board", processed.validation["checks"])

    def test_decode_error(self):
        with self.assertRaises(IVError) as ctx:
            decode_board(b"not an image")
        self.assertEqual(ctx.exception.code, "IMAGE_VALIDATION_FAILED")


# ------------------------------------------------------------------ Prompt
class TestGridPrompt(unittest.TestCase):
    def test_contains_all_sections(self):
        prompt = build_grid_prompt(registry=REGISTRY, direction_key="state", grid_size=3,
                                   aspect_ratio="3:4", resolution_tier="4K",
                                   subject_anchor="同一只浅灰陶瓷杯", tile_edge_min_px=1024)
        for section in ("GLOBAL_RULES", "SUBJECT_ANCHOR", "DIRECTION_RULES",
                        "ASPECT_AND_SIZE", "CELL_PLAN", "OUTPUT_RULES"):
            self.assertIn(f"[{section}]", prompt.text)
        self.assertIn("同一只浅灰陶瓷杯", prompt.text)
        self.assertIn("3×3", prompt.text)
        self.assertIn("3:4", prompt.text)
        self.assertEqual(prompt.template_version, "grid-prompt-v1")
        self.assertNotIn("{", prompt.text)

    def test_cell_plan_unique_and_complete(self):
        for grid_size in (2, 3, 4):
            with self.subTest(grid_size=grid_size):
                prompt = build_grid_prompt(registry=REGISTRY, direction_key="scene",
                                           grid_size=grid_size, aspect_ratio="1:1",
                                           resolution_tier="4K", subject_anchor="x",
                                           tile_edge_min_px=1024)
                total = grid_size * grid_size
                self.assertEqual(len(prompt.cell_plan), total)
                self.assertEqual(len(set(prompt.cell_plan)), total)
                self.assertIn(f"第 {total} 格", prompt.text)

    def test_no_narrative_conjunctions_in_grid(self):
        for key in REGISTRY.keys():
            with self.subTest(direction=key):
                for point in build_cell_plan(REGISTRY.get(key), 4):
                    for word in ("然后", "接着", "最终", "随后"):
                        self.assertNotIn(word, point)

    def test_all_twelve_directions_usable(self):
        self.assertEqual(len(REGISTRY.keys()), 12)
        for key in REGISTRY.keys():
            with self.subTest(direction=key):
                prompt = build_grid_prompt(registry=REGISTRY, direction_key=key, grid_size=4,
                                           aspect_ratio="1:1", resolution_tier="4K",
                                           subject_anchor="x", tile_edge_min_px=1024)
                self.assertIn(key, prompt.text)

    def test_unknown_direction_blocked(self):
        with self.assertRaises(IVError) as ctx:
            build_grid_prompt(registry=REGISTRY, direction_key="light+scene", grid_size=2,
                              aspect_ratio="1:1", resolution_tier="2K", subject_anchor="x",
                              tile_edge_min_px=1024)
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")

    def test_direction_default_branch_available(self):
        for key in REGISTRY.keys():
            rules = REGISTRY.get(key)
            self.assertIn(rules.default_branch, rules.supported_branches)


# ------------------------------------------------------------------ 端到端
class TestExecuteGrid(unittest.TestCase):
    def run_grid(self, images, **overrides):
        adapter = overrides.pop("adapter", None) or mock_adapter(images)
        options = dict(direction_key="light", grid_size=2, aspect_ratio="1:1",
                       input_type="text_only", subject_anchor="同一只浅灰陶瓷杯",
                       registry=REGISTRY, config=CONFIG, sleep=lambda _: None)
        options.update(overrides)
        return adapter, execute_grid(adapter=adapter, **options)

    def test_success_path(self):
        adapter, execution = self.run_grid([png(smooth_board(2, 512))])
        self.assertEqual(execution.plan["tier"], "2K")
        self.assertEqual(len(execution.tiles), 4)
        self.assertEqual(execution.result.verdict, "pass")
        self.assertEqual(execution.budget["calls_used"], 1)
        self.assertEqual(execution.result.postprocess.validation["grid_attempts"], 1)
        self.assertEqual(len(adapter.trace), 1, "一次成板只能有一次模型调用")
        self.assertEqual(execution.prompt_meta["template_version"], "grid-prompt-v1")

    def test_risk_board_still_delivers_with_warning(self):
        adapter, execution = self.run_grid([png(faint_seam_board(2, 768, delta=3))])
        self.assertEqual(execution.result.verdict, "risk")
        codes = {w["code"] for w in execution.warnings}
        self.assertIn("GRID_SEAM_RISK", codes)
        self.assertEqual(len(adapter.trace), 1)
        self.assertGreaterEqual(execution.result.seam.score, CONFIG["seam"]["risk_threshold"])
        self.assertLess(execution.result.seam.score, CONFIG["seam"]["pass_threshold"])

    def test_tier_warning_propagates(self):
        _, execution = self.run_grid([png(smooth_board(2, 512))], requested_tier="1K")
        codes = {w["code"] for w in execution.warnings}
        self.assertIn("TILE_RESOLUTION_RISK", codes)
        self.assertEqual(execution.plan["tier_policy"], "user_override")

    def test_retry_on_bad_board_then_success(self):
        adapter, execution = self.run_grid([png(hard_seam_board(2, 512)),
                                            png(smooth_board(2, 512))])
        self.assertEqual(len(adapter.trace), 2)
        self.assertEqual(execution.budget["calls_used"], 2)
        self.assertEqual(execution.result.verdict, "pass")
        self.assertEqual(execution.result.attempt, 2)

    def test_gives_up_after_max_attempts(self):
        bad = png(hard_seam_board(2, 512))
        adapter = mock_adapter([bad] * 5)
        with self.assertRaises(IVError) as ctx:
            execute_grid(adapter=adapter, direction_key="light", grid_size=2, aspect_ratio="1:1",
                         input_type="text_only", subject_anchor="x", registry=REGISTRY,
                         config=CONFIG, sleep=lambda _: None)
        self.assertEqual(ctx.exception.code, "GRID_VALIDATION_FAILED")
        self.assertEqual(len(adapter.trace), CONFIG["retry"]["grid_max_attempts"])
        self.assertIn("grid_attempts", ctx.exception.detail)

    def test_corrupt_board_retried(self):
        adapter = mock_adapter([b"broken", png(smooth_board(2, 512))])
        execution = execute_grid(adapter=adapter, direction_key="light", grid_size=2,
                                 aspect_ratio="1:1", input_type="text_only", subject_anchor="x",
                                 registry=REGISTRY, config=CONFIG, sleep=lambda _: None)
        self.assertEqual(len(adapter.trace), 2)
        self.assertEqual(execution.result.verdict, "pass")

    def test_corrupt_board_exhausts_attempts(self):
        adapter = mock_adapter([b"broken"] * 5)
        with self.assertRaises(IVError) as ctx:
            execute_grid(adapter=adapter, direction_key="light", grid_size=2, aspect_ratio="1:1",
                         input_type="text_only", subject_anchor="x", registry=REGISTRY,
                         config=CONFIG, sleep=lambda _: None)
        self.assertEqual(ctx.exception.code, "IMAGE_VALIDATION_FAILED")
        self.assertEqual(len(adapter.trace), CONFIG["retry"]["grid_max_attempts"])

    def test_border_line_board_rejected(self):
        adapter = mock_adapter([png(border_line_board(2, 512))] * 3)
        with self.assertRaises(IVError) as ctx:
            execute_grid(adapter=adapter, direction_key="light", grid_size=2, aspect_ratio="1:1",
                         input_type="text_only", subject_anchor="x", registry=REGISTRY,
                         config=CONFIG, sleep=lambda _: None)
        self.assertEqual(ctx.exception.code, "GRID_VALIDATION_FAILED")

    def test_tile_too_low_blocks(self):
        with self.assertRaises(IVError) as ctx:
            execute_grid(adapter=mock_adapter([png(smooth_board(4, 128))]),
                         direction_key="light", grid_size=4, aspect_ratio="1:1",
                         input_type="text_only", subject_anchor="x", registry=REGISTRY,
                         config=CONFIG, sleep=lambda _: None)
        self.assertEqual(ctx.exception.code, "TILE_RESOLUTION_TOO_LOW")

    def test_direction_without_grid_mode_blocked(self):
        with self.assertRaises(IVError) as ctx:
            execute_grid(adapter=mock_adapter([png(smooth_board(2, 512))]),
                         direction_key="light", grid_size=2, aspect_ratio="1:1",
                         input_type="text_only", subject_anchor="x",
                         registry=RegistryWithoutGrid(), config=CONFIG, sleep=lambda _: None)
        self.assertEqual(ctx.exception.code, "INVALID_MODE_OR_COUNT")

    def test_reference_image_passed_through(self):
        adapter = mock_adapter([png(smooth_board(2, 512))])
        reference = ReferenceImage(fake_png((64, 64)), "image/png", "ref.png")
        execute_grid(adapter=adapter, direction_key="light", grid_size=2, aspect_ratio="1:1",
                     input_type="image_only", subject_anchor="同一只杯",
                     reference_image=reference, registry=REGISTRY, config=CONFIG,
                     sleep=lambda _: None)
        payload = json.loads(adapter.trace[0].body.decode("utf-8"))
        self.assertTrue(payload["uses_reference"])
        self.assertGreater(payload["reference_bytes"], 0)

    def test_budget_limit_blocks_retry(self):
        bad = png(hard_seam_board(2, 512))
        adapter = mock_adapter([bad, bad, bad])
        # planned=1 时前置检查放行；第一次调用后重试需要第 2 次调用 → 记账处阻断
        budget = Budget(limit=1, planned=1, cost_per_call=CONFIG["budget"]["cost_per_call"])
        with self.assertRaises(IVError) as ctx:
            execute_grid(adapter=adapter, direction_key="light", grid_size=2, aspect_ratio="1:1",
                         input_type="text_only", subject_anchor="x", registry=REGISTRY,
                         config=CONFIG, budget=budget, sleep=lambda _: None)
        self.assertEqual(ctx.exception.code, "BUDGET_EXCEEDED")
        self.assertEqual(len(adapter.trace), 1)

    def test_plan_records_tier_and_cost(self):
        execution = execute_grid(adapter=mock_adapter([png(smooth_board(4, 1024))]),
                                 direction_key="scene", grid_size=4, aspect_ratio="1:1",
                                 input_type="text_only", subject_anchor="x",
                                 registry=REGISTRY, config=CONFIG, sleep=lambda _: None)
        self.assertEqual(execution.plan["tier"], "4K")
        self.assertEqual(execution.plan["tile_long_edge_px"], 1024)
        self.assertGreater(execution.plan["cost_estimate"], 0)
        self.assertEqual(execution.budget["calls_planned"], 3)


class RegistryWithoutGrid(DirectionRegistry):
    """用于验证方向模式不匹配时明确报错。"""

    def __init__(self):
        super().__init__()
        self._dirs["light"] = {**self._dirs["light"], "modes": ["sequence"]}


if __name__ == "__main__":
    unittest.main(verbosity=2)
