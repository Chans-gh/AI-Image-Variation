"""Skill 01 全量回归入口：契约 / 预检 / Provider / Grid / Sequence。

用法：
    python skills/image-variations/tests/run_all.py
    python skills/image-variations/tests/run_all.py --skip-baseline
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]

SUITES = [
    ("基线只读校验", [sys.executable, str(REPO / "shared" / "check_baseline.py")]),
    ("基线自带契约 24/24", [sys.executable,
                            str(REPO / "Image_Variations_Dual_Skill_SPEC_v1.1" / "tests"
                                / "validate_example_contracts.py")]),
    ("契约与夹具", [sys.executable, str(HERE / "run_contract_tests.py")]),
    ("预检与预算", [sys.executable, str(HERE / "test_preflight.py")]),
    ("Provider 错误与安全", [sys.executable, str(HERE / "test_provider_errors.py")]),
    ("适配器协议", [sys.executable, str(HERE / "test_adapters.py")]),
]


def main(argv: list[str]) -> int:
    skip_baseline = "--skip-baseline" in argv
    failures: list[str] = []
    for name, command in SUITES:
        if skip_baseline and name.startswith("基线"):
            continue
        print(f"\n=== {name} ===")
        proc = subprocess.run(command, capture_output=True, text=True, cwd=str(REPO))
        output = (proc.stdout or "") + (proc.stderr or "")
        tail = [line for line in output.strip().splitlines() if line.strip()][-3:]
        for line in tail:
            print("   ", line)
        if proc.returncode != 0:
            failures.append(name)
            print(output[-2000:])
    print("\n" + "=" * 46)
    if failures:
        print(f"失败套件: {failures}")
        return 1
    print(f"全部 {len(SUITES) - (1 if skip_baseline else 0)} 个套件通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
