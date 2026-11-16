# 12 个变化方向索引

**注册表版本**：1.0　|　**机器可读源**：`registry.json`

| # | key | 名称 | 支持模式 | 默认分支 | 规则文件 |
|---|---|---|---|---|---|
| 1 | `state` | 状态拓展 | grid/sequence | `variants` | [state.md](state.md) |
| 2 | `view` | 视角拓展 | grid/sequence | `variants` | [view.md](view.md) |
| 3 | `scene` | 场景拓展 | grid/sequence | `variants` | [scene.md](scene.md) |
| 4 | `detail` | 细节拓展 | grid/sequence | `progression` | [detail.md](detail.md) |
| 5 | `light` | 光影拓展 | grid/sequence | `variants` | [light.md](light.md) |
| 6 | `composition` | 构图拓展 | grid/sequence | `variants` | [composition.md](composition.md) |
| 7 | `style` | 风格拓展 | grid/sequence | `variants` | [style.md](style.md) |
| 8 | `styling` | 造型拓展 | grid/sequence | `variants` | [styling.md](styling.md) |
| 9 | `weather` | 时间天气拓展 | grid/sequence | `progression` | [weather.md](weather.md) |
| 10 | `lens` | 镜头语言拓展 | grid/sequence | `variants` | [lens.md](lens.md) |
| 11 | `usage` | 用途拓展 | grid/sequence | `variants` | [usage.md](usage.md) |
| 12 | `narrative` | 情节拓展 | grid/sequence | `narrative` | [narrative.md](narrative.md) |

## 扩展约定

新增第 13 个方向时：只在 `registry.json` 增加一条记录 → 重跑生成脚本 → 补一条方向测试。
**不得**在 Skill 主流程里增加 `if direction == ...` 之类的分支（SPEC 01 AC-25 / SPEC 00 §00.7）。
