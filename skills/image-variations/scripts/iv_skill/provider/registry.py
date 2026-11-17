"""Provider 解析：builtin / external × 两种兼容协议。

`builtin` 只提供**可注入接口**：DSH 当前没有内置生图，找不到注入实现时必须
显式返回 BUILTIN_UNAVAILABLE，不得退回 external 或静默降级（O-02 决议）。
"""
from __future__ import annotations

from typing import Callable

from shared.errors import IVError

from ..config import load_config
from .base import ProviderAdapter
from .capability import ModelCapability
from .gemini_compat import GeminiImageAdapter
from .mock import MockAdapter
from .openai_compat import OpenAIImageAdapter
from .qingshui_compat import QingshuiImageAdapter

BUILTIN_FACTORY: Callable[[], ProviderAdapter] | None = None

EXTERNAL_ADAPTERS = {
    "openai_image_compatible": OpenAIImageAdapter,
    "gemini_image_compatible": GeminiImageAdapter,
    "qingshui_image_compatible": QingshuiImageAdapter,
    "mock": MockAdapter,
}


def register_builtin(factory: Callable[[], ProviderAdapter] | None) -> None:
    """宿主注入内置生图实现（由宿主适配层调用；本仓库不自带）。"""
    global BUILTIN_FACTORY
    BUILTIN_FACTORY = factory


def builtin_adapter() -> ProviderAdapter:
    if BUILTIN_FACTORY is None:
        raise IVError("BUILTIN_UNAVAILABLE",
                      "宿主未提供内置生图能力；请配置 external Provider（如 gemini_image_compatible）")
    return BUILTIN_FACTORY()


def build_adapter(*, provider_type: str, capability_declaration: dict | None = None,
                  base_url: str = "", secret_ref: str | None = None,
                  model_id: str = "", transport=None, secret_resolver=None,
                  config: dict | None = None, request_timeout_sec: float | None = None) -> ProviderAdapter:
    """按 provider_type 构造适配器；能力声明缺失即视为不达标。"""
    config = config or load_config()
    if provider_type == "builtin":
        return builtin_adapter()
    adapter_cls = EXTERNAL_ADAPTERS.get(provider_type)
    if adapter_cls is None:
        raise IVError("MODEL_CAPABILITY_UNSUPPORTED",
                      f"未知 provider_type {provider_type!r}；支持 {sorted(EXTERNAL_ADAPTERS)}")
    capability = ModelCapability.from_declaration(capability_declaration)
    if provider_type == "mock":
        return MockAdapter(capability=capability, config=config, transport=transport,
                           secret_resolver=secret_resolver)
    if not base_url:
        raise IVError("MODEL_CAPABILITY_UNSUPPORTED", f"{provider_type} 需要 base_url")
    return adapter_cls(capability=capability, base_url=base_url, secret_ref=secret_ref,
                       model_id=model_id, transport=transport, secret_resolver=secret_resolver,
                       config=config, request_timeout_sec=request_timeout_sec)
