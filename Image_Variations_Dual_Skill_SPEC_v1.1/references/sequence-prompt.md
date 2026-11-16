# Sequence Prompt 模板（`sequence-prompt-v1`）

**适用范围**：Skill 01 Sequence 模式（4–16 张），**每张一条独立 Prompt**、每张一次独立请求。
**配套契约**：`contracts/image-artifact.schema.json`（1.1）、`job-state`（1.0）。
**模板版本**：`sequence-prompt-v1`。

## 1. 组装顺序（每张一份）

```text
GLOBAL_RULES        # 主体核心识别 + 单方向 + 单分支 + 禁止换主体 + 画幅/档位
+ IDENTITY_ANCHOR   # 原图描述，或纯文本模式下"图 1"的锚点描述
+ DIRECTION_RULES   # 当前唯一方向
+ BRANCH_RULES      # variants / progression / narrative 其中之一
+ FRAME_PLAN[i]     # 第 i 张独有的主变化点与画面目标
+ ORDER_CONTEXT     # 阶段摘要，仅 progression / narrative 需要
+ OUTPUT_RULES       # 单张成图，无编号、无分镜边框、无 UI、无水印
```

**禁止**：把 `FRAME_PLAN[i]` 的变化点复制成 N 条几乎相同的 Prompt；也禁止用一次 `n=N` 的调用代替逐张差异化请求。

## 2. 三种分支模板

### 2.1 `variants`（离散变体）

```text
[BRANCH_RULES]
本组是同一主题下的 {n} 个**独立且明显不同**的方案，不构成时间线或因果。
每张之间的差异必须显著且可描述；不允许近重复。
覆盖强弱/风格/形态的多个层次，而不是同一方案的微调。
```

| 检查点 | 判据 |
|---|---|
| 差异显著性 | 任意两张的主变化点必须不同，且不属于同一变化点的幅度微调 |
| 近重复 | 语义相似度超过阈值（实现侧配置，默认 0.9）即视为重复，需重规划 |
| 主体稳定 | 每张都带同一 `identity_anchor` |

### 2.2 `progression`（渐进演变）

```text
[BRANCH_RULES]
先确定一条**单一主变量**的阶段轴：{axis}。
第 1 张为起点、第 {n} 张为终点，中间各张按阶段顺序单调推进。
除用户明确要求往返的情况外，不得大幅跳变或回退。
除主变量外，其余元素保持稳定。
```

| 检查点 | 判据 |
|---|---|
| 单调性 | 阶段指标沿轴单调（允许平台期，不允许回退） |
| 跨度 | 起点与终点差异必须显著；不得只在同一档位内微调 |
| 连续性 | 相邻两张之间没有未解释的跳跃 |

### 2.3 `narrative`（情节叙事）

```text
[BRANCH_RULES]
同一世界观内的 {n} 个关键时刻，按 起因 → 发展 → 转折 → 结果 展开（按张数扩展）。
角色/物品的身份、造型、空间关系必须可解释地连续。
不得每张另起一个故事，也不得补造未展示的关键事实。
```

| 检查点 | 判据 |
|---|---|
| 关系连续 | 人物/物品的前后状态可解释 |
| 空间连续 | 场景切换有合理过渡 |
| 不编造 | 画面只呈现可画出的情节，不宣称真实事件 |

## 3. 锚点规则（与 SPEC 01 §6 一致）

| 输入类型 | 锚点 | 每张请求携带 |
|---|---|---|
| `image_only` / `image_plus_text` | 用户原图 | 原图作为参考图，**每一张都携带** |
| `text_only` | 图 1（先单独生成） | 图 1 作为参考图，图 2–N 全部引用图 1 |

- 纯文本模式的图 1 用 `identity_anchor + frame_01_change` 生成，且**图 1 就是序列的第一张结果**，不额外生成隐藏的第 0 张。
- 图 1 失败：停止后续，先重试图 1（`SEQUENCE_ANCHOR_FAILED`）。
- 图 i 失败：保留已成功图，按原索引补生成；**索引不移动**，避免故事顺序错乱。
- 模型不支持参考图输入：预检报 `REFERENCE_REQUIRED`，不得降级为各帧独立文生图。

## 4. `FRAME_PLAN` 数据结构

```json
{
  "direction": "light",
  "branch": "progression",
  "identity_anchor": "同一只浅灰陶瓷杯，形状、把手、釉面不变",
  "axis": "一天内的时间光轴",
  "frames": [
    {"index": 1, "change_point": "柔和清晨侧光", "phase_label": "起点", "locked_elements": ["主体形态", "场景陈设", "构图"]},
    {"index": 2, "change_point": "上午清晰自然光", "phase_label": "阶段2", "locked_elements": ["同上"]},
    {"index": 3, "change_point": "正午高对比顶光", "phase_label": "阶段3", "locked_elements": ["同上"]},
    {"index": 4, "change_point": "黄昏温暖低角度逆光", "phase_label": "终点", "locked_elements": ["同上"]}
  ]
}
```

- `index` 连续 1..N；`phase_label` 仅在 `progression` / `narrative` 下必填。
- 计划必须先于任何模型调用产出（SPEC 01 §8.1）；计划对象落盘并在 manifest 中以 `prompt_summary` / `phase_label` 反映。

## 5. 调用与预算

| 分支 | 计划调用数 | 说明 |
|---|---|---|
| 纯文本 Sequence | `1 + (N−1) + 重试` | 先出图 1，再逐张带参考图生成 |
| 有图 Sequence | `N + 重试` | 每张都用原图锚定 |

- 每个单帧最多 **2 次额外重试**；重试计入 `execution.calls_used` 与 `retry_counts`。
- 达到 `call_budget_limit` 时返回 `BUDGET_EXCEEDED`，保留已成功帧并给出拆批建议（例如 16 张拆成 2 批 ×8）。

## 6. 反例

- ❌ 用同一条 Prompt 调 N 次，或用 `n=N` 一次拿到 N 张。
- ❌ 图 2 引用图 1、图 3 引用图 2 的**链式接力**（会造成漂移）。
- ❌ `progression` 中途来回跳变；`narrative` 每张换一个故事。
- ❌ 单张失败后把后续索引前移。
- ❌ 各帧比例/档位不一致却静默接受。

## 7. 版本与变更

- 模板文本变更升级 `sequence-prompt-vN` 并写入 manifest；旧任务按旧版本解释。
- 方向与分支的具体措辞来自 `directions/registry.json` 与本文档，**不得**硬编码进 Skill 主流程分支判断。
