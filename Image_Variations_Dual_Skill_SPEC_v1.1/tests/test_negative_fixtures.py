"""只跑负例夹具，作为独立回归入口。

用法：python tests/test_negative_fixtures.py
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_example_contracts import run_negatives  # noqa: E402


def main() -> int:
    results = run_negatives()
    passed, total = sum(1 for r in results if r), len(results)
    print(f"\n负例 {passed}/{total} 按预期被拒")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
