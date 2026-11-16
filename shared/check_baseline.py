"""基线只读校验：任何人改动冻结规范包都会在这里失败。

用法：
    python shared/check_baseline.py            # 哈希清单校验
    python shared/check_baseline.py --rebuild  # 仅在用户明确要求重建快照时使用
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared import baseline  # noqa: E402


def main(argv: list[str]) -> int:
    if "--rebuild" in argv:
        path = baseline.build_manifest(baseline.baseline_root(), baseline.MANIFEST_PATH)
        print(f"[OK] 已重建基线清单: {path}")
        return 0
    problems = baseline.verify_baseline()
    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}")
        print("\n基线包是只读的。请恢复被改动/新增/删除的文件，不要更新清单。")
        return 1
    count = len(baseline.read_json(baseline.MANIFEST_PATH)["files"])
    print(f"[PASS] 基线只读校验通过：{count} 个文件哈希一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
