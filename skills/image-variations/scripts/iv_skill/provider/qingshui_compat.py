"""青水（qingshui）图片适配器：异步提交 → 轮询 → 下载；参考图走"上传 URL + 用完即删"。

与 openai/gemini 兼容层的关键差别：上游是**异步任务**（提交拿 `task_id`，再轮询到完成），
所以本适配器覆盖 `generate()` 编排三段（提交 / 轮询 / 下载），三段共用基类的错误归一与重试策略。

上游接口（实测，非推测）：
    POST   {base}/api/v1/images/generate   {prompt, model, image_count, aspect_ratio,
                                            negative_prompt, reference_images[]}
           → {success, task_id, quota_cost}
    GET    {base}/api/v1/images/{task_id}  → {success, task:{status, progress,
                                            result:{images:[url], model, prompt, usage_info},
                                            error_msg}}
    POST   {base}/api/v1/assets            multipart(file) → {success, url, asset:{hash}}
    DELETE {base}/api/v1/assets/{hash}     → 取消分享（公开 URL 立即 404）

实测事实（2026-11-17，写在这里避免后人重复踩坑）：
    - `gpt-image-2.5-flare` 在 1:1 下输出 **1024×1024**；报文里没有尺寸参数，
      多传 `size: "2048x2048"` 会被后端**忽略**（实测仍 1024）。
    - `reference_images` **不接受 `data:` 内联**：提交会成功，但任务必然失败
      （3/3 次，`AIMS 图片编辑失败 [502] UPSTREAM_PROVIDER_ERROR`），因此参考图必须先是真实 URL。
    - 参考图 URL 由 `/api/v1/assets` 上传得到，用后必须 `DELETE` 收回（用户已授权此通路）。

档位与边长：`provider-compat.md` 要求"各档位对应的真实边长必须在真实服务上测量并回填
`tier_to_board_side_px`"。本适配器不猜尺寸——能力声明由任务/`config/provider-profiles.json`
给出，缺声明即按底线清单报错，绝不静默降档。
"""
from __future__ import annotations

import json
import re
import time
import uuid

from shared.errors import IVError

from . import GenerationResult
from .base import ProviderAdapter
from .error_map import MappedError, classify
from .retry import RetryPolicy
from .transport import TransportRequest, TransportResponse, redact

# 上游把真实故障码写在 error_msg 文本里，例如 "AIMS API 错误 [502]: {...}"
_STATUS_IN_TEXT = re.compile(r"\[(\d{3})\]|HTTP\s*(\d{3})", re.IGNORECASE)
_REFUSAL_HINTS = ("内容安全", "审核", "safety", "policy", "refus", "违规")
# 上游尺寸参数报错的措辞（实测 Doubao 经 AIMS 会回 "image size must be at least 3686400 pixels"）
_SIZE_HINTS = ("must be at least", "pixels", "size` specified", "size specified", "尺寸")


# 参考图公开 URL 的收回结果。**注意**：这个码不在冻结的 error-registry.json 里
# （image_variations 的 23 个码没有覆盖"资产收回失败"），规范包只读、不得擅自新增，
# 因此这里只作为**警告**如实披露，并在文案里标明待规范补充；绝不静默吞掉。
CLEANUP_WARNING_CODE = "REFERENCE_CLEANUP_FAILED"
CLEANUP_WARNING_NOTE = "（该码不在冻结错误码表内，待在规范中补充）"


