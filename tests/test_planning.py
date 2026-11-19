import io
import json
from copy import deepcopy

import pytest
from conftest import request_for
from image_variations.demo import fixture_creative
from image_variations.errors import VariationError
from image_variations.planning import compile_plan
from image_variations.prompts import render_template, render_text
from image_variations.request import resolve_request
from image_variations.resources import REFERENCES, registry
from PIL import Image


@pytest.mark.parametrize("n,tier", [(2, "1K"), (3, "2K"), (4, "4K")])
def test_grid_defaults(n, tier):
    task = resolve_request(request_for(grid_size=n))
    assert (task["grid_size"], task["image_count"], task["resolution_tier"]) == (n, n * n, tier)
    assert task["aspect_ratio"] == "3:4"
    assert task["field_sources"]["resolution_tier"] == "confirmed_default"


@pytest.mark.parametrize(
    "mode,fields",
    [
        ("grid", {"grid_size": 5}),
        ("grid", {"grid_size": True}),
        ("sequence", {"image_count": 3}),
        ("sequence", {"image_count": 17}),
        ("grid", {"branch": "variants"}),
        ("sequence", {"grid_size": 2}),
    ],
)
def test_invalid_mode_or_count(mode, fields):
    with pytest.raises(VariationError) as error:
        resolve_request(request_for(mode, **fields))
    assert error.value.code == "INVALID_MODE_OR_COUNT"


def test_empty_ambiguous_multi_direction():
    for req, code in [
        ({"input": {}}, "INVALID_INPUT"),
        ({"input": {"user_prompt": "杯子生成九张"}}, "CLARIFICATION_REQUIRED"),
        (
            {"input": {"user_prompt": "杯子"}, "variation": {"direction": ["style", "scene"]}},
            "MULTIPLE_DIRECTIONS",
        ),
    ]:
        with pytest.raises(VariationError) as exc:
            resolve_request(req)
        assert exc.value.code == code


def test_structured_selections_win():
    task = resolve_request(
        request_for(grid_size=2, resolution_tier="4K"),
        {"output_mode": "grid", "grid_size": 3, "resolution_tier": "2K", "aspect_ratio": "6:8"},
    )
    assert task["grid_size"] == 2 and task["resolution_tier"] == "4K" and task["aspect_ratio"] == "3:4"
    assert len(task["issues"]) == 2


def test_exif_ratio_and_image_inputs(tmp_path):
    image = Image.new("RGB", (200, 100), "red")
    exif = Image.Exif()
    exif[274] = 6
    buf = io.BytesIO()
    image.save(buf, format="JPEG", exif=exif)
    path = tmp_path / "rotated.jpg"
    path.write_bytes(buf.getvalue())
    request = {"input": {"reference_image_ref": str(path)}}
    assert resolve_request(request)["aspect_ratio"] == "1:2"
    assert resolve_request(request)["input_type"] == "image_only"
    request["input"]["user_prompt"] = "同一人物"
    request["output"] = {"aspect_ratio": "16:9"}
    task = resolve_request(request)
    assert (task["input_type"], task["aspect_ratio"]) == ("image_plus_text", "16:9")


@pytest.mark.parametrize("direction", list(registry()[1]))
@pytest.mark.parametrize(
    "mode,branch",
    [("grid", None), ("sequence", "variants"), ("sequence", "progression"), ("sequence", "narrative")],
)
def test_all_direction_mode_branch_combinations(direction, mode, branch):
    raw = request_for(mode, **({"branch": branch} if branch else {}))
    raw["variation"]["direction"] = direction
    task = resolve_request(raw)
    compiled = compile_plan(task, fixture_creative(task))
    assert len(compiled["prompts"]) == (1 if mode == "grid" else 4)
    assert len(set(compiled["prompts"])) == len(compiled["prompts"])
    assert all("{{" not in p for p in compiled["prompts"])


