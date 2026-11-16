"""任务编排：把一份 JSON 任务描述变成可交付的产物目录（Skill 01 的唯一入口）。

调用形状（与 `SKILL.md` 一致）：

    {"task_id": "demo_grid_001", "output_mode": "grid", "grid_size": 2,
     "direction": "light", "input_type": "text_only", "subject": "一只浅灰陶瓷杯",
     "aspect_ratio": "1:1", "resolution_tier": "2K",
     "provider": {"type": "mock_synthetic"}, "pack_zip": true}

原则：
    - 任何能力/预算/校验问题都抛 `IVError`（错误码来自基线注册表），不静默降级；
    - 真实 Provider 必须显式给出 `capability`（能力声明就是底线清单）与
      `api_key_secret_ref`（只接受 `secret://` / `env:` 引用）；
    - 参考图只从 workspace 内安全解析，越界直接 `PATH_TRAVERSAL_BLOCKED`。
"""
from __future__ import annotations

from pathlib import Path

from shared.errors import IVError
from shared.paths import PathTraversalError, is_illegal_component, safe_resolve

from .anchor import Anchor, build_anchor
from .artifact import ArtifactResult, write_grid_artifact, write_sequence_artifact
from .config import load_config
from .executor import execute_grid
from .prompt_builder import DirectionRegistry
from .provider import ReferenceImage
from .provider.base import sniff_image
from .provider.capability import ModelCapability
from .provider.mock import default_capability
from .provider.registry import build_adapter
from .sequence import SequenceExecution, SequenceRunner, execute_sequence
from .synthetic import build_synthetic_adapter

REQUIRED_FIELDS = ("task_id", "output_mode", "direction", "input_type")


def _require(spec: dict, field: str):
    value = spec.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise IVError("INVALID_INPUT", f"任务缺少必填字段 {field}")
    return value


def _sniff_media(data: bytes, filename: str) -> str:
    sniffed = sniff_image(data)
    if sniffed is None:
        raise IVError("INVALID_INPUT", f"参考图 {filename} 不是可识别的图片格式")
    return sniffed[1]


def load_reference(spec: dict, workspace: Path) -> ReferenceImage | None:
    """按任务描述读取参考图；不接受 workspace 之外的路径。"""
    if spec.get("input_type") == "text_only":
        return None
    relative = spec.get("reference_path")
    if not relative:
        raise IVError("INVALID_INPUT", "图片输入必须提供 reference_path（相对 workspace）")
    if is_illegal_component(relative):
        raise IVError("PATH_TRAVERSAL_BLOCKED", f"参考图路径越界: {relative!r}")
    try:
        path = safe_resolve(workspace, relative)
    except PathTraversalError as exc:
        raise IVError("PATH_TRAVERSAL_BLOCKED", f"参考图路径越界: {exc}") from exc
    if not path.is_file():
        raise IVError("INVALID_INPUT", f"参考图不存在: {relative}")
    data = path.read_bytes()
    return ReferenceImage(data=data, media_type=_sniff_media(data, path.name), filename=path.name)


def build_task_adapter(spec: dict, *, config: dict, grid_size: int):
    provider = spec.get("provider") or {"type": "mock_synthetic"}
    provider_type = provider.get("type", "mock_synthetic")
    if provider_type == "mock_synthetic":
        return build_synthetic_adapter(grid_size=grid_size, config=config)
    if provider_type == "builtin":
        # O-02：宿主未注入内置能力时必须显式报码，不允许自动改走 external
        return build_adapter(provider_type="builtin")
    declaration = provider.get("capability")
    if not declaration:
        raise IVError("MODEL_CAPABILITY_UNSUPPORTED",
                      f"Provider {provider_type} 必须提供 capability 能力声明（底线清单）")
    if provider_type == "mock":
        declaration = declaration or default_capability()
    return build_adapter(
        provider_type=provider_type,
        capability_declaration=declaration,
        base_url=provider.get("base_url", ""),
        secret_ref=provider.get("api_key_secret_ref") or provider.get("secret_ref"),
        model_id=provider.get("model_id", ""),
        request_timeout_sec=provider.get("request_timeout_sec"),
        config=config)


def build_task_anchor(spec: dict, workspace: Path, reference: ReferenceImage | None) -> Anchor:
    return build_anchor(input_type=spec["input_type"],
                        user_description=spec.get("subject"),
                        reference_path=(spec.get("reference_path") if reference else None))


def _single_direction(spec: dict) -> str:
    """同一任务只能有一个变化方向（登记表里的 MULTIPLE_DIRECTIONS）。"""
    for field in ("direction", "directions"):
        value = spec.get(field)
        if isinstance(value, (list, tuple)):
            unique = [item for item in dict.fromkeys(value) if item]
            if len(unique) > 1:
                raise IVError("MULTIPLE_DIRECTIONS",
                              f"一个任务只能选一个方向，收到 {unique}；请拆成多个任务")
            if unique:
                return unique[0]
            raise IVError("INVALID_INPUT", "direction 为空列表")
    return _require(spec, "direction")


