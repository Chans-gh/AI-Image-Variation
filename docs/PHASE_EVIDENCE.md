# Skill 01 实现阶段证据

每次运行追加一节，只记录**可执行证据**，不写"已完成"这类无证据结论。

## Phase 0+1 — 仓库骨架 + 夹具先行（2026-11-16）

**环境**：`.venv`（Python 3.12.14）+ Pillow 13.0.0；基线仍可用系统 `python3`（3.9.6 + Pillow 11.3.0）运行。

### 证据

| 命令 | 结果 |
|---|---|
| `.venv/bin/python shared/check_baseline.py` | `[PASS] 基线只读校验通过：238 个文件哈希一致` |
| `.venv/bin/python Image_.../tests/validate_example_contracts.py` | `总计 24/24 通过`（基线零回归） |
| `.venv/bin/python Image_.../tests/test_negative_fixtures.py` | `负例 10/10 按预期被拒` |
| `.venv/bin/python Image_.../tests/mock_provider.py --selftest` | 全部 PASS（19 项） |
| `.venv/bin/python skills/image-variations/tests/build_fixtures.py` | 正例 5 组 / 负例 25 组 |
| `.venv/bin/python skills/image-variations/tests/run_contract_tests.py` | `总计 48/48 通过` |

### 本阶段落地

- `shared/`：`baseline.py`（路径 + 只读哈希校验）、`errors.py`（错误码绑定，未登记码直接抛错）、`hashing.py`、`jsonio.py`、`paths.py`（路径穿越防护）、`pkg.py`（清单 + ZIP 安全）、`check_baseline.py`。
- `skills/image-variations/config/iv.config.json`：**全部阈值外置**（1024/768/512、seam 0.85/0.60、Grid 3 次尝试、单帧 2 次重试、预算、打包上限）。
- `skills/image-variations/config/error-registry-bindings.json`：AC ↔ 夹具 ↔ 错误码绑定，所有码必须存在于基线注册表。
- `skills/image-variations/tests/build_fixtures.py`：生成 5 组正例 + 25 组负例；图片为本地合成，非模型输出。
- `skills/image-variations/tests/run_contract_tests.py`：契约 + 语义 + 包清单三段回归。

### 本阶段发现（已修，记录以免复发）

1. `delivery-package` 缺 `package_id/package_type/created_at` 会违反基线契约 → 已在 `shared/pkg.py` 补齐。
2. 包清单路径穿越原先被吞成 `PACKAGE_MANIFEST_INVALID` → 现在优先 `PATH_TRAVERSAL_BLOCKED`。
3. `run_contract_tests` 的 `error_registry` 断言方向写反（把"校验器报错"当"已声明"）→ 已改为读取 manifest 显式声明。
4. 契约层（JSON Schema）**不校验** `image_count == tile 数` 与序列帧数，这类语义由业务校验器负责；对应用例的 `expect_schema_fail` 已按事实修正。

### 待确认的规范口径（不影响当前测试，需回填 DECISIONS）

- **单格长边口径**：SPEC 01 §3.4 写"单格**长边**不低于 1024"，基线 `tests/checks.py` 用 `min(tileW, tileH)`。
  本轮实现采用"长边为主判据 + 基线的 min 边安全下限"，fixture 的 `validation.tile_edge_px` 仍按基线写 min，
  同时在 `validation.tile_long_edge_px` 记录长边。两者对 3:4 等非正方形单格结论不同，需在 DECISIONS 增补一条口径说明（不改基线）。

## Phase 2 — Provider 适配与预检（2026-11-16）

### 证据

| 套件 | 结果 |
|---|---|
| `test_preflight.py` | `Ran 37 tests ... OK` |
| `test_provider_errors.py` | `Ran 33 tests ... OK` |
| `test_adapters.py` | `Ran 19 tests ... OK` |
| `run_all.py` | `全部 6 个套件通过`（含基线只读 + 基线 24/24 + 契约 48/48） |

### 本阶段落地

- `provider/capability.py`：`ModelCapability` 能力声明（缺字段/缺档位表即 `MODEL_CAPABILITY_UNSUPPORTED`）
  + D-21 档位推导（`pick_tier`，含 `tile_min_edge_default` / `user_override` / `provider_degraded` 三态）
  + D-26 底线清单（`check_baseline_requirements`）。
