"""Phase 2 测试：错误归一、重试语义、密钥与 Endpoint 安全。

运行：.venv/bin/python skills/image-variations/tests/test_provider_errors.py
"""
from __future__ import annotations

import http.server
import sys
import threading
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "support"))

import paths  # noqa: E402

from iv_skill.config import load_config  # noqa: E402
from iv_skill.provider.error_map import classify  # noqa: E402
from iv_skill.provider.mock import (  # noqa: E402
    MockAdapter,
    MockScenario,
    MockTransport,
    default_capability,
    fake_png,
)
from iv_skill.provider.net import validate_endpoint, validate_redirect  # noqa: E402
from iv_skill.provider.registry import build_adapter  # noqa: E402
from iv_skill.provider.retry import Budget, RetryPolicy  # noqa: E402
from iv_skill.provider.transport import (  # noqa: E402
    TransportRequest,
    UrllibTransport,
    ValidatingRedirectHandler,
    resolve_secret,
)
from iv_skill.provider import GenerationRequest  # noqa: E402
from shared.errors import IVError, is_known  # noqa: E402

CONFIG = load_config()
PUBLIC_RESOLVER = lambda host: ["93.184.216.34"]  # noqa: E731


def make_adapter(scenarios, **capability_overrides):
    capability = default_capability(**capability_overrides)
    return MockAdapter(capability=capability, scenarios=scenarios)


class TestErrorMapping(unittest.TestCase):
    """provider-compat §5 的映射表逐条验证。"""

    def test_auth(self):
        for status in (401, 403):
            with self.subTest(status=status):
                mapped = classify(status, "unauthorized")
                self.assertEqual(mapped.code, "AUTH_FAILED")
                self.assertFalse(mapped.retryable)

    def test_rate_limited_with_retry_after(self):
        mapped = classify(429, "slow down", retry_after="5")
        self.assertEqual(mapped.code, "RATE_LIMITED")
        self.assertTrue(mapped.retryable)
        self.assertEqual(mapped.retry_after, 5.0)

    def test_timeout(self):
        self.assertEqual(classify(None, "", network_error="ReadTimeout").code, "PROVIDER_TIMEOUT")
        self.assertTrue(classify(None, "", network_error="ReadTimeout").retryable)
        self.assertEqual(classify(504, "").code, "PROVIDER_TIMEOUT")

    def test_refusal(self):
        for body in ("content policy violation", "moderation flagged", "safety blocked"):
            with self.subTest(body=body):
                mapped = classify(400, body)
                self.assertEqual(mapped.code, "MODEL_REFUSAL")
                self.assertFalse(mapped.retryable)

    def test_ratio(self):
        mapped = classify(400, "unsupported aspect_ratio 5:3")
        self.assertEqual(mapped.code, "UNSUPPORTED_RATIO")
        self.assertFalse(mapped.retryable)

    def test_resolution(self):
        mapped = classify(400, "image_size 8K is not supported")
        self.assertEqual(mapped.code, "UNSUPPORTED_RESOLUTION")

    def test_reference(self):
        mapped = classify(400, "reference image input not supported for this model")
        self.assertEqual(mapped.code, "REFERENCE_REQUIRED")

    def test_format(self):
        mapped = classify(400, "unsupported mime type")
        self.assertEqual(mapped.code, "IMAGE_VALIDATION_FAILED")

    def test_unknown_param_is_capability(self):
        mapped = classify(400, "unknown parameter foo")
        self.assertEqual(mapped.code, "MODEL_CAPABILITY_UNSUPPORTED")

    def test_all_mapped_codes_registered(self):
        samples = [
            classify(401, ""), classify(429, ""), classify(None, "", network_error="x"),
            classify(400, "policy"), classify(400, "aspect_ratio"), classify(400, "size"),
            classify(400, "reference"), classify(400, "mime"), classify(400, "other"),
            classify(500, ""), classify(418, ""),
        ]
        for mapped in samples:
            self.assertTrue(is_known(mapped.code), mapped.code)


