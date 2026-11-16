"""Phase 2 测试：能力抽象、排产预检、预算与重试策略。

运行：.venv/bin/python skills/image-variations/tests/test_preflight.py
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "support"))

import paths  # noqa: E402

from iv_skill.config import load_config  # noqa: E402
from iv_skill.preflight import capabilities_declaration_for, plan_generation, retry_policy_for  # noqa: E402
from iv_skill.provider.capability import ModelCapability  # noqa: E402
from iv_skill.provider.mock import MockAdapter, MockScenario, default_capability  # noqa: E402
from iv_skill.provider.registry import build_adapter  # noqa: E402
from iv_skill.provider.retry import Budget  # noqa: E402
from shared.errors import IVError, is_known  # noqa: E402

CONFIG = load_config()
MOCK_DECL = capabilities_declaration_for("mock")


def plan(**overrides):
    base = dict(output_mode="grid", grid_size=3, image_count=None, aspect_ratio="1:1",
                input_type="text_only", direction="state", branch=None,
                capability=ModelCapability.from_declaration(MOCK_DECL), config=CONFIG,
                provider_type="mock")
    base.update(overrides)
    return plan_generation(**base)


class TestCapabilityDeclaration(unittest.TestCase):
    def test_missing_field_blocked(self):
        declaration = dict(MOCK_DECL)
        del declaration["distinguishable_errors"]
        with self.assertRaises(IVError) as ctx:
            ModelCapability.from_declaration(declaration)
        self.assertEqual(ctx.exception.code, "MODEL_CAPABILITY_UNSUPPORTED")

    def test_missing_tier_table_blocked(self):
        declaration = dict(MOCK_DECL)
        declaration["tier_to_board_side_px"] = {"1K": 1024}   # 缺 2K/4K
        with self.assertRaises(IVError) as ctx:
            ModelCapability.from_declaration(declaration)
        self.assertEqual(ctx.exception.code, "MODEL_CAPABILITY_UNSUPPORTED")
        self.assertIn("2K", str(ctx.exception))

    def test_no_declaration_blocked(self):
        result = plan(capability=None)
        self.assertFalse(result.ok)
        self.assertEqual(result.errors[0]["code"], "MODEL_CAPABILITY_UNSUPPORTED")

    def test_empty_declaration_blocked(self):
        with self.assertRaises(IVError) as ctx:
            ModelCapability.from_declaration({})
        self.assertEqual(ctx.exception.code, "MODEL_CAPABILITY_UNSUPPORTED")


class TestTierDerivation(unittest.TestCase):
    """AC-06 / AC-27：满足单格长边下限的最低档位。"""

    def setUp(self):
        self.cap = ModelCapability.from_declaration(MOCK_DECL)

    def pick(self, grid_size, aspect="1:1", requested=None):
        return self.cap.pick_tier(grid_size=grid_size, aspect_ratio=aspect,
                                  tile_edge_min_px=CONFIG["tile_edge_min_px"],
                                  requested_tier=requested)

    def test_2x2_needs_2k(self):
        self.assertEqual(self.pick(2)["tier"], "2K")
        self.assertEqual(self.pick(2)["policy"], "tile_min_edge_default")

    def test_3x3_needs_4k(self):
        self.assertEqual(self.pick(3)["tier"], "4K")

    def test_4x4_needs_4k(self):
        self.assertEqual(self.pick(4)["tier"], "4K")

    def test_3x3_on_2k_provider_degrades_with_warning(self):
        cap = ModelCapability.from_declaration(
            {**MOCK_DECL, "supported_resolution_tiers": ["1K", "2K"],
             "tier_to_board_side_px": {"1K": 1024, "2K": 2048}})
        pick = cap.pick_tier(grid_size=3, aspect_ratio="1:1", tile_edge_min_px=1024)
        self.assertEqual(pick["tier"], "2K")
        self.assertEqual(pick["policy"], "provider_degraded")
        self.assertEqual(pick["warnings"][0][0], "TILE_RESOLUTION_RISK")

    def test_user_override_recorded(self):
        pick = self.pick(4, requested="1K")
        self.assertEqual(pick["tier"], "1K")
        self.assertEqual(pick["policy"], "user_override")
        self.assertEqual(pick["warnings"][0][0], "TILE_RESOLUTION_RISK")

    def test_unsupported_override_blocked(self):
        with self.assertRaises(IVError) as ctx:
            self.pick(2, requested="8K")
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_RESOLUTION")

    def test_hard_min_blocked(self):
        cap = ModelCapability.from_declaration(
            {**MOCK_DECL, "supported_resolution_tiers": ["1K"],
             "tier_to_board_side_px": {"1K": 512}})
        with self.assertRaises(IVError) as ctx:
            cap.pick_tier(grid_size=4, aspect_ratio="1:1", tile_edge_min_px=1024)
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_RESOLUTION")
        self.assertIn("硬下限", str(ctx.exception))


class TestPreflightGate(unittest.TestCase):
    """AC-02 / AC-05 / AC-07 / AC-10 / AC-20 / AC-30：排产前阻断。"""

    def test_grid_ok(self):
        result = plan()
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.plan["calls_planned"], 3)
        self.assertEqual(result.plan["image_count"], 9)

    def test_grid_size_invalid(self):
        for size in (1, 5, None, "3"):
            with self.subTest(size=size):
                result = plan(grid_size=size)
                self.assertFalse(result.ok)
                self.assertEqual(result.errors[0]["code"], "INVALID_MODE_OR_COUNT")

    def test_grid_image_count_cannot_be_set(self):
        result = plan(grid_size=2, image_count=9)
        self.assertFalse(result.ok)
        self.assertEqual(result.errors[0]["code"], "INVALID_MODE_OR_COUNT")

    def test_grid_rejects_branch(self):
        result = plan(grid_size=2, branch="variants")
        self.assertFalse(result.ok)
        self.assertIn("INVALID_MODE_OR_COUNT", {e["code"] for e in result.errors})

    def test_sequence_bounds(self):
        for count, ok in ((4, True), (16, True), (3, False), (17, False)):
            with self.subTest(count=count):
                result = plan(output_mode="sequence", grid_size=None, image_count=count,
                              branch="progression", input_type="image_only")
                self.assertEqual(result.ok, ok, result.errors)
                if not ok:
                    self.assertEqual(result.errors[0]["code"], "INVALID_MODE_OR_COUNT")

    def test_sequence_requires_single_branch(self):
        for branch in (None, "mixed", "variants+progression"):
            with self.subTest(branch=branch):
                result = plan(output_mode="sequence", grid_size=None, image_count=4,
                              branch=branch, input_type="image_only")
                self.assertFalse(result.ok)
                self.assertEqual(result.errors[0]["code"], "INVALID_MODE_OR_COUNT")

    def test_sequence_rejects_grid_size(self):
        result = plan(output_mode="sequence", grid_size=3, image_count=4,
                      branch="variants", input_type="image_only")
        self.assertFalse(result.ok)
        self.assertIn("INVALID_MODE_OR_COUNT", {e["code"] for e in result.errors})

    def test_unsupported_ratio(self):
        result = plan(aspect_ratio="5:3")
        self.assertFalse(result.ok)
        self.assertEqual(result.errors[0]["code"], "UNSUPPORTED_RATIO")

    def test_reference_required(self):
        cap = ModelCapability.from_declaration(
            {**MOCK_DECL, "reference_image_input": False, "image_edit": False})
        result = plan(output_mode="sequence", grid_size=None, image_count=4, branch="variants",
                      input_type="text_only", capability=cap)
        self.assertFalse(result.ok)
        self.assertEqual(result.errors[0]["code"], "REFERENCE_REQUIRED")

    def test_no_text_to_image(self):
        cap = ModelCapability.from_declaration({**MOCK_DECL, "text_to_image": False})
        result = plan(capability=cap)
        self.assertFalse(result.ok)
        self.assertEqual(result.errors[0]["code"], "MODEL_CAPABILITY_UNSUPPORTED")

    def test_multiple_requests_required_for_sequence(self):
        cap = ModelCapability.from_declaration({**MOCK_DECL, "multiple_independent_requests": False})
        result = plan(output_mode="sequence", grid_size=None, image_count=4, branch="variants",
                      input_type="image_only", capability=cap)
        self.assertFalse(result.ok)
        self.assertEqual(result.errors[0]["code"], "MODEL_CAPABILITY_UNSUPPORTED")

    def test_unsupported_response_format(self):
        cap = ModelCapability.from_declaration({**MOCK_DECL, "response_image_formats": ["tiff"]})
        result = plan(capability=cap)
        self.assertFalse(result.ok)
        self.assertEqual(result.errors[0]["code"], "IMAGE_VALIDATION_FAILED")

    def test_all_error_codes_registered(self):
        cases = [
            plan(grid_size=5),
            plan(aspect_ratio="5:3"),
            plan(capability=ModelCapability.from_declaration({**MOCK_DECL, "text_to_image": False})),
        ]
        for result in cases:
            for error in result.errors:
                self.assertTrue(is_known(error["code"]), error)

    def test_blocked_preflight_raises_with_code(self):
        result = plan(aspect_ratio="5:3")
        with self.assertRaises(IVError) as ctx:
            result.raise_if_blocked()
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_RATIO")


class TestBudget(unittest.TestCase):
    """AC-29：预算前置与拆批。"""

    def test_plan_grid_and_sequence(self):
        grid = Budget.plan(output_mode="grid", image_count=9, input_type="text_only",
                           tier="4K", config=CONFIG)
        self.assertEqual(grid.planned, 1 + CONFIG["retry"]["grid_max_attempts"] - 1)
        sequence = Budget.plan(output_mode="sequence", image_count=16, input_type="text_only",
                               tier="1K", config=CONFIG)
        self.assertEqual(sequence.planned, 16 + CONFIG["retry"]["frame_max_extra_retries"])

    def test_budget_exceeded_blocks_before_calls(self):
        config = json.loads(json.dumps(CONFIG))
        config["budget"]["default_call_budget_limit"] = 2
        result = plan(config=config, grid_size=3)
        self.assertFalse(result.ok)
        self.assertEqual(result.errors[0]["code"], "BUDGET_EXCEEDED")
        self.assertIn("split_suggestion", result.errors[0])

    def test_consume_stops_at_limit(self):
        budget = Budget(limit=2, planned=2, cost_per_call=CONFIG["budget"]["cost_per_call"],
                        currency="USD")
        budget.consume(tier="1K")
        budget.consume(tier="1K")
        with self.assertRaises(IVError) as ctx:
            budget.consume(tier="1K")
        self.assertEqual(ctx.exception.code, "BUDGET_EXCEEDED")
        execution = budget.to_execution()
        self.assertTrue(execution["budget_exceeded"])
        self.assertEqual(execution["calls_used"], 3)

    def test_cost_recorded(self):
        budget = Budget.plan(output_mode="grid", image_count=9, input_type="text_only",
                             tier="4K", config=CONFIG)
        execution = budget.to_execution()
        self.assertGreater(execution["cost_estimate"], 0)
        self.assertEqual(execution["cost_currency"], CONFIG["budget"]["cost_currency"])


class TestRetryPolicy(unittest.TestCase):
    """provider-compat §5：可重试/不可重试的边界。"""

    def test_frame_policy(self):
        policy = retry_policy_for("sequence", CONFIG)
        self.assertEqual(policy.max_extra_retries, CONFIG["retry"]["frame_max_extra_retries"])
        self.assertTrue(policy.should_retry("RATE_LIMITED", 1))
        self.assertTrue(policy.should_retry("RATE_LIMITED", 2))
        self.assertFalse(policy.should_retry("RATE_LIMITED", 3))
        self.assertFalse(policy.should_retry("AUTH_FAILED", 1))
        self.assertFalse(policy.should_retry("MODEL_REFUSAL", 1))
        self.assertFalse(policy.should_retry("UNSUPPORTED_RATIO", 1))

    def test_grid_policy(self):
        policy = retry_policy_for("grid", CONFIG)
        self.assertEqual(policy.max_extra_retries, CONFIG["retry"]["grid_max_attempts"] - 1)
        self.assertTrue(policy.should_retry("GRID_VALIDATION_FAILED", 2))
        self.assertFalse(policy.should_retry("GRID_VALIDATION_FAILED", 3))

    def test_backoff_respects_retry_after(self):
        policy = retry_policy_for("grid", CONFIG)
        self.assertEqual(policy.backoff_seconds(1, retry_after=7), 7.0)

    def test_backoff_bounded(self):
        policy = retry_policy_for("grid", CONFIG)
        for attempt in range(1, 6):
            delay = policy.backoff_seconds(attempt)
            self.assertGreaterEqual(delay, 0.0)
            self.assertLessEqual(delay, policy.backoff_max_sec * (1 + policy.jitter_ratio))


class TestBuiltinPath(unittest.TestCase):
    """O-02 决议：builtin 只做可注入接口，未注入必须显式报码。"""

    def test_builtin_unavailable(self):
        with self.assertRaises(IVError) as ctx:
            build_adapter(provider_type="builtin")
        self.assertEqual(ctx.exception.code, "BUILTIN_UNAVAILABLE")

    def test_unknown_provider_blocked(self):
        with self.assertRaises(IVError) as ctx:
            build_adapter(provider_type="some_vendor", capability_declaration=MOCK_DECL)
        self.assertEqual(ctx.exception.code, "MODEL_CAPABILITY_UNSUPPORTED")

    def test_mock_adapter_builds(self):
        adapter = build_adapter(provider_type="mock",
                                capability_declaration=MOCK_DECL,
                                transport=__import__("iv_skill.provider.mock",
                                                     fromlist=["MockTransport"]).MockTransport([]))
        self.assertIsInstance(adapter, MockAdapter)


class TestMockScenarioShape(unittest.TestCase):
    def test_scenario_defaults(self):
        response = MockScenario().to_response()
        self.assertEqual(response.status, 200)
        self.assertTrue(response.body.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