def _resolved(spec: dict) -> dict:
    for field in ("task_id", "output_mode", "input_type"):
        _require(spec, field)
    direction = _single_direction(spec)
    mode = spec["output_mode"]
    if mode not in ("grid", "sequence"):
        raise IVError("INVALID_MODE_OR_COUNT", f"output_mode 非法: {mode!r}")
    input_type = spec["input_type"]
    if input_type not in ("image_only", "text_only", "image_plus_text"):
        raise IVError("INVALID_INPUT", f"input_type 非法: {input_type!r}")
    grid_size = spec.get("grid_size") if mode == "grid" else None
    if mode == "grid" and grid_size is None:
        grid_size = load_config().get("defaults", {}).get("grid_size", 3)
    return {"mode": mode, "grid_size": grid_size, "input_type": input_type,
            "direction": direction,
            "branch": spec.get("branch") if mode == "sequence" else None,
            "image_count": spec.get("image_count"),
            "aspect_ratio": spec.get("aspect_ratio") or load_config()["defaults"]["text_only_aspect_ratio"],
            "requested_tier": spec.get("resolution_tier"),
            "revision": spec.get("artifact_revision", "rev1"),
            "parent": spec.get("parent_artifact_revision")}


def run_task(spec: dict, *, workspace: str | Path | None = None) -> dict:
    """执行一次完整任务并落盘，返回可序列化的结果摘要。"""
    config = load_config()
    resolved = _resolved(spec)
    workspace_path = Path(workspace or spec.get("workspace") or "workspace").resolve()
    registry = DirectionRegistry()
    adapter = build_task_adapter(spec, config=config, grid_size=resolved["grid_size"] or 1)
    reference = load_reference(spec, workspace_path)
    anchor = build_task_anchor(spec, workspace_path, reference)
    if spec.get("plan_only"):
        raise IVError("INVALID_INPUT", "plan_only 仅用于预检：请调用 preflight 接口而非 run_task")

    pack_zip = bool(spec.get("pack_zip"))
    if resolved["mode"] == "grid":
        execution = execute_grid(adapter=adapter, direction_key=resolved["direction"],
                                 grid_size=resolved["grid_size"],
                                 aspect_ratio=resolved["aspect_ratio"],
                                 input_type=resolved["input_type"], subject_anchor=anchor.description,
                                 reference_image=reference,
                                 requested_tier=resolved["requested_tier"], registry=registry,
                                 config=config)
        artifact = write_grid_artifact(workspace=workspace_path, task_id=spec["task_id"],
                                       execution=execution, anchor=anchor.to_dict(),
                                       registry=registry, input_type=resolved["input_type"],
                                       revision=resolved["revision"],
                                       parent_revision=resolved["parent"], pack_zip=pack_zip,
                                       config=config)
        summary = _execution_summary(execution, "grid")
    else:
        execution = execute_sequence(adapter=adapter, direction_key=resolved["direction"],
                                     branch=resolved["branch"] or "variants",
                                     frame_count=int(resolved["image_count"] or 0),
                                     aspect_ratio=resolved["aspect_ratio"],
                                     input_type=resolved["input_type"], anchor=anchor,
                                     reference_image=reference,
                                     requested_tier=resolved["requested_tier"],
                                     registry=registry, config=config)
        artifact = write_sequence_artifact(workspace=workspace_path, task_id=spec["task_id"],
                                           execution=execution, anchor=anchor.to_dict(),
                                           registry=registry, input_type=resolved["input_type"],
                                           revision=resolved["revision"],
                                           parent_revision=resolved["parent"], pack_zip=pack_zip,
                                           config=config)
        summary = _execution_summary(execution, "sequence")
    return {"ok": True, **summary, **artifact.to_dict()}


def _execution_summary(execution, mode: str) -> dict:
    if mode == "grid":
        return {
            "output_mode": "grid",
            "status": execution.result.verdict,
            "tier": execution.plan["tier"],
            "tier_policy": execution.plan["tier_policy"],
            "seam_score": round(execution.result.seam.score, 6),
            "attempts": execution.result.attempt,
            "calls_used": execution.budget["calls_used"],
            "cost_estimate": execution.budget.get("cost_estimate"),
            "warnings": execution.warnings,
            "errors": [],
        }
    return {
        "output_mode": "sequence",
        "status": execution.status,
        "tier": execution.plan["tier"],
        "tier_policy": execution.plan["tier_policy"],
        "frame_count": len(execution.frames),
        "failed_indices": [frame.index for frame in execution.failed_frames],
        "calls_used": execution.budget["calls_used"],
        "cost_estimate": execution.budget.get("cost_estimate"),
        "warnings": execution.warnings,
        "errors": execution.errors,
    }