class TestAdapterErrorFlow(unittest.TestCase):
    def test_auth_fails_without_retry(self):
        adapter = make_adapter([MockScenario(name="auth", status=401, body_text="unauthorized")])
        with self.assertRaises(IVError) as ctx:
            adapter.generate(GenerationRequest(prompt="x"))
        self.assertEqual(ctx.exception.code, "AUTH_FAILED")
        self.assertEqual(len(adapter.trace), 1)

    def test_rate_limited_then_success(self):
        adapter = make_adapter([
            MockScenario(name="429", status=429, body_text="slow down",
                         headers={"Retry-After": "3"}),
            MockScenario(name="ok", image_bytes=fake_png()),
        ])
        sleeps: list[float] = []
        result = adapter.generate(GenerationRequest(prompt="x"), sleep=sleeps.append)
        self.assertTrue(result.image_bytes.startswith(b"\x89PNG"))
        self.assertEqual(len(adapter.trace), 2)
        self.assertEqual(sleeps, [3.0])

    def test_retry_budget_exhausted(self):
        scenarios = [MockScenario(name=f"429_{i}", status=429, body_text="slow down")
                     for i in range(5)]
        adapter = make_adapter(scenarios)
        with self.assertRaises(IVError) as ctx:
            adapter.generate(GenerationRequest(prompt="x"), sleep=lambda _: None)
        self.assertEqual(ctx.exception.code, "RATE_LIMITED")
        policy = RetryPolicy.from_config(CONFIG)
        self.assertEqual(len(adapter.trace), policy.max_extra_retries + 1)

    def test_corrupt_image_is_retryable(self):
        adapter = make_adapter([
            MockScenario(name="corrupt", image_bytes=b"not-a-png"),
            MockScenario(name="ok", image_bytes=fake_png()),
        ])
        result = adapter.generate(GenerationRequest(prompt="x"), sleep=lambda _: None)
        self.assertTrue(result.image_bytes.startswith(b"\x89PNG"))
        self.assertEqual(len(adapter.trace), 2)

    def test_budget_blocks_before_extra_call(self):
        scenarios = [MockScenario(status=429, body_text="slow down") for _ in range(4)]
        adapter = make_adapter(scenarios)
        budget = Budget(limit=1, planned=3, cost_per_call=CONFIG["budget"]["cost_per_call"])
        with self.assertRaises(IVError) as ctx:
            adapter.generate(GenerationRequest(prompt="x"), budget=budget, sleep=lambda _: None)
        self.assertEqual(ctx.exception.code, "BUDGET_EXCEEDED")
        self.assertEqual(len(adapter.trace), 1)

    def test_result_records_tier_and_adapter_version(self):
        adapter = make_adapter([MockScenario(image_bytes=fake_png())])
        result = adapter.generate(GenerationRequest(prompt="x", resolution_tier="2K"))
        self.assertEqual(result.requested_tier, "2K")
        self.assertEqual(result.adapter_version, "mock/1.0")

    def test_request_carries_reference_when_present(self):
        import json as _json

        adapter = make_adapter([MockScenario(image_bytes=fake_png())])
        from iv_skill.provider import ReferenceImage

        request = GenerationRequest(prompt="x",
                                    reference_image=ReferenceImage(fake_png((64, 64)), "image/png", "r.png"))
        adapter.generate(request)
        payload = _json.loads(adapter.trace[0].body.decode("utf-8"))
        self.assertTrue(payload["uses_reference"])
        self.assertGreater(payload["reference_bytes"], 0)

    def test_empty_prompt_blocked(self):
        with self.assertRaises(IVError) as ctx:
            GenerationRequest(prompt="   ")
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")


class TestSecretHandling(unittest.TestCase):
    def test_plaintext_key_rejected(self):
        with self.assertRaises(IVError) as ctx:
            resolve_secret("sk-abcdef123456")
        self.assertEqual(ctx.exception.code, "AUTH_FAILED")

    def test_env_reference_resolved(self):
        self.assertEqual(resolve_secret("env:IV_TEST_KEY", resolver=lambda name: "s3cret"),
                         "s3cret")
        self.assertEqual(resolve_secret("secret://IV_TEST_KEY", resolver=lambda name: "s3cret"),
                         "s3cret")

    def test_unresolved_reference_fails(self):
        with self.assertRaises(IVError) as ctx:
            resolve_secret("secret://MISSING_KEY", resolver=lambda name: None)
        self.assertEqual(ctx.exception.code, "AUTH_FAILED")

    def test_missing_reference_fails(self):
        with self.assertRaises(IVError) as ctx:
            resolve_secret(None)
        self.assertEqual(ctx.exception.code, "AUTH_FAILED")

    def test_error_message_redacts_secret(self):
        adapter = make_adapter([MockScenario(status=400, body_text="unknown parameter")])
        adapter.secret_ref = "env:IV_SECRET_FOR_TEST"
        adapter._secret_resolver = lambda name: "TOPSECRET"
        with self.assertRaises(IVError) as ctx:
            adapter.generate(GenerationRequest(prompt="x"))
        self.assertNotIn("TOPSECRET", str(ctx.exception))
        self.assertIn("MODEL_CAPABILITY_UNSUPPORTED", str(ctx.exception))


