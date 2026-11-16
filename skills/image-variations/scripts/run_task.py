#!/usr/bin/env python3
"""Skill 01 命令行入口：读一份 JSON 任务描述，输出 JSON 结果。

用法：
    python scripts/run_task.py --input task.json
    python scripts/run_task.py --input -          # 从 stdin 读
    python scripts/run_task.py --input task.json --workspace ./workspace --pretty
    python scripts/run_task.py --input task.json --preflight-only
    python scripts/run_task.py --input task.json --retry-failed-of ./workspace/iv_x_rev1

退出码：0=完成（可能带风险警告）；2=被错误码阻断；3=参数/任务描述非法。
错误一律输出 {"ok": false, "error": {"code": ..., "message": ..., ...}}，不打印堆栈。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
REPO_ROOT = SCRIPTS.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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
from shared.errors import IVError  # noqa: E402


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Image Variations Skill 01（Grid / Sequence）")
    parser.add_argument("--input", required=True, help="任务描述 JSON 路径，或 - 表示 stdin")
    parser.add_argument("--workspace", default="workspace", help="产物根目录（默认 ./workspace）")
    parser.add_argument("--pretty", action="store_true", help="格式化输出 JSON")
    parser.add_argument("--preflight-only", action="store_true", help="只做预检，不生成不落盘")
    parser.add_argument("--retry-failed-of", default=None,
                        help="指定上一版产物目录，仅补生成失败帧（新修订）")
    args = parser.parse_args(argv)

    indent = 2 if args.pretty else None
    try:
        spec = _read_spec(args.input)
        workspace = Path(args.workspace)
        if args.preflight_only:
            payload = preflight(spec, workspace)
        elif args.retry_failed_of:
            payload = resume_sequence_task(spec, previous_dir=args.retry_failed_of,
                                           workspace=workspace)
        else:
            payload = run_task(spec, workspace=workspace)
    except IVError as exc:
        print(json.dumps({"ok": False, "error": exc.to_dict()}, ensure_ascii=False, indent=indent))
        return 3 if exc.code == "INVALID_INPUT" else 2
    except FileNotFoundError as exc:
        print(json.dumps({"ok": False, "error": {"code": "INVALID_INPUT", "message": str(exc)}},
                         ensure_ascii=False, indent=indent))
        return 3
    print(json.dumps(payload, ensure_ascii=False, indent=indent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
