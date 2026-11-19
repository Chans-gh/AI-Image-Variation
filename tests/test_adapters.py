import base64
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
from image_variations.adapters.gemini_compatible import GeminiCompatible
from image_variations.adapters.host import CodexSkillProvider
from image_variations.adapters.network import SecureTransport, public_addresses, validate_url
from image_variations.adapters.openai_compatible import OpenAICompatible
from image_variations.errors import VariationError
from image_variations.imaging import png_bytes, sha256
from PIL import Image


class Transport:
    def __init__(self, response):
        self.response, self.calls = response, []

    def request(self, method, url, headers=None, body=None):
        self.calls.append((method, url, headers or {}, body))
        if isinstance(self.response, tuple):
            return self.response
        return json.dumps(self.response).encode(), "application/json; charset=utf-8"


def sample():
    return png_bytes(Image.new("RGB", (96, 128), "blue"))


def test_codex_skill_2_6_uses_bound_proxy_and_attaches_fixed_reference(tmp_path, monkeypatch):
    script = tmp_path / "generate.mjs"
    script.write_text("// pinned local tool")
    proxy = "http://127.0.0.1:10100/v1"
    adapter = CodexSkillProvider(script, sha256(script.read_bytes()), quality="xhigh", base_url=proxy)
    reference = sample()
    model = "gpt-image-2.6-sunburst"
    plan = {
        "status": "ready",
        "model_id": model,
        "api_route": "codex.images.generations",
        "reference_api_route": "codex.images.edits",
        "provider_parameters": {"size": "96x128"},
    }
    monkeypatch.delenv("CODEX_IMAGE_BASE_URL", raising=False)

    def run(args, *, env, **kwargs):
        assert args[:2] == ["node", str(script)]
        assert args[args.index("--image-model") + 1] == model
        assert args[args.index("--size") + 1] == "96x128"
        assert args[args.index("--quality") + 1] == "xhigh"
        assert Path(args[args.index("--reference-image") + 1]).read_bytes() == reference
        assert env["CODEX_IMAGE_BASE_URL"] == proxy
        output = Path(args[args.index("--out-dir") + 1]) / "image.png"
        output.write_bytes(reference)
        payload = {
            "ok": True,
            "path": str(output),
            "route": "images/edits",
            "requestedImageModel": model,
            "reportedImageModel": None,
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))

    monkeypatch.setattr("image_variations.adapters.host.subprocess.run", run)
    result = adapter.generate("same subject, new pose", plan, reference)
    assert result.data == reference
    assert result.public_metadata["route_used"] == "codex.images.edits"


def test_codex_skill_propagates_native_dimension_failure(tmp_path, monkeypatch):
    script = tmp_path / "generate.mjs"
    script.write_text("// pinned local tool")
    adapter = CodexSkillProvider(script, sha256(script.read_bytes()), quality="high")
    monkeypatch.delenv("CODEX_IMAGE_BASE_URL", raising=False)
    payload = {
        "ok": False,
        "code": "NATIVE_RESOLUTION_VALIDATION_FAILED",
        "actualWidth": 1672,
        "actualHeight": 941,
        "reportedQuality": "low",
        "unsafe_error": "do not echo raw provider text",
    }
    monkeypatch.setattr(
        "image_variations.adapters.host.subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=2, stdout=json.dumps(payload)),
    )
    plan = {
        "status": "ready",
        "model_id": "gpt-image-2.6-sunburst",
        "api_route": "codex.images.generations",
        "provider_parameters": {"size": "3840x2160"},
    }
    with pytest.raises(VariationError) as error:
        adapter.generate("a cube", plan)
    assert error.value.code == "NATIVE_RESOLUTION_VALIDATION_FAILED"
    assert error.value.details["actual_native_dimensions"] == {"width": 1672, "height": 941}
    assert error.value.details["reported_quality"] == "low"
    assert "unsafe_error" not in error.value.details


def test_openai_generation_and_multipart_edit_attach_real_bytes():
    image = sample()
    transport = Transport({"data": [{"b64_json": base64.b64encode(image).decode()}]})
    adapter = OpenAICompatible("https://public.example/v1", "fake-test-key", transport)
    plan = {
        "status": "ready",
        "model_id": "test-model",
        "api_route": "images/generations",
        "reference_api_route": "images/edits",
        "provider_parameters": {"size": "96x128"},
    }
    assert adapter.generate("prompt", plan).data == image
    body = json.loads(transport.calls[0][3])
    assert body["n"] == 1 and body["size"] == "96x128" and "quality" not in body
    assert adapter.generate("prompt", plan, image).data == image
    call = transport.calls[1]
    assert call[1].endswith("/images/edits")
    assert (
        image in call[3] and b'name="image"' in call[3] and "multipart/form-data" in call[2]["Content-Type"]
    )


