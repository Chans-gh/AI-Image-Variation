# 待规范补充清单（提交给冻结包下一版）

本文件只记录**实现过程中发现的、必须由规范包处理的问题**。规范包
`Image_Variations_Dual_Skill_SPEC_v1.1/` 全程只读，本仓库未改动其任何字节
（`shared/check_baseline.py` 每次校验 238/238，CI 强制）。

每条都带：现象、实测证据、当前实现如何应对（不静默降级）、建议的规范动作。

---

## R-1｜缺错误码：参考图临时分享的收回失败

- **现象**：上游平台要求参考图先用它的接口上传换成公开 URL（内联 data URL 会被上游拒绝，实测 3/3 失败）。
  用完必须 `DELETE /api/v1/assets/{hash}` 取消分享。这一步可能失败（网络异常、非 2xx）。
- **缺什么**：`references/error-registry.json` 里 image_variations 的 **23 个码没有覆盖"资产收回失败"**；
  最接近的 `REFERENCE_REQUIRED` 语义完全不符（那是"该给图却没给"）。
- **当前实现**：检查 DELETE 状态码；异常与非 2xx（404 视为本就不存在）记为**警告**
  `REFERENCE_CLEANUP_FAILED`（`severity=warning`），文案注明"URL 可能仍公开可达，请到后台手动删除"，
  并在末尾标注「该码不在冻结错误码表内，待在规范中补充」。不抛错、不丢已生成的图；
  警告经 `take_warnings()` 并入产物 `warnings[]`，生成失败时并入 `IVError.detail["warnings"]`。
- **为什么不能自己加码**：用户约束「一律显式报 error-registry.json 里的错误码」+「规范包只读，
  要改先提理由确认」。自造码会破坏"错误码单一事实来源"。
- **建议**：下一版规范新增正式码，例如 `ASSET_CLEANUP_FAILED`
  （`blocking=false`、`retryable=true`、语义="临时资产收回失败，需人工清理"），本仓库随即将常量切换过去。
- **用户裁决（2026-11-17）**：需要补正式码，先记录在此。

---

## R-2｜D-24 接缝判据与冻结的 Grid 提示词模板互相矛盾

- **现象**：一张**质量良好**的真实 2×2 整板被判结构失败。
- **双方原文**（都在冻结包内）：
  - `references/grid-prompt.md` §3 模板正文要求「**每一格都是完整构图**」「主体不得跨格」
    「第 k 格：<不同变化点>」→ 产物是 **N² 张各自完整的变体（接触表）**；
  - `specs/01-Image-Variations-Skill-v1.2.md` 第 267–272 行 D-24 要求 `boundary_diff ≈ interior_diff`
    （`score ≥0.85` 需 `ratio ≤1.3`）→ 要求**跨格连续**。
- **实测证据**：真实整板 `boundary_means=[48.6, 75.1]`、`interior_means=[2.9~14.5]`、
  `seam_ratio=8.57` → `seam_score=1−(8.57−1)/2=0.000`；`border_lines=[]`（无框线，"无框"这项通过）。
  接触表在数学上**不可能**满足 D-24：任何阈值都救不回。
- **旁证**：`specs/01-…-v1.2.md` 第 597 行本就标注该阈值「**[修订决策 D-24；待实测校准]**」——
  本文即第一个真实校准数据点。
- **当前实现**：`config/iv.config.json` 新增 `seam.grade_mode`，默认 `artifact_only`：
  结构判 `pass`（等大格子、无框线、无留白带仍是硬门禁），但**强制**带 `GRID_SEAM_RISK` 披露警告
  （"本产物不是无缝整板，不得宣称无缝"）；`seam_score`/`seam_check_method`/`seam_ratio` 照写（满足 AC-28），
  并新增 `validation.seam_gate_mode` 记录当时判据。`continuity_gate` 可随时切回。
- **建议（三选一，需规范作者定）**：
  1. 明确 Grid 语义为**接触表**，把 AC-13/AC-28/第 597 行的"无缝"要求改写为"无框线/无留白带/等大格子"；
  2. 或拆成两个变体：`grid_contact_sheet`（默认，伪影检测）与 `grid_mosaic`（要求跨格连续，保留 D-24）；
  3. 或保留 D-24 但降为警告（等价于当前实现），并修正文档中"无缝"的措辞。
- **用户裁决（2026-11-17）**：采用方案 A（连续性降为警告、保留伪影硬检）。

---

## R-3｜错误码总数对不上：36 还是 37

- `references/ACCEPTANCE_MATRIX.md` 写「36 个错误码」，而 `references/error-registry.json` 实际是 **37** 条
  （image_variations 占 23 条）。
- **当前实现**：以 JSON 为唯一事实来源（运行时读取），未硬编码任何计数。
- **建议**：核对后统一两处数字。

---

## R-4｜单格下限的度量口径：长边还是短边

- `specs/01-…-v1.2.md` §3.4 说单格**长边** ≥1024；冻结的 `tests/checks.py` 用**短边**判定。
- **当前实现**：配置里同时保留 `tile_edge_min_px=1024`（推荐下限，按长边）、
  `tile_edge_warn_px=768`、`tile_edge_hard_min_px=512`（硬下限），`validation` 同时写
  `tile_edge_px`（短边）与 `tile_long_edge_px`（长边），两个口径都能核。
- **建议**：规范明确唯一口径，并说明另一个口径的用途。

---

## R-5｜`tier_to_board_side_px` 是标量，但真实输出按比例取不同边长

- 实测：上游 1:1 恒 1024×1024；3:4 输出 **896×1200**（长边 1200）。同一模型的"1K"在不同比例下边长不同。
- `references/provider-compat.md` 第 34 行称 `tier_to_board_side_px` 是档位推导的**唯一输入**，
  第 104 行要求"各档位真实边长必须实测回填"——当前结构能表达"1K=1024"，
  但**无法表达"1K 在 3:4 下长边是 1200"**。
- **当前实现**：档案按实测回填 1:1 的整板边长（Grid 只用 1:1，恰好够用）；
  真实输出尺寸一律以产出的 `board_size_px` / `images[].width/height` 为准，不用标量反推。
- **建议**：能力声明允许 `tier_to_board_side_px` 写成按比例的对象，例如
  `{"1K": {"1:1": 1024, "3:4": 1200}}`（保留标量形式兼容）。

---

## R-6｜失败尝试的逐次原因未进入产物

- `validation.grid_attempts` 只记尝试次数，**不记每次为什么失败**。本轮真实跑测中
  `grid_attempts=2`，但第一次失败的原因（框线？留白带？接缝？）在产物里查不到，
  排查只能靠日志。
- **当前实现**：不改产物结构（避免引入未经契约的字段），跑测台的 `result.json` 里保留了
  执行器返回的 `attempts` 汇总，但不足以定位单次失败原因。
- **建议**：在 `validation` 里增加逐次尝试摘要（`attempts[]`：序号、verdict、失败码、`seam_score`）。

---

## R-7｜成本口径未打通：USD 单价 vs 实际计费单位

- 配置里 `budget.cost_per_call` 是 USD 单价，产出的 `execution.cost_estimate`/`cost_currency="USD"`
  都是美元口径；而真实平台按「算力/张」计费（实测 1 算力/张）。
- **当前实现**：`calls_used` 是真实调用张数（可对账），`cost_estimate` 只是配置推测值。
  跑测台额外用 `--max-calls` 做**算力硬闸**，逐 cell 记账。
- **建议**：预算与成本允许声明"计费单位"（`billing_unit: usd|credit|call`）与单价，
  避免把美元估算误当真实账单。
