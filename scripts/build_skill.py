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
    parser.add_argument("--output", default=f"dist/image-variations-{project['version']}.zip")
    args = parser.parse_args()
    target = Path(args.output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "image-variations"
        shutil.copytree(
            ROOT / "skills/image-variations",
            folder,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
        )
        shutil.copytree(
            ROOT / "shared", folder / "scripts/shared", ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
        shutil.copytree(ROOT / "contracts", folder / "references/contracts")
        shutil.copyfile(ROOT / "LICENSE", folder / "LICENSE")
        (folder / "scripts/requirements.txt").write_text("\n".join(project["dependencies"]) + "\n")
        with ZipFile(target, "w", ZIP_DEFLATED) as z:
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    z.write(path, path.relative_to(Path(tmp)).as_posix())
    print(target)


if __name__ == "__main__":
    main()