class TestEndpointSecurity(unittest.TestCase):
    """AC-22 / D-29：HTTPS-only、SSRF 防护、本地例外。"""

    def test_http_rejected_by_default(self):
        with self.assertRaises(IVError) as ctx:
            validate_endpoint("http://api.example.com/v1", resolver=PUBLIC_RESOLVER)
        self.assertEqual(ctx.exception.code, "MODEL_CAPABILITY_UNSUPPORTED")

    def test_https_public_ok(self):
        self.assertEqual(validate_endpoint("https://api.example.com/v1", resolver=PUBLIC_RESOLVER),
                         "https://api.example.com/v1")

    def test_private_ip_blocked(self):
        for url in ("https://10.0.0.5/v1", "https://192.168.1.10/v1",
                    "https://169.254.169.254/latest/meta-data", "https://[::1]/v1"):
            with self.subTest(url=url):
                with self.assertRaises(IVError):
                    validate_endpoint(url, resolver=PUBLIC_RESOLVER)

    def test_localhost_requires_flag(self):
        with self.assertRaises(IVError):
            validate_endpoint("http://127.0.0.1:8080/v1", resolver=PUBLIC_RESOLVER)
        self.assertEqual(validate_endpoint("http://127.0.0.1:8080/v1", allow_local=True,
                                           resolver=PUBLIC_RESOLVER),
                         "http://127.0.0.1:8080/v1")

    def test_local_flag_does_not_open_other_hosts(self):
        with self.assertRaises(IVError):
            validate_endpoint("http://10.0.0.5/v1", allow_local=True, resolver=PUBLIC_RESOLVER)

    def test_metadata_host_blocked(self):
        with self.assertRaises(IVError):
            validate_endpoint("https://metadata.google.internal/v1", resolver=PUBLIC_RESOLVER)

    def test_dns_to_private_blocked(self):
        with self.assertRaises(IVError):
            validate_endpoint("https://inner.example.com/v1", resolver=lambda host: ["10.1.2.3"])

    def test_credentials_in_url_blocked(self):
        with self.assertRaises(IVError):
            validate_endpoint("https://user:pass@api.example.com/v1", resolver=PUBLIC_RESOLVER)
        with self.assertRaises(IVError):
            validate_endpoint("https://api.example.com/v1?api_key=abc", resolver=PUBLIC_RESOLVER)

    def test_redirect_to_private_blocked(self):
        with self.assertRaises(IVError):
            validate_redirect("https://10.0.0.5/x", original="https://api.example.com/v1",
                              resolver=PUBLIC_RESOLVER)

    def test_redirect_handler_blocks_metadata(self):
        """初始 URL 校验过不了重定向这道口子：169.254.169.254 必须在跟随之被拦下。"""
        handler = ValidatingRedirectHandler(allow_local=False)
        req = urllib.request.Request("https://api.example.com/v1/images")
        with self.assertRaises(IVError) as ctx:
            handler.redirect_request(req, None, 302, "Found", {},
                                     "http://169.254.169.254/latest/meta-data/")
        self.assertEqual(ctx.exception.code, "MODEL_CAPABILITY_UNSUPPORTED")

    def test_redirect_handler_blocks_private_even_with_local_flag(self):
        """allow_local 只放行 127.0.0.1/localhost，不能顺手放行整个私网。"""
        handler = ValidatingRedirectHandler(allow_local=True)
        req = urllib.request.Request("http://127.0.0.1:8000/v1")
        with self.assertRaises(IVError):
            handler.redirect_request(req, None, 302, "Found", {}, "http://10.0.0.5/v1")

    def test_redirect_handler_blocks_plain_http_cross_host(self):
        handler = ValidatingRedirectHandler(allow_local=False)
        req = urllib.request.Request("https://api.example.com/v1")
        with self.assertRaises(IVError):
            handler.redirect_request(req, None, 302, "Found", {},
                                     "http://api.other.example/v1")

    def test_build_external_adapter_validates_url(self):
        from iv_skill.provider.openai_compat import OpenAIImageAdapter
        from iv_skill.provider.mock import DEFAULT_CAPABILITY_DECLARATION
        from iv_skill.provider.capability import ModelCapability

        with self.assertRaises(IVError) as ctx:
            build_adapter(provider_type="openai_image_compatible",
                          capability_declaration=dict(DEFAULT_CAPABILITY_DECLARATION),
                          base_url="http://api.example.com/v1")
        self.assertEqual(ctx.exception.code, "MODEL_CAPABILITY_UNSUPPORTED")



class TestTransportRedirectIntegration(unittest.TestCase):
    """用本地回环服务器验证：传输层真的走了校验，而不是只测了校验函数。"""

    @classmethod
    def setUpClass(cls):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/ok":
                    body = b"ok"
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/to-private":
                    self.send_response(302)
                    self.send_header("Location", "http://10.0.0.5/secret")
                    self.end_headers()
                elif self.path == "/to-local-ok":
                    self.send_response(302)
                    self.send_header("Location", f"/ok")
                    self.end_headers()
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args):
                pass

        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_plain_200_passes(self):
        transport = UrllibTransport(allow_local=True)
        response = transport(TransportRequest(method="GET", url=f"{self.base}/ok"))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b"ok")

    def test_same_host_redirect_followed_when_local_allowed(self):
        transport = UrllibTransport(allow_local=True)
        response = transport(TransportRequest(method="GET", url=f"{self.base}/to-local-ok"))
        self.assertEqual(response.status, 200, "同源重定向不该被误杀")

    def test_redirect_to_private_is_blocked(self):
        transport = UrllibTransport(allow_local=True)
        with self.assertRaises(IVError) as ctx:
            transport(TransportRequest(method="GET", url=f"{self.base}/to-private"))
        self.assertEqual(ctx.exception.code, "MODEL_CAPABILITY_UNSUPPORTED")

    def test_local_redirect_denied_without_flag(self):
        """allow_local 必须真的传进重定向校验，否则这道防线等于没接上。"""
        transport = UrllibTransport(allow_local=False)
        with self.assertRaises(IVError):
            transport(TransportRequest(method="GET", url=f"{self.base}/to-local-ok"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
