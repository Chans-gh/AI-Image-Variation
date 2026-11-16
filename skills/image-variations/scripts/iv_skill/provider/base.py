"""适配器公共基类：请求组装 → 传输 → 响应解析 → 错误归一。

子类只需实现 `build_request` 与 `parse_response`；重试、预算、脱敏与错误映射
都在基类里统一处理，避免每个适配器各写一套。
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

from shared.errors import IVError

from ..config import load_config
from .capability import ModelCapability
from .error_map import classify
from .net import validate_endpoint
from .retry import Budget, RetryPolicy
from .transport import TransportRequest, TransportResponse, basic_auth_header, redact, resolve_secret

IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpeg", "image/jpeg"),
    (b"RIFF", "webp", "image/webp"),
    (b"GIF8", "gif", "image/gif"),
)


def sniff_image(data: bytes) -> tuple[str, str] | None:
    """按魔数识别图片格式；识别失败返回 None（由调用方报 IMAGE_VALIDATION_FAILED）。"""
    for signature, fmt, media in IMAGE_SIGNATURES:
        if data.startswith(signature):
            if fmt == "webp" and data[8:12] != b"WEBP":
                continue
            return fmt, media
    return None


class ProviderAdapter(ABC):
    """所有适配器的统一入口。"""

    provider_type = "abstract"

    def __init__(self, *, capability: ModelCapability, base_url: str = "",
                 secret_ref: str | None = None, transport=None,
                 secret_resolver=None, config: dict | None = None,
                 model_id: str = "", request_timeout_sec: float | None = None,
                 allow_local_endpoint: bool | None = None) -> None:
        self.config = config or load_config()
        self.capability = capability
        self.model_id = model_id or capability.model_id
        self.secret_ref = secret_ref
        local_cfg = self.config["local_endpoint"]
        self.allow_local = (local_cfg["allow_local_endpoint"] if allow_local_endpoint is None
                            else bool(allow_local_endpoint))
        self.base_url = validate_endpoint(base_url, allow_local=self.allow_local) if base_url else ""
        self.timeout_sec = float(request_timeout_sec or 60.0)
        if transport is None:
            from .transport import UrllibTransport

            transport = UrllibTransport(allow_local=self.allow_local)
        self._transport = transport
        self._secret_resolver = secret_resolver
        self._resolved_secret: str | None = None

    # ------------------------------------------------------------ 对外接口
    @property
    def adapter_version(self) -> str:
        return self.capability.adapter_version

    def generate(self, request, *, budget: Budget | None = None,
                 policy: RetryPolicy | None = None, sleep=None) -> "GenerationResult":
        """发起一次生成请求，带有限重试与预算记账。"""
        policy = policy or RetryPolicy.from_config(self.config)
        attempt = 0
        last_code = None
        while True:
            attempt += 1
            if budget is not None:
                budget.consume(tier=request.resolution_tier or "1K")
            transport_request = self.build_request(request)
            started = time.time()
            response = self._transport(transport_request)
            elapsed_ms = int((time.time() - started) * 1000)

            if response.status == 200:
                try:
                    result = self.parse_response(response, request)
                except IVError as exc:
                    # 解析失败（损坏图/比例不符/无图片）同样计入单帧重试预算
                    last_code = exc.code
                    retryable = bool(exc.detail.get("retryable", exc.spec.retryable)) and \
                        exc.code in policy.retry_on
                    if retryable and policy.should_retry(exc.code, attempt):
                        if sleep is not None:
                            sleep(policy.backoff_seconds(attempt))
                        continue
                    raise
                result.latency_ms = elapsed_ms
                return result

            mapped = classify(response.status, response.text(), network_error=response.network_error,
                              retry_after=response.header("Retry-After"))
            last_code = mapped.code
            if policy.should_retry(mapped.code, attempt):
                if sleep is not None:
                    sleep(policy.backoff_seconds(attempt, mapped.retry_after))
                continue
            message = redact(mapped.message, [self._resolved_secret or ""])
            raise IVError(mapped.code, message, upstream_status=response.status,
                          retryable=mapped.retryable, attempts=attempt)

    # ------------------------------------------------------------ 子类实现
    @abstractmethod
    def build_request(self, request) -> TransportRequest:
        """组装一次上游请求。"""

    @abstractmethod
    def parse_response(self, response: TransportResponse, request) -> "GenerationResult":
        """解析上游响应为统一结果。"""

    # ------------------------------------------------------------ 共用工具
    @property
    def _secret(self) -> str:
        if self._resolved_secret is None:
            self._resolved_secret = resolve_secret(self.secret_ref, self._secret_resolver)
        return self._resolved_secret

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": basic_auth_header(self._secret)}

    def decode_image(self, payload: bytes, media_hint: str | None = None) -> tuple[bytes, str]:
        """解码并校验图片；不可解码一律 IMAGE_VALIDATION_FAILED（可重试）。"""
        sniffed = sniff_image(payload)
        if sniffed is None:
            raise IVError("IMAGE_VALIDATION_FAILED",
                          f"上游返回内容无法识别为图片（media_type={media_hint or 'unknown'}）")
        fmt, media = sniffed
        allowed = self.capability.response_image_formats
        if allowed and fmt not in allowed and not (fmt == "jpeg" and "jpg" in allowed):
            raise IVError("IMAGE_VALIDATION_FAILED",
                          f"上游返回格式 {fmt} 不在声明范围 {list(allowed)}")
        return payload, media
