import base64
import binascii
import uuid

from ..errors import VariationError, require
from ..imaging import read_image, sha256
from .base import GeneratedImage, Provider, require_ready
from .network import SecureTransport, json_response, validate_url


def decode_image(value):
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError, binascii.Error):
        raise VariationError("PROVIDER_RESPONSE_INVALID", "图像 base64 无效") from None


def multipart(fields, image):
    boundary = "image-variations-" + uuid.uuid4().hex
    chunks = []
    for key, value in fields.items():
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        )
    info = read_image(image)
    mime = info["mime"]
    extension = "jpg" if info["format"] == "jpeg" else info["format"]
    chunks.extend(
        [
            f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="reference.{extension}"\r\nContent-Type: {mime}\r\n\r\n'.encode(),
            image,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class OpenAICompatible(Provider):
    def __init__(self, base_url, api_key, transport=None):
        validate_url(base_url)
        require(bool(api_key), "AUTH_FAILED", "缺少选定 Provider 的本地密钥")
        self.base_url, self._key = base_url.rstrip("/"), api_key
        self.transport = transport or SecureTransport()

    def generate(self, prompt, plan, reference_bytes=None):
        require_ready(plan)
        route = plan.get("reference_api_route") if reference_bytes else plan["api_route"]
        expected = "images/edits" if reference_bytes else "images/generations"
        require(route == expected, "MODEL_CAPABILITY_UNSUPPORTED", "档案未声明该图像生成/编辑路由")
        params = plan["provider_parameters"]
        require(set(params) == {"size"}, "MODEL_CAPABILITY_UNSUPPORTED", "GPT Image 映射须使用 size")
        fields = {"model": plan["model_id"], "prompt": prompt, "n": 1, **params}
        headers = {"Authorization": "Bearer " + self._key}
        if reference_bytes:
            body, mime = multipart(fields, reference_bytes)
            result = json_response(
                self.transport, self.base_url + "/" + route, {**headers, "Content-Type": mime}, body=body
            )
        else:
            result = json_response(self.transport, self.base_url + "/" + route, headers, fields)
        entries = result.get("data")
        require(
            isinstance(entries, list) and len(entries) == 1,
            "PROVIDER_RESPONSE_INVALID",
            "每次请求须返回一张图片",
        )
        entry = entries[0]
        if entry.get("b64_json"):
            data = decode_image(entry["b64_json"])
            mime = None
        elif entry.get("url"):
            # Never forward API authorization to an image URL or store a signed URL in artifacts.
            data, mime = self.transport.request("GET", entry["url"])
        else:
            raise VariationError("PROVIDER_RESPONSE_INVALID", "响应中没有图片")
        read_image(data, mime=mime)
        metadata = {"route_used": route}
        if isinstance(entry.get("revised_prompt"), str):
            metadata["returned_prompt_sha256"] = sha256(entry["revised_prompt"].encode())
        return GeneratedImage(data, mime, metadata)
