"""Phase 2 测试：两类兼容协议的请求组装与响应解析（AC-21 的离线证据）。

做法：适配器保持**真实**实现，只把传输层换成脚本化响应；
因此这里断言的是真实会发出的 URL / 头部 / 请求体，以及真实的解析分支。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "support"))

import paths  # noqa: E402

from iv_skill.provider import GenerationRequest, GenerationResult, ReferenceImage  # noqa: E402
from iv_skill.provider.base import sniff_image  # noqa: E402
from iv_skill.provider.gemini_compat import GeminiImageAdapter  # noqa: E402
from iv_skill.provider.mock import (  # noqa: E402
    DEFAULT_CAPABILITY_DECLARATION,
    MockScenario,
    MockTransport,
    ScriptedAdapter,
    default_capability,
    fake_png,
)
from iv_skill.provider.openai_compat import OpenAIImageAdapter, tier_to_size  # noqa: E402
from iv_skill.provider.capability import ModelCapability  # noqa: E402
from iv_skill.provider.transport import resolve_secret  # noqa: E402
from shared.errors import IVError  # noqa: E402

REFERENCE = ReferenceImage(fake_png((32, 32)), "image/png", "ref.png")


def scripted_openai(scenarios, *, base_url="https://api.example.com/v1", **overrides):
    declaration = {**DEFAULT_CAPABILITY_DECLARATION, "provider_type": "openai_image_compatible",
                   **overrides}
    capability = ModelCapability.from_declaration(declaration)
    delegate = OpenAIImageAdapter(capability=capability, base_url=base_url,
                                  secret_ref="env:IV_TEST_KEY", model_id="gpt-image-1",
                                  secret_resolver=lambda name: "TESTSECRET")
    return ScriptedAdapter(capability=capability, delegate=delegate, scenarios=scenarios)


def scripted_gemini(scenarios, *, base_url="https://generativelanguage.example.com/v1beta",
                    **overrides):
    declaration = {**DEFAULT_CAPABILITY_DECLARATION, "provider_type": "gemini_image_compatible",
                   **overrides}
    capability = ModelCapability.from_declaration(declaration)
    delegate = GeminiImageAdapter(capability=capability, base_url=base_url,
                                  secret_ref="env:IV_TEST_KEY", model_id="nano-banana",
                                  secret_resolver=lambda name: "TESTSECRET")
    return ScriptedAdapter(capability=capability, delegate=delegate, scenarios=scenarios)


class TestTierToSize(unittest.TestCase):
    def test_landscape(self):
        self.assertEqual(tier_to_size("16:9", 2048), "2048x1152")

    def test_portrait(self):
        self.assertEqual(tier_to_size("3:4", 4096), "3072x4096")

    def test_square(self):
        self.assertEqual(tier_to_size("1:1", 1024), "1024x1024")


class TestOpenAIGeneration(unittest.TestCase):
    def test_wire_shape(self):
        adapter = scripted_openai([MockScenario(shape="openai")])
        adapter.generate(GenerationRequest(prompt="一只杯子", aspect_ratio="3:4",
                                           resolution_tier="2K"))
        request = adapter.trace[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url, "https://api.example.com/v1/images/generations")
        self.assertEqual(request.headers["Content-Type"], "application/json")
        self.assertEqual(request.headers["Authorization"], "Basic " +
                         __import__("base64").b64encode(b"TESTSECRET:").decode())
        payload = json.loads(request.body.decode("utf-8"))
        self.assertEqual(payload["model"], "gpt-image-1")
        self.assertEqual(payload["n"], 1)
        self.assertEqual(payload["size"], "1536x2048")   # 2K=2048 长边，3:4
        self.assertEqual(payload["response_format"], "b64_json")

    def test_parses_b64_json(self):
        adapter = scripted_openai([MockScenario(shape="openai", image_bytes=fake_png((64, 64)))])
        result = adapter.generate(GenerationRequest(prompt="x", aspect_ratio="1:1"))
        self.assertIsInstance(result, GenerationResult)
        self.assertEqual(sniff_image(result.image_bytes)[0], "png")

    def test_url_only_response_is_violation(self):
        adapter = scripted_openai([MockScenario(
            body_text=json.dumps({"data": [{"url": "https://cdn.example.com/a.png"}]}),
            headers={"Content-Type": "application/json"})])
        with self.assertRaises(IVError) as ctx:
            adapter.generate(GenerationRequest(prompt="x"))
        self.assertEqual(ctx.exception.code, "IMAGE_VALIDATION_FAILED")

    def test_empty_data_is_violation(self):
        adapter = scripted_openai([MockScenario(
            body_text=json.dumps({"data": []}), headers={"Content-Type": "application/json"})])
        with self.assertRaises(IVError) as ctx:
            adapter.generate(GenerationRequest(prompt="x"))
        self.assertEqual(ctx.exception.code, "IMAGE_VALIDATION_FAILED")

    def test_raw_image_response_accepted(self):
        adapter = scripted_openai([MockScenario(image_bytes=fake_png())])
        result = adapter.generate(GenerationRequest(prompt="x"))
        self.assertTrue(result.image_bytes.startswith(b"\x89PNG"))


class TestOpenAIEdit(unittest.TestCase):
    def test_multipart_with_reference(self):
        adapter = scripted_openai([MockScenario(shape="openai")])
        adapter.generate(GenerationRequest(prompt="保持主体", aspect_ratio="1:1",
                                           resolution_tier="1K", reference_image=REFERENCE))
        request = adapter.trace[0]
        self.assertEqual(request.url, "https://api.example.com/v1/images/edits")
        content_type = request.headers["Content-Type"]
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        boundary = content_type.split("boundary=")[1].encode()
        body = request.body
        self.assertIn(b'name="prompt"', body)
        self.assertIn(b'name="image"; filename="ref.png"', body)
        self.assertIn(b"Content-Type: image/png", body)
        self.assertIn(REFERENCE.data, body)
        self.assertTrue(body.rstrip().endswith(b"--" + boundary + b"--"))

    def test_reference_is_never_placeholder(self):
        adapter = scripted_openai([MockScenario(shape="openai")])
        adapter.generate(GenerationRequest(prompt="x", reference_image=REFERENCE,
                                           aspect_ratio="1:1", resolution_tier="1K"))
        self.assertIn(REFERENCE.data, adapter.trace[0].body)


class TestGeminiGeneration(unittest.TestCase):
    def test_wire_shape(self):
        adapter = scripted_gemini([MockScenario(shape="gemini")])
        adapter.generate(GenerationRequest(prompt="一只杯子", aspect_ratio="3:4",
                                           resolution_tier="2K"))
        request = adapter.trace[0]
        self.assertEqual(request.url,
                         "https://generativelanguage.example.com/v1beta/models/nano-banana:generateContent")
        self.assertEqual(request.headers["x-goog-api-key"], "TESTSECRET")
        self.assertNotIn("key=", request.url)      # 密钥不进 URL（SPEC 01 §9.5）
        payload = json.loads(request.body.decode("utf-8"))
        parts = payload["contents"][0]["parts"]
        self.assertEqual(parts[0]["text"], "一只杯子")
        self.assertEqual(payload["generationConfig"]["imageConfig"]["aspectRatio"], "3:4")
        self.assertEqual(payload["generationConfig"]["imageConfig"]["imageSize"], "2K")
        self.assertEqual(payload["generationConfig"]["responseModalities"], ["IMAGE"])

    def test_reference_as_inline_data(self):
        import base64

        adapter = scripted_gemini([MockScenario(shape="gemini")])
        adapter.generate(GenerationRequest(prompt="x", aspect_ratio="1:1", resolution_tier="1K",
                                           reference_image=REFERENCE))
        payload = json.loads(adapter.trace[0].body.decode("utf-8"))
        parts = payload["contents"][0]["parts"]
        self.assertEqual(parts[1]["inline_data"]["mime_type"], "image/png")
        self.assertEqual(base64.b64decode(parts[1]["inline_data"]["data"]), REFERENCE.data)

    def test_parses_inline_data(self):
        adapter = scripted_gemini([MockScenario(shape="gemini", image_bytes=fake_png((48, 48)))])
        result = adapter.generate(GenerationRequest(prompt="x", aspect_ratio="1:1"))
        self.assertTrue(result.image_bytes.startswith(b"\x89PNG"))

    def test_prompt_feedback_blocked(self):
        adapter = scripted_gemini([MockScenario(
            body_text=json.dumps({"promptFeedback": {"blockReason": "SAFETY"}}),
            headers={"Content-Type": "application/json"})])
        with self.assertRaises(IVError) as ctx:
            adapter.generate(GenerationRequest(prompt="x", aspect_ratio="1:1"))
        self.assertEqual(ctx.exception.code, "MODEL_REFUSAL")

    def test_no_image_part(self):
        adapter = scripted_gemini([MockScenario(
            body_text=json.dumps({"candidates": [{"finishReason": "STOP", "content": {"parts": [
                {"text": "抱歉，我不能生成该图片"}]}}]}),
            headers={"Content-Type": "application/json"})])
        with self.assertRaises(IVError) as ctx:
            adapter.generate(GenerationRequest(prompt="x", aspect_ratio="1:1"))
        self.assertEqual(ctx.exception.code, "IMAGE_VALIDATION_FAILED")

    def test_unsupported_ratio_blocked_before_call(self):
        adapter = scripted_gemini([MockScenario(shape="gemini")],
                                  supported_ratios=["1:1", "3:4"])
        with self.assertRaises(IVError) as ctx:
            adapter.generate(GenerationRequest(prompt="x", aspect_ratio="9:16"))
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_RATIO")
        self.assertEqual(adapter.trace, [])

    def test_safety_finish_reason_is_refusal(self):
        adapter = scripted_gemini([MockScenario(
            body_text=json.dumps({"candidates": [{"finishReason": "SAFETY",
                                                  "content": {"parts": []}}]}),
            headers={"Content-Type": "application/json"})])
        with self.assertRaises(IVError) as ctx:
            adapter.generate(GenerationRequest(prompt="x", aspect_ratio="1:1"))
        self.assertEqual(ctx.exception.code, "MODEL_REFUSAL")


class TestScenarioHelpers(unittest.TestCase):
    def test_openai_and_gemini_shapes_differ(self):
        openai_body = json.loads(MockScenario(shape="openai").to_response().body.decode())
        gemini_body = json.loads(MockScenario(shape="gemini").to_response().body.decode())
        self.assertIn("data", openai_body)
        self.assertIn("candidates", gemini_body)

    def test_transport_records_every_call(self):
        transport = MockTransport([MockScenario()])
        adapter = openai_stub(transport)
        adapter.generate(GenerationRequest(prompt="a", aspect_ratio="1:1"))
        adapter.generate(GenerationRequest(prompt="b", aspect_ratio="1:1"))
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(len(adapter.trace), 2)


def openai_stub(transport):
    capability = default_capability(provider_type="openai_image_compatible")
    delegate = OpenAIImageAdapter(capability=capability, base_url="https://api.example.com/v1",
                                  secret_ref="env:IV_TEST_KEY", model_id="gpt-image-1",
                                  secret_resolver=lambda name: "TESTSECRET")

    class Stub(ScriptedAdapter):
        def __init__(self):
            super().__init__(capability=capability, delegate=delegate, scenarios=[])

    stub = Stub()
    stub._transport = transport
    return stub


if __name__ == "__main__":
    unittest.main(verbosity=2)
