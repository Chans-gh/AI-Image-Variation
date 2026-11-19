import json
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]
REFERENCES = SKILL_ROOT / "references"
if not REFERENCES.is_dir():
    REFERENCES = Path(sys.prefix) / "share/image-variations/references"


def registry(root=None):
    root = Path(root or REFERENCES / "directions")
    data = json.loads((root / "registry.json").read_text())
    entries = {}
    for item in data["directions"]:
        path = (root / item["rule_file"]).resolve()
        if not path.is_relative_to(root.resolve()) or item["key"] in entries:
            raise ValueError("Invalid direction registry")
        entries[item["key"]] = {**item, "text": path.read_text()}
    return data["version"], entries


def contracts_root():
    bundled = REFERENCES / "contracts"
    return bundled if bundled.exists() else SKILL_ROOT.parents[1] / "contracts"


def prompt_template_version():
    return json.loads((REFERENCES / "prompt-index.json").read_text())["prompt_template_version"]
