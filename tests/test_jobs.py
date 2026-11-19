import json

import pytest
from conftest import elapse_backoff, finish
from image_variations.adapters.mock import MockProvider
from image_variations.demo import fixture_review
from image_variations.errors import VariationError
from image_variations.jobs import get_status, resume_job, review_job, step_job
from image_variations.resources import contracts_root

from shared.artifacts import validate_artifact


@pytest.mark.parametrize("n,tier,count", [(2, "1K", 4), (3, "2K", 9), (4, "4K", 16)])
def test_one_board_generation_and_integer_equal_tiles(make_job, n, tier, count):
    root = make_job(grid_size=n)
    provider = MockProvider()
    status = finish(root, provider)
    assert status["status"] == "completed" and len(provider.calls) == 1
    result = validate_artifact(status["artifact_path"], contracts_root())
    assert len(result["content_assets"]) == count
    manifest = result["manifest"]
    assert manifest["requested_resolution_tier"] == tier
    assert all("native_resolution_tier" not in x for x in result["content_assets"])
    assert len({(x["width"], x["height"]) for x in result["content_assets"]}) == 1
    if n == 3:
        dim = manifest["dimension_execution"]
        assert dim["native_output"] == {"width": 192, "height": 256}
        assert dim["effective_output"] == {"width": 192, "height": 255}


@pytest.mark.parametrize("branch", ["variants", "progression", "narrative"])
@pytest.mark.parametrize("count", [4, 16])
def test_text_sequence_uses_frame_one_fixed_anchor(make_job, branch, count):
    root = make_job("sequence", image_count=count, branch=branch)
    provider = MockProvider()
    result = finish(root, provider)
    assert result["status"] == "completed" and len(provider.calls) == count
    assert provider.calls[0]["reference_sha256"] is None
    manifest = validate_artifact(result["artifact_path"], contracts_root())["manifest"]
    first_hash = manifest["images"][0]["sha256"]
    assert all(call["reference_sha256"] == first_hash for call in provider.calls[1:])
    assert len({call["prompt"] for call in provider.calls}) == count
    assert len(manifest["dimension_execution"]["frames"]) == count


def test_no_more_generations_until_review_and_stale_report_is_rejected(make_job):
    root = make_job("sequence")
    provider = MockProvider()
    step_job(root, provider)
    request = json.loads((root / "review-request.json").read_text())
    assert request["image_path"].endswith(".png")
    step_job(root, provider)
    assert len(provider.calls) == 1 and get_status(root)["pending_review"]
    report = fixture_review(root)
    report["asset_sha256"] = "0" * 64
    with pytest.raises(VariationError) as error:
        review_job(root, report)
    assert error.value.code == "STALE_REVIEW"


def test_visual_failure_never_yields_tiles_even_when_dimensions_divide(make_job):
    root = make_job(grid_size=4)
    provider = MockProvider()
    for _ in range(3):
        elapse_backoff(root)
        step_job(root, provider)
        report = fixture_review(root)
        report["result"].update(
            grid_structure_ok=False,
            issues=[{"severity": "major", "type": "white_seam", "visible_evidence": "gap"}],
        )
        review_job(root, report)
    result = get_status(root)
    assert result["status"] == "failed" and result["artifact_path"] is None
    assert result["calls_used"] == 3
    assert not list(root.parent.rglob("tiles/*.png"))


def test_revised_grid_blocks_pass_without_per_cell_evidence(make_job):
    root = make_job(direction="weather", grid_size=2, retries=0)
    step_job(root, MockProvider())
    report = fixture_review(root)
    items = report["result"].pop("item_reviews")
    assert [item["index"] for item in items] == [1, 2, 3, 4]
    with pytest.raises(VariationError) as error:
        review_job(root, report)
    assert error.value.code == "INVALID_REVIEW" and get_status(root)["pending_review"]
    report["result"]["item_reviews"] = items
    items[1]["observed"] = "Wet path and visible rain; the person has no shelter"
    items[1]["target_ok"] = False
    assert report["result"]["pass"] is True
    result = review_job(root, report)
    assert result["status"] == "failed" and result["artifact_path"] is None


def test_revised_sequence_reviews_only_the_current_frame(make_job):
    root = make_job("sequence", direction="view", branch="variants")
    provider = MockProvider()
    step_job(root, provider)
    assert review_job(root, fixture_review(root))["completed_count"] == 1
    step_job(root, provider)
    report = fixture_review(root)
    assert [item["index"] for item in report["result"]["item_reviews"]] == [2]
    assert review_job(root, report)["completed_count"] == 2


def test_first_text_frame_failure_stops_other_frames(make_job):
    root = make_job("sequence")
    provider = MockProvider({1: [b"broken"] * 3})
    status = finish(root, provider)
    assert status["status"] == "failed"
    assert [c["index"] for c in provider.calls] == [1, 1, 1]


def test_partial_failure_and_resume_preserve_indices_and_passed_bytes(make_job, tmp_path):
    root = make_job("sequence", branch="narrative")
    provider = MockProvider({2: [b"broken"] * 3})
    status = finish(root, provider)
    assert status["status"] == "partial_failed"
    partial = validate_artifact(status["artifact_path"], contracts_root(), require_complete=False)
    before = {x["index"]: x["sha256"] for x in partial["content_assets"]}
    assert set(before) == {1, 3, 4}
    resumed = tmp_path / "resumed"
    resume_job(root, resumed)
    retry_provider = MockProvider()
    after_status = finish(resumed, retry_provider)
    after = validate_artifact(after_status["artifact_path"], contracts_root())["manifest"]
    assert [c["index"] for c in retry_provider.calls] == [2]
    assert after["artifact_revision"] == "r2" and after["parent_artifact_revision"] == "r1"
    assert all(x["sha256"] == before[x["index"]] for x in after["images"] if x["index"] != 2)
    assert retry_provider.calls[0]["reference_sha256"] == before[1]


def test_safety_refusal_is_not_retried(make_job):
    root = make_job("sequence")
    provider = MockProvider({1: [VariationError("MODEL_REFUSAL", "policy refusal")]})
    assert finish(root, provider)["status"] == "failed"
    assert len(provider.calls) == 1


def test_crash_intent_blocks_automatic_duplicate(make_job):
    root = make_job()
    path = root / "job.json"
    data = json.loads(path.read_text())
    data["in_flight"] = {"index": 0, "attempt": 1}
    path.write_text(json.dumps(data))
    provider = MockProvider()
    with pytest.raises(VariationError) as error:
        step_job(root, provider)
    assert error.value.code == "UNCERTAIN_GENERATION" and not provider.calls
