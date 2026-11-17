#!/usr/bin/env python3
"""Skill 01 命令行入口：读一份 JSON 任务描述，输出 JSON 结果。

用法：
    python scripts/run_task.py --input task.json
    python scripts/run_task.py --input -          # 从 stdin 读
    python scripts/run_task.py --input task.json --workspace ./workspace --pretty
    python scripts/run_task.py --input task.json --preflight-only
    python scripts/run_task.py --input task.json --retry-failed-of ./workspace/iv_x_rev1
    python scripts/run_task.py --self-check       # 部署自检：宿主装完先跑这个

退出码：
    0 = 完成（可能带风险警告）
    2 = 被错误码阻断（业务失败）
    3 = 参数/任务描述非法（业务失败）
    4 = **部署不完整**（安装问题，不是任务失败）

前三种走业务面：错误一律输出 {"ok": false, "error": {"code": ...}}，code 取自
references/error-registry.json，不打印堆栈。第四种走安装面：输出
{"ok": false, "deployment_error": {"stage": ...}}，**不占用业务错误码**——"装错了"不属于
"能力不足 / 预算不足 / 校验不通过"这三类任务失败，混进业务码会让宿主误判成模型问题。

部署形态（两种都支持，见 DEPLOY.md）：
    1) 整仓：<repo>/{shared,Image_Variations_Dual_Skill_SPEC_v1.1,skills}/ —— 零配置
    2) 分离：IV_REPO_ROOT 指向含 shared/ 的目录；冻结基线另放时再设 IV_BASELINE_ROOT
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS.parent
EXIT_OK, EXIT_BLOCKED, EXIT_INVALID, EXIT_DEPLOY = 0, 2, 3, 4
MIN_PYTHON = (3, 9)
BASELINE_DIRNAME = "Image_Variations_Dual_Skill_SPEC_v1.1"


def _repo_root_candidates() -> list[Path]:
    """仓库根候选：IV_REPO_ROOT 显式指定优先，其次按整仓布局推断。"""
    candidates: list[Path] = []
    override = os.environ.get("IV_REPO_ROOT")
    if override:
        candidates.append(Path(override).expanduser())
    candidates.append(SCRIPTS.parents[2])
    return candidates


def _bootstrap() -> tuple[Path | None, dict | None]:
    """把仓库根放进 sys.path（供 `shared` 导入）；找不到时返回部署错误而不抛异常。"""
    tried: list[str] = []
    for candidate in _repo_root_candidates():
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        tried.append(str(resolved))
        if (resolved / "shared" / "baseline.py").is_file():
            for entry in (str(resolved), str(SCRIPTS)):
                if entry not in sys.path:
                    sys.path.insert(0, entry)
            return resolved, None
    return None, {
        "stage": "bootstrap",
        "message": "找不到仓库根：候选目录里都没有 shared/baseline.py",
        "tried": tried,
        "hint": ("整仓部署保持 <repo>/{shared,Image_Variations_Dual_Skill_SPEC_v1.1,skills}/ 结构；"
                 "分离部署用 IV_REPO_ROOT 指向含 shared/ 的目录，"
                 "冻结基线另放时再设 IV_BASELINE_ROOT"),
    }


REPO_ROOT, DEPLOYMENT_ERROR = _bootstrap()

try:
    from iv_skill.config import load_config  # noqa: E402
    from iv_skill.preflight import plan_generation  # noqa: E402
    from iv_skill.prompt_builder import DirectionRegistry  # noqa: E402
    from iv_skill.task import (  # noqa: E402
        _resolved,
        build_task_adapter,
        build_task_anchor,
        load_reference,
        resume_sequence_task,
        run_task,
    )
    from shared.baseline import MANIFEST_PATH, baseline_root, verify_baseline  # noqa: E402
    from shared.errors import IVError  # noqa: E402
    from shared.jsonio import read_json  # noqa: E402

    IMPORT_ERROR: str | None = None
except Exception as exc:  # noqa: BLE001  部署不完整时给出可判断的错误，而不是 traceback
    IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


def _emit(payload: dict, indent: int | None) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=indent))


def _deployment_failure(indent: int | None, *, stage: str, message: str, **extra) -> int:
    _emit({"ok": False, "deployment_error": {"stage": stage, "message": message, **extra}}, indent)
    return EXIT_DEPLOY


def _read_spec(path: str) -> dict:
    if path == "-":
        raw = sys.stdin.read()
    else:
        file = Path(path)
        if not file.is_file():
            raise IVError("INVALID_INPUT", f"任务文件不存在: {path}")
        raw = file.read_text(encoding="utf-8")
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IVError("INVALID_INPUT", f"任务描述不是合法 JSON: {exc}") from exc
    if not isinstance(spec, dict):
        raise IVError("INVALID_INPUT", "任务描述必须是 JSON 对象")
    return spec


def preflight(spec: dict, workspace: Path) -> dict:
    """只做排产检查：不发起任何模型调用，也不落盘。"""
    config = load_config()
    resolved = _resolved(spec)
    registry = DirectionRegistry()
    adapter = build_task_adapter(spec, config=config, grid_size=resolved["grid_size"] or 1)
    reference = load_reference(spec, workspace)
    anchor = build_task_anchor(spec, workspace, reference)
    result = plan_generation(
        output_mode=resolved["mode"], grid_size=resolved["grid_size"],
        image_count=resolved["image_count"], aspect_ratio=resolved["aspect_ratio"],
        input_type=resolved["input_type"], direction=resolved["direction"],
        branch=resolved["branch"], capability=adapter.capability,
        requested_tier=resolved["requested_tier"], config=config,
        provider_type=adapter.provider_type,
        reference_bytes=len(reference.data) if reference else None)
    if not result.ok:
        error = result.errors[0]
        raise IVError(error["code"], error["message"],
                      all_errors=result.errors,
                      split_suggestion=error.get("split_suggestion"))
    plan = dict(result.plan)
    plan["anchor"] = anchor.to_dict()
    plan["direction_registry_version"] = registry.version
    return {"ok": True, "preflight": plan}


def self_check(workspace: Path, indent: int | None) -> int:
    """部署自检：把"宿主环境能不能跑"与"任务能不能成"彻底分开报。"""
    report: dict = {
        "ok": True,
        "check": "self_check",
        "python": platform.python_version(),
        "skill_dir": str(SKILL_DIR),
        "repo_root": str(REPO_ROOT) if REPO_ROOT else None,
    }

    def fail(stage: str, message: str, **extra) -> int:
        report["ok"] = False
        report["deployment_error"] = {"stage": stage, "message": message, **extra}
        _emit(report, indent)
        return EXIT_DEPLOY

    if sys.version_info < MIN_PYTHON:
        return fail("python", f"需要 Python >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}，"
                              f"当前 {platform.python_version()}")
    try:
        import PIL
        report["pillow"] = getattr(PIL, "__version__", "unknown")
    except Exception as exc:  # noqa: BLE001
        return fail("dependencies", f"缺少 Pillow：{exc}",
                    hint="pip install -r requirements.txt（技能目录内自带）")
    if DEPLOYMENT_ERROR is not None:
        return fail(**DEPLOYMENT_ERROR)
    if IMPORT_ERROR is not None:
        return fail("import", f"依赖或代码不完整：{IMPORT_ERROR}",
                    hint="确认 shared/ 与冻结基线目录随技能一起部署")

    problems = verify_baseline()
    declared = len(read_json(MANIFEST_PATH).get("files", {}))
    report["baseline"] = {"root": str(baseline_root()), "declared_files": declared,
                          "ok": not problems, "problems": problems[:3]}
    if problems:
        return fail("baseline", f"冻结基线校验未通过：{problems[0]}", problems=problems[:3])

    try:
        config = load_config()
        registry = DirectionRegistry()
        report["skill_config"] = {"config_version": config.get("config_version"),
                                  "directions": len(registry.keys()),
                                  "registry_version": registry.version}
    except Exception as exc:  # noqa: BLE001
        return fail("skill_config", f"技能配置或方向注册表不可用：{exc}")

    try:
        workspace.mkdir(parents=True, exist_ok=True)
        probe = Path(tempfile.mkdtemp(prefix=".iv_selfcheck_", dir=str(workspace)))
        shutil.rmtree(probe, ignore_errors=True)
        report["workspace"] = {"path": str(workspace.resolve()), "writable": True}
    except Exception as exc:  # noqa: BLE001
        return fail("workspace", f"workspace 不可写：{exc}",
                    hint="宿主需要允许在产物目录下创建与删除文件")

    # 本地私有扩展：没有是正常情况（公开仓库/CI 都没有），有则如实列出。
    # 插件损坏会在这里暴露，而不是等到真正调用模型时才炸。
    try:
        from iv_skill.local_plugins import status as local_status
        from iv_skill.provider.registry import available_provider_types

        report["local_extensions"] = local_status()
        report["providers"] = available_provider_types()
    except Exception as exc:  # noqa: BLE001
        return fail("local_extensions", f"本地扩展加载失败：{exc}",
                    hint="检查 local/providers/ 下的插件是否可导入（它不该让自检崩掉）")

    _emit(report, indent)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Image Variations Skill 01（Grid / Sequence）")
    parser.add_argument("--input", default=None,
                        help="任务描述 JSON 路径，或 - 表示 stdin（--self-check 时不需要）")
    parser.add_argument("--workspace", default="workspace", help="产物根目录（默认 ./workspace）")
    parser.add_argument("--pretty", action="store_true", help="格式化输出 JSON")
    parser.add_argument("--preflight-only", action="store_true", help="只做预检，不生成不落盘")
    parser.add_argument("--retry-failed-of", default=None,
                        help="指定上一版产物目录，仅补生成失败帧（新修订）")
    parser.add_argument("--self-check", action="store_true", dest="self_check",
                        help="部署自检（Python/依赖/仓库根/基线/技能配置/workspace 可写）；"
                             "失败退出码 4，输出 deployment_error")
    args = parser.parse_args(argv)

    indent = 2 if args.pretty else None
    workspace = Path(args.workspace)

    if args.self_check:
        return self_check(workspace, indent)
    if DEPLOYMENT_ERROR is not None:
        return _deployment_failure(indent, **DEPLOYMENT_ERROR)
    if IMPORT_ERROR is not None:
        return _deployment_failure(indent, stage="import", message=f"依赖或代码不完整：{IMPORT_ERROR}",
                                   hint="确认 shared/ 与冻结基线目录随技能一起部署")
    if not args.input:
        _emit({"ok": False, "error": {"code": "INVALID_INPUT",
                                      "message": "--input 必填（或用 --self-check 只做部署自检）"}},
              indent)
        return EXIT_INVALID

    try:
        spec = _read_spec(args.input)
        if args.preflight_only:
            payload = preflight(spec, workspace)
        elif args.retry_failed_of:
            payload = resume_sequence_task(spec, previous_dir=args.retry_failed_of,
                                           workspace=workspace)
        else:
            payload = run_task(spec, workspace=workspace)
    except IVError as exc:
        _emit({"ok": False, "error": exc.to_dict()}, indent)
        return EXIT_INVALID if exc.code == "INVALID_INPUT" else EXIT_BLOCKED
    except FileNotFoundError as exc:
        _emit({"ok": False, "error": {"code": "INVALID_INPUT", "message": str(exc)}}, indent)
        return EXIT_INVALID
    _emit(payload, indent)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