# ------------------------------------------------------------------ 续跑（CLI 入口）
def load_sequence_execution(previous_dir: str | Path, *, spec: dict, adapter, registry: DirectionRegistry,
                            config: dict, workspace: Path) -> SequenceExecution:
    """从上一版产物目录还原执行态，供"只补失败帧"使用。"""
    from shared.jsonio import read_json

    directory = Path(previous_dir).resolve()
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise IVError("INVALID_INPUT", f"续跑需要上一版 manifest.json：{manifest_path}")
    manifest = read_json(manifest_path)
    if manifest.get("output_mode") != "sequence":
        raise IVError("INVALID_MODE_OR_COUNT", "只有 Sequence 支持按失败帧续跑")
    resolved = _resolved(spec)
    reference = load_reference(spec, workspace)
    anchor = build_task_anchor(spec, workspace, reference)
    runner = SequenceRunner(adapter=adapter, registry=registry, config=config)

    planned = _planner(runner, spec=spec, resolved=resolved, anchor=anchor)
    frames = []
    by_index = {entry["index"]: entry for entry in manifest.get("images", [])}
    failed = set(manifest.get("validation", {}).get("failed_indices", []))
    for planned_frame in planned.frames:
        record = type(planned_frame)(**{**planned_frame.__dict__})
        entry = by_index.get(planned_frame.index)
        if entry is not None and planned_frame.index not in failed:
            record.status = "completed"
            record.image_bytes = (directory / entry["path"]).read_bytes()
            record.width = entry["width"]
            record.height = entry["height"]
            record.sha256 = entry["sha256"]
            record.attempts = 1
        frames.append(record)
    plan = _plan_dict(runner, spec=spec, resolved=resolved, anchor=anchor, reference=reference)
    return SequenceExecution(plan=plan, sequence_plan=planned, anchor=anchor.to_dict(),
                             anchor_revision=(manifest.get("consistency_anchor", {}).get("revision")
                                              or None),
                             frames=frames,
                             status=manifest["status"], budget=manifest.get("execution", {}),
                             warnings=list(manifest.get("warnings", [])),
                             errors=list(manifest.get("errors", [])), retry_counts={})


def _planner(runner: SequenceRunner, *, spec: dict, resolved: dict, anchor: Anchor):
    from .sequence import plan_sequence

    plan = _plan_dict(runner, spec=spec, resolved=resolved, anchor=anchor, reference=None)
    return plan_sequence(registry=runner.registry, direction_key=resolved["direction"],
                         branch=resolved["branch"] or "variants",
                         frame_count=int(resolved["image_count"] or 0),
                         aspect_ratio=resolved["aspect_ratio"], resolution_tier=plan["tier"],
                         anchor=anchor, config=runner.config)


def _plan_dict(runner: SequenceRunner, *, spec: dict, resolved: dict, anchor: Anchor,
               reference: ReferenceImage | None) -> dict:
    from .preflight import plan_generation

    result = plan_generation(output_mode="sequence", grid_size=None,
                             image_count=int(resolved["image_count"] or 0),
                             aspect_ratio=resolved["aspect_ratio"],
                             input_type=resolved["input_type"], direction=resolved["direction"],
                             branch=resolved["branch"] or "variants",
                             capability=runner.adapter.capability,
                             requested_tier=resolved["requested_tier"], config=runner.config,
                             provider_type=runner.adapter.provider_type,
                             reference_bytes=len(reference.data) if reference else None)
    result.raise_if_blocked()
    return result.plan


def resume_sequence_task(spec: dict, *, previous_dir: str | Path,
                         workspace: str | Path | None = None) -> dict:
    """按原索引补生成失败帧，输出**新修订**（`parent_artifact_revision` 指向上一版）。"""
    from shared.jsonio import read_json

    config = load_config()
    resolved = _resolved(spec)
    workspace_path = Path(workspace or spec.get("workspace") or "workspace").resolve()
    registry = DirectionRegistry()
    adapter = build_task_adapter(spec, config=config, grid_size=1)
    execution = load_sequence_execution(previous_dir, spec=spec, adapter=adapter,
                                        registry=registry, config=config, workspace=workspace_path)
    reference = load_reference(spec, workspace_path)
    runner = SequenceRunner(adapter=adapter, registry=registry, config=config)
    execution = runner.retry_failed_frames(execution, reference_image=reference)
    previous_manifest = read_json(Path(previous_dir).resolve() / "manifest.json")
    anchor = build_task_anchor(spec, workspace_path, reference)
    artifact = write_sequence_artifact(
        workspace=workspace_path, task_id=spec["task_id"], execution=execution,
        anchor=anchor.to_dict(), registry=registry, input_type=resolved["input_type"],
        revision=resolved["revision"] if resolved["revision"] != "rev1" else "rev2",
        parent_revision=previous_manifest.get("artifact_revision"),
        pack_zip=bool(spec.get("pack_zip")), config=config)
    return {"ok": True, **_execution_summary(execution, "sequence"), **artifact.to_dict()}
