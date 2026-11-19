"""HTTPS without proxies, redirects or a second unvalidated DNS lookup."""

import http.client
import ipaddress
import json
import socket
import ssl
from datetime import UTC
from urllib.parse import urlsplit

from ..errors import VariationError, require
from ..imaging import MAX_BYTES


def public_addresses(host, port=443):
    try:
        entries = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise VariationError("PROVIDER_NETWORK_ERROR", "模型端点 DNS 解析失败", retryable=True) from None
    require(bool(entries), "INVALID_ENDPOINT", "端点无有效地址")
    for entry in entries:
        ip = ipaddress.ip_address(entry[4][0].split("%", 1)[0])
        require(
            ip.is_global and not ip.is_multicast and not ip.is_reserved,
            "INVALID_ENDPOINT",
            "禁止访问内网、环回、保留地址或元数据服务",
        )
    return entries


def validate_url(url, *, allow_query=False):
    require(isinstance(url, str) and not any(c in url for c in "\r\n\\"), "INVALID_ENDPOINT", "端点格式无效")
    u = urlsplit(url)
    require(
        u.scheme == "https"
        and bool(u.hostname)
        and not u.username
        and not u.password
        and not u.fragment
        and (allow_query or not u.query),
        "INVALID_ENDPOINT",
        "仅支持无鉴权信息的 HTTPS 端点",
    )
    try:
        port = u.port or 443
    except ValueError:
        raise VariationError("INVALID_ENDPOINT", "端点端口无效") from None
    return u, port


class PinnedHTTPS(http.client.HTTPSConnection):
    def connect(self):
        addresses = public_addresses(self.host, self.port)
        last = None
        for family, socktype, proto, _, address in addresses:
            sock = socket.socket(family, socktype, proto)
            sock.settimeout(self.timeout)
            try:
                sock.connect(address)
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                return
            except OSError as exc:
                sock.close()
                last = exc
        raise OSError("Connection failed") from last


class SecureTransport:
    def __init__(self, timeout=240):
        require(type(timeout) in (int, float) and 0 < timeout <= 600, "INVALID_INPUT", "超时须为 0–600 秒")
        self.timeout = timeout

    def request(self, method, url, headers=None, body=None):
        u, port = validate_url(url, allow_query=method == "GET")
        conn = PinnedHTTPS(u.hostname, port, timeout=self.timeout, context=ssl.create_default_context())
        try:
            conn.request(method, (u.path or "/") + ("?" + u.query if u.query else ""), body, headers or {})
            response = conn.getresponse()
            status = response.status
            if 300 <= status < 400:
                raise VariationError("INVALID_ENDPOINT", "不跟随第三方重定向")
            retry_after = response.getheader("Retry-After", "0")
            if status in (401, 403):
                raise VariationError("AUTH_FAILED", "模型鉴权失败，请检查本地配置")
            if status == 429:
                try:
                    delay = float(retry_after)
                except ValueError:
                    from datetime import datetime
                    from email.utils import parsedate_to_datetime

                    try:
                        delay = (parsedate_to_datetime(retry_after) - datetime.now(UTC)).total_seconds()
                    except (TypeError, ValueError):
                        delay = 1
                raise VariationError("RATE_LIMITED", "服务限流", retryable=True, retry_after=delay)
            if status >= 500:
                raise VariationError("PROVIDER_UNAVAILABLE", "模型服务暂时不可用", retryable=True)
            data = response.read(MAX_BYTES * 2 + 1)
            require(len(data) <= MAX_BYTES * 2, "PROVIDER_RESPONSE_INVALID", "响应超出大小限制")
            if status >= 400:
                # Classify policy errors without persisting provider messages (which can echo secrets).
                try:
                    error = json.loads(data).get("error", {})
                    code = str(error.get("code", "")).lower()
                except (ValueError, AttributeError):
                    code = ""
                refusal = code in {"content_policy_violation", "safety", "image_safety", "moderation_blocked"}
                raise VariationError(
                    "MODEL_REFUSAL" if refusal else "PROVIDER_REQUEST_REJECTED",
                    "模型拒绝该内容" if refusal else "接口拒绝参数；请核对型号和路由",
                )
            return data, response.getheader("Content-Type", "")
        except VariationError:
            raise
        except TimeoutError:
            raise VariationError(
                "PROVIDER_TIMEOUT", "请求超时，结果可能已生成，请核对后再试", retryable=True
            ) from None
        except (OSError, http.client.HTTPException):
            raise VariationError("PROVIDER_NETWORK_ERROR", "网络请求失败", retryable=True) from None
        finally:
            conn.close()


def json_response(transport, url, headers, payload=None, body=None):
    if body is None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        headers = {**headers, "Content-Type": "application/json"}
    data, mime = transport.request("POST", url, headers, body)
    require(
        mime.split(";")[0].strip().lower() == "application/json",
        "PROVIDER_RESPONSE_INVALID",
        "接口返回非 JSON MIME",
    )
    try:
        obj = json.loads(data)
    except ValueError:
        raise VariationError("PROVIDER_RESPONSE_INVALID", "接口返回无效 JSON") from None
    require(isinstance(obj, dict), "PROVIDER_RESPONSE_INVALID", "接口 JSON 须为对象")
    return obj
