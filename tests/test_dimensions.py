from copy import deepcopy

import pytest
from conftest import request_for
from image_variations.adapters.mock import fixture_profile
from image_variations.dimensions import plan_dimensions, validate_native
from image_variations.errors import VariationError
from image_variations.imaging import png_bytes
from image_variations.request import resolve_request
from PIL import Image


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda p: p["supported_native_tiers"].remove("4K"), "UNSUPPORTED_RESOLUTION"),
        (lambda p: p["verification"].update(status="unverified"), "UNVERIFIED_NATIVE_TIER"),
        (lambda p: p["supported_aspect_ratios"].remove("3:4"), "UNSUPPORTED_RATIO"),
        (lambda p: p["tier_map"]["4K"].pop("3:4"), "RESOLUTION_RATIO_COMBINATION_UNSUPPORTED"),
        (lambda p: p.update(model_id="wrong"), "MODEL_CAPABILITY_UNSUPPORTED"),
        (lambda p: p["tier_map"]["4K"]["3:4"].update(expected_dimensions=None), "UNVERIFIED_NATIVE_TIER"),
        (lambda p: p["tier_map"]["4K"]["3:4"]["parameters"].update(quality="high"), "UNVERIFIED_NATIVE_TIER"),
    ],
)
def test_preflight_rejections_have_requested_and_supported_metadata(mutate, code):
    raw = request_for(grid_size=4)
    raw["model"] = {"model_id": "synthetic-fixture-v1"}
    task, profile = resolve_request(raw), fixture_profile()
    mutate(profile)
    with pytest.raises(VariationError) as error:
        plan_dimensions(task, profile, "mock")
    assert error.value.code == code
    assert error.value.details["requested_tier"] == "4K"
    assert error.value.details["requested_ratio"] == "3:4"
    assert "supported_options" in error.value.details


def test_pure_text_sequence_requires_reference_support_before_first_call():
    task, profile = resolve_request(request_for("sequence")), fixture_profile()
    profile["reference_image_input"] = False
    with pytest.raises(VariationError) as error:
        plan_dimensions(task, profile, "mock")
    assert error.value.code == "REFERENCE_REQUIRED"


def test_mock_profiles_never_authorize_real_model():
    with pytest.raises(VariationError) as error:
        plan_dimensions(resolve_request(request_for()), fixture_profile(), "builtin")
    assert error.value.code == "UNVERIFIED_NATIVE_TIER"


def test_native_validation_uses_original_pixels_and_not_http_success():
    plan = plan_dimensions(resolve_request(request_for()), fixture_profile(), "mock")
    good = png_bytes(Image.new("RGB", (192, 256)))
    assert validate_native(good, plan)["native_output"] == {"width": 192, "height": 256}
    for size in [(96, 128), (256, 256), (384, 512)]:
        with pytest.raises(VariationError) as error:
            validate_native(png_bytes(Image.new("RGB", size)), plan)
        assert error.value.code == "NATIVE_RESOLUTION_VALIDATION_FAILED"


def test_size_mapping_is_distinct_from_quality_and_auto():
    profile = fixture_profile()
    profile["mapping_kind"] = "verified_native_size_mapping"
    profile["tier_map"]["2K"]["3:4"]["parameters"] = {"size": "192x256"}
    task = resolve_request(request_for())
    plan = plan_dimensions(task, profile, "mock")
    assert plan["provider_parameters"] == {"size": "192x256"}
    invalid = deepcopy(profile)
    invalid["tier_map"]["2K"]["3:4"]["parameters"] = {"size": "auto"}
    with pytest.raises(VariationError):
        plan_dimensions(task, invalid, "mock")
