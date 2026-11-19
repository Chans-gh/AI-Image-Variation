"""Extract normative prompt blocks from SPEC 01 and its prompt revision."""

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "specs/01-Image-Variations-Skill-v1.3.md"
REVISION = ROOT / "specs/01-Image-Variations-Prompt-Revision-v1.4.md"
TARGET = ROOT / "skills/image-variations/references"
PROMPT_VERSION = "1.4.3"
DIRECTION_REVISIONS = {"view": "1.4.1", "scene": "1.4.2", "lens": "1.4.3"}
EVIDENCE_FIELDS = {
    "view": ("camera_position", "visible_projection", "world_projection"),
    "scene": ("place", "route_connection", "visible_landmark"),
    "weather": ("condition", "environment_effect", "subject_response"),
    "lens": ("framing", "capture_method", "visible_effect"),
    "usage": ("deliverable", "communication_goal", "placement_or_crop", "acceptance_test"),
}


def main():
    sections = re.split(r"(?=^### )", SOURCE.read_text(), flags=re.MULTILINE)
    index = {}

    def save(name, body, heading, source=SOURCE):
        body = body.rstrip() + "\n"
        path = TARGET / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        index[name] = {
            "source": source.relative_to(ROOT).as_posix(),
            "source_heading": heading,
            "sha256": hashlib.sha256(body.encode()).hexdigest(),
        }

    for section in sections:
        heading = section.splitlines()[0]
        blocks = re.findall(r"```text\n(.*?)\n```", section, re.DOTALL)
        if not blocks:
            continue
        match = re.search(r"P1-(\d+)｜", heading)
        if match and int(match[1]) <= 10:
            save(f"prompts/P1-{match[1]}.txt", blocks[0], heading)
        elif heading.startswith("### 16.13."):
            key = re.search(r"`(\w+)`", heading)[1]
            save(f"directions/{key}.txt", blocks[0], heading)
        elif heading.startswith("### 16.8 "):
            for match in re.finditer(r"\[BRANCH: (\w+) — .*?(?=\n\[BRANCH:|\Z)", blocks[0], re.DOTALL):
                save(f"prompts/branch-{match[1]}.txt", match[0], heading)
    assert len(index) == 25, f"Expected 10 templates, 3 branches, 12 rules; got {len(index)}"
    revised = set()
    for section in re.split(r"(?=^### )", REVISION.read_text(), flags=re.MULTILINE)[1:]:
        heading = section.splitlines()[0]
        name = heading.removeprefix("### ")
        blocks = re.findall(r"```text\n(.*?)\n```", section, re.DOTALL)
        if name not in index or name in revised or len(blocks) != 1:
            raise ValueError(f"Invalid prompt revision section: {heading}")
        save(name, blocks[0], heading, REVISION)
        revised.add(name)
    if not {f"directions/{key}.txt" for key in EVIDENCE_FIELDS} <= revised:
        raise ValueError("Prompt revision is missing a direction with required evidence")
    (TARGET / "prompt-index.json").write_text(
        json.dumps(
            {
                "spec_version": "1.3",
                "prompt_template_version": PROMPT_VERSION,
                "files": index,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    keys = [
        "state",
        "view",
        "scene",
        "detail",
        "light",
        "composition",
        "style",
        "styling",
        "weather",
        "lens",
        "usage",
        "narrative",
    ]
    labels = [
        "状态拓展",
        "视角拓展",
        "场景拓展",
        "细节拓展",
        "光影拓展",
        "构图拓展",
        "风格拓展",
        "造型拓展",
        "时间天气拓展",
        "镜头语言拓展",
        "用途拓展",
        "情节拓展",
    ]
    directions = [
        {
            "key": key,
            "label": label,
            "version": DIRECTION_REVISIONS.get(key, "1.4.0" if key in EVIDENCE_FIELDS else "1.3.0"),
            "rule_file": key + ".txt",
            **({"plan_evidence_fields": EVIDENCE_FIELDS[key]} if key in EVIDENCE_FIELDS else {}),
            "branch_default": "progression"
            if key in {"detail", "weather"}
            else "narrative"
            if key == "narrative"
            else "variants",
        }
        for key, label in zip(keys, labels)
    ]
    (TARGET / "directions/registry.json").write_text(
        json.dumps(
            {
                "version": PROMPT_VERSION,
                "directions": directions,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    print(f"Extracted {len(index)} normative resources")


if __name__ == "__main__":
    main()
