"""契约与夹具回归（Phase 1 起持续运行）。

覆盖：
    A. 基线只读 + 基线自带 24/24 契约校验不回归
    B. 夹具清单完整（FIXTURES.json 与实际目录一致）
    C. 5 组正例：schema + 语义 + 交付包
    D. 23 组负例：按 case.json 断言触发预期错误码
    E. 绑定文件引用的错误码全部已登记
    F. 配置完整性（阈值全部外置）

用法：
    python skills/image-variations/tests/run_contract_tests.py
    python skills/image-variations/tests/run_contract_tests.py --verbose
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "support"))

import paths  # noqa: E402

from shared import baseline, errors  # noqa: E402
from shared.jsonio import read_json  # noqa: E402
from iv_skill import acceptance  # noqa: E402
from iv_skill.config import (  # noqa: E402
    REQUIRED_BINDING_CODES,
    binding_codes,
    load_bindings,
    load_config,
)

FIXTURES = paths.FIXTURES_DIR
PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return ok


# ---------------------------------------------------------------- A
def check_baseline_untouched() -> bool:
    problems = baseline.verify_baseline()
    return record("baseline: 冻结规范包未被改动", not problems, "; ".join(problems[:3]))


def check_baseline_suite() -> bool:
    script = paths.baseline_test_dir() / "validate_example_contracts.py"
    proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    tail = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else proc.stderr[-200:]
    ok = proc.returncode == 0 and "24/24 通过" in (proc.stdout or "")
    return record("baseline: 自带契约校验 24/24 不回归", ok, tail)


# ---------------------------------------------------------------- B
def check_fixture_inventory() -> bool:
    meta = read_json(FIXTURES / "FIXTURES.json")
    on_disk = {p.name for p in FIXTURES.iterdir()
               if p.is_dir() and (p / "case.json").is_file() or p.name.startswith("valid-")}
    declared = set(meta["valid"]) | set(meta["invalid"])
    ok = on_disk == declared
    missing = declared - on_disk
    extra = on_disk - declared
    detail = f"缺 {sorted(missing)} 多 {sorted(extra)}" if not ok else ""
    record(f"fixtures: 清单一致（正例 {len(meta['valid'])} / 负例 {len(meta['invalid'])}）", ok, detail)
    case_files = [p for p in FIXTURES.glob("*/case.json")]
    return record("fixtures: 每个负例都有 case.json", len(case_files) == len(meta["invalid"]),
                  f"{len(case_files)} != {len(meta['invalid'])}")


# ---------------------------------------------------------------- C
def check_positives() -> list[bool]:
    meta = read_json(FIXTURES / "FIXTURES.json")
    outcomes = []
    for name in meta["valid"]:
        folder = FIXTURES / name
        manifest = acceptance.load_manifest(folder)
        schema_errs = baseline.validate_instance("image-artifact.schema.json", manifest)
        outcomes.append(record(f"positive {name}: schema", not schema_errs, str(schema_errs[:2])))
        sem_errs = acceptance.image_artifact_errors(folder, manifest)
        fatal = [(c, m) for c, m in sem_errs if c not in ("GRID_SEAM_RISK", "TILE_RESOLUTION_RISK")]
        outcomes.append(record(f"positive {name}: 语义", not fatal, str(fatal[:3])))
        pkg_errs = acceptance.package_errors(folder)
        outcomes.append(record(f"positive {name}: delivery-package", not pkg_errs, str(pkg_errs)))
    return outcomes


# ---------------------------------------------------------------- D
def check_negatives(verbose: bool = False) -> list[bool]:
    meta = read_json(FIXTURES / "FIXTURES.json")
    outcomes = []
    for name in meta["invalid"]:
        folder = FIXTURES / name
        case = read_json(folder / "case.json")
        expect = case.get("expect", {})
        problems: list[str] = []
        schema_failed = False
        if case.get("schema"):
            # 契约名与文件一一对应，避免 case.json 的 target 与 schema 不一致
            fname = "delivery-package.json" if "delivery-package" in case["schema"] else "manifest.json"
            target = read_json(folder / fname)
            schema_errs = baseline.validate_instance(case["schema"], target)
            schema_failed = bool(schema_errs)
            want = expect.get("schema_fail")
            if want is True and not schema_failed:
                problems.append("期望 schema 失败但没有失败")
            if want is False and schema_failed:
                problems.append(f"不期望 schema 失败却失败: {schema_errs[:2]}")
        got: set[str] = set()
        check = case.get("check")
        target = read_json(folder / case.get("target", "manifest.json"))
        if check == "image_artifact":
            got = {code for code, _ in acceptance.image_artifact_errors(folder, target)}
        elif check == "direction_registry":
            got = {code for code, _ in acceptance.check_direction_registry(target)}
        elif check == "error_registry":
            # 负例语义：manifest 必须**显式声明**该错误码；同时不得出现未登记码。
            declared = {e.get("code") for e in (target.get("errors") or [])}
            declared |= {w.get("code") for w in (target.get("warnings") or [])}
            unregistered = {c for c, _ in acceptance.check_error_codes(target)}
            got = declared | unregistered
        elif check == "delivery_package":
            got = {code for code, _ in acceptance.package_errors(folder)}
        for code in expect.get("codes", []):
            if code not in got:
                problems.append(f"缺少期望错误码 {code}（实际 {sorted(got)}）")
        ok = record(f"negative {name}", not problems, "; ".join(problems))
        if verbose and problems:
            print(f"        note: {case.get('note')}")
        outcomes.append(ok)
    return outcomes


# ---------------------------------------------------------------- E / F
def check_bindings() -> bool:
    problems = acceptance.registry_code_bindings()
    registered = record("bindings: 引用的错误码均已登记", not problems, str(problems))
    acs = {item["ac"] for item in load_bindings()["acceptance_bindings"]}
    missing = [ac for ac in REQUIRED_BINDING_CODES if ac not in acs]
    coverage = record("bindings: AC 覆盖 AC-01..AC-32 中被实现的部分",
                      not missing, f"缺 {missing}")
    unknown = [c for c in binding_codes() if not errors.is_known(c)]
    return registered and coverage and record("bindings: 无未登记错误码", not unknown, str(unknown))


def check_config() -> bool:
    config = load_config()
    ok = record("config: 阈值外置且完整",
                config["tile_edge_min_px"] == 1024 and config["seam"]["pass_threshold"] == 0.85,
                "配置值与规范默认不一致（若为有意校准，请同步文档）")
    return ok


def main(argv: list[str]) -> int:
    verbose = "--verbose" in argv
    print("=== A. 基线 ===")
    check_baseline_untouched()
    check_baseline_suite()
    print("\n=== B. 夹具清单 ===")
    check_fixture_inventory()
    print("\n=== C. 正例 ===")
    check_positives()
    print("\n=== D. 负例 ===")
    check_negatives(verbose=verbose)
    print("\n=== E. 错误码与验收绑定 ===")
    check_bindings()
    print("\n=== F. 配置 ===")
    check_config()
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n总计 {passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
