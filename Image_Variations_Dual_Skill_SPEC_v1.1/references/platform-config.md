# 平台配置说明（`platform-config.json`）

**配置版本**：1.0　|　**验证状态**：`unverified`
**机器可读源**：[`platform-config.json`](platform-config.json) —— 实现只读该文件，不把规则写死在代码里。

## 1. 两个必须区分的概念

| 字段 | 含义 | 能否当官方要求 |
|---|---|---|
| `product_recommendation` | 产品侧建议值（例如 2.35:1 横版封面） | **不能**。仅作默认值 |
| `platform_requirement` | 平台实际规则 | 只有实测确认后才能填；当前为 `null` |

`null` 表示**未知**，实现必须走「询问/降级/提示待验证」路径，**不得**把产品建议当平台硬限制，也不得凭空填一个数字。

## 2. 全局规则

- `tile_edge_min_px = 1024`：Skill 01 单格长边推荐下限（SPEC 01 §3.4）。
- `max_cover_upscale_factor = 1.5`：封面源图放大幅度上限（SPEC 02 §7.4）。
- `cover_fit_method_order = ["crop", "contain_pad"]`：优先智能裁切，其次等比补边；禁止拉伸。
- `batch_default_max_items = 5`：批量内容默认单批篇数，超过需用户确认拆批。
- `ai_disclosure_required_when = image_generated_by_ai`：AI 生图内容建议披露。

## 3. 已知待实测项（对应 DECISIONS 的 O-03 / O-04 / O-05）

| ID | 事项 | 当前处置 |
|---|---|---|
| O-03 | 公众号封面现行比例、像素、编辑器导入格式 | `platform_requirement=null`，先按产品建议导出并标注 `verified=false` |
| O-04 | 小红书标题/正文/图片数/话题限制 | 全部 `null`；实现必须按「未知则不硬校验，只提示」处理 |
| O-05 | 批量内容最大篇数 | 用 5 篇软限制 + 超限确认，不写死为平台上限 |

## 4. 回填流程（开发阶段）

1. 在真实平台确认规则（截图/官方文档记录）。
2. 更新 `platform-config.json` 对应 `platform_requirement` 与 `verified=true`，并写入 `verified_at` 与来源。
3. 同步更新 SPEC 02 §15 与 DECISIONS 的 O-03/O-04。
4. 在 `CHANGELOG` 记录，并更新受影响的测试夹具尺寸。

> 未完成回填前，任何"符合平台最新规格"的说法都属于未验证声明，不得写进交付说明。
