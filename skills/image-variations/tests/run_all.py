"""Skill 01 全量回归入口：契约 / 预检 / Provider / Grid / Sequence / 产物 / 部署。

除上面这些**随仓库发布**的套件外，还会自动发现并运行本地私有套件
（`local/tests/*.py` 与 `local/<平台>/tests/*.py`，两者都不进版本库）。

用法：
    python skills/image-variations/tests/run_all.py
    python skills/image-variations/tests/run_all.py --skip-baseline
    python skills/image-variations/tests/run_all.py --no-local   # 模拟 CI（不带本地扩展）
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SCRIPTS = HERE.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

SUITES = [
    ("基线只读校验", [sys.executable, str(REPO / "shared" / "check_baseline.py")]),
    ("基线自带契约 24/24", [sys.executable,
                            str(REPO / "Image_Variations_Dual_Skill_SPEC_v1.1" / "tests"
                                / "validate_example_contracts.py")]),
    ("契约与夹具", [sys.executable, str(HERE / "run_contract_tests.py")]),
    ("预检与预算", [sys.executable, str(HERE / "test_preflight.py")]),
    ("Provider 错误与安全", [sys.executable, str(HERE / "test_provider_errors.py")]),
    ("适配器协议", [sys.executable, str(HERE / "test_adapters.py")]),
    ("Grid 成板与切图", [sys.executable, str(HERE / "test_grid.py")]),
    ("Sequence 计划与逐帧", [sys.executable, str(HERE / "test_sequence.py")]),
    ("产物落盘与打包", [sys.executable, str(HERE / "test_artifact.py")]),
    ("部署与自检", [sys.executable, str(HERE / "test_deploy.py")]),
    ("本地插件机制", [sys.executable, str(HERE / "test_local_plugins.py")]),
]


def local_suites() -> list[tuple[str, list[str]]]:
    """本地私有套件（不进版本库；没有就是空列表，属正常情况）。"""
    from iv_skill.local_plugins import local_tests_dirs

    found: list[tuple[str, list[str]]] = []
    for directory in local_tests_dirs():
        for script in sorted(directory.glob("test_*.py")):
            found.append((f"本地：{script.stem}", [sys.executable, str(script)]))
    return found


def main(argv: list[str]) -> int:
    skip_baseline = "--skip-baseline" in argv
    no_local = "--no-local" in argv
    suites = list(SUITES)
    if not no_local:
        suites.extend(local_suites())
    failures: list[str] = []
    for name, command in suites:
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
    ran = len([s for s in suites if not (skip_baseline and s[0].startswith("基线"))])
    local_count = len(suites) - len(SUITES)
    suffix = f"（含 {local_count} 个本地套件）" if local_count else ""
    print(f"全部 {ran} 个套件通过{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
