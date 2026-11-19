import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from conftest import finish
from image_variations.adapters.mock import MockProvider
from image_variations.errors import VariationError
from image_variations.packaging import import_package, package_artifact
from image_variations.resources import contracts_root

from shared.artifacts import content_assets, validate_artifact, verify_delivery
from shared.paths import safe_path


def result_root(make_job):
    status = finish(make_job(grid_size=2), MockProvider())
    return Path(status["artifact_path"])


@pytest.mark.parametrize(
    "path", ["../escape", "/tmp/escape", "a/../b", "a\\..\\b", "C:/escape", "./a", "a//b"]
)
def test_paths_are_contained(tmp_path, path):
    with pytest.raises(ValueError):
        safe_path(tmp_path, path)


def test_symlink_rejected(tmp_path):
    (tmp_path / "link").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError):
        safe_path(tmp_path, "link/escape")


def test_artifact_and_delivery_inventory_roundtrip(make_job, tmp_path):
    root = result_root(make_job)
    before = validate_artifact(root, contracts_root())
    assert len(before["content_assets"]) == 4 and before["native_resolution_verified"]
    verify_delivery(root, contracts_root())
    archive = package_artifact(root, tmp_path / "result.zip")
    imported = import_package(archive, tmp_path / "imported")
    assert validate_artifact(imported, contracts_root())["manifest"] == before["manifest"]


def test_tamper_and_undeclared_files_are_rejected(make_job):
    root = result_root(make_job)
    (root / "extra.txt").write_text("unlisted")
    with pytest.raises(ValueError, match="PACKAGE_INVENTORY_MISMATCH"):
        verify_delivery(root, contracts_root())
    (root / "extra.txt").unlink()
    tile = root / "tiles/01.png"
    tile.write_bytes(tile.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="ASSET_INTEGRITY_FAILED"):
        validate_artifact(root, contracts_root())


def test_crop_coordinates_and_board_exclusion(make_job):
    root = result_root(make_job)
    manifest = json.loads((root / "manifest.json").read_text())
    assert all(x["kind"] == "tile" for x in content_assets(manifest))
    manifest["images"][1]["crop_box"][0] = 1
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="GRID_GEOMETRY_INVALID"):
        validate_artifact(root, contracts_root())


@pytest.mark.parametrize("version", ["1.0", "1.1"])
def test_legacy_versions_read_without_inventing_native_evidence(make_job, version):
    root = result_root(make_job)
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["schema_version"] = version
    manifest.pop("dimension_execution")
    path.write_text(json.dumps(manifest))
    result = validate_artifact(root, contracts_root())
    assert result["validation_unverified"] and not result["native_resolution_verified"]


@pytest.mark.parametrize("bad_name", ["../escape.txt", "/absolute.txt", "a\\..\\escape", "C:/escape"])
def test_zip_slip_never_writes_destination(tmp_path, bad_name):
    archive = tmp_path / "bad.zip"
    with ZipFile(archive, "w") as z:
        z.writestr(bad_name, "bad")
    with pytest.raises((ValueError, VariationError)):
        import_package(archive, tmp_path / "target")
    assert not (tmp_path / "target").exists()


def test_zip_collision_and_bomb_limits(tmp_path):
    for i, entries in enumerate([[("A.txt", "a"), ("a.txt", "b")], [("a", "a"), ("a/b", "b")]]):
        archive = tmp_path / f"{i}.zip"
        with ZipFile(archive, "w") as z:
            for name, data in entries:
                z.writestr(name, data)
        with pytest.raises(VariationError):
            import_package(archive, tmp_path / f"target{i}")


def test_new_schema_version_is_explicitly_rejected(make_job):
    root = result_root(make_job)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_version"] = "2.0"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="UNSUPPORTED_SCHEMA_VERSION"):
        validate_artifact(root, contracts_root())
