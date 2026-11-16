"""从 references/directions/registry.json 生成 12 份方向规则正文 Markdown。

这是规则文档生成器，不是 Skill 运行时。修改规则只需改 registry.json 后重跑本脚本，
以保证「注册表是唯一真相源」（SPEC 00 §00.7 第 3 条：新增方向不改主流程）。
"""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "references" / "directions" / "registry.json"
OUT = ROOT / "references" / "directions"

BRANCH_LABEL = {"variants": "离散变体", "progression": "渐进演变", "narrative": "情节叙事"}


def bullets(items):
    return "\n".join(f"- {x}" for x in items)


def build(direction, registry_version):
    d = direction
    supported = "、".join(f"`{b}`（{BRANCH_LABEL[b]}）" for b in d["branch_defaults"]["supported"])
    return f"""# 方向规则：{d['label']}（`{d['key']}`）

**规则版本**：{d['version']}　|　**注册表版本**：{registry_version}　|　**状态**：`{d['status']}`　|　**支持模式**：{'、'.join(d['modes'])}
**默认分支**：`{d['branch_defaults']['default']}`（{BRANCH_LABEL[d['branch_defaults']['default']]}）　|　**可选分支**：{supported}

> 本文件由 `tests/build_direction_docs.py` 从 `registry.json` 生成，请勿手改；规则变更改注册表后重跑脚本。

## 允许变化（allowed_changes）

{bullets(d['allowed_changes'])}

## 必须保持（must_preserve）

{bullets(d['must_preserve'])}

## 禁止项（avoid）

{bullets(d['avoid'])}

## 变化示例（variation_examples）

{bullets(d['variation_examples'])}

## Grid 规则

- 行优先映射：{'是' if d['grid_rules'].get('row_major') else '否'}
- 零间距零边框：{'是' if d['grid_rules'].get('no_border_no_gap') else '否'}
- Grid 不表达时间/因果顺序：{'是' if d['grid_rules'].get('no_sequence_semantics') else '否'}
- 格位提示：{d['grid_rules'].get('cell_hint', '—')}

## Sequence 规则

- 张数范围：{d['sequence_rules'].get('min_frames', 4)}–{d['sequence_rules'].get('max_frames', 16)} 张
- 渐进轴提示：{d['sequence_rules'].get('progression_axis_hint', '—')}
- 叙事分支需要世界观上下文：{'是' if d['sequence_rules'].get('narrative_context_required') else '否'}

## 与其他方向的边界

- 本方向只改变上表「允许变化」中的元素；出现 `must_preserve` 被改动，或落入 `avoid`，即判为该张不通过。
- 一个任务只能选择一个方向。若用户表达同时命中两个方向（例如"换场景同时换天气"），先请用户确认优先级，不自动混用。
- 主体一致性由 SPEC 01 §6 的锚点规则统一保证，本文件不重复定义锚点。
"""


def main():
    data = json.loads(REG.read_text(encoding="utf-8"))
    version = data["registry_version"]
    keys = []
    for d in data["directions"]:
        keys.append(d["key"])
        path = OUT / f"{d['key']}.md"
        path.write_text(build(d, version), encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    index = ["# 12 个变化方向索引", "",
             f"**注册表版本**：{version}　|　**机器可读源**：`registry.json`", "",
             "| # | key | 名称 | 支持模式 | 默认分支 | 规则文件 |", "|---|---|---|---|---|---|"]
    for i, d in enumerate(data["directions"], 1):
        index.append(f"| {i} | `{d['key']}` | {d['label']} | {'/'.join(d['modes'])} | "
                     f"`{d['branch_defaults']['default']}` | [{d['key']}.md]({d['key']}.md) |")
    index += ["",
              "## 扩展约定",
              "",
              "新增第 13 个方向时：只在 `registry.json` 增加一条记录 → 重跑生成脚本 → 补一条方向测试。",
              "**不得**在 Skill 主流程里增加 `if direction == ...` 之类的分支（SPEC 01 AC-25 / SPEC 00 §00.7）。",
              ""]
    (OUT / "README.md").write_text("\n".join(index), encoding="utf-8")
    print(f"wrote references/directions/README.md ({len(keys)} directions)")


if __name__ == "__main__":
    main()
