"""Prompt 构建：Grid（一次成板）与 Sequence（逐帧）。

规则来源（只读）：
    - `references/grid-prompt.md`      → `grid-prompt-v1`，组装顺序固定
    - `references/sequence-prompt.md`  → `sequence-prompt-v1`，三分支
    - `references/directions/registry.json` → 12 方向规则

不变量：
    - 模板版本写进 manifest（`prompt_template_version` / `prompt_template_id`）；
    - 方向措辞**不硬编码**进模板，只以 `{direction_rules}` 注入；
    - 模板结构在加载时校验，缺少必需小节直接失败（防止静默降级成"差不多"的 Prompt）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from shared import baseline
from shared.errors import IVError

GRID_TEMPLATE_VERSION = "grid-prompt-v1"
SEQUENCE_TEMPLATE_VERSION = "sequence-prompt-v1"
GRID_SECTIONS = ("GLOBAL_RULES", "SUBJECT_ANCHOR", "DIRECTION_RULES", "ASPECT_AND_SIZE",
                 "CELL_PLAN", "OUTPUT_RULES")
NARRATIVE_CONJUNCTIONS = ("然后", "接着", "最终", "后来", "随后")


def _template_body(markdown: str, heading_pattern: str) -> str:
    """从 markdown 里截取模板代码块正文。"""
    match = re.search(heading_pattern, markdown)
    if not match:
        raise IVError("INVALID_INPUT", f"模板缺少小节: {heading_pattern}")
    fence = re.search(r"```text\n(.*?)```", markdown[match.end():], re.S)
    if not fence:
        raise IVError("INVALID_INPUT", f"模板小节 {heading_pattern} 缺少 ```text 代码块")
    return fence.group(1).rstrip() + "\n"


@dataclass
class DirectionRules:
    key: str
    label: str
    allowed_changes: list[str]
    must_preserve: list[str]
    avoid: list[str]
    default_branch: str
    supported_branches: list[str]
    progression_axis_hint: str
    narrative_context_required: bool

    def prompt_block(self) -> str:
        allowed = "；".join(self.allowed_changes)
        preserve = "；".join(self.must_preserve)
        avoid = "；".join(self.avoid)
        return (f"变化方向：{self.key}（{self.label}）。\n"
                f"允许变化：{allowed}。\n"
                f"必须保持：{preserve}。\n"
                f"禁止：{avoid}。")

    def change_points(self) -> list[str]:
        return [point for point in self.allowed_changes if point.strip()]


class DirectionRegistry:
    """12 方向注册表包装；新增第 13 个方向不需要改主流程。"""

    def __init__(self, data: dict | None = None) -> None:
        self.data = data or baseline.load_direction_registry()
        self.version = str(self.data["registry_version"])
        self._dirs = {item["key"]: item for item in self.data["directions"]}

    def keys(self) -> list[str]:
        return list(self._dirs)

    def get(self, key: str) -> DirectionRules:
        item = self._dirs.get(key)
        if item is None:
            raise IVError("INVALID_INPUT",
                          f"方向 {key!r} 不在注册表内；可用 {self.keys()}")
        branches = item.get("branch_defaults", {})
        return DirectionRules(
            key=item["key"],
            label=item["label"],
            allowed_changes=list(item["allowed_changes"]),
            must_preserve=list(item["must_preserve"]),
            avoid=list(item["avoid"]),
            default_branch=branches.get("default", "variants"),
            supported_branches=list(branches.get("supported", ["variants"])),
            progression_axis_hint=str(item.get("sequence_rules", {}).get("progression_axis_hint", "")),
            narrative_context_required=bool(
                item.get("sequence_rules", {}).get("narrative_context_required", False)),
        )


# ---------------------------------------------------------------- Grid
@dataclass
class GridPrompt:
    text: str
    template_version: str
    cell_plan: list[str]
    direction_key: str

    def to_manifest_fields(self) -> dict:
        return {"prompt_template_version": self.template_version,
                "cell_plan": list(self.cell_plan)}


def build_cell_plan(direction: DirectionRules, grid_size: int) -> list[str]:
    """N² 条不重复变化点（行优先）。不足时按方向规则扩展，仍保证两两不同。"""
    points = direction.change_points()
    if not points:
        raise IVError("INVALID_INPUT", f"方向 {direction.key} 没有可用的 allowed_changes")
    total = grid_size * grid_size
    plan: list[str] = []
    round_index = 0
    while len(plan) < total:
        for point in points:
            if len(plan) >= total:
                break
            label = point if round_index == 0 else f"{point}（第 {round_index + 1} 组层次）"
            plan.append(label)
        round_index += 1
    for phrase in NARRATIVE_CONJUNCTIONS:
        if any(phrase in point for point in plan):
            raise IVError("GRID_VALIDATION_FAILED",
                          f"Grid 变化点包含叙事连词 {phrase!r}：Grid 不表达时间顺序")
    if len(set(plan)) != total:
        raise IVError("GRID_VALIDATION_FAILED", "Grid 变化点存在重复")
    return plan


def build_grid_prompt(*, registry: DirectionRegistry, direction_key: str, grid_size: int,
                      aspect_ratio: str, resolution_tier: str, subject_anchor: str,
                      tile_edge_min_px: int) -> GridPrompt:
    direction = registry.get(direction_key)
    template = _template_body(baseline.load_prompt_template("grid"),
                              r"##\s*3\.\s*模板正文")
    missing = [name for name in GRID_SECTIONS if f"[{name}]" not in template]
    if missing:
        raise IVError("INVALID_INPUT", f"grid-prompt 模板缺少小节 {missing}")
    plan = build_cell_plan(direction, grid_size)
    cell_lines = "\n".join(
        f"第 {index} 格：{point}（保持：{'、'.join(direction.must_preserve)}）"
        for index, point in enumerate(plan, start=1)
    )
    values = {
        "grid_size": str(grid_size),
        "tile_count": str(grid_size * grid_size),
        "aspect_ratio": aspect_ratio,
        "resolution_tier": resolution_tier,
        "tile_edge_min_px": str(tile_edge_min_px),
        "subject_anchor": subject_anchor,
        "direction_key": direction_key,
        "direction_rules": direction.prompt_block(),
        "cell_plan": cell_lines,
    }
    text = template
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    leftover = re.findall(r"\{([a-z_]+)\}", text)
    if leftover:
        raise IVError("INVALID_INPUT", f"grid-prompt 模板仍有未替换占位符 {sorted(set(leftover))}")
    return GridPrompt(text=text, template_version=GRID_TEMPLATE_VERSION,
                      cell_plan=plan, direction_key=direction_key)


# ---------------------------------------------------------------- Sequence
@dataclass
class FramePlanItem:
    index: int
    change_point: str
    phase_label: str
    locked_elements: list[str]
    dimension: str = ""

    def to_dict(self) -> dict:
        return {"index": self.index, "change_point": self.change_point,
                "phase_label": self.phase_label, "locked_elements": list(self.locked_elements)}


@dataclass
class SequencePrompt:
    text: str
    template_version: str
    frame_index: int
    change_point: str
    phase_label: str

    def to_manifest_fields(self) -> dict:
        return {"prompt_summary": self.change_point, "phase_label": self.phase_label,
                "prompt_template_id": self.template_version}


def _branch_rules_block(markdown: str, branch: str) -> str:
    pattern = {
        "variants": r"###\s*2\.1",
        "progression": r"###\s*2\.2",
        "narrative": r"###\s*2\.3",
    }[branch]
    body = _template_body(markdown, pattern)
    lines = body.splitlines()
    if lines and lines[0].strip().startswith("[BRANCH_RULES]"):
        lines = lines[1:]
    return "\n".join(lines).strip()


def build_frame_plan(*, direction: DirectionRules, branch: str, frame_count: int) -> list[FramePlanItem]:
    """生成 N 条有序且互不重复的 FRAME_PLAN（先于任何模型调用）。"""
    points = direction.change_points()
    if not points:
        raise IVError("INVALID_INPUT", f"方向 {direction.key} 没有可用 allowed_changes")
    if branch not in ("variants", "progression", "narrative"):
        raise IVError("INVALID_MODE_OR_COUNT", f"分支非法: {branch!r}")
    if branch not in direction.supported_branches:
        raise IVError("INVALID_MODE_OR_COUNT",
                      f"方向 {direction.key} 不支持分支 {branch}，支持 {direction.supported_branches}")
    items: list[FramePlanItem] = []
    for index in range(1, frame_count + 1):
        dimension = points[(index - 1) % len(points)]
        cycle = (index - 1) // len(points) + 1
        if branch in ("progression", "narrative"):
            phase = ("起点" if index == 1 else "终点" if index == frame_count else f"阶段{index}")
        else:
            phase = f"变体{index}"
        # 帧数可能多于方向可用的维度数，此时维度按轮次循环，但**阶段标签保证唯一**，
        # 且拼进变化点，避免"同一个变化点重复 N 次"。
        suffix = f"（第 {cycle} 轮）" if cycle > 1 else ""
        change_point = f"{phase}/{frame_count}：{dimension}{suffix}"
        items.append(FramePlanItem(index=index, change_point=change_point, phase_label=phase,
                                   locked_elements=list(direction.must_preserve),
                                   dimension=dimension))
    if len({item.change_point for item in items}) != frame_count:
        raise IVError("INVALID_MODE_OR_COUNT", "帧计划存在重复变化点，需重规划")
    first_cycle = items[:min(frame_count, len(points))]
    if len({item.dimension for item in first_cycle}) != len(first_cycle):
        raise IVError("INVALID_MODE_OR_COUNT",
                      "方向允许的变化维度在同一轮内近重复（完全相同或高度相似），无法为每一帧给出不同的主变化点")
    return items


def build_sequence_prompt(*, registry: DirectionRegistry, direction_key: str, branch: str,
                          frame: FramePlanItem, frame_count: int, aspect_ratio: str,
                          resolution_tier: str, subject_anchor: str) -> SequencePrompt:
    """按 sequence-prompt.md §1 的固定顺序组装**单帧** Prompt。

    注意基线结构：§1 是组装顺序（不含 {占位符}），§2 才是三分支规则正文。
    因此这里按顺序拼装，并对 §1 声明的顺序做一致性校验。
    """
    direction = registry.get(direction_key)
    markdown = baseline.load_prompt_template("sequence")
    outline = _template_body(markdown, r"##\s*1\.\s*组装顺序")
    declared: list[str] = []
    for line in outline.splitlines():
        name = line.split("#", 1)[0].strip().lstrip("+").strip()
        if name:
            declared.append(name)
    branch_block = _branch_rules_block(markdown, branch).format(
        n=frame_count, axis=direction.progression_axis_hint or "按方向允许的阶段推进")

    order_context = ""
    if branch in ("progression", "narrative"):
        order_context = (f"当前为第 {frame.index}/{frame_count} 帧（{frame.phase_label}）；"
                         f"整体阶段轴：{direction.progression_axis_hint or '按方向允许的阶段推进'}。")

    sections: dict[str, str] = {
        "GLOBAL_RULES": ("[GLOBAL_RULES]\n"
                         f"只生成 1 张图（这是 {frame_count} 张序列中的第 {frame.index} 张）；"
                         "主体核心识别始终不变；\n"
                         f"整个任务只体现一个变化方向：{direction_key}；只体现一个分支：{branch}；\n"
                         f"画幅 {aspect_ratio}，目标档位 {resolution_tier}；不得更换主体或新增无关主体。"),
        "IDENTITY_ANCHOR": f"[IDENTITY_ANCHOR]\n{subject_anchor}",
        "DIRECTION_RULES": f"[DIRECTION_RULES]\n{direction.prompt_block()}",
        "BRANCH_RULES": f"[BRANCH_RULES]\n{branch_block.strip()}",
        "FRAME_PLAN[i]": (f"[FRAME_PLAN[{frame.index}]]\n"
                          f"本帧主变化点：{frame.change_point}；"
                          f"保持：{'、'.join(frame.locked_elements)}。"),
        "ORDER_CONTEXT": (f"[ORDER_CONTEXT]\n{order_context}" if order_context
                          else "[ORDER_CONTEXT]\n（本分支不表达时间顺序，可省略）"),
        "OUTPUT_RULES": ("[OUTPUT_RULES]\n只输出这一张图；不要分镜边框、编号、字幕、水印或 UI；"
                         "单张成图，可独立解码。"),
    }
    missing = [name for name in sections if name not in declared]
    if missing:
        raise IVError("INVALID_INPUT", f"sequence-prompt §1 缺少小节 {missing}")
    text = "\n\n".join(sections[name] for name in declared if name in sections)
    return SequencePrompt(text=text, template_version=SEQUENCE_TEMPLATE_VERSION,
                          frame_index=frame.index, change_point=frame.change_point,
                          phase_label=frame.phase_label)


def registry() -> DirectionRegistry:
    return DirectionRegistry()
