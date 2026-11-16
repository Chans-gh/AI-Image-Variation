"""GPT Image 兼容适配器（openai_image_compatible / 1.0）。

字段映射见 references/provider-compat.md §4.1：
    - 无参考图 → POST {base}/images/generations，JSON `{model, prompt, size, ...}`
    - 有参考图 → POST {base}/images/edits，multipart（image 文件 + 参数）
    - 响应支持 `b64_json` 与 `url` 两种形态；URL 仅作为下载入口，最终以字节为准
    - 1K/2K/4K **不是**官方的 resolution 参数，这里显式折算成宽×高字符串
"""
from __future__ import annotations

import base64
import json
import uuid

from shared.errors import IVError

from . import GenerationResult
from .base import ProviderAdapter
from .transport import TransportRequest, TransportResponse


def tier_to_size(aspect_ratio: str, board_side_px: int) -> str:
    """按整板长边与比例折算宽×高字符串（不做任何静默裁剪）。"""
    num, den = (int(x) for x in aspect_ratio.split(":"))
    if num >= den:
        width = board_side_px
        height = max(1, round(board_side_px * den / num))
    else:
        height = board_side_px
        width = max(1, round(board_side_px * num / den))
    return f"{width}x{height}"


class OpenAIImageAdapter(ProviderAdapter):
    provider_type = "openai_image_compatible"

    def build_request(self, request) -> TransportRequest:
        tier = request.resolution_tier or "1K"
        size = tier_to_size(request.aspect_ratio or "1:1", self.capability.board_side(tier))
        if request.reference_image is not None:
            return self._edit_request(request, size)
        body = json.dumps({
            "model": self.model_id,
            "prompt": request.prompt,
            "n": 1,
            "size": size,
            "response_format": "b64_json",
        }).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "image/png",
            **self.auth_headers(),
        }
        return TransportRequest("POST", f"{self.base_url.rstrip('/')}/images/generations",
                                headers, body, self.timeout_sec)

    def _edit_request(self, request, size: str) -> TransportRequest:
        image = request.reference_image
        boundary = "----iv" + uuid.uuid4().hex
        parts: list[bytes] = []

        def field(name: str, value: str) -> None:
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n"
                .encode("utf-8")
            )

        field("model", self.model_id)
        field("prompt", request.prompt)
        field("n", "1")
        field("size", size)
        parts.append(
            (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
             f"filename=\"{image.filename}\"\r\nContent-Type: {image.media_type}\r\n\r\n").encode("utf-8")
        )
        parts.append(image.data)
        parts.append(b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "image/png",
            **self.auth_headers(),
        }
        return TransportRequest("POST", f"{self.base_url.rstrip('/')}/images/edits",
                                headers, b"".join(parts), self.timeout_sec)

    def parse_response(self, response: TransportResponse, request):
        content_type = (response.header("Content-Type") or "").lower()
        if content_type.startswith("image/"):
            payload, media = self.decode_image(response.body, content_type)
            return GenerationResult(payload, media, request.resolution_tier, self.adapter_version)
        try:
            data = json.loads(response.text() or "{}")
        except json.JSONDecodeError as exc:
            raise IVError("IMAGE_VALIDATION_FAILED", f"上游响应不是 JSON/图片: {exc}") from exc
        items = data.get("data") or []
        if not items:
            raise IVError("IMAGE_VALIDATION_FAILED", "上游响应没有 data[0]（无图片）")
        first = items[0]
        if first.get("b64_json"):
            try:
                raw = base64.b64decode(first["b64_json"])
            except (KeyError, ValueError) as exc:
                raise IVError("IMAGE_VALIDATION_FAILED", f"b64_json 解码失败: {exc}") from exc
            payload, media = self.decode_image(raw)
            return GenerationResult(payload, media, request.resolution_tier, self.adapter_version,
                                    provider_raw_meta={"revised_prompt": first.get("revised_prompt")})
        if first.get("url"):
            raise IVError("IMAGE_VALIDATION_FAILED",
                          "上游仅返回 URL：需由下载器取回字节后再判定（本适配器不把 URL 当最终引用）")
        raise IVError("IMAGE_VALIDATION_FAILED", "上游响应既无 b64_json 也无 url")