def test_duplicate_wrong_count_and_order_are_rejected():
    task = resolve_request(request_for("sequence", branch="progression"))
    for mutate in [
        lambda x: x["plan"]["frames"].pop(),
        lambda x: x["plan"]["frames"][1].update(change_point=x["plan"]["frames"][0]["change_point"]),
        lambda x: x["plan"]["frames"][0].update(index=2),
        lambda x: x["plan"]["frames"][0].update(order_context=""),
    ]:
        creative = fixture_creative(task)
        mutate(creative)
        with pytest.raises(VariationError):
            compile_plan(task, creative)


@pytest.mark.parametrize(
    ("direction", "field"),
    [
        ("view", "world_projection"),
        ("scene", "route_connection"),
        ("weather", "subject_response"),
        ("lens", "framing"),
        ("lens", "capture_method"),
        ("usage", "acceptance_test"),
    ],
)
def test_revised_plan_rejects_missing_direction_evidence(direction, field):
    raw = request_for(grid_size=2)
    raw["variation"]["direction"] = direction
    task = resolve_request(raw)
    creative = fixture_creative(task)
    del creative["plan"]["cells"][1]["direction_evidence"][field]
    with pytest.raises(VariationError) as error:
        compile_plan(task, creative)
    assert error.value.details == {"index": 2, "direction": direction, "missing_fields": [field]}


@pytest.mark.parametrize("mode,items_key", [("grid", "cells"), ("sequence", "frames")])
def test_lens_compiles_shot_boundaries_in_both_modes(mode, items_key):
    raw = request_for(mode)
    raw["variation"]["direction"] = "lens"
    task = resolve_request(raw)
    creative = fixture_creative(task)
    first_shot = creative["plan"][items_key][0]
    framing = "A small full figure framed by the entire path and canopy"
    first_shot["direction_evidence"]["framing"] = framing
    compiled = compile_plan(task, creative)
    assert framing in compiled["prompts"][0]
    assert compiled["item_evidence_required"]

    first_shot["direction_evidence"] = {"optical_mechanism": "Wide lens", "visible_effect": "Large tree"}
    with pytest.raises(VariationError) as error:
        compile_plan(task, creative)
    assert error.value.details["missing_fields"] == ["framing", "capture_method"]


def test_revised_view_rejects_renamed_duplicate_frames_and_label_as_cue():
    raw = request_for("sequence", branch="variants")
    raw["variation"]["direction"] = "view"
    task = resolve_request(raw)
    creative = fixture_creative(task)
    frames = creative["plan"]["frames"]
    frames[1]["visual_target"] = frames[0]["visual_target"]
    with pytest.raises(VariationError, match="重复使用同一画面目标"):
        compile_plan(task, creative)
    frames[1]["visual_target"] = "A visibly different frame"
    frames[1]["distinctive_cue"] = frames[1]["change_point"] + "。"
    with pytest.raises(VariationError, match="不能复写变化名称"):
        compile_plan(task, creative)


def test_placeholder_values_never_execute_as_templates():
    assert render_text("{{X}}", {"X": "{{USER_REQUEST}}"}) == "{{USER_REQUEST}}"
    with pytest.raises(VariationError):
        render_template("P1-05", {})


def test_normative_prompt_resources_are_unchanged():
    from image_variations.imaging import sha256

    index = json.loads((REFERENCES / "prompt-index.json").read_text())
    assert len(index["files"]) == 25
    for path, item in index["files"].items():
        assert sha256((REFERENCES / path).read_bytes()) == item["sha256"]


def test_direction_extension_is_registry_only(tmp_path):
    source = json.loads((REFERENCES / "directions/registry.json").read_text())
    extra = deepcopy(source["directions"][0])
    extra.update(key="new_direction", rule_file="new.txt")
    source["directions"] = [extra]
    (tmp_path / "registry.json").write_text(json.dumps(source))
    (tmp_path / "new.txt").write_text("A new direction's rules")
    assert registry(tmp_path)[1]["new_direction"]["text"] == "A new direction's rules"
