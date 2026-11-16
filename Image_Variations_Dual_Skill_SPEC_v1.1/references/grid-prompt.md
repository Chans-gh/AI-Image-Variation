# Grid Prompt 模板（`grid-prompt-v1`）

**适用范围**：Skill 01 Grid 模式（2×2 / 3×3 / 4×4），**一次请求**生成整块网格板。
**配套契约**：`contracts/image-artifact.schema.json`（1.1）。
**模板版本**：`grid-prompt-v1`，写入 manifest 的 `prompt_template_version`。

## 1. 组装顺序（固定，不得调换）

```text
GLOBAL_RULES        # 一次成板、NxN、等格、主体唯一、单方向、禁止元素
+ SUBJECT_ANCHOR    # 原图派生或文本锚点；全格共享同一份
+ DIRECTION_RULES   # 所选方向一条：allowed / must_preserve / avoid
+ ASPECT_AND_SIZE    # 单格比例 = 整板比例；目标档位与最小单格边长
+ CELL_PLAN[1..N²]  # 行优先，每格一个不重复的主变化点
+ OUTPUT_RULES       # 成图格式、无编号无边框无文字无 UI
```

## 2. 变量表

| 变量 | 来源 | 说明 |
|---|---|---|
| `{grid_size}` | 任务参数 | 2 / 3 / 4 |
| `{tile_count}` | 派生 | `grid_size²` |
| `{aspect_ratio}` | 参数/继承 | 单格与整板同一比例（N×N 等格） |
| `{resolution_tier}` | §3.4 推导 | 1K / 2K / 4K（整板档位） |
| `{tile_edge_min_px}` | 规范 | 默认 1024，写进 Prompt 供模型理解目标尺寸 |
| `{subject_anchor}` | 锚点模块 | 主体身份/结构/材质/关键部件的稳定描述 |
| `{direction_key}` / `{direction_rules}` | registry | 单方向规则正文摘要 |
| `{cell_plan}` | 规划器 | N² 条不重复变化点，行优先编号 |
| `{avoid_list}` | 合并 | 方向 avoid + 平台安全 + 通用禁止项 |

## 3. 模板正文

```text
[GLOBAL_RULES]
生成一张完整的 {grid_size}×{grid_size} 网格作品板，共 {tile_count} 个等大画格，一次成图。
整板与每一格都是 {aspect_ratio} 比例；所有画格宽度与高度完全相等，边界可按整数坐标直接切分。
整板零间距、零边框、零底衬、零投影、无任何序号、文字、水印、UI 元素。
画面中只有一个主体；主体不得跨格、不得被格线裁断。
整板只体现一个变化方向：{direction_key}。

[SUBJECT_ANCHOR]
{subject_anchor}

[DIRECTION_RULES]
{direction_rules}

[ASPECT_AND_SIZE]
目标档位 {resolution_tier}（整板），单格长边不低于 {tile_edge_min_px}px；
若无法同时满足，请在保持画格等大与比例的前提下尽量提高分辨率。

[CELL_PLAN]
{cell_plan}
（按从左到右、从上到下依次对应第 1 格到第 {tile_count} 格）

[OUTPUT_RULES]
输出一张可直接解码的位图。不要拼贴、不要留白、不要边框、不要任何文字或编号。
每一格都是完整构图，主体在格内完整可见。
```

## 4. `{cell_plan}` 生成规则

1. 逐格写"第 i 格：<变化点>（保持：<该格需稳定的元素>）"，`i` 从 1 到 N²。
2. **N² 条变化点不得重复**；相邻格位不得只做幅度微调（例如只在亮度上差一档）。
3. 变化点必须落在所选方向的 `allowed_changes` 内；不得越界到别的方向。
4. 每格都必须复述同一份 `must_preserve`，防止模型在个别格位漂移主体。
5. Grid 不表达时间/因果顺序：不得使用"然后、接着、最终"等叙事连词（`narrative` 方向在 Grid 下同样不承诺顺序）。

### 示例（`light` 方向，2×2，1:1）

```text
第 1 格：柔和清晨侧光，阴影向右延伸（保持：主体形态、场景陈设、构图不变）
第 2 格：正午高对比顶光，短硬阴影（保持：主体形态、场景陈设、构图不变）
第 3 格：黄昏低角度逆光，暖调轮廓光（保持：主体形态、场景陈设、构图不变）
第 4 格：夜间单侧台灯暖光，明显明暗交界（保持：主体形态、场景陈设、构图不变）
```

### 示例（`state` 方向，3×3，3:4）

```text
第 1 格：静置未启动（保持：身份、造型、场景、光影）
第 2 格：刚被拿起，手部入画（保持：同上）
第 3 格：使用中，主体处于工作状态（保持：同上）
...（共 9 格，每格一个独立状态，格间无时间顺序）
```

## 5. 反例（生成时必须显式排除）

- ❌ 输出 N² 张独立图片，或先分别生成再拼接。
- ❌ 出现网格线、编号、留白带、"1/4"字样、水印。
- ❌ 主体跨格，或某格主体缺失。
- ❌ 各格比例不一致（例如把 3:4 单格拼成 1:1 整板）。
- ❌ 格与格之间只有亮度/大小微调，方向差异不显著。
- ❌ 在 `styling` 任务里被通用规则误锁"服装不变"（方向规则优先于通用约束）。
- ❌ 在 `style` 任务里替换主体身份（风格可变，主体不可变）。

## 6. 与校验的对应关系

| Prompt 约束 | 对应校验 | 失败处理 |
|---|---|---|
| 一次成板、NxN、等格 | `grid_structure_passed` | 重试（最多 2 次额外） |
| 零间距零边框 | `seam_score` + 框线检测（SPEC 01 §7.3） | `<0.60` 失败；`0.60–0.85` 记 `GRID_SEAM_RISK` |
| 单格长边达标 | `tile_edge_px` / `tile_edge_ok` | 按 §3.4 分级：警告或阻断 |
| 格间差异显著、主体一致 | 语义视觉核验（可用时） | 只作为警告，不单独阻断 |

## 7. 版本与变更

- 模板文本变更必须升级 `grid-prompt-vN` 并写入 manifest `prompt_template_version`；旧任务仍按旧版本解释，便于复现。
- 方向相关的措辞**不得**硬编码进模板：模板只放 `{direction_rules}` 占位，规则正文来自 `directions/registry.json`。
