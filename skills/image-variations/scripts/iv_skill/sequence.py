"""Sequence 模式：先出计划，再逐帧生成、逐帧校验、失败保槽、可续跑。

规则（SPEC 01 §8、§12.1，`references/sequence-prompt.md`）：
    - 计划（含每帧 Prompt）必须先于**任何**模型调用产出；
    - N 条**实质不同**的 Prompt，每帧一个唯一主变化点；禁止同一 Prompt 调 N 次；
    - `text_only`：图 1 用 `identity_anchor + frame_01_change` 生成，图 2–N 全部引用**图 1**，
      不链式引用图 i−1；图 1 失败 → 停止后续并报 `SEQUENCE_ANCHOR_FAILED`；
    - 图 i 失败：保留已成功帧，按**原索引**重试，索引不移动；
    - 最终状态只可能是 `completed` 或 `partial_failed`，绝不伪造成功数量。
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

from PIL import Image, ImageChops, ImageStat

from shared.errors import IVError
from shared.hashing import sha256_bytes

from .anchor import Anchor
from .config import load_config
from .preflight import plan_generation
from .prompt_builder import (
    SEQUENCE_TEMPLATE_VERSION,
    DirectionRegistry,
    FramePlanItem,
    build_frame_plan,
    build_sequence_prompt,
)
from .provider import GenerationRequest, ReferenceImage
from .provider.retry import Budget, RetryPolicy

# 一帧失败但整个任务仍可继续（保留槽位，稍后补生成）；
# 除此之外的能力/鉴权/输入类错误视为**任务级致命**，立即中止，避免无意义地烧预算。
FRAME_LEVEL_CODES = frozenset({
    "MODEL_REFUSAL", "IMAGE_VALIDATION_FAILED", "RATE_LIMITED", "PROVIDER_TIMEOUT",
    "GRID_VALIDATION_FAILED",
})
SIMILARITY_SIDE = 32


# ------------------------------------------------------------------ 相似度
def image_similarity(first: bytes, second: bytes) -> float:
    """确定性相似度：32×32 灰度 + 逐像素平均绝对差，1.0 = 完全相同。"""
    try:
        left = Image.open(io.BytesIO(first)).convert("L").resize((SIMILARITY_SIDE, SIMILARITY_SIDE),
                                                                Image.BILINEAR)
        right = Image.open(io.BytesIO(second)).convert("L").resize((SIMILARITY_SIDE, SIMILARITY_SIDE),
                                                                  Image.BILINEAR)
    except Exception:  # noqa: BLE001 - 无法解码的图由调用方另行报错
        return 0.0
    difference = ImageChops.difference(left, right)
    mean = ImageStat.Stat(difference).mean[0]
    return max(0.0, 1.0 - mean / 255.0)


def text_similarity(first: str, second: str) -> float:
    """字符二元组 Jaccard 相似度，用于计划期的"近重复变化点"检查。"""
    def bigrams(text: str) -> set[str]:
        cleaned = "".join(text.split())
        if len(cleaned) < 2:
            return {cleaned} if cleaned else set()
        return {cleaned[i:i + 2] for i in range(len(cleaned) - 1)}

    left, right = bigrams(first), bigrams(second)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


# ------------------------------------------------------------------ 计划
@dataclass
class FrameRecord:
    index: int
    change_point: str
    phase_label: str
    visual_description: str
    prompt: str
    status: str = "failed"
    image_bytes: bytes | None = None
    width: int | None = None
    height: int | None = None
    bytes_size: int | None = None
    sha256: str | None = None
    attempts: int = 0
    near_duplicate_of: int | None = None
    similarity_max: float | None = None
    error: dict | None = None

    def to_dict(self) -> dict:
        payload = {
            "index": self.index,
            "status": self.status,
            "change_point": self.change_point,
            "phase_label": self.phase_label,
            "visual_description": self.visual_description,
            "attempts": self.attempts,
        }
        for key in ("width", "height", "sha256"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        if self.near_duplicate_of is not None:
            payload["near_duplicate_of"] = self.near_duplicate_of
            payload["similarity_max"] = self.similarity_max
        if self.error is not None:
            payload["error"] = self.error
        return payload


@dataclass
class SequencePlan:
    direction: str
    branch: str
    identity_anchor: str
    axis: str
    template_version: str
    frames: list[FrameRecord] = field(default_factory=list)

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    def change_points(self) -> list[str]:
        return [frame.change_point for frame in self.frames]

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "branch": self.branch,
            "identity_anchor": self.identity_anchor,
            "axis": self.axis,
            "prompt_template_version": self.template_version,
            "frames": [frame.to_dict() for frame in self.frames],
        }


def plan_sequence(*, registry: DirectionRegistry, direction_key: str, branch: str, frame_count: int,
                  aspect_ratio: str, resolution_tier: str, anchor: Anchor,
                  config: dict | None = None) -> SequencePlan:
    """纯计划阶段：不发起任何模型调用，失败即报错，不"先试试看"。"""
    config = config or load_config()
    low, high = config["sequence"]["min_frames"], config["sequence"]["max_frames"]
    if not isinstance(frame_count, int) or not (low <= frame_count <= high):
        raise IVError("INVALID_MODE_OR_COUNT",
                      f"Sequence 张数必须在 {low}–{high}，收到 {frame_count!r}")
    rules = registry.get(direction_key)
    items: list[FramePlanItem] = build_frame_plan(direction=rules, branch=branch,
                                                 frame_count=frame_count)
    frames: list[FrameRecord] = []
    for item in items:
        prompt = build_sequence_prompt(registry=registry, direction_key=direction_key,
                                       branch=branch, frame=item, frame_count=frame_count,
                                       aspect_ratio=aspect_ratio, resolution_tier=resolution_tier,
                                       subject_anchor=anchor.prompt_block())
        description = (f"第 {item.index}/{frame_count} 帧（{item.phase_label}）："
                       f"主变化点「{item.change_point}」；"
                       f"保持 {'、'.join(item.locked_elements)} 不变。")
        frames.append(FrameRecord(index=item.index, change_point=item.change_point,
                                  phase_label=item.phase_label,
                                  visual_description=description, prompt=prompt.text))
    limit = float(config["sequence"]["near_duplicate_similarity_max"])
    seen: dict[str, int] = {}
    for frame in frames:
        key = "".join(frame.change_point.split())
        for other_key, other_index in seen.items():
            similarity = text_similarity(key, other_key)
            if similarity >= limit:
                raise IVError("INVALID_MODE_OR_COUNT",
                              f"第 {other_index} 帧与第 {frame.index} 帧的変化点近重复"
                              f"（相似度 {similarity:.2f} ≥ {limit}），计划不可用",
                              frames=[other_index, frame.index])
        seen[key] = frame.index
    prompts = [frame.prompt for frame in frames]
    if len(set(prompts)) != len(prompts):
        raise IVError("INVALID_MODE_OR_COUNT", "存在完全相同的帧 Prompt，禁止同一 Prompt 调 N 次")
    return SequencePlan(direction=direction_key, branch=branch,
                        identity_anchor=anchor.description,
                        axis=rules.progression_axis_hint,
                        template_version=SEQUENCE_TEMPLATE_VERSION, frames=frames)


# ------------------------------------------------------------------ 执行
@dataclass
class SequenceExecution:
    plan: dict
    sequence_plan: SequencePlan
    anchor: dict
    anchor_revision: str | None
    frames: list[FrameRecord]
    status: str
    budget: dict
    warnings: list[dict]
    errors: list[dict]
    retry_counts: dict

    @property
    def completed_frames(self) -> list[FrameRecord]:
        return [frame for frame in self.frames if frame.status == "completed"]

    @property
    def failed_frames(self) -> list[FrameRecord]:
        return [frame for frame in self.frames if frame.status != "completed"]

    def validation(self) -> dict:
        intact = all(frame.status == "failed" or
                     (frame.sha256 and frame.width and frame.height) for frame in self.frames)
        return {
            "asset_integrity_passed": bool(intact),
            "sequence_complete": len(self.completed_frames) == len(self.frames),
            "completed_count": len(self.completed_frames),
            "total_count": len(self.frames),
            "failed_indices": [frame.index for frame in self.failed_frames],
        }


def _validate_frame(image_bytes: bytes, aspect_ratio: str, config: dict) -> tuple[int, int]:
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
    except Exception as exc:  # noqa: BLE001
        raise IVError("IMAGE_VALIDATION_FAILED", f"单帧无法解码: {exc}",
                      retryable=True) from exc
    width, height = image.size
    if width < 1 or height < 1:
        raise IVError("IMAGE_VALIDATION_FAILED", f"单帧尺寸非法 {width}×{height}", retryable=True)
    numerator, denominator = (float(part) for part in aspect_ratio.split(":"))
    target = numerator / denominator
    actual = width / height
    error = abs(actual - target) / target
    if error > float(config["aspect_ratio_tolerance_ratio"]):
        raise IVError("IMAGE_VALIDATION_FAILED",
                      f"第 {width}×{height} 帧比例 {actual:.4f} 偏离请求 {aspect_ratio}"
                      f"（误差 {error:.2%}）",
                      aspect_ratio_error_ratio=round(error, 6), retryable=True)
    return width, height


class SequenceRunner:
    """逐帧生成器；保留已成功帧与帧计划，支持按原索引补生成。"""

    def __init__(self, *, adapter, registry: DirectionRegistry | None = None,
                 config: dict | None = None, sleep=None) -> None:
        self.adapter = adapter
        self.registry = registry or DirectionRegistry()
        self.config = config or load_config()
        self.sleep = sleep

    # ---------------------------------------------------------- 单帧
    def _generate_one(self, *, frame: FrameRecord, prompt: str, aspect_ratio: str, tier: str,
                      budget: Budget, reference: ReferenceImage | None,
                      previous: list[FrameRecord]) -> tuple[FrameRecord, list[dict]]:
        policy_extra = int(self.config["retry"]["frame_max_extra_retries"])
        warnings: list[dict] = []
        attempt = 0
        last_error: IVError | None = None
        while attempt <= policy_extra:
            attempt += 1
            try:
                # 单帧重试由本层负责（次数取自 retry.frame_max_extra_retries），
                # 适配器内层不再重复重试，索引与预算都保持可控。
                result = self.adapter.generate(
                    GenerationRequest(prompt=prompt, aspect_ratio=aspect_ratio,
                                      resolution_tier=tier, reference_image=reference,
                                      purpose=f"sequence_frame_{frame.index:02d}"),
                    budget=budget, sleep=self.sleep,
                    policy=RetryPolicy.from_config(self.config, max_extra_retries=0))
                width, height = _validate_frame(result.image_bytes, aspect_ratio, self.config)
            except IVError as exc:
                last_error = exc
                if exc.code not in FRAME_LEVEL_CODES:
                    raise
                if exc.spec.retryable and attempt <= policy_extra:
                    continue
                break

            duplicate_of, similarity = self._nearest_duplicate(result.image_bytes, previous)
            limit = float(self.config["sequence"]["near_duplicate_similarity_max"])
            if duplicate_of is not None and similarity >= limit and attempt <= policy_extra:
                last_error = IVError("IMAGE_VALIDATION_FAILED",
                                     f"与第 {duplicate_of} 帧相似度 {similarity:.3f} ≥ {limit}，重试本帧",
                                     retryable=True)
                continue

            frame.status = "completed"
            frame.image_bytes = result.image_bytes
            frame.width, frame.height = width, height
            frame.bytes_size = len(result.image_bytes)
            frame.sha256 = sha256_bytes(result.image_bytes)
            frame.attempts = attempt
            frame.error = None
            if duplicate_of is not None and similarity >= limit:
                frame.near_duplicate_of = duplicate_of
                frame.similarity_max = round(similarity, 6)
                warnings.append({
                    "code": "IMAGE_VALIDATION_FAILED",
                    "message": (f"第 {frame.index} 帧与第 {duplicate_of} 帧相似度 {similarity:.3f}"
                                f" ≥ {limit}，重试 {attempt - 1} 次后仍然近重复；保留原槽位并标注"),
                    "asset_id": f"frame_{frame.index:02d}",
                    "severity": "warning",
                })
            return frame, warnings

        frame.status = "failed"
        frame.attempts = attempt
        code = last_error.code if last_error else "IMAGE_VALIDATION_FAILED"
        message = last_error.message if last_error else "未知失败"
        # asset_id 与产物清单用同一套 id，便于下游按帧定位；item_ref 保留给 IVError 语义
        frame.error = {"code": code, "message": message,
                       "asset_id": f"frame_{frame.index:02d}",
                       "item_ref": f"frame_{frame.index:02d}",
                       "recoverable": True, "retry_available": True,
                       "attempts": attempt}
        return frame, warnings

    def _nearest_duplicate(self, image_bytes: bytes,
                           previous: list[FrameRecord]) -> tuple[int | None, float]:
        best_index, best = None, 0.0
        for frame in previous:
            if frame.status != "completed" or not frame.image_bytes:
                continue
            similarity = image_similarity(image_bytes, frame.image_bytes)
            if similarity > best:
                best, best_index = similarity, frame.index
        return best_index, best

    # ---------------------------------------------------------- 全量
    def run(self, *, direction_key: str, branch: str, frame_count: int, aspect_ratio: str,
            input_type: str, anchor: Anchor, reference_image: ReferenceImage | None = None,
            requested_tier: str | None = None, budget: Budget | None = None) -> SequenceExecution:
        plan_result = plan_generation(
            output_mode="sequence", grid_size=None, image_count=frame_count,
            aspect_ratio=aspect_ratio, input_type=input_type, direction=direction_key,
            branch=branch, capability=self.adapter.capability, requested_tier=requested_tier,
            config=self.config, provider_type=self.adapter.provider_type, reference_bytes=
            len(reference_image.data) if reference_image else None)
        plan_result.raise_if_blocked()
        plan = plan_result.plan
        # manifest 的 model_id 是追溯的关键字段：能力档案里没有它（声明只有 13 个字段），
        # 必须由适配器回填真实模型名，否则产物里会留空串。
        plan["model_id"] = getattr(self.adapter, "model_id", "")
        sequence_plan = plan_sequence(registry=self.registry, direction_key=direction_key,
                                      branch=branch, frame_count=frame_count,
                                      aspect_ratio=aspect_ratio, resolution_tier=plan["tier"],
                                      anchor=anchor, config=self.config)
        budget = budget or Budget.plan(output_mode="sequence", image_count=frame_count,
                                       input_type=input_type, tier=plan["tier"],
                                       config=self.config)
        budget.preflight()

        warnings: list[dict] = [
            {"code": code, "message": message, "severity": severity}
            for code, message, severity in (plan["tier_warnings"] or [])
        ]
        errors: list[dict] = []
        anchor_revision: str | None = (
            sha256_bytes(reference_image.data) if reference_image is not None else None)
        frames: list[FrameRecord] = []
        retry_counts: dict[str, int] = {}

        for frame in sequence_plan.frames:
            if input_type == "text_only" and frame.index > 1:
                anchor_frame = frames[0]
                if anchor_frame.status != "completed" or not anchor_frame.image_bytes:
                    break
                frame_reference = ReferenceImage(anchor_frame.image_bytes, "image/png", "frame_01.png")
            else:
                frame_reference = reference_image
            previous = [item for item in frames if item.status == "completed"]
            record, frame_warnings = self._generate_one(
                frame=frame, prompt=frame.prompt, aspect_ratio=aspect_ratio, tier=plan["tier"],
                budget=budget, reference=frame_reference, previous=previous)
            warnings.extend(frame_warnings)
            retry_counts[f"frame_{frame.index:02d}"] = max(0, record.attempts - 1)
            if input_type == "text_only" and frame.index == 1 and record.status != "completed":
                error = record.error or {"code": "SEQUENCE_ANCHOR_FAILED"}
                raise IVError("SEQUENCE_ANCHOR_FAILED",
                              f"纯文本序列第 1 帧失败（{error.get('code')}），停止后续张数；"
                              "可重试第 1 帧",
                              failed_index=1, attempts=record.attempts,
                              upstream_code=error.get("code"))
            frames.append(record)
            if record.error:
                errors.append(record.error)

        completed = [frame for frame in frames if frame.status == "completed"]
        status = "completed" if len(completed) == len(sequence_plan.frames) else "partial_failed"
        return SequenceExecution(plan=plan, sequence_plan=sequence_plan, anchor=anchor.to_dict(),
                                 anchor_revision=anchor_revision, frames=frames, status=status,
                                 budget=budget.to_execution(), warnings=warnings, errors=errors,
                                 retry_counts=retry_counts)

    # ---------------------------------------------------------- 续跑
    def retry_failed_frames(self, execution: SequenceExecution, *,
                            reference_image: ReferenceImage | None = None,
                            budget: Budget | None = None) -> SequenceExecution:
        """只补生成失败帧：保持 task/方向/分支/锚点/索引，成功帧一字不动。"""
        if execution.anchor_revision is not None and reference_image is not None:
            if sha256_bytes(reference_image.data) != execution.anchor_revision:
                raise IVError("INVALID_INPUT",
                              "参考图与首次执行时的锚点版本不一致，需整组重建",
                              expected_revision=execution.anchor_revision)
        anchor_frame = next((frame for frame in execution.frames
                             if frame.index == 1 and frame.status == "completed"
                             and frame.image_bytes), None)
        if execution.plan["input_type"] == "text_only" and anchor_frame is None:
            raise IVError("SEQUENCE_ANCHOR_FAILED",
                          "第 1 帧尚未成功，图 2–N 无从锚定；必须先重试第 1 帧",
                          failed_index=1)
        budget = budget or Budget.plan(output_mode="sequence",
                                       image_count=max(1, len(execution.failed_frames)),
                                       input_type=execution.plan["input_type"],
                                       tier=execution.plan["tier"], config=self.config)
        budget.preflight()
        retried: dict[int, FrameRecord] = {}
        for frame in execution.failed_frames:
            if execution.plan["input_type"] == "text_only":
                frame_reference = ReferenceImage(anchor_frame.image_bytes, "image/png", "frame_01.png")
            else:
                frame_reference = reference_image
            previous = [item for item in execution.frames
                        if item.status == "completed" and item.index != frame.index]
            record, frame_warnings = self._generate_one(
                frame=frame, prompt=frame.prompt, aspect_ratio=execution.plan["aspect_ratio"],
                tier=execution.plan["tier"], budget=budget, reference=frame_reference,
                previous=previous)
            execution.warnings.extend(frame_warnings)
            execution.retry_counts[f"frame_{frame.index:02d}"] = max(0, record.attempts - 1)
            retried[frame.index] = record
        execution.frames = [retried.get(frame.index, frame) for frame in execution.frames]
        completed = [frame for frame in execution.frames if frame.status == "completed"]
        execution.status = ("completed" if len(completed) == len(execution.frames)
                            else "partial_failed")
        execution.errors = [frame.error for frame in execution.frames if frame.error]
        execution.budget = budget.to_execution()
        return execution


def execute_sequence(*, adapter, direction_key: str, branch: str, frame_count: int,
                     aspect_ratio: str, input_type: str, anchor: Anchor,
                     reference_image: ReferenceImage | None = None,
                     requested_tier: str | None = None,
                     registry: DirectionRegistry | None = None, config: dict | None = None,
                     budget: Budget | None = None,
                     sleep=None) -> SequenceExecution:
    runner = SequenceRunner(adapter=adapter, registry=registry, config=config, sleep=sleep)
    return runner.run(direction_key=direction_key, branch=branch, frame_count=frame_count,
                      aspect_ratio=aspect_ratio, input_type=input_type, anchor=anchor,
                      reference_image=reference_image, requested_tier=requested_tier,
                      budget=budget)
