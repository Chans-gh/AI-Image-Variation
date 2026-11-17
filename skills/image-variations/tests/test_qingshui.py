"""青水（qingshui）异步适配器：提交→轮询→下载、参考图上传即删、错误归一、重试边界。

全部用假传输（FakeTransport），不联网、不需要密钥。

运行：.venv/bin/python skills/image-variations/tests/test_qingshui.py
"""
from __future__ import annotations

import copy
import io
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
REPO = HERE.parents[2]
for entry in (str(SCRIPTS), str(REPO)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from iv_skill.config import load_config, load_provider_profiles  # noqa: E402
from iv_skill.provider import GenerationRequest, ReferenceImage  # noqa: E402
from iv_skill.provider.capability import ModelCapability  # noqa: E402
from iv_skill.provider.qingshui_compat import QingshuiImageAdapter  # noqa: E402
from iv_skill.provider.registry import build_adapter  # noqa: E402
from iv_skill.provider.retry import RetryPolicy  # noqa: E402
from iv_skill.provider.transport import TransportResponse  # noqa: E402
from shared.errors import IVError  # noqa: E402

BASE = "https://qingshui.hqqt.com"
SECRET = "qs-test-secret-value"
PROFILES = load_provider_profiles()["profiles"]
GEMINI_CAP = PROFILES["qingshui_gemini3.1"]["capability"]


def png_bytes(size: tuple[int, int] = (8, 8)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, (128, 128, 128)).save(buffer, format="PNG")
    return buffer.getvalue()


PNG = png_bytes()


def board_png(size: tuple[int, int] = (1024, 1024)) -> bytes:
    """有纹理的整板：纯色板会被"留白带"规则判成结构失败，不能用来测流水线。"""
    from PIL import Image

    buffer = io.BytesIO()
    Image.effect_noise(size, 32).convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def json_response(status: int, payload: dict) -> TransportResponse:
    return TransportResponse(status, {"Content-Type": "application/json"},
                             json.dumps(payload).encode("utf-8"))


def task_response(status: str, **extra) -> TransportResponse:
    payload = {"success": True,
               "task": {"status": status, "progress": 30, "task_id": "task-1", **extra}}
    return json_response(200, payload)


def image_response(payload: bytes = PNG, status: int = 200) -> TransportResponse:
    return TransportResponse(status, {"Content-Type": "image/png"}, payload)


class FakeTransport:
    """按脚本回放响应，并记录每一次请求。请求耗尽即失败（避免测试漏掉步骤）。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        if not self.responses:
            raise AssertionError(f"未预期的请求：{request.method} {request.url}")
        item = self.responses.pop(0)
        return item(request) if callable(item) else item

    # ---- 便于断言
    def methods(self) -> list[str]:
        return [call.method for call in self.calls]

    def urls(self) -> list[str]:
        return [call.url for call in self.calls]

    def bodies(self) -> list[dict]:
        parsed = []
        for call in self.calls:
            try:
                parsed.append(json.loads(call.body.decode("utf-8")))
            except Exception:  # noqa: BLE001
                parsed.append({})
        return parsed


def no_retry_policy() -> RetryPolicy:
    """生产里执行器把重试收口在自己身上，适配器只允许一次尝试（max_extra_retries=0）。"""
    return RetryPolicy.from_config(load_config(), max_extra_retries=0)


def make_adapter(transport, capability: dict | None = None, **qingshui_overrides):
    config = copy.deepcopy(load_config())
    config["qingshui"].update({"poll_interval_sec": 0, "max_wait_sec": 30,
                               "poll_consecutive_error_limit": 3, "download_timeout_sec": 5})
    config["qingshui"].update(qingshui_overrides)
    return QingshuiImageAdapter(
        capability=ModelCapability.from_declaration(capability or GEMINI_CAP),
        base_url=BASE, secret_ref="env:IV_TEST_KEY", model_id="test-model",
        transport=transport, config=config, secret_resolver=lambda name: SECRET)


def request_with(**kwargs) -> GenerationRequest:
    payload = {"prompt": "keep the same subject, warmer light", "aspect_ratio": "1:1",
               "resolution_tier": "1K"}
    payload.update(kwargs)
    return GenerationRequest(**payload)


def reference() -> ReferenceImage:
    return ReferenceImage(data=PNG, media_type="image/png", filename="reference.png")


SUBMITTED = json_response(200, {"success": True, "task_id": "task-1", "quota_cost": 1})
COMPLETED = task_response("completed", result={"images": ["https://cdn.example.com/out.png"],
                                               "model": "test-model", "usage_info": {"n": 1}})


class TestHappyPath(unittest.TestCase):
    def test_submit_poll_download_success(self):
        transport = FakeTransport([SUBMITTED, task_response("processing"), COMPLETED, image_response()])
        result = make_adapter(transport).generate(request_with(), sleep=lambda _s: None)
        self.assertEqual(result.image_bytes, PNG)
        self.assertEqual(result.media_type, "image/png")
        self.assertEqual(result.provider_raw_meta["task_id"], "task-1")
        self.assertEqual(transport.methods(), ["POST", "GET", "GET", "GET"])
        self.assertTrue(transport.urls()[0].endswith("/api/v1/images/generate"))
        self.assertEqual(transport.bodies()[0]["image_count"], 1)

    def test_submit_payload_uses_aspect_ratio_and_no_size(self):
        """实测：报文里不能带未被支持的尺寸参数（后端会忽略或报 400）。"""
        transport = FakeTransport([SUBMITTED, COMPLETED, image_response()])
        make_adapter(transport).generate(request_with(aspect_ratio="3:4", resolution_tier="1K"),
                                         sleep=lambda _s: None)
        body = transport.bodies()[0]
        self.assertEqual(body["aspect_ratio"], "3:4")
        for forbidden in ("size", "image_size", "width", "height"):
            self.assertNotIn(forbidden, body)

    def test_no_reference_means_no_upload_call(self):
        transport = FakeTransport([SUBMITTED, COMPLETED, image_response()])
        make_adapter(transport).generate(request_with(), sleep=lambda _s: None)
        self.assertFalse([url for url in transport.urls() if "/api/v1/assets" in url])


class TestReferenceLifecycle(unittest.TestCase):
    def test_reference_uploaded_as_url_then_deleted(self):
        transport = FakeTransport([
            json_response(201, {"success": True, "url": "https://cdn.example.com/ref.png",
                                "asset": {"hash": "a" * 32}}),
            SUBMITTED, COMPLETED, image_response(),
            json_response(200, {"success": True, "deleted": True}),
        ])
        result = make_adapter(transport).generate(request_with(reference_image=reference()),
                                                  sleep=lambda _s: None)
        self.assertEqual(result.image_bytes, PNG)
        self.assertEqual(transport.methods(), ["POST", "POST", "GET", "GET", "DELETE"])
        self.assertTrue(transport.urls()[0].endswith("/api/v1/assets"))
        self.assertTrue(transport.urls()[-1].endswith("/api/v1/assets/" + "a" * 32))
        submit_body = transport.bodies()[1]
        self.assertEqual(submit_body["reference_images"], ["https://cdn.example.com/ref.png"])

    def test_reference_is_never_sent_inline(self):
        """实测结论：内联 data URL 会被上游拒绝，必须走上传换 URL。"""
        transport = FakeTransport([
            json_response(201, {"success": True, "url": "https://cdn.example.com/ref.png",
                                "asset": {"hash": "b" * 32}}),
            SUBMITTED, COMPLETED, image_response(),
            json_response(200, {"success": True, "deleted": True}),
        ])
        make_adapter(transport).generate(request_with(reference_image=reference()),
                                         sleep=lambda _s: None)
        submit_body = transport.bodies()[1]
        self.assertFalse(any(str(ref).startswith("data:")
                             for ref in submit_body["reference_images"]))

    def test_reference_deleted_even_when_generation_fails(self):
        transport = FakeTransport([
            json_response(201, {"success": True, "url": "https://cdn.example.com/ref.png",
                                "asset": {"hash": "c" * 32}}),
            SUBMITTED,
            task_response("failed", error_msg="image gen failed: AIMS 图片编辑失败: [502] UPSTREAM_PROVIDER_ERROR"),
            json_response(200, {"success": True, "deleted": True}),
        ])
        with self.assertRaises(IVError):
            make_adapter(transport).generate(request_with(reference_image=reference()),
                                             policy=no_retry_policy(), sleep=lambda _s: None)
        self.assertEqual(transport.methods()[-1], "DELETE", "生成失败也必须收回公开 URL")

    def test_upload_failure_is_reported_with_registry_code(self):
        transport = FakeTransport([json_response(401, {"success": False, "error": "unauthorized"})])
        with self.assertRaises(IVError) as ctx:
            make_adapter(transport).generate(request_with(reference_image=reference()),
                                             policy=no_retry_policy(), sleep=lambda _s: None)
        self.assertEqual(ctx.exception.code, "AUTH_FAILED")

    def test_cleanup_can_be_disabled_by_config(self):
        transport = FakeTransport([
            json_response(201, {"success": True, "url": "https://cdn.example.com/ref.png",
                                "asset": {"hash": "d" * 32}}),
            SUBMITTED, COMPLETED, image_response(),
        ])
        make_adapter(transport, asset_cleanup=False).generate(
            request_with(reference_image=reference()), sleep=lambda _s: None)
        self.assertNotIn("DELETE", transport.methods())


class TestReferenceLifecycleRetry(unittest.TestCase):
    def test_retry_reuses_uploaded_reference_without_second_upload(self):
        """重试不得重复上传：否则每次重试都多留一个公开 URL。"""
        transport = FakeTransport([
            json_response(201, {"success": True, "url": "https://cdn.example.com/ref.png",
                                "asset": {"hash": "e" * 32}}),
            json_response(502, {"error": "bad gateway"}),
            SUBMITTED, COMPLETED, image_response(),
            json_response(200, {"success": True, "deleted": True}),
        ])
        policy = RetryPolicy.from_config(load_config(), max_extra_retries=1)
        result = make_adapter(transport).generate(request_with(reference_image=reference()),
                                                  policy=policy, sleep=lambda _s: None)
        self.assertEqual(result.image_bytes, PNG)
        uploads = [url for url in transport.urls() if url.endswith("/api/v1/assets")]
        self.assertEqual(len(uploads), 1, "参考图只应上传一次")
        self.assertEqual(transport.methods().count("DELETE"), 1)
        submits = [body for call, body in zip(transport.calls, transport.bodies())
                   if call.url.endswith("/api/v1/images/generate")]
        self.assertEqual([body["reference_images"] for body in submits],
                         [["https://cdn.example.com/ref.png"]] * 2)


class TestReferenceCleanupVisibility(unittest.TestCase):
    """授权上传的前提是"用完即删"：删失败必须可见，且不能因此丢掉已生成的图。"""

    def _run(self, delete_response):
        transport = FakeTransport([
            json_response(201, {"success": True, "url": "https://cdn.example.com/ref.png",
                                "asset": {"hash": "f" * 32}}),
            SUBMITTED, COMPLETED, image_response(), delete_response,
        ])
        adapter = make_adapter(transport)
        result = adapter.generate(request_with(reference_image=reference()),
                                  policy=no_retry_policy(), sleep=lambda _s: None)
        return adapter, result

    def test_successful_cleanup_yields_no_warning(self):
        adapter, result = self._run(json_response(200, {"success": True, "deleted": True}))
        self.assertEqual(result.image_bytes, PNG)
        self.assertEqual(adapter.take_warnings(), [])

    def test_failed_delete_is_surfaced_but_delivery_kept(self):
        adapter, result = self._run(json_response(500, {"error": "boom"}))
        self.assertEqual(result.image_bytes, PNG, "收回失败不能丢掉已生成的图")
        warnings = adapter.take_warnings()
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["code"], "REFERENCE_CLEANUP_FAILED")
        self.assertEqual(warnings[0]["severity"], "warning")
        self.assertIn("手动删除", warnings[0]["message"])
        self.assertIn("冻结错误码表", warnings[0]["message"])

    def test_cleanup_request_exception_is_surfaced(self):
        def explode(_request):
            raise OSError("network down")

        adapter, _ = self._run(explode)
        warnings = adapter.take_warnings()
        self.assertEqual([w["code"] for w in warnings], ["REFERENCE_CLEANUP_FAILED"])
        self.assertIn("OSError", warnings[0]["message"])

    def test_take_warnings_clears(self):
        adapter, _ = self._run(json_response(503, {}))
        self.assertEqual(len(adapter.take_warnings()), 1)
        self.assertEqual(adapter.take_warnings(), [], "取走后不能重复计入")

    def test_already_deleted_404_is_not_a_warning(self):
        adapter, _ = self._run(json_response(404, {}))
        self.assertEqual(adapter.take_warnings(), [], "本来就不存在，不算收回失败")

    def test_secret_not_leaked_in_cleanup_warning(self):
        adapter, _ = self._run(json_response(500, {"echo": SECRET}))
        message = adapter.take_warnings()[0]["message"]
        self.assertNotIn(SECRET, message)


class TestErrorNormalisation(unittest.TestCase):
    def _failed_with(self, error_msg: str, **overrides):
        transport = FakeTransport([SUBMITTED, task_response("failed", error_msg=error_msg)])
        adapter = make_adapter(transport, **overrides)
        with self.assertRaises(IVError) as ctx:
            adapter.generate(request_with(), policy=no_retry_policy(), sleep=lambda _s: None)
        return ctx.exception, transport

    def test_upstream_502_maps_to_retryable_provider_timeout(self):
        exc, _ = self._failed_with(
            'image gen failed: AIMS 图片编辑失败: AIMS API 错误 [502]: '
            '{"error":{"message":"模型服务异常，请稍后重试","code":"UPSTREAM_PROVIDER_ERROR"}}')
        self.assertEqual(exc.code, "PROVIDER_TIMEOUT")
        self.assertTrue(exc.detail.get("retryable"))

    def test_unsupported_upstream_size_is_not_retried(self):
        exc, _ = self._failed_with(
            'image gen failed: AIMS 图片生成失败: AIMS API 错误 [400]: '
            '{"error":{"message":"The parameter `size` specified in the request is not valid: '
            'image size must be at least 3686400 pixels.","code":"InvalidParameter"}}')
        self.assertEqual(exc.code, "UNSUPPORTED_RESOLUTION")
        self.assertFalse(exc.detail.get("retryable"))

    def test_content_refusal_maps_to_model_refusal(self):
        exc, _ = self._failed_with('image gen failed: 内容安全审核未通过')
        self.assertEqual(exc.code, "MODEL_REFUSAL")
        self.assertFalse(exc.detail.get("retryable"))

    def test_unknown_task_failure_is_retryable_single_image_error(self):
        exc, _ = self._failed_with("image gen failed: 未知原因")
        self.assertEqual(exc.code, "IMAGE_VALIDATION_FAILED")
        self.assertTrue(exc.detail.get("retryable"))

    def test_poll_timeout_maps_to_provider_timeout(self):
        transport = FakeTransport([SUBMITTED] + [task_response("processing")] * 4)
        adapter = make_adapter(transport, max_wait_sec=0)
        with self.assertRaises(IVError) as ctx:
            adapter.generate(request_with(), policy=no_retry_policy(), sleep=lambda _s: None)
        self.assertEqual(ctx.exception.code, "PROVIDER_TIMEOUT")

    def test_consecutive_poll_errors_stop_at_limit(self):
        transport = FakeTransport([SUBMITTED, json_response(500, {}), json_response(500, {}),
                                   json_response(500, {})])
        adapter = make_adapter(transport, poll_consecutive_error_limit=3)
        with self.assertRaises(IVError) as ctx:
            adapter.generate(request_with(), policy=no_retry_policy(), sleep=lambda _s: None)
        self.assertEqual(ctx.exception.code, "PROVIDER_TIMEOUT")
        self.assertEqual(transport.methods().count("GET"), 3)

    def test_download_failure_is_reported(self):
        transport = FakeTransport([SUBMITTED, COMPLETED, image_response(b"", status=500)])
        with self.assertRaises(IVError) as ctx:
            make_adapter(transport).generate(request_with(), policy=no_retry_policy(),
                                             sleep=lambda _s: None)
        self.assertEqual(ctx.exception.code, "PROVIDER_TIMEOUT")

    def test_missing_task_id_is_invalid_upstream_response(self):
        transport = FakeTransport([json_response(200, {"success": True})])
        with self.assertRaises(IVError) as ctx:
            make_adapter(transport).generate(request_with(), policy=no_retry_policy(),
                                             sleep=lambda _s: None)
        self.assertEqual(ctx.exception.code, "IMAGE_VALIDATION_FAILED")

    def test_secret_never_appears_in_error_message(self):
        transport = FakeTransport([json_response(200, {"success": False, "task_id": "",
                                                       "echo": SECRET})])
        with self.assertRaises(IVError) as ctx:
            make_adapter(transport).generate(request_with(), policy=no_retry_policy(),
                                             sleep=lambda _s: None)
        self.assertNotIn(SECRET, ctx.exception.message)
        self.assertNotIn(SECRET, json.dumps(ctx.exception.detail, ensure_ascii=False))


class TestRetryBoundary(unittest.TestCase):
    def test_zero_extra_retries_submits_exactly_once(self):
        """执行器把重试收口在自己身上，适配器不得二次重试（否则算力会翻倍）。"""
        transport = FakeTransport([json_response(502, {"error": "bad gateway"})])
        adapter = make_adapter(transport)
        policy = RetryPolicy.from_config(load_config(), max_extra_retries=0)
        with self.assertRaises(IVError) as ctx:
            adapter.generate(request_with(), policy=policy, sleep=lambda _s: None)
        self.assertEqual(ctx.exception.code, "PROVIDER_TIMEOUT")
        self.assertEqual(transport.methods(), ["POST"])

    def test_permissive_policy_retries_submit(self):
        transport = FakeTransport([json_response(502, {"error": "bad gateway"}), SUBMITTED,
                                   COMPLETED, image_response()])
        adapter = make_adapter(transport)
        policy = RetryPolicy.from_config(load_config(), max_extra_retries=1)
        result = adapter.generate(request_with(), policy=policy, sleep=lambda _s: None)
        self.assertEqual(result.image_bytes, PNG)
        self.assertEqual(transport.methods().count("POST"), 2)


class TestModelIdTraceability(unittest.TestCase):
    def test_execute_grid_records_real_model_id(self):
        """回归：manifest 的 model_id 曾因能力声明里没有该字段而落成空串，追溯就断了。"""
        from iv_skill.executor import execute_grid
        from iv_skill.prompt_builder import DirectionRegistry

        # 用真实尺寸（1K → 1024²）：512² 会被单格硬下限 TILE_RESOLUTION_TOO_LOW 拦住
        transport = FakeTransport([SUBMITTED, COMPLETED,
                                   image_response(board_png((1024, 1024)))])
        execution = execute_grid(adapter=make_adapter(transport), direction_key="light",
                                 grid_size=2, aspect_ratio="1:1", input_type="text_only",
                                 subject_anchor="一只浅灰陶瓷杯", registry=DirectionRegistry(),
                                 config=load_config(), sleep=lambda _s: None)
        self.assertEqual(execution.plan["model_id"], "test-model")


class TestAdapterWarningDrain(unittest.TestCase):
    def test_drain_calls_take_warnings_and_tolerates_plain_adapters(self):
        from iv_skill.task import _drain_adapter_warnings

        class WithWarnings:
            def take_warnings(self):
                return [{"code": "REFERENCE_CLEANUP_FAILED", "message": "x"}]

        self.assertEqual([w["code"] for w in _drain_adapter_warnings(WithWarnings())],
                         ["REFERENCE_CLEANUP_FAILED"])
        self.assertEqual(_drain_adapter_warnings(object()), [],
                         "没有 take_warnings 的适配器不能报错")


class TestRegistrationAndProfiles(unittest.TestCase):
    def test_registry_builds_qingshui_adapter(self):
        adapter = build_adapter(provider_type="qingshui_image_compatible",
                                capability_declaration=GEMINI_CAP, base_url=BASE,
                                secret_ref="env:IV_TEST_KEY", model_id="m",
                                transport=FakeTransport([]), config=load_config(),
                                secret_resolver=lambda name: SECRET)
        self.assertIsInstance(adapter, QingshuiImageAdapter)
        self.assertEqual(adapter.auth_headers(), {"Authorization": f"Bearer {SECRET}"})

    def test_unknown_provider_type_still_rejected(self):
        with self.assertRaises(IVError) as ctx:
            build_adapter(provider_type="not_a_provider", capability_declaration=GEMINI_CAP)
        self.assertEqual(ctx.exception.code, "MODEL_CAPABILITY_UNSUPPORTED")

    def test_every_profile_has_required_capability_fields(self):
        for name, entry in PROFILES.items():
            with self.subTest(profile=name):
                capability = ModelCapability.from_declaration(entry["capability"])
                self.assertEqual(capability.adapter_version, "qingshui-image/1.0")
                self.assertTrue(entry.get("measured_at"), "档案必须写明实测日期")
                self.assertTrue(entry["evidence"], "档案必须写明实测依据")

    def test_doubao_profile_declares_no_tiers(self):
        """Doubao 在本平台不可用：必须不声明档位，让底线清单直接拦住而不是白花算力。"""
        doubao = PROFILES["qingshui_doubao45"]["capability"]
        self.assertFalse(doubao["text_to_image"])
        self.assertEqual(doubao["supported_resolution_tiers"], [])
        self.assertEqual(doubao["tier_to_board_side_px"], {})

    def test_image25_profile_blocks_reference_input(self):
        """实测 image2.5 的图片编辑通路 3/3 失败，档案必须声明不支持参考图。"""
        capability = PROFILES["qingshui_image2.5"]["capability"]
        self.assertFalse(capability["reference_image_input"])
        self.assertFalse(capability["image_edit"])


class TestTaskSpecProfiles(unittest.TestCase):
    def _spec(self, provider: dict) -> dict:
        return {"task_id": "qh", "output_mode": "sequence", "image_count": 4,
                "branch": "progression", "direction": "light", "input_type": "text_only",
                "subject": "一只浅灰陶瓷杯", "provider": provider}

    def test_capability_profile_supplies_declaration_and_defaults(self):
        from iv_skill.task import build_task_adapter

        adapter = build_task_adapter(
            self._spec({"type": "qingshui_image_compatible",
                        "capability_profile": "qingshui_image2.5",
                        "api_key_secret_ref": "env:IV_TEST_KEY"}),
            config=load_config(), grid_size=1)
        self.assertEqual(adapter.model_id, "gpt-image-2.5-flare")
        self.assertEqual(adapter.base_url, BASE)
        self.assertEqual(adapter.capability.tier_to_board_side_px, {"1K": 1024})
        self.assertEqual(adapter.capability.supported_resolution_tiers, ("1K",))

    def test_explicit_capability_still_wins_over_profile(self):
        from iv_skill.task import build_task_adapter

        declaration = dict(GEMINI_CAP)
        declaration["supported_resolution_tiers"] = ["1K", "2K"]
        declaration["tier_to_board_side_px"] = {"1K": 1024, "2K": 2048}
        adapter = build_task_adapter(
            self._spec({"type": "qingshui_image_compatible", "capability": declaration,
                        "base_url": BASE, "api_key_secret_ref": "env:IV_TEST_KEY"}),
            config=load_config(), grid_size=1)
        self.assertEqual(adapter.capability.tier_to_board_side_px["2K"], 2048)

    def test_unknown_profile_is_rejected(self):
        from iv_skill.task import build_task_adapter

        with self.assertRaises(IVError) as ctx:
            build_task_adapter(self._spec({"type": "qingshui_image_compatible",
                                           "capability_profile": "nope"}), config=load_config(),
                               grid_size=1)
        self.assertEqual(ctx.exception.code, "MODEL_CAPABILITY_UNSUPPORTED")

    def test_profile_type_mismatch_is_rejected(self):
        from iv_skill.task import build_task_adapter

        with self.assertRaises(IVError) as ctx:
            build_task_adapter(self._spec({"type": "openai_image_compatible",
                                           "capability_profile": "qingshui_image2.5"}),
                               config=load_config(), grid_size=1)
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
