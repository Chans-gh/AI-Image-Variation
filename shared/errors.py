"""错误码绑定：代码中出现的错误码必须存在于基线 references/error-registry.json。

对应 SPEC 01 §12 / DECISIONS C5「错误码唯一真相源」。
本模块**不**定义错误码，只做查找与校验，防止实现层偷偷新增码。
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from . import baseline
from .jsonio import read_json


@dataclass(frozen=True)
class ErrorSpec:
    code: str
    message: str
    blocking: bool
    retryable: bool
    skills: tuple[str, ...]


@lru_cache(maxsize=1)
def registry() -> dict[str, ErrorSpec]:
    data = baseline.load_error_registry()
    return {
        item["code"]: ErrorSpec(
            code=item["code"],
            message=item["message"],
            blocking=bool(item["blocking"]),
            retryable=bool(item["retryable"]),
            skills=tuple(item.get("skills", ())),
        )
        for item in data["errors"]
    }


def lookup(code: str) -> ErrorSpec:
    """未登记的错误码直接抛错，禁止悄悄使用。"""
    table = registry()
    if code not in table:
        raise KeyError(f"SCHEMA_MAJOR_UNSUPPORTED: 错误码 {code!r} 未在 error-registry 登记")
    return table[code]


def is_known(code: str) -> bool:
    return code in registry()


def skill_codes(skill: str = "image_variations") -> set[str]:
    return {code for code, spec in registry().items() if skill in spec.skills}


class IVError(Exception):
    """Skill 01 的结构化错误：必须携带注册表里的 code。"""

    def __init__(self, code: str, message: str, *, item_ref: str | None = None, **detail):
        spec = lookup(code)
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.spec = spec
        self.item_ref = item_ref
        self.detail = detail

    def to_dict(self) -> dict:
        payload = {
            "code": self.code,
            "message": self.message,
            "blocking": self.spec.blocking,
            "retryable": self.spec.retryable,
        }
        if self.item_ref is not None:
            payload["item_ref"] = self.item_ref
        if self.detail:
            payload["detail"] = self.detail
        return payload
