"""Gemini Nano Banana 兼容适配器（gemini_image_compatible / 1.0）。

字段映射见 references/provider-compat.md §4.2：
    - POST {base}/models/{model}:generateContent?key=...（官方形态）
    - 文本 part + 可选 inline_data 图片 part
    - 档位通过 generationConfig.imageConfig.aspectRatio / imageSize 表达
    - 从 candidates[0].content.parts[].inlineData.data（base64）解析字节
"""
from __future__ import annotations

import base64
import json

from shared.errors import IVError

from . import GenerationResult
from .base import ProviderAdapter
from .transport import TransportRequest, TransportResponse

TIER_TO_IMAGE_SIZE = {"1K": "1K", "2K": "2K", "4K": "4K"}


class GeminiImageAdapter(ProviderAdapter):
    provider_type = "gemini_image_compatible"

    def build_request(self, request) -> TransportRequest:
        tier = request.resolution_tier or "1K"
        if request.aspect_ratio and not self.capability.supports_ratio(request.aspect_ratio):
            raise IVError("UNSUPPORTED_RATIO",
                          f"模型不支持比例 {request.aspect_ratio}，可用 {list(self.capability.supported_ratios)}")
        parts: list[dict] = [{"text": request.prompt}]
        if request.reference_image is not None:
            parts.append({
                "inline_data": {
                    "mime_type": request.reference_image.media_type,
                    "data": base64.b64encode(request.reference_image.data).decode("ascii"),
                }
            })
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {
                    "aspectRatio": request.aspect_ratio or "1:1",
                    "imageSize": TIER_TO_IMAGE_SIZE.get(tier, "1K"),
                },
            },
        }
        url = f"{self.base_url.rstrip('/')}/models/{self.model_id}:generateContent"
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "x-goog-api-key": self._secret}
        return TransportRequest("POST", url, headers,
                                json.dumps(payload).encode("utf-8"), self.timeout_sec)

    def parse_response(self, response: TransportResponse, request):
        content_type = (response.header("Content-Type") or "").lower()
        if content_type.startswith("image/"):
            payload, media = self.decode_image(response.body, content_type)
            return GenerationResult(payload, media, request.resolution_tier, self.adapter_version)
        try:
            data = json.loads(response.text() or "{}")
        except json.JSONDecodeError as exc:
            raise IVError("IMAGE_VALIDATION_FAILED", f"上游响应不是 JSON/图片: {exc}") from exc

        candidates = data.get("candidates") or []
        if not candidates:
            blocked = (data.get("promptFeedback") or {}).get("blockReason")
            if blocked:
                raise IVError("MODEL_REFUSAL", f"内容被拒绝: {blocked}")
            raise IVError("IMAGE_VALIDATION_FAILED", "上游响应没有 candidates（无图片）")
        for part in candidates[0].get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if not inline or not inline.get("data"):
                continue
            try:
                raw = base64.b64decode(inline["data"])
            except ValueError as exc:
                raise IVError("IMAGE_VALIDATION_FAILED", f"inlineData 解码失败: {exc}") from exc
            payload, media = self.decode_image(raw, inline.get("mimeType") or inline.get("mime_type"))
            return GenerationResult(payload, media, request.resolution_tier, self.adapter_version)
        finish = candidates[0].get("finishReason")
        if finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
            raise IVError("MODEL_REFUSAL", f"内容被拒绝: finishReason={finish}")
        raise IVError("IMAGE_VALIDATION_FAILED", f"响应中没有图片 part（finishReason={finish}）")
