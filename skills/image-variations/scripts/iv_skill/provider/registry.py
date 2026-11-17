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
from ..local_plugins import providers_dirs, register_providers

BUILTIN_FACTORY: Callable[[], ProviderAdapter] | None = None

# 公开仓库只带通用适配器；真实平台适配器作为**本地私有插件**注册（见 local_plugins）。
EXTERNAL_ADAPTERS: dict[str, type] = {
    "openai_image_compatible": OpenAIImageAdapter,
    "gemini_image_compatible": GeminiImageAdapter,
    "mock": MockAdapter,
}

_LOCAL_PROVIDERS_LOADED = False


def ensure_local_providers() -> list[str]:
    """注册本地私有 Provider 插件，返回新增的 provider_type。

    没有 `local/` 目录是**正常情况**（公开仓库与 CI 都没有），不报错。
    插件存在但加载失败会抛 `BUILTIN_UNAVAILABLE`——不静默跳过。
    """
    global _LOCAL_PROVIDERS_LOADED
    if _LOCAL_PROVIDERS_LOADED:
        return []
    added = register_providers(EXTERNAL_ADAPTERS)
    _LOCAL_PROVIDERS_LOADED = True
    return added


def available_provider_types() -> list[str]:
    ensure_local_providers()
    return sorted(EXTERNAL_ADAPTERS)


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
    ensure_local_providers()
    adapter_cls = EXTERNAL_ADAPTERS.get(provider_type)
    if adapter_cls is None:
        dirs = "、".join(str(p) for p in providers_dirs()) or "(无)"
        raise IVError("MODEL_CAPABILITY_UNSUPPORTED",
                      f"未知 provider_type {provider_type!r}；当前可用 "
                      f"{sorted(EXTERNAL_ADAPTERS)}。本仓库只带通用适配器；"
                      f"接入真实平台请把适配器放到本地插件目录：{dirs}")
    capability = ModelCapability.from_declaration(capability_declaration)
    if provider_type == "mock":
        return MockAdapter(capability=capability, config=config, transport=transport,
                           secret_resolver=secret_resolver)
    if not base_url:
        raise IVError("MODEL_CAPABILITY_UNSUPPORTED", f"{provider_type} 需要 base_url")
    return adapter_cls(capability=capability, base_url=base_url, secret_ref=secret_ref,
                       model_id=model_id, transport=transport, secret_resolver=secret_resolver,
                       config=config, request_timeout_sec=request_timeout_sec)
