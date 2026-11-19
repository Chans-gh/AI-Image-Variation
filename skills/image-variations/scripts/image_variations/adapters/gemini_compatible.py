import base64
import re

from ..errors import VariationError, require
from ..imaging import read_image
from .base import GeneratedImage, Provider, require_ready
from .network import SecureTransport, json_response, validate_url
from .openai_compatible import decode_image


class GeminiCompatible(Provider):
    def __init__(self, base_url, api_key, transport=None, api_revision=None):
        validate_url(base_url)
        require(bool(api_key), "AUTH_FAILED", "缺少选定 Provider 的本地密钥")
        self.base_url, self._key = base_url.rstrip("/"), api_key
        self.transport, self.api_revision = transport or SecureTransport(), api_revision

    def generate(self, prompt, plan, reference_bytes=None):
        require_ready(plan)
        model, route = plan["model_id"], plan["api_route"]
        require(bool(re.fullmatch(r"[a-zA-Z0-9_.-]+", model)), "INVALID_INPUT", "型号 ID 包含无效字符")
        params = plan["provider_parameters"]
        tier = params.get("image_size", params.get("imageSize"))
        ratio = params.get("aspect_ratio", params.get("aspectRatio"))
        require(tier and ratio, "MODEL_CAPABILITY_UNSUPPORTED", "Gemini 需要原生档位和比例")
        image_part = None
        if reference_bytes:
            image_part = {
                "mime_type": read_image(reference_bytes)["mime"],
                "data": base64.b64encode(reference_bytes).decode(),
            }
        headers = {"x-goog-api-key": self._key}
        if route == "generateContent":
            parts = [{"text": prompt}]
            if image_part:
                parts.append(
                    {"inlineData": {"mimeType": image_part["mime_type"], "data": image_part["data"]}}
                )
            body = {
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {
                    "responseModalities": ["TEXT", "IMAGE"],
                    "imageConfig": {"imageSize": tier, "aspectRatio": ratio},
                },
            }
            url = self.base_url + f"/models/{model}:generateContent"
        elif route == "interactions":
            parts = [{"type": "text", "text": prompt}]
            if image_part:
                parts.append({"type": "image", **image_part})
            body = {
                "model": model,
                "input": parts,
                "response_format": {"type": "image", "image_size": tier, "aspect_ratio": ratio},
            }
            url = self.base_url + "/interactions"
            if self.api_revision:
                require(
                    bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.api_revision)),
                    "INVALID_INPUT",
                    "API revision 须为日期",
                )
                headers["Api-Revision"] = self.api_revision
        else:
            raise VariationError("MODEL_CAPABILITY_UNSUPPORTED", "未实现的 Gemini API 路由")
        result = json_response(self.transport, url, headers, body)
        if result.get("promptFeedback", {}).get("blockReason"):
            raise VariationError("MODEL_REFUSAL", "模型拒绝该内容")
        images = []
        if route == "generateContent":
            for candidate in result.get("candidates", []):
                if candidate.get("finishReason") in {"SAFETY", "IMAGE_SAFETY", "PROHIBITED_CONTENT"}:
                    raise VariationError("MODEL_REFUSAL", "模型拒绝该内容")
                for part in candidate.get("content", {}).get("parts", []):
                    image = part.get("inlineData", part.get("inline_data"))
                    if image and image.get("data"):
                        images.append((image["data"], image.get("mimeType", image.get("mime_type"))))
        else:
            # Different Interactions revisions use outputs or typed step content.
            blocks = list(result.get("outputs", []))
            for step in result.get("steps", []):
                content = step.get("content", [])
                blocks.extend(content if isinstance(content, list) else [content])
            if result.get("output_image"):
                blocks.append({"type": "image", **result["output_image"]})
            for block in blocks:
                if block.get("type") == "image" and block.get("data"):
                    images.append((block["data"], block.get("mime_type")))
        require(len(images) == 1, "PROVIDER_RESPONSE_INVALID", "响应须包含一张图片")
        data, mime = decode_image(images[0][0]), images[0][1]
        read_image(data, mime=mime)
        return GeneratedImage(data, mime, {"route_used": route})
