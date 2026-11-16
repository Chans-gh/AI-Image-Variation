"""Endpoint 安全：HTTPS-only、SSRF 防护、本地开发例外（D-29 / SPEC 01 §9.5）。

规则：
    1. 默认仅允许 HTTPS；
    2. 阻断内网、环回、链路本地、云元数据地址，以及跳转到上述地址的重定向；
    3. `allow_local_endpoint` 默认关闭，显式开启后**仅**对 127.0.0.1 / localhost 生效；
    4. URL 中不允许出现凭据或密钥参数。
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import parse_qsl, urlparse

from shared.errors import IVError

BLOCKED_HOSTNAMES = {"metadata.google.internal", "metadata", "instance-data"}
BLOCKED_QUERY_KEYS = {"api_key", "apikey", "key", "access_token", "token", "sig", "signature"}


def _is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def resolve_host(host: str) -> list[str]:
    """解析主机名到 IP 列表；解析失败视为不可访问。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return []
    return sorted({info[4][0] for info in infos})


def validate_endpoint(url: str, *, allow_local: bool = False,
                      resolver=resolve_host) -> str:
    """校验并返回规范化 URL；任何越界情况抛 PATH_TRAVERSAL_BLOCKED 之外的明确错误。"""
    parsed = urlparse(url or "")
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    if scheme not in ("https", "http"):
        raise IVError("MODEL_CAPABILITY_UNSUPPORTED", f"Endpoint 协议不受支持: {scheme or '(空)'}")
    if scheme == "http":
        local_ok = allow_local and host in ("127.0.0.1", "localhost")
        if not local_ok:
            raise IVError("MODEL_CAPABILITY_UNSUPPORTED",
                          "自定义 Endpoint 默认必须使用 HTTPS（本地开发需显式开启 allow_local_endpoint）")
    if not host:
        raise IVError("MODEL_CAPABILITY_UNSUPPORTED", "Endpoint 缺少主机名")
    if parsed.username or parsed.password:
        raise IVError("MODEL_CAPABILITY_UNSUPPORTED", "Endpoint 不得内嵌用户名/密码")
    for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in BLOCKED_QUERY_KEYS:
            raise IVError("MODEL_CAPABILITY_UNSUPPORTED",
                          f"Endpoint 查询参数不得携带凭据: {key}")

    if host in BLOCKED_HOSTNAMES:
        raise IVError("MODEL_CAPABILITY_UNSUPPORTED", f"Endpoint 命中云元数据主机 {host}")

    is_local_name = host in ("127.0.0.1", "localhost")
    if is_local_name:
        if not allow_local:
            raise IVError("MODEL_CAPABILITY_UNSUPPORTED",
                          "Endpoint 指向本机，必须显式开启 allow_local_endpoint")
        return url

    if _looks_like_ip(host):
        ip = ipaddress.ip_address(host)
        if _is_blocked_ip(ip):
            raise IVError("MODEL_CAPABILITY_UNSUPPORTED", f"Endpoint 指向受限地址 {host}")
        return url

    for address in resolver(host):
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            raise IVError("MODEL_CAPABILITY_UNSUPPORTED",
                          f"Endpoint 解析到受限地址 {address}（SSRF 防护）")
    return url


def validate_redirect(target: str, *, original: str, allow_local: bool = False,
                      resolver=resolve_host) -> str:
    """重定向只允许同主机或仍为安全的公网地址；跨主机降级一律阻断。"""
    src = urlparse(original)
    dst = urlparse(target)
    if (dst.scheme or "").lower() not in ("https", "http"):
        raise IVError("MODEL_CAPABILITY_UNSUPPORTED", "重定向协议不受支持")
    validate_endpoint(target, allow_local=allow_local, resolver=resolver)
    if (dst.hostname or "").lower() != (src.hostname or "").lower():
        if (dst.scheme or "").lower() != "https":
            raise IVError("MODEL_CAPABILITY_UNSUPPORTED", "禁止重定向到非 HTTPS 的其它主机")
    return target


def _looks_like_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False
