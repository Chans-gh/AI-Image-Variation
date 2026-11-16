"""HTTP 传输层与密钥解析。

密钥解析只接受**引用**（secret://... / env:NAME），从不接收明文 Key；
解析结果只进入请求头，不写入日志、manifest、ZIP 或异常消息。
"""
from __future__ import annotations

import base64
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from shared.errors import IVError


@dataclass(frozen=True)
class TransportRequest:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    timeout_sec: float = 60.0


@dataclass(frozen=True)
class TransportResponse:
    status: int | None
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    network_error: str | None = None

    def text(self) -> str:
        try:
            return self.body.decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover
            return ""

    def header(self, name: str) -> str | None:
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return None


class UrllibTransport:
    """标准库实现：不引入第三方 HTTP 依赖（O-02 方案 B 的依赖约束）。"""

    def __call__(self, request: TransportRequest) -> TransportResponse:
        req = urllib.request.Request(request.url, data=request.body or None,
                                     headers=request.headers, method=request.method)
        try:
            with urllib.request.urlopen(req, timeout=request.timeout_sec) as response:  # noqa: S310
                return TransportResponse(
                    status=response.status,
                    headers={k.lower(): v for k, v in response.headers.items()},
                    body=response.read(),
                )
        except urllib.error.HTTPError as exc:
            return TransportResponse(
                status=exc.code,
                headers={k.lower(): v for k, v in (exc.headers or {}).items()},
                body=exc.read() if exc.fp else b"",
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return TransportResponse(status=None, network_error=str(exc))


def resolve_secret(ref: str | None, resolver: Callable[[str], str] | None = None) -> str:
    """把 secret://... 引用解析为密钥；拒绝明文。

    支持的引用：
        secret://NAME  -> 环境变量 NAME（宿主密钥库在本仓库外的适配器里实现）
        env:NAME       -> 环境变量 NAME
    任何看起来像明文 Key 的输入直接拒绝，避免把密钥写进配置或日志。
    """
    if not ref:
        raise IVError("AUTH_FAILED", "缺少凭据引用（secret://...），不读取明文 Key")
    if ref.startswith("secret://") or ref.startswith("env:"):
        name = ref.split("://", 1)[-1] if ref.startswith("secret://") else ref.split(":", 1)[1]
        if not name:
            raise IVError("AUTH_FAILED", f"凭据引用为空: {ref}")
        value = (resolver or _env_resolver)(name)
        if not value:
            raise IVError("AUTH_FAILED", f"凭据引用 {ref} 未解析到值（宿主密钥库未配置）")
        return value
    raise IVError("AUTH_FAILED", "凭据必须以 secret:// 或 env: 引用传入，禁止明文 Key")


def _env_resolver(name: str) -> str | None:
    return os.environ.get(name)


def basic_auth_header(key: str) -> str:
    token = base64.b64encode(f"{key}:".encode()).decode()
    return f"Basic {token}"


def redact(text: str, secrets: list[str]) -> str:
    """异常/日志脱敏：把已解析的密钥替换掉。"""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***REDACTED***")
    return text
