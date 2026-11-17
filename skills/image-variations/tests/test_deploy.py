"""部署与自检：整仓/分离两种形态、部署错误不占业务码、失败不出 traceback。

运行：.venv/bin/python skills/image-variations/tests/test_deploy.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
REPO = HERE.parents[2]
CLI = SKILL / "scripts" / "run_task.py"
SPEC_DIRNAME = "Image_Variations_Dual_Skill_SPEC_v1.1"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".venv")

GRID_SPEC = {
    "task_id": "deploy_grid", "output_mode": "grid", "grid_size": 2, "direction": "light",
    "input_type": "text_only", "subject": "一只浅灰陶瓷杯", "resolution_tier": "2K",
    "provider": {"type": "mock_synthetic"},
}


def invoke(cli: Path, *args: str, env: dict | None = None,
           cwd: Path | None = None) -> subprocess.CompletedProcess:
    """跑 CLI，并清掉宿主环境里可能残留的覆盖变量，保证用例之间互不污染。"""
    environ = dict(os.environ)
    for key in ("IV_REPO_ROOT", "IV_BASELINE_ROOT"):
        environ.pop(key, None)
    if env:
        environ.update(env)
    return subprocess.run([sys.executable, str(cli), *args],
                          capture_output=True, text=True, cwd=str(cwd or REPO), env=environ)


def payload(proc: subprocess.CompletedProcess) -> dict:
    return json.loads(proc.stdout)


class DeployCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="iv_deploy_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write_task(self, spec: dict | None = None) -> Path:
        path = self.tmp / "task.json"
        path.write_text(json.dumps(spec or GRID_SPEC, ensure_ascii=False), encoding="utf-8")
        return path

    def copy_skill_only(self, root: Path) -> Path:
        """模拟"宿主只把技能目录当分发单元"：没有 shared/、没有基线。"""
        target = root / "skills" / "image-variations"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(SKILL, target, ignore=IGNORE)
        return target / "scripts" / "run_task.py"

    def build_split_tree(self) -> tuple[Path, Path]:
        """分离部署：技能在 app/ 下（深度随意），shared/ 与基线在 vendor/ 下。"""
        vendor = self.tmp / "vendor"
        (vendor / "shared").parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(REPO / "shared", vendor / "shared", ignore=IGNORE)
        shutil.copytree(REPO / SPEC_DIRNAME, vendor / SPEC_DIRNAME, ignore=IGNORE)
        return self.copy_skill_only(self.tmp / "app"), vendor


class TestSelfCheck(DeployCase):
    def test_self_check_ok_in_repo_layout(self):
        proc = invoke(CLI, "--self-check", "--pretty")
        data = payload(proc)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(data["ok"])
        self.assertEqual(data["check"], "self_check")
        self.assertEqual(data["baseline"]["declared_files"], 238)
        self.assertTrue(data["baseline"]["ok"])
        self.assertEqual(data["skill_config"]["directions"], 12)
        self.assertTrue(data["workspace"]["writable"])
        self.assertTrue(data["pillow"])

    def test_self_check_skips_business_error_channel(self):
        proc = invoke(CLI, "--self-check")
        data = payload(proc)
        self.assertNotIn("error", data, "自检不是任务失败，不应占用业务错误码")

    def test_self_check_missing_repo_root_reports_deployment_error(self):
        cli = self.copy_skill_only(self.tmp / "only")
        proc = invoke(cli, "--self-check", "--pretty")
        data = payload(proc)
        self.assertEqual(proc.returncode, 4)
        self.assertFalse(data["ok"])
        self.assertEqual(data["deployment_error"]["stage"], "bootstrap")
        self.assertIn("hint", data["deployment_error"])
        self.assertNotIn("Traceback", proc.stderr + proc.stdout)

    def test_self_check_uses_iv_baseline_root_override(self):
        other = self.tmp / "elsewhere"
        shutil.copytree(REPO / SPEC_DIRNAME, other, ignore=IGNORE)
        proc = invoke(CLI, "--self-check", "--pretty",
                      env={"IV_BASELINE_ROOT": str(other)})
        data = payload(proc)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(data["baseline"]["root"], str(other.resolve()))
        self.assertEqual(data["baseline"]["declared_files"], 238)


class TestDeploymentFailures(DeployCase):
    def test_task_run_without_repo_root_is_deployment_error_not_traceback(self):
        cli = self.copy_skill_only(self.tmp / "only")
        proc = invoke(cli, "--input", str(self.write_task()), "--workspace", str(self.tmp / "ws"))
        data = payload(proc)
        self.assertEqual(proc.returncode, 4)
        self.assertEqual(data["deployment_error"]["stage"], "bootstrap")
        self.assertNotIn("error", data)
        self.assertNotIn("Traceback", proc.stderr + proc.stdout)

    def test_missing_input_is_invalid_input(self):
        proc = invoke(CLI, "--workspace", str(self.tmp / "ws"))
        data = payload(proc)
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(data["error"]["code"], "INVALID_INPUT")


class TestSplitDeployment(DeployCase):
    def test_repo_layout_runs_without_any_env(self):
        proc = invoke(CLI, "--input", str(self.write_task()), "--workspace", str(self.tmp / "ws"),
                      cwd=self.tmp)
        data = payload(proc)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(data["status"], "completed")

    def test_split_deployment_fails_loudly_without_override(self):
        cli, _ = self.build_split_tree()
        proc = invoke(cli, "--input", str(self.write_task()), "--workspace", str(self.tmp / "ws"))
        data = payload(proc)
        self.assertEqual(proc.returncode, 4)
        self.assertEqual(data["deployment_error"]["stage"], "bootstrap")

    def test_split_deployment_runs_with_iv_repo_root(self):
        cli, vendor = self.build_split_tree()
        proc = invoke(cli, "--input", str(self.write_task()), "--workspace", str(self.tmp / "ws"),
                      env={"IV_REPO_ROOT": str(vendor)})
        data = payload(proc)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(data["status"], "completed")
        self.assertGreater(data["seam_score"], 0.85)

    def test_split_self_check_reports_vendor_root(self):
        cli, vendor = self.build_split_tree()
        data = payload(invoke(cli, "--self-check", env={"IV_REPO_ROOT": str(vendor)}))
        self.assertTrue(data["ok"])
        self.assertEqual(data["repo_root"], str(vendor.resolve()))


class TestDeploymentAssets(DeployCase):
    def test_skill_declares_its_own_dependencies(self):
        requirements = (SKILL / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("Pillow>=10.0", requirements)
        self.assertIn("Pillow 13", requirements, "要写明 Pillow 13 需要 Python>=3.11，避免有人抬高下界")

    def test_deploy_doc_lists_minimal_set(self):
        doc = (SKILL / "DEPLOY.md").read_text(encoding="utf-8")
        for token in ("shared/", SPEC_DIRNAME, "skills/image-variations/", "IV_REPO_ROOT",
                      "IV_BASELINE_ROOT", "--self-check", ".venv"):
            self.assertIn(token, doc, f"DEPLOY.md 缺少 {token!r}")

    def test_skill_dir_carries_no_venv(self):
        self.assertFalse((SKILL / ".venv").exists(), "技能目录内不得带 venv（含绝对路径，换机即坏）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