def test_nonready_dimension_plan_sends_no_request():
    transport = Transport({"data": []})
    adapter = OpenAICompatible("https://public.example/v1", "fake-test-key", transport)
    with pytest.raises(VariationError) as error:
        adapter.generate("prompt", {"status": "unverified"})
    assert error.value.code == "MODEL_CAPABILITY_UNSUPPORTED" and not transport.calls


def test_provider_rewritten_prompt_is_hash_only_metadata():
    transport = Transport(
        {
            "data": [
                {
                    "b64_json": base64.b64encode(sample()).decode(),
                    "revised_prompt": "private-user-description",
                }
            ]
        }
    )
    adapter = OpenAICompatible("https://public.example/v1", "fake-test-key", transport)
    result = adapter.generate(
        "prompt",
        {
            "status": "ready",
            "model_id": "test-model",
            "api_route": "images/generations",
            "provider_parameters": {"size": "96x128"},
        },
    )
    assert len(result.public_metadata["returned_prompt_sha256"]) == 64
    assert "private-user-description" not in json.dumps(result.public_metadata)


@pytest.mark.parametrize("route", ["generateContent", "interactions"])
def test_gemini_route_case_and_reference_mapping(route):
    data = base64.b64encode(sample()).decode()
    response = (
        {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": data}}]}}]}
        if route == "generateContent"
        else {"outputs": [{"type": "image", "mime_type": "image/png", "data": data}]}
    )
    transport = Transport(response)
    adapter = GeminiCompatible("https://public.example/v1beta", "fake-test-key", transport)
    plan = {
        "status": "ready",
        "model_id": "test-model",
        "api_route": route,
        "provider_parameters": {"image_size": "4K", "aspect_ratio": "3:4"},
    }
    assert adapter.generate("prompt", plan, sample()).data == sample()
    request = json.loads(transport.calls[0][3])
    assert "key=" not in transport.calls[0][1]
    if route == "generateContent":
        assert request["generationConfig"]["imageConfig"] == {"imageSize": "4K", "aspectRatio": "3:4"}
        assert "inlineData" in request["contents"][0]["parts"][1]
    else:
        assert request["response_format"] == {"type": "image", "image_size": "4K", "aspect_ratio": "3:4"}
        assert request["input"][1]["type"] == "image"


@pytest.mark.parametrize(
    "response",
    [
        {"data": []},
        {"data": [{"b64_json": "garbage"}]},
        {"data": [{"b64_json": base64.b64encode(b"broken").decode()}]},
    ],
)
def test_broken_image_responses_rejected(response):
    adapter = OpenAICompatible("https://public.example/v1", "fake", Transport(response))
    with pytest.raises(VariationError):
        adapter.generate(
            "prompt",
            {
                "api_route": "images/generations",
                "status": "ready",
                "model_id": "model",
                "provider_parameters": {"size": "96x128"},
            },
        )


def test_wrong_response_mime_and_safety_refusal():
    adapter = GeminiCompatible("https://public.example/v1beta", "fake", Transport((b"{}", "text/html")))
    plan = {
        "api_route": "generateContent",
        "status": "ready",
        "model_id": "model",
        "provider_parameters": {"imageSize": "1K", "aspectRatio": "3:4"},
    }
    with pytest.raises(VariationError) as error:
        adapter.generate("prompt", plan)
    assert error.value.code == "PROVIDER_RESPONSE_INVALID"
    adapter.transport = Transport({"promptFeedback": {"blockReason": "SAFETY"}})
    with pytest.raises(VariationError) as error:
        adapter.generate("prompt", plan)
    assert error.value.code == "MODEL_REFUSAL"


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1", "224.0.0.1"]
)
def test_ssrf_blocks_nonpublic_dns_addresses(monkeypatch, address):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))
        ],
    )
    with pytest.raises(VariationError) as error:
        public_addresses("public-looking.example")
    assert error.value.code == "INVALID_ENDPOINT"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://key:secret@example.com",
        "https://example.com?key=secret",
        "https://example.com/#secret",
    ],
)
def test_credential_urls_rejected(url):
    with pytest.raises(VariationError):
        validate_url(url)


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "AUTH_FAILED", False),
        (403, "AUTH_FAILED", False),
        (429, "RATE_LIMITED", True),
        (503, "PROVIDER_UNAVAILABLE", True),
        (302, "INVALID_ENDPOINT", False),
    ],
)
def test_http_failures_are_safe_and_classified(monkeypatch, status, code, retryable):
    from image_variations.adapters import network

    class Response:
        def __init__(self):
            self.status = status

        def getheader(self, name, default=""):
            return "2" if name == "Retry-After" else default

        def read(self, limit):
            return b'"fake-test-secret"'

    class Connection:
        def __init__(self, *a, **k):
            pass

        def request(self, *a):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(network, "PinnedHTTPS", Connection)
    with pytest.raises(VariationError) as error:
        SecureTransport().request("POST", "https://public.example", {"Authorization": "fake-test-secret"})
    assert error.value.code == code and error.value.retryable == retryable
    assert "fake-test-secret" not in json.dumps(error.value.as_dict())
