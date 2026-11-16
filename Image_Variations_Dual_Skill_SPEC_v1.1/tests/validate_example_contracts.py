"""契约校验：正例必须全过、负例必须按预期被拒。

用法：
    python tests/validate_example_contracts.py            # 正例 + 负例
    python tests/validate_example_contracts.py --negatives # 只跑负例
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checks  # noqa: E402
import schema_lite  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EX = ROOT / "examples"
SCHEMA_DIR = ROOT / "contracts"
WARNING_CODES = {"GRID_SEAM_RISK", "TILE_RESOLUTION_RISK"}
SCHEMAS = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted(SCHEMA_DIR.glob("*.json"))}


def schema_check(name: str, instance) -> list:
    return schema_lite.validate(instance, SCHEMAS[name], SCHEMAS[name])


def guard(fn, *args) -> list:
    try:
        return fn(*args)
    except ValueError as exc:
        code = str(exc).split(":")[0] if str(exc).split(":")[0].isupper() else "PATH_TRAVERSAL_BLOCKED"
        return [(code, str(exc))]
    except Exception as exc:  # noqa: BLE001
        return [("UNEXPECTED", f"{fn.__name__}: {exc!r}")]


def image_semantics(folder: Path, manifest) -> list:
    errs = []
    errs += guard(checks.check_schema_major, manifest)
    errs += guard(checks.check_images_on_disk, folder, manifest)
    if manifest.get("output_mode") == "grid":
        errs += guard(checks.check_grid_structure, folder, manifest)
    else:
        errs += guard(checks.check_sequence_structure, manifest)
    errs += guard(checks.check_content_assets_order, manifest)
    return errs


def content_semantics(folder: Path, manifest, sources) -> list:
    errs = []
    source = sources.get(manifest["source"]["task_id"])
    if source is None:
        errs.append(("SOURCE_MANIFEST_INVALID", f"找不到上游 {manifest['source']['task_id']}"))
        return errs
    available = {i["id"] for i in checks.content_assets(source[1])}
    if not set(manifest["source"]["selected_asset_ids"]) <= available:
        errs.append(("SOURCE_ASSET_MISSING", "source.selected_asset_ids 含上游不存在的资产"))
    evidence = None
    ev_path = folder / "research" / "evidence.json"
    if ev_path.is_file():
        evidence = checks.load_json(ev_path)
    declared = set()
    for item in manifest["items"]:
        declared |= {f["path"] for f in item.get("package_images", [])}
    for item in manifest["items"]:
        selected = item.get("selected_asset_ids", [])
        if not set(selected) <= available:
            errs.append(("SOURCE_ASSET_MISSING", f"{item['content_id']}: 选中资产不在上游"))
        errs += guard(checks.check_claim_linkage, item, evidence)
        plan_path = item.get("topic_plan_file")
        if plan_path:
            try:
                path = checks.safe_path(folder, plan_path["path"])
                if checks.sha256(path) != plan_path["sha256"]:
                    errs.append(("OUTPUT_ASSET_INVALID", f"{item['content_id']}: topic_plan_file 哈希不符"))
                plan = checks.load_json(path)
                if plan.get("platform") != item["platform"]:
                    errs.append(("QUALITY_GATE_FAILED", f"{item['content_id']}: 选题平台与条目不一致"))
                if not plan.get("quality_scorecard"):
                    errs.append(("QUALITY_GATE_FAILED", f"{item['content_id']}: 选题缺 quality_scorecard"))
            except ValueError as exc:
                errs.append(("PATH_TRAVERSAL_BLOCKED", str(exc)))
        if item.get("profile_file"):
            try:
                checks.safe_path(folder, item["profile_file"]["path"])
            except ValueError as exc:
                errs.append(("PATH_TRAVERSAL_BLOCKED", str(exc)))
        errs += guard(checks.check_content_package_images, folder, item, declared)
        if item["platform"] == "wechat":
            for key in ("article_json", "article_md", "article_html"):
                if key not in item["files"]:
                    errs.append(("OUTPUT_ASSET_INVALID", f"{item['content_id']}: 缺 {key}"))
            for key in ("cover_wide", "cover_square"):
                if key not in item:
                    errs.append(("COVER_FIT_FAILED", f"{item['content_id']}: 缺 {key}"))
                else:
                    errs += guard(checks.check_cover, folder, f"{item['content_id']}:{key}",
                                  item[key], selected)
            art_path = checks.safe_path(folder, item["files"]["article_json"]["path"])
            article = checks.load_json(art_path)
            if article.get("topic_id") != item["topic_id"]:
                errs.append(("CONTENT_STRUCTURE_INVALID", f"{item['content_id']}: article.topic_id 不一致"))
            errs += guard(checks.check_blocks, article["blocks"], selected)
        else:
            note_path = checks.safe_path(folder, item["files"]["note_json"]["path"])
            note = checks.load_json(note_path)
            for tag in note.get("hashtags", []):
                if tag not in note["body_with_hashtags"]:
                    errs.append(("CONTENT_STRUCTURE_INVALID", f"{item['content_id']}: 标签 {tag} 未出现在正文"))
            if not note.get("first_comment"):
                errs.append(("CONTENT_STRUCTURE_INVALID", f"{item['content_id']}: 缺首条评论"))
            if "cover_wide" in item or "cover_square" in item:
                errs.append(("CONTENT_STRUCTURE_INVALID", f"{item['content_id']}: 小红书不应带封面字段"))
    pkg_path = folder / "delivery-package.json"
    if not pkg_path.is_file():
        errs.append(("PACKAGE_MANIFEST_INVALID", "缺 delivery-package.json"))
    else:
        errs += guard(checks.check_package_coverage, folder, checks.load_json(pkg_path))
    return errs


def report(title: str, errs: list, expect_warnings_only=False) -> bool:
    fatal = [e for e in errs if e[0] not in WARNING_CODES]
    ok = not fatal if expect_warnings_only else not errs
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {title}")
    for code, msg in errs:
        level = "warn" if code in WARNING_CODES else "err "
        print(f"        {level} {code}: {msg}")
    return ok


# ------------------------------------------------------------------ positives
def run_positives() -> list:
    results = []
    grid_root = EX / "valid-grid-2x2"
    grid = checks.load_json(grid_root / "manifest.json")
    results.append(report("schema image-artifact (grid)", schema_check("image-artifact.schema.json", grid)))
    results.append(report("semantics valid-grid-2x2", image_semantics(grid_root, grid), True))
    results.append(report("schema delivery-package (grid)",
                          schema_check("delivery-package.schema.json",
                                       checks.load_json(grid_root / "delivery-package.json"))))

    seq_root = EX / "valid-sequence-4"
    seq = checks.load_json(seq_root / "manifest.json")
    results.append(report("schema image-artifact (sequence)",
                          schema_check("image-artifact.schema.json", seq)))
    results.append(report("semantics valid-sequence-4", image_semantics(seq_root, seq), True))

    sources = {grid["task_id"]: (grid_root, grid), seq["task_id"]: (seq_root, seq)}
    content_root = EX / "valid-content-job"
    content = checks.load_json(content_root / "manifest.json")
    results.append(report("schema content-artifact (grid source)",
                          schema_check("content-artifact.schema.json", content)))
    results.append(report("semantics valid-content-job", content_semantics(content_root, content, sources), True))
    wechat = content["items"][0]
    article = checks.load_json(content_root / wechat["files"]["article_json"]["path"])
    results.append(report("schema article-body", schema_check("article-body.schema.json", article)))
    results.append(report("blocks grammar (image_group)",
                          [e for e in checks.check_blocks(article["blocks"], wechat["selected_asset_ids"])
                           if e[0] not in WARNING_CODES]))
    note = checks.load_json(content_root / content["items"][1]["files"]["note_json"]["path"])
    results.append(report("schema xiaohongshu-note",
                          schema_check("xiaohongshu-note.schema.json", note)))
    results.append(report("schema topic-plan",
                          schema_check("topic-plan.schema.json",
                                       checks.load_json(EX / "topic-plan.example.json"))))
    results.append(report("schema research-evidence",
                          schema_check("research-evidence.schema.json",
                                       checks.load_json(EX / "research-evidence-R1.example.json"))))

    seq_content_root = EX / "valid-content-from-sequence"
    seq_content = checks.load_json(seq_content_root / "manifest.json")
    results.append(report("schema content-artifact (sequence source)",
                          schema_check("content-artifact.schema.json", seq_content)))
    results.append(report("semantics valid-content-from-sequence",
                          content_semantics(seq_content_root, seq_content, sources), True))
    return results


# ------------------------------------------------------------------ negatives
def run_case(case_dir: Path) -> tuple[bool, str]:
    spec = checks.load_json(case_dir / "case.json")
    expect = spec.get("expect", {})
    schema_errs = []
    sem_errs = []
    if spec["schema"]:
        instance = checks.load_json(case_dir / spec["target"])
        schema_errs = schema_check(spec["schema"], instance)
    if spec["check"] == "image_artifact":
        sem_errs = image_semantics(case_dir, checks.load_json(case_dir / spec["target"]))
    elif spec["check"] == "content_artifact":
        sources = {}
        for name in ("valid-grid-2x2", "valid-sequence-4"):
            m = checks.load_json(EX / name / "manifest.json")
            sources[m["task_id"]] = (EX / name, m)
        sem_errs = content_semantics(case_dir, checks.load_json(case_dir / spec["target"]), sources)
    elif spec["check"] == "article_body":
        article = checks.load_json(case_dir / spec["target"])
        sem_errs = checks.check_blocks(article["blocks"], ["grid_img_01", "grid_img_02",
                                                           "grid_img_03", "grid_img_04"])
    elif spec["check"] == "delivery_package":
        sem_errs = guard(checks.check_package_coverage, case_dir,
                         checks.load_json(case_dir / spec["target"]))
    ok = True
    notes = []
    if expect.get("schema_fail") and not schema_errs:
        ok = False
        notes.append("期望 schema 失败但没有失败")
    if expect.get("schema_fail") is False and schema_errs:
        ok = False
        notes.append(f"不期望 schema 失败却失败: {schema_errs[:2]}")
    got = {code for code, _ in sem_errs}
    for code in expect.get("codes", []):
        if code not in got:
            ok = False
            notes.append(f"缺少期望错误码 {code}（实际 {sorted(got)}）")
    detail = "; ".join(notes)
    return ok, detail


def run_negatives() -> list:
    results = []
    inv = EX / "invalid"
    for case_dir in sorted(p for p in inv.iterdir() if p.is_dir()):
        ok, detail = run_case(case_dir)
        print(f"[{'PASS' if ok else 'FAIL'}] negative {case_dir.name}" + (f" — {detail}" if detail else ""))
        results.append(ok)
    return results


def main() -> int:
    only_neg = "--negatives" in sys.argv
    results = []
    if not only_neg:
        print("=== 正例 ===")
        results += run_positives()
        print()
    print("=== 负例 ===")
    results += run_negatives()
    passed, total = sum(1 for r in results if r), len(results)
    print(f"\n总计 {passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