class QingshuiImageAdapter(ProviderAdapter):
    """青水图片适配器（`possible async`：`supports_async_result` 应为 true）。"""

    provider_type = "qingshui_image_compatible"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        section = (self.config.get("qingshui") or {})
        self.poll_interval_sec = float(section.get("poll_interval_sec", 3.0))
        self.max_wait_sec = float(section.get("max_wait_sec", 600))
        self.poll_error_limit = int(section.get("poll_consecutive_error_limit", 10))
        self.download_timeout_sec = float(section.get("download_timeout_sec", 120))
        self.asset_cleanup = bool(section.get("asset_cleanup", True))
        self._reference_urls: list[str] = []
        self._reference_warnings: list[dict] = []
        self._asset_hash: str | None = None

    # ------------------------------------------------------------------ 鉴权
    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._secret}"}

    # ------------------------------------------------- 覆盖 generate：三段编排
    def generate(self, request, *, budget=None, policy: RetryPolicy | None = None,
                 sleep=None):
        """提交 → 轮询 → 下载。参考图先上传，无论成败都在 finally 里收回。"""
        policy = policy or RetryPolicy.from_config(self.config)
        self._reference_urls = []
        try:
            if request.reference_image is not None:
                self._asset_hash, url = self._upload_reference(request.reference_image)
                self._reference_urls = [url]

            attempt = 0
            while True:
                attempt += 1
                if budget is not None:
                    budget.consume(tier=request.resolution_tier or "1K")
                started = time.time()
                try:
                    response = self._transport(self.build_request(request))
                    if response.status != 200:
                        mapped = classify(response.status, response.text(),
                                          network_error=response.network_error,
                                          retry_after=response.header("Retry-After"))
                        raise IVError(mapped.code,
                                      redact(mapped.message, [self._resolved_secret or ""]),
                                      upstream_status=response.status, retryable=mapped.retryable,
                                      attempts=attempt)
                    task_id = self._task_id(response)
                    task = self._await_task(task_id, started, policy, sleep)
                    result = self.parse_response(task, request)
                except IVError as exc:
                    # 提交失败 / 任务失败 / 解析失败统一在这里按策略重试；
                    # 参考图已上传的情况下重试会复用同一 URL，不会重复上传。
                    retryable = bool(exc.detail.get("retryable", exc.spec.retryable)) and \
                        exc.code in policy.retry_on
                    if retryable and policy.should_retry(exc.code, attempt):
                        if sleep is not None:
                            sleep(policy.backoff_seconds(attempt))
                        continue
                    raise
                result.latency_ms = int((time.time() - started) * 1000)
                return result
        finally:
            self._release_reference()

    # ----------------------------------------------------- 基类抽象方法实现
    def build_request(self, request) -> TransportRequest:
        """组装**提交**请求（轮询与下载在 `generate`/`_await_task`/`parse_response` 里）。"""
        payload = {
            "prompt": request.prompt,
            "model": self.model_id,
            "image_count": 1,
            "aspect_ratio": request.aspect_ratio or "1:1",
            "negative_prompt": "",
            "reference_images": list(self._reference_urls),
        }
        return TransportRequest("POST", f"{self.base_url.rstrip('/')}/api/v1/images/generate",
                                {"Content-Type": "application/json", **self.auth_headers()},
                                json.dumps(payload).encode("utf-8"), self.timeout_sec)

    def parse_response(self, response: TransportResponse, request) -> GenerationResult:
        """把**已完成任务**的 JSON 解析为统一结果：取首图 URL 并下载为字节。"""
        try:
            task = json.loads(response.text() or "{}")
        except json.JSONDecodeError as exc:
            raise IVError("IMAGE_VALIDATION_FAILED", f"任务响应不是 JSON: {exc}") from exc
        task = task.get("task", task)
        result = task.get("result") or {}
        urls = result.get("images") or []
        if not urls:
            raise IVError("IMAGE_VALIDATION_FAILED", "上游任务完成但没有返回图片 URL")
        payload = self._download(urls[0])
        data, media = self.decode_image(payload)
        return GenerationResult(
            data, media, request.resolution_tier, self.adapter_version,
            provider_raw_meta={"task_id": task.get("task_id"), "upstream_model": result.get("model"),
                               "quota_cost": task.get("quota_cost"),
                               "usage_info": result.get("usage_info")})

    # --------------------------------------------------------------- 内部步骤
    def _task_id(self, response: TransportResponse) -> str:
        try:
            body = json.loads(response.text() or "{}")
        except json.JSONDecodeError as exc:
            raise IVError("IMAGE_VALIDATION_FAILED", f"提交响应不是 JSON: {exc}") from exc
        if not body.get("success") or not body.get("task_id"):
            raise IVError("IMAGE_VALIDATION_FAILED",
                          f"提交未返回 task_id：{redact(str(body)[:200], [self._resolved_secret or ''])}")
        return str(body["task_id"])

    def _await_task(self, task_id: str, started: float, policy: RetryPolicy,
                    sleep) -> TransportResponse:
        """轮询到 completed；failed / 超时 / 连续轮询失败都抛注册表错误码由 generate 统一决策。"""
        url = f"{self.base_url.rstrip('/')}/api/v1/images/{task_id}"
        consecutive_errors = 0
        while time.time() - started < self.max_wait_sec:
            poll = self._transport(TransportRequest("GET", url, dict(self.auth_headers()), b"",
                                                    self.timeout_sec))
            if poll.status == 200:
                consecutive_errors = 0
                try:
                    task = json.loads(poll.text() or "{}")
                except json.JSONDecodeError:
                    task = {}
                task = task.get("task", task)
                state = str(task.get("status") or "")
                if state == "completed":
                    return poll
                if state == "failed":
                    mapped, upstream_status = self._task_failure_error(task.get("error_msg"))
                    raise IVError(mapped.code,
                                  redact(mapped.message, [self._resolved_secret or ""]),
                                  upstream_status=upstream_status, retryable=mapped.retryable,
                                  attempts=None, task_id=task_id)
            else:
                consecutive_errors += 1
                if consecutive_errors >= self.poll_error_limit:
                    mapped = classify(poll.status, poll.text(), network_error=poll.network_error)
                    raise IVError(mapped.code,
                                  redact(f"轮询连续失败 {consecutive_errors} 次：{mapped.message}",
                                         [self._resolved_secret or '']),
                                  upstream_status=poll.status, retryable=mapped.retryable,
                                  task_id=task_id)
            if sleep is not None:
                sleep(self.poll_interval_sec)
            else:
                time.sleep(self.poll_interval_sec)
        raise IVError("PROVIDER_TIMEOUT", f"任务 {task_id} 等待超时（{self.max_wait_sec:.0f}s）",
                      retryable=True, task_id=task_id)

    def _task_failure_error(self, message: str | None) -> tuple[MappedError, int | None]:
        """任务失败的归一：优先按上游故障码，其次复用 400 关键字表，最后视为可重试的单图失败。

        返回 `(归一结果, 上游真实状态码)`——状态码必须来自响应原文，不能写死，
        否则反馈给上游的 `upstream_status` 会与事实不符（实测把 503 记成过 502）。
        """
        text = str(message or "")
        if any(hint in text.lower() for hint in _REFUSAL_HINTS):
            return MappedError("MODEL_REFUSAL", "上游拒绝生成（内容安全）：不得换 Provider 绕过",
                               retryable=False), None
        match = _STATUS_IN_TEXT.search(text)
        status = int(match.group(1) or match.group(2)) if match else None
        if status is not None:
            mapped = classify(status, text)
            if mapped.code in ("PROVIDER_TIMEOUT", "RATE_LIMITED", "AUTH_FAILED"):
                return mapped, status
        if any(hint in text.lower() for hint in _SIZE_HINTS):
            # 上游能接受的尺寸与本平台映射出的尺寸不一致：这是能力/参数问题，重试没有意义。
            return MappedError("UNSUPPORTED_RESOLUTION",
                               f"上游不接受该尺寸（平台映射的尺寸不满足上游要求）：{text[:160]}",
                               retryable=False), status
        # 上游明确的"服务端故障"码：与状态码无关，一律按可重试的限时故障处理。
        # UPSTREAM_CAPACITY_UNAVAILABLE 是实测到的第二个码（503 容量不可用）。
        for upstream_code in ("UPSTREAM_PROVIDER_ERROR", "UPSTREAM_CAPACITY_UNAVAILABLE"):
            if upstream_code in text:
                return MappedError("PROVIDER_TIMEOUT", "上游模型服务异常：可有限重试",
                                   retryable=True), (status or 502)
        mapped = classify(400, text)
        if mapped.code != "MODEL_CAPABILITY_UNSUPPORTED":
            return mapped, status
        return MappedError("IMAGE_VALIDATION_FAILED",
                           f"上游任务失败：{redact(text[:200], [self._resolved_secret or ''])}",
                           retryable=True), status

    def _upload_reference(self, image) -> tuple[str | None, str]:
        """上传参考图换公开 URL（唯一可行通路：内联 data URL 已被实测否定）。"""
        boundary = "----iv" + uuid.uuid4().hex
        parts: list[bytes] = []
        for name, value in (("name", image.filename), ("extension", "png"), ("allow_scripts", "1")):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
                         f'\r\n\r\n{value}\r\n'.encode("utf-8"))
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                      f'filename="{image.filename}"\r\n'
                      f'Content-Type: {image.media_type}\r\n\r\n').encode("utf-8"))
        parts.append(image.data)
        parts.append(b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        request = TransportRequest(
            "POST", f"{self.base_url.rstrip('/')}/api/v1/assets",
            {"Content-Type": f"multipart/form-data; boundary={boundary}", **self.auth_headers()},
            b"".join(parts), self.timeout_sec)
        response = self._transport(request)
        if response.status != 201:
            mapped = classify(response.status, response.text(), network_error=response.network_error)
            raise IVError(mapped.code,
                          redact(f"参考图上传失败：{mapped.message}", [self._resolved_secret or '']),
                          upstream_status=response.status, retryable=mapped.retryable)
        try:
            body = json.loads(response.text() or "{}")
        except json.JSONDecodeError as exc:
            raise IVError("IMAGE_VALIDATION_FAILED", f"上传响应不是 JSON: {exc}") from exc
        url = body.get("url")
        asset_hash = (body.get("asset") or {}).get("hash")
        if not url:
            raise IVError("REFERENCE_REQUIRED", "上传参考图未返回 URL，无法作为参考图输入")
        return asset_hash, str(url)

    def _release_reference(self) -> None:
        """用完即删：取消分享，公开 URL 立即 404。

        收回失败**不抛**（不能因此丢掉已经生成好的图），但也**绝不静默**：
        记进 `_reference_warnings`，由调用方取走写进产物 `warnings[]`。
        授权上传的前提就是"用完即删"，删失败必须让人看得见。
        """
        if not (self.asset_cleanup and self._asset_hash):
            return
        asset_hash, self._asset_hash = self._asset_hash, None
        url = f"{self.base_url.rstrip('/')}/api/v1/assets/{asset_hash}"
        try:
            response = self._transport(TransportRequest(
                "DELETE", url, dict(self.auth_headers()), b"", self.timeout_sec))
        except Exception as exc:  # noqa: BLE001
            self._record_cleanup_failure(asset_hash, f"请求异常：{type(exc).__name__}")
            return
        if response.status not in (200, 201, 202, 204, 404):
            self._record_cleanup_failure(
                asset_hash, redact(f"上游返回 {response.status}：{response.text()[:200]}",
                                   [self._resolved_secret or ""]))

    def _record_cleanup_failure(self, asset_hash: str, reason: str) -> None:
        self._reference_warnings.append({
            "code": CLEANUP_WARNING_CODE,
            "message": (f"参考图临时分享收回失败（asset {asset_hash[:8]}…，{reason}）。"
                        f"该 URL 可能仍然公开可达，请到青水后台手动删除。" + CLEANUP_WARNING_NOTE),
            "severity": "warning",
        })

    def take_warnings(self) -> list[dict]:
        """取走并清空警告（调用方负责写进产物 warnings[]）。"""
        warnings, self._reference_warnings = self._reference_warnings, []
        return warnings

    def _download(self, url: str) -> bytes:
        """下载上游图片字节；最终以字节为准，不把 URL 当引用。"""
        response = self._transport(TransportRequest("GET", url, {"Accept": "image/png"}, b"",
                                                    self.download_timeout_sec))
        if response.status != 200 or not response.body:
            mapped = classify(response.status, response.text(), network_error=response.network_error)
            raise IVError(mapped.code,
                          redact(f"图片下载失败：{mapped.message}", [self._resolved_secret or '']),
                          upstream_status=response.status, retryable=mapped.retryable)
        return response.body
