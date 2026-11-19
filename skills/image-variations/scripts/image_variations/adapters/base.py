from dataclasses import dataclass, field

from ..errors import require


def require_ready(plan):
    require(plan.get("status") == "ready", "MODEL_CAPABILITY_UNSUPPORTED", "尺寸计划尚未通过预检")


@dataclass
class GeneratedImage:
    data: bytes
    mime: str | None = None
    public_metadata: dict = field(default_factory=dict)


class Provider:
    environment = "external"

    def generate(self, prompt, plan, reference_bytes=None):
        raise NotImplementedError
