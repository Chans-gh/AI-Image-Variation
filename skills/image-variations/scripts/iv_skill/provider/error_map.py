"""上游错误 → 注册表错误码 的归一化（provider-compat.md §5）。

铁律：
    - 错误码必须来自基线 references/error-registry.json；
    - 参数不支持/安全拒绝**不重试**；鉴权错误不重试；换 Provider 永远不是自动行为；
    - 拿不准的上游错误按"能力不达标"处理，不笼统重试（SPEC 01 §9.2 底线表）。
"""
from __future__ import annotations

from dataclasses import dataclass

from shared.errors import IVError

_RATIO_HINTS = ("aspect_ratio", "aspect ratio", "ratio", "width", "height")
_RESOLUTION_HINTS = ("resolution", "image_size", "imagesize", "size is not supported",
                     "quality", "tier")
_REFERENCE_HINTS = ("reference", "image input", "edit", "image_urls", "inline_data")
_FORMAT_HINTS = ("mime", "media type", "unsupported format", "decode")
_AUTH_HINTS = ("api key", "unauthorized", "permission", "forbidden", "invalid key")
_REFUSAL_HINTS = ("policy", "moderation", "content_policy", "safety", "blocked", "refus")


@dataclass(frozen=True)
class MappedError:
    code: str
    message: str
    retryable: bool
    retry_after: float | None = None
    verbose_body: str = ""

    def to_iv_error(self, item_ref: str | None = None) -> IVError:
        return IVError(self.code, self.message, item_ref=item_ref, upstream_retryable=self.retryable)


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def classify(status: int | None, body: str, *, retry_after: str | None = None,
             network_error: str | None = None) -> MappedError:
    """把一次上游响应归类为注册表错误码。"""
    text = (body or "").lower()

    if network_error is not None or status is None:
        return MappedError("PROVIDER_TIMEOUT", f"上游网络失败：{network_error or 'unknown'}",
                           retryable=True)

    if status in (401, 403):
        return MappedError("AUTH_FAILED",
                           f"鉴权失败（HTTP {status}）：检查密钥配置与权限",
                           retryable=False, verbose_body=body)

    if status == 429:
        return MappedError("RATE_LIMITED", "上游限流：按 Retry-After 退避重试",
                           retryable=True, retry_after=_retry_after(retry_after),
                           verbose_body=body)

    if status in (500, 502, 503, 504):
        return MappedError("PROVIDER_TIMEOUT", f"上游 {status}：可有限重试",
                           retryable=True, retry_after=_retry_after(retry_after),
                           verbose_body=body)

    if status == 408:
        return MappedError("PROVIDER_TIMEOUT", "上游请求超时", retryable=True,
                           retry_after=_retry_after(retry_after), verbose_body=body)

    if status == 400:
        if any(hint in text for hint in _REFUSAL_HINTS):
            return MappedError("MODEL_REFUSAL", "模型/内容安全策略拒绝；不得更换 Provider 绕过",
                               retryable=False, verbose_body=body)
        if any(hint in text for hint in _AUTH_HINTS):
            return MappedError("AUTH_FAILED", "上游鉴权失败", retryable=False, verbose_body=body)
        if any(hint in text for hint in _RATIO_HINTS):
            return MappedError("UNSUPPORTED_RATIO", "上游不支持该比例", retryable=False,
                               verbose_body=body)
        if any(hint in text for hint in _RESOLUTION_HINTS):
            return MappedError("UNSUPPORTED_RESOLUTION", "上游不支持该档位/尺寸", retryable=False,
                               verbose_body=body)
        if any(hint in text for hint in _REFERENCE_HINTS):
            return MappedError("REFERENCE_REQUIRED", "上游不支持参考图输入", retryable=False,
                               verbose_body=body)
        if any(hint in text for hint in _FORMAT_HINTS):
            return MappedError("IMAGE_VALIDATION_FAILED", "上游返回格式不可用", retryable=False,
                               verbose_body=body)
        return MappedError("MODEL_CAPABILITY_UNSUPPORTED", "上游参数不支持：需改参数后再试",
                           retryable=False, verbose_body=body)

    if 400 < status < 500:
        return MappedError("MODEL_CAPABILITY_UNSUPPORTED", f"上游返回 HTTP {status}",
                           retryable=False, verbose_body=body)

    return MappedError("MODEL_CAPABILITY_UNSUPPORTED", f"未预期的上游状态 {status}",
                       retryable=False, verbose_body=body)
