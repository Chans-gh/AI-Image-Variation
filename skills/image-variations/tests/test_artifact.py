"""Phase 5 测试：产物落盘、契约校验、打包、续跑与 CLI。

运行：.venv/bin/python skills/image-variations/tests/test_artifact.py
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "support"))
sys.path.insert(0, str(HERE.parent / "scripts"))

import paths  # noqa: E402

from iv_skill.anchor import Anchor  # noqa: E402
from iv_skill.artifact import write_sequence_artifact  # noqa: E402
from iv_skill.config import load_config  # noqa: E402
from iv_skill.prompt_builder import DirectionRegistry  # noqa: E402
from iv_skill.provider import ReferenceImage  # noqa: E402
from iv_skill.provider.mock import MockAdapter, MockScenario, default_capability  # noqa: E402
from iv_skill.sequence import execute_sequence  # noqa: E402
from iv_skill.task import run_task, _resolved  # noqa: E402
from shared import baseline  # noqa: E402
from shared.errors import IVError  # noqa: E402
from shared.hashing import sha256_file  # noqa: E402
from shared.jsonio import read_json  # noqa: E402
from shared.pkg import inspect_zip, verify_manifest  # noqa: E402

REPO = HERE.parents[2]
SCRIPTS = HERE.parent / "scripts"
CONFIG = load_config()
REGISTRY = DirectionRegistry()


def grid_spec(**overrides) -> dict:
    spec = {"task_id": "t_grid", "output_mode": "grid", "grid_size": 2, "direction": "light",
            "input_type": "text_only", "subject": "一只浅灰陶瓷杯", "aspect_ratio": "1:1",
            "provider": {"type": "mock_synthetic"}}
    spec.update(overrides)
    return spec


def sequence_spec(**overrides) -> dict:
    spec = {"task_id": "t_seq", "output_mode": "sequence", "image_count": 4,
            "branch": "progression", "direction": "light", "input_type": "text_only",
            "subject": "一只浅灰陶瓷杯", "aspect_ratio": "3:4",
            "provider": {"type": "mock_synthetic"}}
    spec.update(overrides)
    return spec


def frame_png(index: int, size=(768, 1024)) -> bytes:
    """彼此可区分的帧图（避免触发近重复重试）。"""
    color = ((index * 97) % 256, (index * 61 + 40) % 256, (index * 151 + 80) % 256)
    return png_bytes(size=size, color=color)


class FlakyAdapter:
    """包装合成适配器：指定帧号永远失败，或整体抛致命错误（用于验证失败时不落半成品）。"""

    provider_type = "mock_synthetic"

    def __init__(self, inner, *, fail_indices=(), fatal: IVError | None = None):
        self.inner = inner
        self.fail_indices = set(fail_indices)
        self.fatal = fatal
        self.calls = 0

    @property
    def capability(self):
        return self.inner.capability

    def generate(self, request, *, budget=None, policy=None, sleep=None):
        self.calls += 1
        if self.fatal is not None:
            if budget is not None:
                budget.consume(tier=request.resolution_tier or "1K")
            raise self.fatal
        tail = request.purpose.rsplit("_", 1)[-1]
        index = int(tail) if tail.isdigit() else 0
        if index in self.fail_indices:
            if budget is not None:
                budget.consume(tier=request.resolution_tier or "1K")
            raise IVError("IMAGE_VALIDATION_FAILED", f"测试注入失败：第 {index} 帧")
        return self.inner.generate(request, budget=budget, policy=policy, sleep=sleep)


def png_bytes(size=(768, 1024), color=(120, 140, 160)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


class TempWorkspace(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="iv_test_")
        self.workspace = Path(self._tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestGridArtifact(TempWorkspace):
    def test_layout_names_and_contract(self):
        result = run_task(grid_spec(pack_zip=True), workspace=self.workspace)
        root = Path(result["artifact_dir"])
        self.assertTrue((root / "board.png").is_file())
        for index in range(1, 5):
            self.assertTrue((root / f"images/{index:02d}.png").is_file())
        self.assertTrue((root / "manifest.json").is_file())
        self.assertTrue((root / "delivery-package.json").is_file())
        self.assertTrue(Path(result["zip_path"]).is_file())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["tier"], "2K")
        self.assertGreater(result["seam_score"], CONFIG["seam"]["pass_threshold"])

        manifest = read_json(root / "manifest.json")
        baseline.validate_instance("image-artifact.schema.json", manifest)
        self.assertEqual(manifest["schema_version"], "1.1")
        self.assertEqual(manifest["artifact_type"], "image_variations")
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(manifest["grid_size"], 2)
        self.assertEqual(manifest["grid_size_source"], "user")
        self.assertIsNone(manifest["branch"])
        self.assertTrue(manifest["validation"]["grid_structure_passed"])
        self.assertEqual(manifest["validation"]["actual_aspect_ratio"], "1:1")
        board = manifest["images"][0]
        self.assertEqual((board["kind"], board["index"], board["path"]),
                         ("board", 0, "board.png"))
        tiles = manifest["images"][1:]
        self.assertEqual([tile["index"] for tile in tiles], [1, 2, 3, 4])
        self.assertEqual([(tile["row"], tile["column"]) for tile in tiles],
                         [(1, 1), (1, 2), (2, 1), (2, 2)])
        self.assertEqual(tiles[0]["crop_box"], [0, 0, 1024, 1024])
        self.assertEqual(tiles[3]["crop_box"], [1024, 1024, 2048, 2048])
        for tile in tiles:
            self.assertEqual(sha256_file(root / tile["path"]), tile["sha256"])
            self.assertEqual(tile["width"], 1024)
        self.assertEqual(manifest["provider_type"], "mock_synthetic")

    def test_rerun_same_revision_leaves_no_stale_files(self):
        """同一 revision 重跑：上一版的文件绝不能留在目录里冒充本版产物。"""
        from unittest import mock

        import iv_skill.task as task_module
        from iv_skill.synthetic import build_synthetic_adapter

        first = run_task(grid_spec(), workspace=self.workspace)   # rev1 完整（2×2：整板 + 4 格）
        def flaky_adapter(spec, *, config, grid_size):
            inner = build_synthetic_adapter(grid_size=grid_size, config=config)
            return FlakyAdapter(inner, fatal=IVError("AUTH_FAILED", "注入的致命错误"))
        with mock.patch.object(task_module, "build_task_adapter", flaky_adapter):
            with self.assertRaises(IVError):
                run_task(grid_spec(), workspace=self.workspace)
        root = self.workspace / "iv_t_grid_rev1"
        manifest = read_json(root / "manifest.json")
        # 本轮没写成功 → 旧产物必须逐字节原样保留，且没有暂存/备份目录残留
        self.assertEqual(len(manifest["images"]), 5)
        self.assertEqual({p: sha256_file(root / p) for p in first["checksums"]},
                         first["checksums"])
        self.assertEqual(verify_manifest(root), [])
        self.assertEqual(list(self.workspace.glob(".*staging*")), [])
        self.assertEqual(list(self.workspace.glob(".*previous*")), [])

    def test_stale_file_is_detected(self):
        """不变量守卫：目录里多出未登记文件必须报 PACKAGE_MANIFEST_INVALID。"""
        from iv_skill.artifact import _assert_files_match_manifest

        root = self.workspace / "probe"
        (root / "images").mkdir(parents=True)
        (root / "images" / "01.png").write_bytes(png_bytes((64, 64)))
        (root / "manifest.json").write_text("{}", encoding="utf-8")
        manifest = {"images": [{"path": "images/01.png"}]}
        self.assertIsNone(_assert_files_match_manifest(root, manifest))
        (root / "images" / "99.png").write_bytes(png_bytes((64, 64)))
        with self.assertRaises(IVError) as ctx:
            _assert_files_match_manifest(root, manifest)
        self.assertEqual(ctx.exception.code, "PACKAGE_MANIFEST_INVALID")
        self.assertIn("99.png", ctx.exception.message)

    def test_package_covers_every_file(self):
        result = run_task(grid_spec(pack_zip=True), workspace=self.workspace)
        root = Path(result["artifact_dir"])
        self.assertEqual(verify_manifest(root), [])
        package = read_json(root / "delivery-package.json")
        baseline.validate_instance("delivery-package.schema.json", package)
        # delivery-package.json 不登记自己（与基线示例一致）
        on_disk = sorted(path.relative_to(root).as_posix()
                         for path in root.rglob("*") if path.is_file()
                         and path.name != "delivery-package.json")
        self.assertEqual(sorted(entry["path"] for entry in package["entries"]), on_disk)
        self.assertEqual(inspect_zip(result["zip_path"]), [])
        with zipfile.ZipFile(result["zip_path"]) as archive:
            self.assertEqual(sorted(archive.namelist()),
                             sorted(entry["path"] for entry in package["entries"]))
            self.assertNotIn("delivery-package.json", archive.namelist())

    def test_manifest_before_zip_has_archive_metadata(self):
        result = run_task(grid_spec(pack_zip=True), workspace=self.workspace)
        package = read_json(Path(result["artifact_dir"]) / "delivery-package.json")
        self.assertIn("archive", package)
        self.assertEqual(package["archive"]["sha256"], sha256_file(result["zip_path"]))
        self.assertEqual(inspect_zip(result["zip_path"]), [])

    def test_same_revision_is_reproducible(self):
        first = run_task(grid_spec(), workspace=self.workspace)
        second = run_task(grid_spec(), workspace=self.workspace)
        self.assertEqual(first["artifact_dir"], second["artifact_dir"])
        # manifest.json 含 created_at，故只要求图片内容逐字节一致
        images = {path: digest for path, digest in first["checksums"].items()
                  if path != "manifest.json"}
        self.assertTrue(images)
        self.assertEqual(images, {path: digest for path, digest in second["checksums"].items()
                                  if path != "manifest.json"})


class TestSequenceArtifact(TempWorkspace):
    def test_contract_of_partial_and_complete(self):
        result = run_task(sequence_spec(pack_zip=True), workspace=self.workspace)
        root = Path(result["artifact_dir"])
        manifest = read_json(root / "manifest.json")
        baseline.validate_instance("image-artifact.schema.json", manifest)
        self.assertEqual(manifest["status"], "completed")
        self.assertIsNone(manifest["grid_size"])
        self.assertNotIn("grid_size_source", manifest)
        self.assertEqual(manifest["branch"], "progression")
        self.assertTrue(manifest["validation"]["sequence_complete"])
        self.assertEqual(manifest["validation"]["actual_aspect_ratio"], "3:4")
        frames = manifest["images"]
        self.assertEqual([frame["index"] for frame in frames], [1, 2, 3, 4])
        for frame in frames:
            self.assertEqual(frame["kind"], "frame")
            self.assertEqual(frame["status"], "completed")
            self.assertEqual(frame["prompt_template_id"], "sequence-prompt-v1")
            self.assertTrue(frame["phase_label"])
            self.assertTrue(frame["prompt_summary"])
        self.assertEqual(manifest["prompt_template_version"], "sequence-prompt-v1")
        self.assertEqual(manifest["consistency_anchor"],
                         {"type": "first_frame", "asset_id": "frame_01"})
        self.assertEqual(verify_manifest(root), [])
        self.assertEqual(inspect_zip(result["zip_path"]), [])

    def test_retry_counts_and_execution(self):
        manifest = read_json(Path(run_task(sequence_spec(), workspace=self.workspace)["artifact_dir"])
                             / "manifest.json")
        self.assertEqual(manifest["execution"]["calls_used"], 4)
        self.assertEqual(manifest["execution"]["calls_planned"], 6)
        self.assertEqual(manifest["execution"]["budget_exceeded"], False)
        self.assertEqual(manifest["retry_counts"]["frame_03"], 0)
        self.assertEqual(manifest["errors"], [])


class TestPartialFailureArtifact(TempWorkspace):
    def build_partial(self) -> Path:
        """帧 3 连续失败，其余成功；直接落盘成 partial_failed 产物。"""
        good = {index: frame_png(index) for index in (1, 2, 4)}
        scenarios = ([MockScenario(image_bytes=good[1]), MockScenario(image_bytes=good[2])]
                     + [MockScenario(image_bytes=b"broken")] * 3
                     + [MockScenario(image_bytes=good[4])])
        adapter = MockAdapter(capability=default_capability(), scenarios=scenarios)
        execution = execute_sequence(adapter=adapter, direction_key="light", branch="progression",
                                     frame_count=4, aspect_ratio="3:4", input_type="text_only",
                                     anchor=Anchor("first_frame", "一只浅灰陶瓷杯"),
                                     registry=REGISTRY, config=CONFIG, sleep=lambda _: None)
        self.assertEqual(execution.status, "partial_failed")
        result = write_sequence_artifact(workspace=self.workspace, task_id="t_partial",
                                         execution=execution,
                                         anchor=Anchor("first_frame", "一只浅灰陶瓷杯").to_dict(),
                                         registry=REGISTRY, input_type="text_only",
                                         config=CONFIG)
        return result.root

    def test_failed_frame_keeps_slot(self):
        root = self.build_partial()
        manifest = read_json(root / "manifest.json")
        baseline.validate_instance("image-artifact.schema.json", manifest)
        self.assertEqual(manifest["status"], "partial_failed")
        self.assertEqual([frame["index"] for frame in manifest["images"]], [1, 2, 4])
        self.assertEqual(manifest["image_count"], 4)
        self.assertFalse(manifest["validation"]["sequence_complete"])
        self.assertEqual(manifest["validation"]["failed_indices"], [3])
        self.assertTrue(manifest["validation"]["asset_integrity_passed"])
        codes = [error["code"] for error in manifest["errors"]]
        self.assertIn("IMAGE_VALIDATION_FAILED", codes)
        self.assertEqual(manifest["errors"][0]["asset_id"], "frame_03")
        self.assertTrue(manifest["errors"][0]["retry_available"])
        self.assertFalse((root / "images/03.png").exists())
        self.assertEqual(verify_manifest(root), [], "只登记真实存在的文件")
        self.assertEqual(inspect_zip_optional(root), [])

    def test_resume_creates_child_revision(self):
        previous = self.build_partial()
        before = {frame["index"]: frame["sha256"]
                  for frame in read_json(previous / "manifest.json")["images"]}
        result = run_task_with_resume(sequence_spec(task_id="t_partial"), previous,
                                      workspace=self.workspace)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["failed_indices"], [])
        root = Path(result["artifact_dir"])
        manifest = read_json(root / "manifest.json")
        baseline.validate_instance("image-artifact.schema.json", manifest)
        self.assertEqual(manifest["artifact_revision"], "rev2")
        self.assertEqual(manifest["parent_artifact_revision"], "rev1")
        self.assertEqual([frame["index"] for frame in manifest["images"]], [1, 2, 3, 4])
        for frame in manifest["images"]:
            if frame["index"] in before:
                self.assertEqual(frame["sha256"], before[frame["index"]],
                                 "已成功帧不得被重写")
        self.assertEqual(verify_manifest(root), [])


def inspect_zip_optional(root: Path) -> list[str]:
    zip_path = root.parent / f"{root.name}.zip"
    return inspect_zip(zip_path) if zip_path.is_file() else []


def run_task_with_resume(spec: dict, previous_dir: Path, *, workspace: Path) -> dict:
    from iv_skill.task import resume_sequence_task

    return resume_sequence_task(spec, previous_dir=previous_dir, workspace=workspace)


from iv_skill.task import resume_sequence_task  # noqa: E402


class TestImageAnchorResume(TempWorkspace):
    """续跑必须确认"还是同一张参考图"，否则会把不同锚点的帧混进同一组。"""

    def partial_with_reference(self, *, name="ref_a.png", fail_index=3):
        from unittest import mock

        import iv_skill.task as task_module
        from iv_skill.synthetic import build_synthetic_adapter

        reference = self.workspace / name
        reference.write_bytes(png_bytes((512, 512)))
        spec = sequence_spec(task_id="t_anchor", input_type="image_only",
                             reference_path=name, image_count=4)
        def flaky_adapter(spec_, *, config, grid_size):
            inner = build_synthetic_adapter(grid_size=grid_size, config=config)
            return FlakyAdapter(inner, fail_indices={fail_index})
        with mock.patch.object(task_module, "build_task_adapter", flaky_adapter):
            result = run_task(spec, workspace=self.workspace)
        self.assertEqual(result["status"], "partial_failed")
        self.assertEqual(result["failed_indices"], [fail_index])
        return spec, Path(result["artifact_dir"])

    def test_fingerprint_is_recorded_and_only_a_hash(self):
        _, root = self.partial_with_reference()
        manifest = read_json(root / "manifest.json")
        anchor = manifest["consistency_anchor"]
        self.assertEqual(anchor["type"], "input_image")
        self.assertEqual(anchor["reference"], "not-exported")
        self.assertRegex(anchor["sha256"], r"^[a-f0-9]{64}$")
        blob = b"".join(path.read_bytes() for path in root.rglob("*") if path.is_file())
        self.assertNotIn(b"ref_a.png", blob)

    def test_resume_same_reference_succeeds(self):
        spec, root = self.partial_with_reference()
        result = resume_sequence_task(spec, previous_dir=root, workspace=self.workspace)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["artifact_revision"], "rev2")
        manifest = read_json(Path(result["artifact_dir"]) / "manifest.json")
        baseline.validate_instance("image-artifact.schema.json", manifest)
        self.assertEqual(manifest["consistency_anchor"]["sha256"],
                         read_json(root / "manifest.json")["consistency_anchor"]["sha256"])
        self.assertEqual([f["index"] for f in manifest["images"]], [1, 2, 3, 4])

    def test_resume_changed_reference_blocked(self):
        spec, root = self.partial_with_reference()
        (self.workspace / "ref_b.png").write_bytes(png_bytes((512, 512), color=(20, 200, 90)))
        swapped = dict(spec, reference_path="ref_b.png")
        with self.assertRaises(IVError) as ctx:
            resume_sequence_task(swapped, previous_dir=root, workspace=self.workspace)
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")
        self.assertIn("锚点版本不一致", ctx.exception.message)
        self.assertFalse((self.workspace / "iv_t_anchor_rev2").exists(),
                         "被拒时不得产出新修订")

    def test_resume_legacy_manifest_without_fingerprint_blocked(self):
        from shared.jsonio import write_json

        spec, root = self.partial_with_reference()
        manifest = read_json(root / "manifest.json")
        manifest["consistency_anchor"].pop("sha256")
        write_json(root / "manifest.json", manifest)
        with self.assertRaises(IVError) as ctx:
            resume_sequence_task(spec, previous_dir=root, workspace=self.workspace)
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")
        self.assertIn("缺少参考图指纹", ctx.exception.message)


class TestGuardsAndSecrets(TempWorkspace):
    def test_task_id_traversal_blocked(self):
        with self.assertRaises(IVError) as ctx:
            run_task(grid_spec(task_id="../evil"), workspace=self.workspace)
        self.assertEqual(ctx.exception.code, "PATH_TRAVERSAL_BLOCKED")
        self.assertEqual(list(self.workspace.iterdir()), [], "被阻断时不得落任何文件")

    def test_reference_traversal_blocked(self):
        outside = self.workspace / "iv_outside.png"
        outside.write_bytes(png_bytes(size=(512, 512)))
        try:
            with self.assertRaises(IVError) as ctx:
                run_task(grid_spec(input_type="image_only", reference_path="../iv_outside.png"),
                         workspace=self.workspace) if False else run_task(
                    grid_spec(input_type="image_only", reference_path="sub/../../iv_outside.png"),
                    workspace=self.workspace)
            self.assertEqual(ctx.exception.code, "PATH_TRAVERSAL_BLOCKED")
        finally:
            outside.unlink(missing_ok=True)

    def test_missing_reference_blocked(self):
        with self.assertRaises(IVError) as ctx:
            run_task(grid_spec(input_type="image_only", reference_path="nope.png"),
                     workspace=self.workspace)
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")

    def test_reference_is_never_exported(self):
        reference = self.workspace / "用户原图.png"
        reference.write_bytes(png_bytes(size=(512, 512)))
        result = run_task(grid_spec(input_type="image_only", reference_path=reference.name),
                          workspace=self.workspace)
        root = Path(result["artifact_dir"])
        manifest = read_json(root / "manifest.json")
        self.assertEqual(manifest["consistency_anchor"],
                         {"type": "input_image", "reference": "not-exported"})
        blob = b"".join(path.read_bytes() for path in root.rglob("*") if path.is_file())
        self.assertNotIn(reference.name.encode("utf-8"), blob)
        self.assertNotIn(str(reference).encode("utf-8"), blob)
        self.assertNotIn(reference.read_bytes(), blob, "原图不得进交付包")

    def test_data_uri_and_plain_key_rejected(self):
        spec = grid_spec(provider={"type": "openai_image_compatible",
                                   "base_url": "https://api.example.com/v1",
                                   "api_key_secret_ref": "sk-plaintext",
                                   "capability": {"adapter_version": "x"}})
        with self.assertRaises(IVError) as ctx:
            run_task(spec, workspace=self.workspace)
        self.assertIn(ctx.exception.code, ("MODEL_CAPABILITY_UNSUPPORTED", "AUTH_FAILED"))

    def test_external_provider_requires_capability(self):
        spec = grid_spec(provider={"type": "openai_image_compatible",
                                   "base_url": "https://api.example.com/v1"})
        with self.assertRaises(IVError) as ctx:
            run_task(spec, workspace=self.workspace)
        self.assertEqual(ctx.exception.code, "MODEL_CAPABILITY_UNSUPPORTED")

    def test_builtin_reports_unavailable(self):
        with self.assertRaises(IVError) as ctx:
            run_task(grid_spec(provider={"type": "builtin"}), workspace=self.workspace)
        self.assertEqual(ctx.exception.code, "BUILTIN_UNAVAILABLE")


class TestCli(TempWorkspace):
    def run_cli(self, spec: dict, *extra: str) -> subprocess.CompletedProcess:
        spec_path = self.workspace / "spec.json"
        spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "run_task.py"), "--input", str(spec_path),
             "--workspace", str(self.workspace / "out"), *extra],
            capture_output=True, text=True, cwd=str(REPO))

    def test_cli_writes_artifact(self):
        proc = self.run_cli(grid_spec(pack_zip=True))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["output_mode"], "grid")
        self.assertTrue(Path(payload["manifest_path"]).is_file())
        manifest = read_json(Path(payload["manifest_path"]))
        baseline.validate_instance("image-artifact.schema.json", manifest)

    def test_cli_preflight_only(self):
        proc = self.run_cli(grid_spec(), "--preflight-only")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["preflight"]["tier"], "2K")
        self.assertEqual(payload["preflight"]["calls_planned"], 3)
        self.assertFalse((self.workspace / "out").exists(), "预检不得落盘")

    def test_cli_reports_error_code_without_traceback(self):
        proc = self.run_cli(grid_spec(grid_size=5))
        self.assertEqual(proc.returncode, 2)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "INVALID_MODE_OR_COUNT")
        self.assertNotIn("Traceback", proc.stdout + proc.stderr)

    def test_cli_invalid_json(self):
        spec_path = self.workspace / "bad.json"
        spec_path.write_text("{not json", encoding="utf-8")
        proc = subprocess.run([sys.executable, str(SCRIPTS / "run_task.py"), "--input",
                               str(spec_path)], capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(json.loads(proc.stdout)["error"]["code"], "INVALID_INPUT")

    def test_cli_sequence_complete(self):
        proc = self.run_cli(sequence_spec(task_id="cli_seq"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["delivered_images"], 4)

    def test_cli_retry_failed_of(self):
        previous = TestPartialFailureArtifact("test_failed_frame_keeps_slot")
        previous.workspace = self.workspace
        partial_dir = previous.build_partial()
        proc = self.run_cli(sequence_spec(task_id="t_partial"),
                            "--retry-failed-of", str(partial_dir))
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["failed_indices"], [])
        self.assertEqual(payload["artifact_revision"], "rev2")
        manifest = read_json(Path(payload["manifest_path"]))
        baseline.validate_instance("image-artifact.schema.json", manifest)
        self.assertEqual([frame["index"] for frame in manifest["images"]], [1, 2, 3, 4])


class TestResolvedSpec(unittest.TestCase):
    def test_defaults_applied(self):
        resolved = _resolved({"task_id": "x", "output_mode": "grid", "direction": "state",
                              "input_type": "text_only"})
        self.assertEqual(resolved["grid_size"], CONFIG["defaults"]["grid_size"])
        self.assertEqual(resolved["aspect_ratio"], CONFIG["defaults"]["text_only_aspect_ratio"])
        self.assertEqual(resolved["revision"], "rev1")
        self.assertIsNone(resolved["branch"])

    def test_multiple_directions_rejected(self):
        with self.assertRaises(IVError) as ctx:
            _resolved(grid_spec(direction=["light", "view"]))
        self.assertEqual(ctx.exception.code, "MULTIPLE_DIRECTIONS")
        self.assertEqual(_resolved(grid_spec(direction=["light"]))["direction"], "light")

    def test_unknown_direction_rejected(self):
        with tempfile.TemporaryDirectory(prefix="iv_dir_") as tmp:
            with self.assertRaises(IVError) as ctx:
                run_task(grid_spec(direction="no_such_direction"), workspace=tmp)
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")
        self.assertIn("no_such_direction", ctx.exception.message)

    def test_missing_fields_rejected(self):
        for missing in ("task_id", "output_mode", "direction", "input_type"):
            with self.subTest(missing=missing):
                spec = grid_spec()
                del spec[missing]
                with self.assertRaises(IVError) as ctx:
                    _resolved(spec)
                self.assertEqual(ctx.exception.code, "INVALID_INPUT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
