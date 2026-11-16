"""Mock Provider：无真实密钥时打通全链路，并驱动各类失败场景。

设计：
    - 走**同一个** `ProviderAdapter.generate` 流程（请求组装、错误归一、重试、预算），
      只在传输层替换为脚本化响应，因此测出来的行为与真实适配器一致；
    - 可指定上游响应形状（`openai` / `gemini` / `raw`），用于验证两个适配器的解析分支；
    - 记录每一次 TransportRequest，供测试断言"确实携带了参考图""确实是 N 次独立请求"。
"""
from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass, field
from typing import Callable, Sequence

from PIL import Image

from .base import ProviderAdapter
from .capability import ModelCapability
from .transport import TransportRequest, TransportResponse


def fake_png(size: tuple[int, int] = (1024, 1024), color: str = "#8FA9C4") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


@dataclass
class MockScenario:
    """一次脚本化上游响应。"""

    name: str = "success"
    status: int | None = 200
    shape: str = "raw"                      # raw | openai | gemini
    image_bytes: bytes | None = None
    body_text: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    network_error: str | None = None

    def to_response(self) -> TransportResponse:
        headers = dict(self.headers)
        if self.network_error is not None:
            return TransportResponse(status=None, headers=headers, network_error=self.network_error)
        if self.status != 200:
            return TransportResponse(status=self.status, headers=headers,
                                     body=self.body_text.encode("utf-8"))
        if self.body_text:
            # 显式文本响应优先：用于构造"无图片/仅 URL/被拒绝"等畸形上游响应
            headers.setdefault("Content-Type", "application/json")
            return TransportResponse(status=200, headers=headers,
                                     body=self.body_text.encode("utf-8"))
        if self.shape == "raw":
            headers.setdefault("Content-Type", "image/png")
            return TransportResponse(status=200, headers=headers,
                                     body=self.image_bytes if self.image_bytes is not None else fake_png())
        encoded = base64.b64encode(self.image_bytes if self.image_bytes is not None else fake_png()).decode()
        if self.shape == "openai":
            headers.setdefault("Content-Type", "application/json")
            body = {"data": [{"b64_json": encoded}]}
        else:
            headers.setdefault("Content-Type", "application/json")
            body = {"candidates": [{"content": {"parts": [
                {"inlineData": {"mimeType": "image/png", "data": encoded}}]}}]}
        return TransportResponse(status=200, headers=headers,
                                 body=json.dumps(body).encode("utf-8"))


class MockTransport:
    """脚本化传输：按顺序返回 Scenario，并记录收到的请求。"""

    def __init__(self, scenarios: Sequence[MockScenario] | None = None,
                 default: MockScenario | None = None) -> None:
        self.scenarios = list(scenarios or [])
        self.default = default or MockScenario()
        self.requests: list[TransportRequest] = []

    def __call__(self, request: TransportRequest) -> TransportResponse:
        self.requests.append(request)
        index = len(self.requests) - 1
        if index < len(self.scenarios):
            scenario = self.scenarios[index]
        elif self.scenarios:
            scenario = self.scenarios[-1]      # 重试默认重复最后一个脚本，避免"意外成功"
        else:
            scenario = self.default
        return scenario.to_response()


class MockAdapter(ProviderAdapter):
    """Mock Provider 适配器：与 openai/gemini 适配器同构。

    只产出 raw 图片字节；若要验证 openai/gemini 的**解析分支**，
    用 ScriptedAdapter 组合真实适配器的 parse_response（见 fakes.py）。
    """

    provider_type = "mock"

    def __init__(self, *, capability: ModelCapability, scenarios: Sequence[MockScenario] | None = None,
                 **kwargs) -> None:
        transport = kwargs.pop("transport", None) or MockTransport(scenarios)
        super().__init__(capability=capability, base_url="", transport=transport, **kwargs)

    @property
    def transport(self) -> MockTransport:
        return self._transport  # type: ignore[return-value]

    @property
    def trace(self) -> list[TransportRequest]:
        return list(self.transport.requests)

    def build_request(self, request) -> TransportRequest:
        payload = {
            "kind": "mock",
            "prompt": request.prompt,
            "aspect_ratio": request.aspect_ratio,
            "resolution_tier": request.resolution_tier,
            "purpose": request.purpose,
            "frame_index": request.frame_index,
            "uses_reference": request.uses_reference,
            "reference_bytes": (request.reference_image.byte_size
                                if request.reference_image is not None else 0),
        }
        headers = {"Content-Type": "application/json"}
        if self.secret_ref:
            headers.update(self.auth_headers())
        return TransportRequest("POST", "mock://generate", headers,
                                json.dumps(payload).encode("utf-8"), self.timeout_sec)

    def parse_response(self, response: TransportResponse, request):
        payload, media = self.decode_image(response.body, response.header("Content-Type"))
        from . import GenerationResult

        return GenerationResult(payload, media, request.resolution_tier, self.adapter_version)


class ScriptedAdapter(ProviderAdapter):
    """把真实适配器的 build/parse 逻辑与脚本化传输组合，用于验证协议解析。"""

    provider_type = "scripted"

    def __init__(self, *, capability: ModelCapability, delegate: ProviderAdapter,
                 scenarios: Sequence[MockScenario]) -> None:
        super().__init__(capability=capability, base_url="",
                         transport=MockTransport(scenarios), model_id=capability.model_id)
        self.delegate = delegate

    @property
    def transport(self) -> MockTransport:
        return self._transport  # type: ignore[return-value]

    @property
    def trace(self) -> list[TransportRequest]:
        return list(self.transport.requests)

    def build_request(self, request) -> TransportRequest:
        return self.delegate.build_request(request)

    def parse_response(self, response: TransportResponse, request):
        return self.delegate.parse_response(response, request)


DEFAULT_CAPABILITY_DECLARATION = {
    "text_to_image": True,
    "reference_image_input": True,
    "image_edit": True,
    "multiple_independent_requests": True,
    "supported_ratios": ["1:1", "3:4", "4:3", "16:9", "9:16"],
    "supported_resolution_tiers": ["1K", "2K", "4K"],
    "tier_to_board_side_px": {"1K": 1024, "2K": 2048, "4K": 4096},
    "custom_dimensions": False,
    "supports_async_result": False,
    "max_input_bytes": 10485760,
    "response_image_formats": ["png", "jpeg", "webp"],
    "distinguishable_errors": True,
    "adapter_version": "mock/1.0",
    "provider_type": "mock",
    "model_id": "mock-provider-v1",
}


def default_capability(**overrides) -> ModelCapability:
    declaration = dict(DEFAULT_CAPABILITY_DECLARATION)
    declaration.update(overrides)
    return ModelCapability.from_declaration(declaration)
