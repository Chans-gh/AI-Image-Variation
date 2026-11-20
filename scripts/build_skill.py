"""Build a standalone portable Skill folder with shared utilities and contracts bundled."""

import argparse
import shutil
import tempfile
import tomllib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]


def main():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skill", choices=["image-variations", "image-content-publishing"], default="image-variations"
    )
    parser.add_argument("--output")
    args = parser.parse_args()
    version = project["version"] if args.skill == "image-variations" else "dev"
    target = Path(args.output or f"dist/{args.skill}-{version}.zip").resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / args.skill
        shutil.copytree(
            ROOT / "skills" / args.skill,
            folder,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
        )
        shutil.copytree(
            ROOT / "shared", folder / "scripts/shared", ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
        contracts = folder / "references/contracts"
        if args.skill == "image-variations":
            shutil.copytree(ROOT / "contracts", contracts)
        else:
            for name in ("image-artifact-1.0", "image-artifact-1.1", "delivery-package"):
                shutil.copyfile(ROOT / "contracts" / f"{name}.schema.json", contracts / f"{name}.schema.json")
            shutil.copyfile(
                ROOT / "skills/image-variations/references/directions/registry.json",
                folder / "references/registry.json",
            )
        shutil.copyfile(ROOT / "LICENSE", folder / "LICENSE")
        (folder / "scripts/requirements.txt").write_text("\n".join(project["dependencies"]) + "\n")
        with ZipFile(target, "w", ZIP_DEFLATED) as z:
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    z.write(path, path.relative_to(Path(tmp)).as_posix())
    print(target)


if __name__ == "__main__":
    main()
