import json
import re
import sys
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

SKILL_ROOT = Path(__file__).resolve().parents[2]
REFERENCES = SKILL_ROOT / "references"
if not REFERENCES.is_dir():
    REFERENCES = Path(sys.prefix) / "share/image-content-publishing/references"

_BUNDLED = REFERENCES / "contracts"
SHARED_CONTRACTS = (
    _BUNDLED
    if (_BUNDLED / "image-artifact-1.0.schema.json").is_file()
    else SKILL_ROOT.parents[1] / "contracts"
)
DIRECTION_REGISTRY = REFERENCES / "registry.json"
if not DIRECTION_REGISTRY.is_file():
    DIRECTION_REGISTRY = SKILL_ROOT.parent / "image-variations/references/directions/registry.json"

_SECRET_TEXT = re.compile(
    r"\b(?:sk-[\w-]{20,}|gh[pousr]_[\w-]{20,}|github_pat_[\w-]{20,}|Bearer\s+[\w.~-]{16,})\b",
    re.IGNORECASE,
)
_SECRET_FIELDS = {"api_key", "apikey", "access_token", "authorization", "cookie", "password", "secret"}


class PublishingError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_dict(self):
        return {"code": self.code, "message": self.message, **self.details}


def require(condition, code, message, **details):
    if not condition:
        raise PublishingError(code, message, **details)


def nonblank(value):
    return isinstance(value, str) and bool(value.strip())


def now():
    return datetime.now(UTC).isoformat()


def json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def read_json(path):
    with Path(path).open("rb") as file:
        data = file.read(4 * 1024 * 1024 + 1)
    require(len(data) <= 4 * 1024 * 1024, "INVALID_INPUT", "JSON 文件超过 4 MiB")
    return json.loads(data)


def public_payload(value):
    """Keep credentials out of plans and content artifacts, including free text."""
    if isinstance(value, dict):
        for key, item in value.items():
            require(
                key.lower().replace("-", "_") not in _SECRET_FIELDS or not item,
                "SENSITIVE_PAYLOAD",
                "待保存数据含凭据字段",
            )
            public_payload(key)
            public_payload(item)
    elif isinstance(value, list):
        for item in value:
            public_payload(item)
    elif isinstance(value, str):
        require(not _SECRET_TEXT.search(value), "SENSITIVE_PAYLOAD", "待保存数据疑似含访问凭据")


@cache
def _validator(name):
    return Draft202012Validator(read_json(_BUNDLED / f"{name}.schema.json"), format_checker=FormatChecker())


def check_schema(name, data, code):
    try:
        _validator(name).validate(data)
    except ValidationError as exc:
        field = ".".join(str(part) for part in exc.absolute_path) or "$"
        raise PublishingError(code, f"{name} 字段校验失败", field=field, rule=exc.validator) from exc


def platform_config():
    return read_json(REFERENCES / "platform-config.json")