- `provider/error_map.py`：`provider-compat §5` 映射表逐条实现，全部落到注册表错误码。
- `provider/retry.py`：`RetryPolicy`（可重试/不可重试边界、Retry-After、指数退避 + 抖动）与 `Budget`（前置 + 记账 + 拆批建议）。
- `provider/net.py`：HTTPS-only、SSRF 防护（内网/环回/链路本地/云元数据/DNS 解析）、`allow_local_endpoint` 仅回环生效、URL 内不得带凭据。
- `provider/transport.py`：`TransportRequest/Response`、`UrllibTransport`（零第三方 HTTP 依赖）、`resolve_secret`（拒绝明文 Key，只用 `secret://` / `env:` 引用）、脱敏 `redact`。
- `provider/openai_compat.py`：`/images/generations`（JSON）与 `/images/edits`（multipart，参考图真实字节进 body）、`tier_to_size` 显式折算宽高、解析 `b64_json`；仅返回 URL 时判 `IMAGE_VALIDATION_FAILED`（不把 URL 当最终引用）。
- `provider/gemini_compat.py`：`:generateContent` + `inline_data`、`x-goog-api-key` 走请求头（不进 URL）、解析 `candidates[].content.parts[].inlineData`、`promptFeedback.blockReason` / `finishReason=SAFETY` → `MODEL_REFUSAL`。
- `provider/mock.py`：`MockScenario` / `MockTransport` / `MockAdapter` / `ScriptedAdapter`；重试默认重复最后一个脚本，避免"意外成功"掩盖失败路径。
- `provider/registry.py`：`builtin` 未注入 → `BUILTIN_UNAVAILABLE`；`external` 两类协议 + mock。
- `preflight.py`：`plan_generation()` 一次过完模式/张数/分支/方向/比例/档位/预算，产出 `plan`（含 calls_planned、cost_estimate、tier、warnings）。

### 本阶段发现（已修）

1. `parse_response` 抛出的可重试错误（损坏图/比例不符/无图片）原先**不进入重试**，现在按 `retry_on` 走单帧重试预算。
2. `MockTransport` 超过脚本长度后回落默认成功响应，会把失败路径测成"成功"；已改为重复最后一条脚本。
3. `MockScenario` 的 `body_text` 原先被 `shape="raw"` 吞掉；已改为文本响应优先。
4. 相对导入越界（`..config`）与 `GenerationResult` 从 `base` 导入：已修正为包级导入。

### 待确认（累计，均不改基线）

- 单格长边口径（同 Phase 1 记录）。
- `provider-compat.md §1` 列出 `provider_type` / `base_url` / `api_key_secret_ref` / `model_id`，
  但**统一请求结构未定义**：本轮按 `ProviderRequest` 抽象实现，请求字段名以适配器版本锁定（`openai-image-compat/1.0`、`gemini-image-compat/1.0`），待真实 API 实测后回填。

## Phase 3 — Grid 单次成板 + 校验切图（2026-11-16）

### 证据

| 套件 | 结果 |
|---|---|
| `test_grid.py` | `Ran 37 tests ... OK`（5.4s） |
| `run_all.py` | `全部 7 个套件通过`（含基线只读 + 基线 24/24 + 契约 48/48） |

### 本阶段落地

- `prompt_builder.py`：从**只读基线** `references/grid-prompt.md` 提取模板正文（`grid-prompt-v1`），
  组装 GLOBAL_RULES / SUBJECT_ANCHOR / DIRECTION_RULES / ASPECT_AND_SIZE / CELL_PLAN / OUTPUT_RULES；
  模板缺小节、残留占位符、变化点重复、Grid 里出现叙事连词 → 一律显式报错。
  12 方向规则全部来自 `directions/registry.json`，代码里**不硬编码方向措辞**。
- `prompt_builder.py`（Sequence 侧同文件）：`build_frame_plan` 先于任何模型调用产出 N 条互不重复的
  `change_point`，并按 §1 组装顺序拼装单帧 Prompt（§2 三分支正文注入 `[BRANCH_RULES]`）。
- `seam.py`：按 §3.4 逐条实现 `boundary_gradient_v1`——
  `boundary_diff`（边界两侧各 `band_px`）与 `interior_diff`（**同几何**、挪进格内）之比
  → `seam_ratio`，再 `seam_score = clamp(1 − (ratio−1)/2, 0, 1)`；外加框线/留白带检测。
- `grid.py`：解码 → 对齐到 N 的整数倍（超 `board_side_tolerance_ratio` 即失败，不静默裁）→
  比例校验 → 行优先切图 → `crop_box` 覆盖/索引连续性校验 → 单格长边分级。
- `executor.py`：`execute_grid()` 一次成板（每次重试 = 再要一张**整板**，绝不改成拼 N 张单图），
  重试次数取 `retry.grid_max_attempts`，每次计入 `execution.calls_used`；
  `risk` 可交付但必须带 `GRID_SEAM_RISK`。
- `anchor.py`：锚点三态（`reference_image` / `first_frame` / `text_description`）。

### 算法校准结果（合成图回归，非真实模型）

| 输入 | seam_ratio | seam_score | 判定 |
|---|---|---|---|
| 连续整板（2/3/4 格） | 1.02–1.13 | 0.94–0.99 | pass |
| 弱接缝（相邻格亮度差 1 级） | 1.13 | 0.93 | pass |
| 弱接缝（差 3 级） | 1.48 | 0.76 | **risk** |
| 硬接缝（异色格） | 11.9 | 0.00 | fail |
| 白色框线 | 48.6 | 0.00 | fail（框线检测先命中） |
| 纯色板 | 0.00 | 1.00 | fail（留白带检测命中） |

### 本阶段发现（已修）

