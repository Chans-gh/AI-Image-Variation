import json
from pathlib import Path

import pytest
from conftest import elapse_backoff, finish, request_for
from image_variations.adapters.mock import MockProvider, fixture_profile
from image_variations.demo import fixture_creative, fixture_review
from image_variations.errors import VariationError
from image_variations.imaging import png_bytes
from image_variations.jobs import create_job, get_status, recover_interrupted, review_job, step_job
from image_variations.request import resolve_request
from image_variations.resources import contracts_root
from PIL import Image

from shared.artifacts import validate_artifact


def test_preflight_failure_creates_no_job_and_no_generation(tmp_path):
    request = request_for()
    profile = fixture_profile()
    profile["verification"]["status"] = "unverified"
    with pytest.raises(VariationError) as error:
        create_job(
            tmp_path / "job",
            request,
            fixture_creative(resolve_request(request)),
            profile,
            tmp_path / "out",
            environment="mock",
        )
    assert error.value.code == "UNVERIFIED_NATIVE_TIER"
    assert not (tmp_path / "job").exists()


def test_original_reference_is_attached_to_every_frame_with_same_hash(tmp_path):
    path = tmp_path / "original.png"
    path.write_bytes(png_bytes(Image.new("RGB", (96, 128), "red")))
    request = request_for("sequence", resolution_tier="4K")
    request["input"]["reference_image_ref"] = str(path)
    task = resolve_request(request)
    root = tmp_path / "job"
    create_job(root, request, fixture_creative(task), fixture_profile(), tmp_path / "out", environment="mock")
    provider = MockProvider()
    status = finish(root, provider)
    assert status["status"] == "completed"
    assert len({c["reference_sha256"] for c in provider.calls}) == 1
    assert provider.calls[0]["reference_sha256"] is not None
    assert all(c["parameters"]["image_size"] == "4K" for c in provider.calls)


def test_call_budget_stops_before_next_unit(make_job):
    root = make_job("sequence")
    state_path = root / "job.json"
    state = json.loads(state_path.read_text())
    state["task"]["call_budget_limit"] = 4
    state_path.write_text(json.dumps(state))
    provider = MockProvider({2: [b"broken", b"broken"]})
    status = finish(root, provider)
    assert status["status"] == "partial_failed" and len(provider.calls) == 4
    assert [c["index"] for c in provider.calls] == [1, 2, 2, 2]
    manifest = validate_artifact(status["artifact_path"], contracts_root(), require_complete=False)[
        "manifest"
    ]
    assert manifest["execution"]["budget_exceeded"]


def test_timeout_requires_explicit_confirmation_before_retry(make_job):
    root = make_job()
    provider = MockProvider({0: [VariationError("PROVIDER_TIMEOUT", "uncertain", retryable=True)]})
    step_job(root, provider)
    elapse_backoff(root)
    with pytest.raises(VariationError) as error:
        step_job(root, provider)
    assert error.value.code == "RETRY_CONFIRMATION_REQUIRED" and len(provider.calls) == 1
    assert step_job(root, provider, confirm_uncertain_retry=True)["pending_review"]
    assert len(provider.calls) == 2


def test_recover_already_generated_bytes_without_duplicate_call(make_job, tmp_path):
    root = make_job(grid_size=2)
    state_path = root / "job.json"
    state = json.loads(state_path.read_text())
    provider = MockProvider()
    generated = provider.generate("previous request", {**state["dimensions"], "grid_size": 2})
    output = tmp_path / "recovered.png"
    output.write_bytes(generated.data)
    state["in_flight"] = {"index": 0, "attempt": 1}
    state["units"][0]["attempts"] = 1
    state["calls_used"] = 1
    state_path.write_text(json.dumps(state))
    status = recover_interrupted(root, image_path=output)
    assert status["pending_review"] and status["calls_used"] == 1
    status = review_job(root, fixture_review(root))
    assert status["status"] == "completed" and len(provider.calls) == 1


def test_tampered_anchor_blocks_next_call(make_job):
    root = make_job("sequence")
    provider = MockProvider()
    step_job(root, provider)
    review_job(root, fixture_review(root))
    state = json.loads((root / "job.json").read_text())
    anchor = root / state["units"][0]["raw_path"]
    anchor.write_bytes(anchor.read_bytes() + b"tamper")
    with pytest.raises(VariationError) as error:
        step_job(root, provider)
    assert error.value.code == "ANCHOR_INTEGRITY_FAILED" and len(provider.calls) == 1


def test_recommendation_once_and_no_content_task_side_effects(make_job):
    root = make_job(grid_size=2)
    provider = MockProvider()
    step_job(root, provider)
    result = review_job(root, fixture_review(root))
    assert result["recommendation"]["skill"] == "image-content-publishing"
    assert "recommendation" not in get_status(root)
    assert not list(root.parent.rglob("content-artifact*"))
    assert len(provider.calls) == 1


def test_native_metadata_mismatch_is_not_verified(make_job):
    root = Path(finish(make_job(grid_size=2), MockProvider())["artifact_path"])
    path = root / "manifest.json"
    data = json.loads(path.read_text())
    data["dimension_execution"]["native_output"]["width"] += 1
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="NATIVE_RESOLUTION_METADATA_MISMATCH"):
        validate_artifact(root, contracts_root())
