"""测试路径装配：保证测试可导入 shared/ 与 iv_skill/。

导入本模块即完成 sys.path 与 IV_REPO_ROOT 设置，任何测试脚本第一行导入它即可。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SKILL_DIR = REPO_ROOT / "skills" / "image-variations"
SCRIPTS_DIR = SKILL_DIR / "scripts"
BASELINE_DIR = REPO_ROOT / "Image_Variations_Dual_Skill_SPEC_v1.1"
FIXTURES_DIR = SKILL_DIR / "tests" / "fixtures"

os.environ.setdefault("IV_REPO_ROOT", str(REPO_ROOT))
os.environ.setdefault("IV_BASELINE_ROOT", str(BASELINE_DIR))

for entry in (str(REPO_ROOT), str(SCRIPTS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)


def baseline_test_dir() -> Path:
    return BASELINE_DIR / "tests"