1. `score = seam/(seam+ref)` 与 `1/(1+max(0,ratio-1))` 都是我自拟的映射，
   **规范 D-24 已给出公式**；已改为 `clamp(1 − (seam_ratio − 1)/2, 0, 1)`（[specs/01 §3.4](Image_Variations_Dual_Skill_SPEC_v1.1/specs/01-Image-Variations-Skill-v1.2.md)）。
2. 边界量用"跨边界相邻对"、参考量用"2px 条带均值"会造成 **口径偏差 2×**（线性渐变下必然 ratio≈2，
   连续图被误判为有缝）。已改为两侧都用同一几何（band 宽条带、band 间距），只是把参考位置挪进格内。
3. 纯色整板在"边界跳变 = 0"时曾拿满分 1.0，属于**用分数掩盖内容缺失**；现由留白带规则直接判失败。
4. 夹具里的合成整板（`tests/support/renderer.py` 逐格贴图）本身就有硬接缝，
   不能拿它当"无缝基准"；测试已改用可控的连续场造图。

### 待确认（累计，均不改基线）

- 单格长边口径：SPEC 01 §3.4 写"单格**长边**不低于 1024"，而基线 `tests/checks.py` 用**短边**。
  当前实现：分级（`TILE_RESOLUTION_RISK/TOO_LOW`）按**长边**，`validation.tile_edge_px` 仍写短边。
  3:4 画幅下两种口径会给出不同结论（3×3 @4K：短边 682 / 长边 909）。

## Phase 4 — Sequence 逐帧生成与锚点/失败续跑（2026-11-16）

### 证据

| 套件 | 结果 |
|---|---|
| `test_sequence.py` | `Ran 34 tests ... OK` |
| `test_grid.py` | `Ran 39 tests ... OK`（新增整板损坏重试两例） |
| `run_all.py` | `全部 8 个套件通过` |

### 本阶段落地

- `sequence.plan_sequence()`：**先计划后调用**。产出 N 条 `FramePlan`（`index` / `change_point` /
  `visual_description` / `locked_elements` / `phase_label` / `prompt`），
  任何一格都不需要"先试试看"；帧数 4–16、分支必须被方向支持、帧计划不得重复。
- 逐帧 Prompt：`phase_label + dimension` 拼成唯一变化点；`FRAME_PLAN[i]` 注入本帧独有的变化点与
  保持项；`progression`/`narrative` 额外注入 `ORDER_CONTEXT`，`variants` 明确"不表达时间顺序"。
- `SequenceRunner`：
  - `text_only` 图 1 不带参考图；图 2–N **全部引用图 1 的字节**，实测断言不链式引用图 i−1；
  - `image_only` / `image_plus_text` 每帧都携带原图，并把原图 SHA256 记为 `anchor_revision`；
  - 图 1 失败 → `SEQUENCE_ANCHOR_FAILED` 并停止一切后续调用（实测只发生 1 次请求）；
  - 图 i 失败 → 保留原索引槽位、继续后续帧、最终 `partial_failed`，
    错误里带 `item_ref`/`retry_available`/`attempts`；
  - 任务级致命错误（鉴权等）立即中止，不继续烧预算。
- 近重复：计划期按变化点文案查重（同一轮维度不得重复）；生成期按 32×32 灰度相似度比对，
  ≥`sequence.near_duplicate_similarity_max` 时**重试本帧**，重试耗尽后保留原槽位并标注
  `near_duplicate_of` + `warnings[]`。
- `retry_failed_frames()` 续跑：只重生成失败帧，成功帧 SHA256 一字不变；参考图版本不一致时
  报 `INVALID_INPUT`（要求整组重建）；图 1 未成功时明确 `SEQUENCE_ANCHOR_FAILED`。

### 本阶段发现（已修）

1. **双层重试叠加**：适配器内层重试与执行器层重试同时生效，实际调用次数可达计划数的数倍，
   预算记账失真。现在执行器/序列器向适配器传 `max_extra_retries=0`，由上层统一掌控次数。
2. Grid 执行器原先用 `blocking` 判断可否重试，语义错了（`blocking` 只表示"必须显式报给用户"）。
   改为按 `retry_on` 集合与尝试次数判定，整板损坏现在会重试而不是立刻失败。
3. `plan_generation()` 缺少 `reference_bytes` 入参，导致"原图超过模型输入上限"这条底线无法生效；
   已补齐（Grid 与 Sequence 都会传真实字节数）。

### 待确认（累计，均不改基线）

- 错误码注册表**没有**"近重复"专用码。当前用 `IMAGE_VALIDATION_FAILED`（非阻断、可重试）承载
  "帧与已有帧近重复"，并把 `near_duplicate_of` 写进资产记录。若希望单独成码，请确认是否允许
  在注册表中新增（本轮未改基线）。
- 计划期查重口径：同一轮内**维度**不得重复（完全判定）；跨轮复用维度时用"（第 k 轮）"显式标注。
  若认为跨轮复用也算近重复，需要提高 `sequence.max_frames` 或为方向补更多 `allowed_changes`。
