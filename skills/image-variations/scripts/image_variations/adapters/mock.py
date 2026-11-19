from PIL import Image, ImageDraw

from ..errors import VariationError
from ..imaging import png_bytes, sha256
from .base import GeneratedImage, Provider


class MockProvider(Provider):
    """Synthetic shapes only. Never evidence for a real model's native tiers or visual quality."""

    environment = "mock"

    def __init__(self, failures=None):
        self.calls = []
        self.failures = {k: list(v) for k, v in (failures or {}).items()}

    def generate(self, prompt, plan, reference_bytes=None):
        index = plan.get("unit_index", 0)
        self.calls.append(
            {
                "index": index,
                "prompt": prompt,
                "reference_sha256": sha256(reference_bytes) if reference_bytes else None,
                "parameters": dict(plan["provider_parameters"]),
            }
        )
        if self.failures.get(index):
            fault = self.failures[index].pop(0)
            if isinstance(fault, VariationError):
                raise fault
            if isinstance(fault, bytes):
                return GeneratedImage(fault)
        w, h = plan["expected_native_dimensions"][0]
        im = Image.new("RGB", (w, h))
        draw = ImageDraw.Draw(im)
        n = plan.get("grid_size", 1) or 1
        for i in range(n * n):
            row, col = divmod(i, n)
            x0, y0, x1, y1 = col * w // n, row * h // n, (col + 1) * w // n, (row + 1) * h // n
            tint = ((i + index + 1) * 31) % 230
            draw.rectangle((x0, y0, x1, y1), fill=(tint, 70, 230 - tint))
            margin = min(x1 - x0, y1 - y0) // 4
            draw.ellipse((x0 + margin, y0 + margin, x1 - margin, y1 - margin), fill=(240, 220, 170))
        return GeneratedImage(png_bytes(im), "image/png", {"synthetic_fixture": True})


def fixture_profile():
    tiers = {"1K": (96, 128), "2K": (192, 256), "4K": (384, 512)}
    ratios = {"3:4": lambda w, h: (w, h), "4:3": lambda w, h: (h, w), "1:1": lambda w, h: (h, h)}
    return {
        "provider_type": "mock",
        "model_id": "synthetic-fixture-v1",
        "api_route": "fixture",
        "environment": "mock",
        "profile_version": "fixture-1.0",
        "reference_image_input": True,
        "mapping_kind": "native_tier_parameter",
        "supported_native_tiers": list(tiers),
        "supported_aspect_ratios": list(ratios),
        "output_tolerance_policy": {"ratio_relative": 0.01},
        "verification": {
            "status": "mock",
            "source": "synthetic deterministic fixtures; no model calls",
            "verified_at": "2026-11-17",
        },
        "tier_map": {
            tier: {
                ratio: {
                    "parameters": {"image_size": tier, "aspect_ratio": ratio},
                    "expected_dimensions": [list(fn(*size))],
                }
                for ratio, fn in ratios.items()
            }
            for tier, size in tiers.items()
        },
    }
