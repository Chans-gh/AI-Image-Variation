"""Provider 统一的请求/结果/错误模型。

字段命名与 SPEC 01 §9.2、references/provider-compat.md 对齐；
错误一律用基线注册表里的 code（shared.errors.IVError）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from shared.errors import IVError


@dataclass(frozen=True)
class ReferenceImage:
    """参考图：字节 + 媒体类型 + 原始文件名。"""

    data: bytes
    media_type: str = "image/png"
    filename: str = "reference.png"

    @property
    def byte_size(self) -> int:
        return len(self.data)


@dataclass
class GenerationRequest:
    """一次独立生成请求（Grid 整板 或 Sequence 单帧）。"""

    prompt: str
    aspect_ratio: str | None = None
    resolution_tier: str | None = None
    size_px: tuple[int, int] | None = None
    reference_image: ReferenceImage | None = None
    purpose: str = "frame"
    frame_index: int | None = None
    seed: str | None = None

    def __post_init__(self) -> None:
        if not self.prompt or not self.prompt.strip():
            raise IVError("INVALID_INPUT", "生成请求缺少 prompt")

    @property
    def uses_reference(self) -> bool:
        return self.reference_image is not None


@dataclass
class GenerationResult:
    image_bytes: bytes
    media_type: str = "image/png"
    requested_tier: str | None = None
    adapter_version: str = ""
    latency_ms: int | None = None
    provider_raw_meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.image_bytes:
            raise IVError("IMAGE_VALIDATION_FAILED", "Provider 返回空图片字节")


@dataclass(frozen=True)
class ProviderErrorInfo:
    code: str
    message: str
    retryable: bool
    retry_after: float | None = None
    detail: dict = field(default_factory=dict)
