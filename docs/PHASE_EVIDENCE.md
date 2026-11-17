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

## Phase 5 — 打包、产物清单与 CLI 入口（2026-11-16）

### 证据

| 套件 | 结果 |
|---|---|
| `test_artifact.py` | `Ran 23 tests ... OK` |
| `run_all.py` | `全部 9 个套件通过`（基线只读 238/238、基线契约 24/24、契约夹具 48/48、预检 37、Provider 33、适配器 19、Grid 39、Sequence 34、产物 23） |
| 端到端 | Grid 与 Sequence 各跑一次 CLI，产物 `manifest.json` 通过基线 `image-artifact.schema.json` 校验，`delivery-package.json` 通过 `delivery-package.schema.json` 校验，`verify_manifest=[]`、`inspect_zip=[]` |

### 交付形状

- `SKILL.md`：唯一入口，只写编排、五条硬规则、分支判定表与产物契约，长文本全部指回只读基线。
- `scripts/run_task.py`：JSON 进、JSON 出。`--preflight-only`（不调用不落盘）、
  正式执行、`--retry-failed-of`（按原索引补失败帧，产出 rev2）。错误只输出错误码，不打印堆栈；
  退出码 0/2/3 区分完成、被错误码阻断、任务描述非法。
- `scripts/iv_skill/artifact.py`：`image-artifact` 清单 + `delivery-package` 清单 + 可选 ZIP。
- `scripts/iv_skill/synthetic.py`：`mock_synthetic` 合成 Provider，产物如实标注，仅用于无密钥自检。
- `scripts/iv_skill/task.py`：任务编排、参考图安全读取（workspace 内、越界即
  `PATH_TRAVERSAL_BLOCKED`）、capability 缺省即拒、失败帧续跑。

### 本阶段的关键取舍（写进清单语义，便于复核）

1. **失败帧不进 `images[]`**。契约 `$defs.image` 把 `width/height/sha256` 列为必填，
   失败的帧没有可验证的字节；若强行填值就是伪造。因此部分失败时只登记真实存在的图，
   `image_count` 仍是请求张数，失败信息放 `errors[].asset_id` 与 `validation.failed_indices`，
   索引保持原值不前移——这正是 AC-23/AC-31 要的"可交付且可复核"。
   若希望失败帧也占 `images[]` 一个位（需要放宽 schema），请确认，我不擅自改基线。
2. **`delivery-package.json` 不登记自己**（与基线示例一致），ZIP 内也不含它；
   `archive` 哈希在打包后写回 `delivery-package.json`。
3. **`consistency_anchor` 不落任何私有路径**：有图输入写
   `{"type": "input_image", "reference": "not-exported"}`，纯文本图 1 写
   `{"type": "first_frame", "asset_id": "frame_01"}`。测试断言原图字节与文件名都不在产物里。
4. **同一修订号重复执行会就地覆盖**（图片逐字节可复现，`manifest.json` 因 `created_at` 不同而不同）。
   若要"同号不同内容必须拒绝"，需要新增规则，本轮未加。
5. `provider` 选项显式映射到适配器参数（`api_key_secret_ref` → `secret_ref`），
   不做 `**kwargs` 透传，避免未知键静默失效。

### 仍未做 / 待确认（累计）

- **真实模型校准**：所有阈值目前只有合成图证据（见上文 Phase 3 校准段），未用真实模型验证过
  `seam 0.85/0.60`、`1024/768/512`、`1.5×`。等真实 Key 到位后按同一套脚本复测。
- **内置能力**：宿主未注入，`builtin` 一律 `BUILTIN_UNAVAILABLE`；外链真实 API 未联调
  （`openai_image_compat/1.0`、`gemini_image_compat/1.0` 的字段名待真实响应确认）。
- **`job-state` 持久化**：契约已加载校验，但队列/进度落盘与跨进程恢复未实现（当前是单进程
  同步执行 + 产物级续跑）。是否需要真正的 job 文件，请确认优先级。
- **错误码数量**：`ACCEPTANCE_MATRIX.md` 写"36"，注册表实为 37 条（`image_variations` 23 条），
  基线内部不一致，未改。
- **AC-25（方向扩展）**：注册表可扩展已验证，但"新增第 13 个方向"的正/负例尚未补测试。
- **`/image-variations` 宿主调用**：`SKILL.md` 已就位，DSH 侧的触发接线未做。

## 收尾修正 — 错误语义（2026-11-16）

`run_all.py` 跑出 9 套件全绿之后又做了一轮"错误码是否名副其实"的复核，改掉三处会误导用户的实现：

| 场景 | 修正前 | 修正后 |
|---|---|---|
| 方向 key 不在注册表内 | `MULTIPLE_DIRECTIONS`（语义不符） | `INVALID_INPUT` + 提示可用键 |
| `direction` 是列表或 `"light+scene"` | 混在一起报 | `MULTIPLE_DIRECTIONS`（任务层与清单层都能识别组合方向） |
| 用户指定 1K 但单格长边低于硬下限 | 文案说"最高可用档位仅 455px"（不实） | 点名"你指定的 1K …低于硬下限 512px；可用档位 [1K,2K,4K]，或改用更小的网格/比例" |

对应新增/修正测试：`test_multiple_directions_rejected`、`test_unknown_direction_rejected`、
`test_requested_tier_too_small_names_the_user_choice`，以及基线夹具
`invalid-direction-multiple`（`light+scene` → `MULTIPLE_DIRECTIONS`）继续通过。

### 复现全部证据

```bash
.venv/bin/python shared/check_baseline.py                                  # 238/238
.venv/bin/python Image_Variations_Dual_Skill_SPEC_v1.1/tests/validate_example_contracts.py   # 24/24
.venv/bin/python skills/image-variations/tests/run_all.py                   # 9 套件全绿
```

## 发布修复 — `.gitignore` 误吞核心包（2026-11-16）

首次推送到 GitHub 后做**全新克隆验证**，发现测试在干净克隆里全部失败：

```text
ModuleNotFoundError: No module named 'iv_skill'
```

**原因**：`.gitignore` 里为忽略运行产物写的 `iv_*/` 是**非锚定**模式，匹配任意层级的 `iv_*` 目录，
于是 `skills/image-variations/scripts/iv_skill/`（24 个文件的整个核心包）被静默忽略，
前 8 次提交里根本没有它。本地测试一直是绿的，因为文件在磁盘上——只有"从零克隆"才能暴露。

**修复**：改成锚定的 `/iv_*/`（只忽略仓库根目录下的产物目录），补交 24 个文件。

**教训**：`git status` 干净不代表仓库完整；发布前必须用**全新克隆**跑一次全量测试，
而不是只在工作区跑。这条已加进发布检查清单。

## 发布后审计修复 — 三个真缺陷（2026-11-16）

上线后做了一轮"自查 + 探针实测"，抓到三个此前测试没覆盖到的真缺陷，均已在
`fix/artifact-integrity-ssrf` 分支修复并补正/负例。

### 缺陷 1：同 revision 重跑会把上一版残留文件混进交付包（高）

探针：先跑满 4 帧，再用"第 4 帧永远失败"的 Provider 跑同一 revision →
`manifest.json` 的 `images[]` 只有 `[1,2,3]`，但 `delivery-package.json` 把上一版残留的
`images/04.png` 也登记进包，`verify_manifest` 还显示通过。打出来的 ZIP 会带一张
"清单里没有、来源不明"的帧。根因：写盘是"就地覆盖"，只写本次成功的文件，不管孤儿文件。

**修复**：
1. 产物先写进同级隐藏暂存目录，全部自检通过后再原子替换（`artifact._staging_dir` / `_commit`）。
   写到一半失败时旧产物**逐字节不变**，暂存目录不留痕。
2. 新增不变量：目录内文件必须**恰好**等于 `images[] + manifest.json`，否则
   `PACKAGE_MANIFEST_INVALID`（`_assert_files_match_manifest`）。

**回归**：`test_rerun_same_revision_leaves_no_stale_files`、`test_stale_file_is_detected`，
加上原有的同号可复现测试（图片逐字节一致）。

### 缺陷 2：有图输入续跑不校验参考图是否同一张（高）

内存态那条路有 `anchor_revision` 校验，但清单里从未落过这个指纹，`load_sequence_execution`
只能传 `None` → CLI 级续跑形同虚设。

探针：用 `ref_a` 生成到部分失败，续跑时传 `ref_b` → 直接 `completed`，无任何提示，
一组图里混了两种锚点。

**修复**：Sequence 清单的 `consistency_anchor` 增加 `sha256`（**只有哈希，没有图、没有路径**）；
续跑时比对，不一致报 `INVALID_INPUT`；旧产物没有指纹则明确拒绝（不许"猜参考图没变"）。
Grid 不加该字段——它没有续跑路径，且保持与基线示例一致。

**回归**：`test_fingerprint_is_recorded_and_only_a_hash`（断言产物里没有原图字节/文件名）、
`test_resume_same_reference_succeeds`、`test_resume_changed_reference_blocked`、
`test_resume_legacy_manifest_without_fingerprint_blocked`。

### 缺陷 3：HTTP 重定向不过 SSRF 校验（中）

`net.validate_redirect()` 写好了却**没有任何生产代码调用**；`urllib` 默认自动跟随 302，
于是"初始 URL 校验通过"之后仍可被重定向到 `169.254.169.254` 或私网地址。

**修复**：`transport.ValidatingRedirectHandler` 在跟随重定向前调用 `validate_redirect`；
`UrllibTransport` 改为持有带该校验的 opener，并把 `allow_local` 从适配器一路传进去。

**回归**：`test_redirect_handler_blocks_metadata`、`..._blocks_private_even_with_local_flag`、
`..._blocks_plain_http_cross_host`，以及本地回环服务器集成测试
（同源 302 正常跟随、跨到私网 302 被拦、`allow_local=False` 时本地重定向被拦）。

### 顺带纠正的一条错测试

`test_local_endpoint_denied_without_flag` 断言"transport 自己会拦初始 URL"——不对，
初始 URL 校验是适配器的职责（`validate_endpoint`）。已改为验证 `allow_local` 真的接进了
重定向校验，避免留一条假绿。

## 仓库卫生（B 组）— 2026-11-16

| 项 | 处理 |
|---|---|
| Actions 版本落后 | `actions/checkout` v4→v7、`actions/setup-python` v5→v7（同时消掉 Node 20 弃用告警） |
| 依赖/动作漂移 | 新增 `.github/dependabot.yml`：github-actions 与 pip 每周一提 PR，走 CI 与分支保护 |
| Dependabot 告警 | 仓库级开启 alerts 与 security updates（secret scanning / push protection 本就是开） |
| Windows 换行风险 | 新增 `.gitattributes`：`* text=auto eol=lf`，**冻结基线 `-text` 放最后覆盖通配**（顺序反了会失效，已用 `git check-attr` 验证基线为 `text: unset`） |

**复现验证**：

```bash
git check-attr text eol -- Image_Variations_Dual_Skill_SPEC_v1.1/README.md   # text: unset
python shared/check_baseline.py                                              # 238/238
```

### Dependabot 首个 PR 是假升级（2026-11-16）

开启 Dependabot 后它立刻提了 #5：`pillow>=10.0` → `pillow>=13.0.0`。**已关闭不合并**：

- `>=10.0` 本来就允许 13.0.0，pip 在新解释器上会自然装最新版；
- 抬下界反而**砍掉兼容性**：PyPI 元数据显示 `Pillow 13.0.0 requires_python >=3.11`，
  而 `Pillow 11.3.0` 是 `>=3.9`；本项目声明并实测支持 3.9（3.9.6 + Pillow 11.3.0 全绿）；
- 后果在 CI 里直接复现：该 PR 的 `tests-py39` = **FAILURE**。

处理：`.github/dependabot.yml` 的 pip 生态加 `versioning-strategy: increase-if-necessary`
——只在新版本**真的被现有约束排除**时才抬下界。这类"下界通胀"PR 不会再产生。

**教训**：Dependabot 的下界提升对"库"合理，对"声明了宽解释器支持矩阵的应用"是负价值；
开自动升级的同时必须同时设 `versioning-strategy` 与解释器矩阵 CI，否则会静默缩小支持范围。

## 多宿主部署支持（Workbuddy 等）— 2026-11-17

目标：让同一个 Skill 在"技能目录 + shell + Python + 读写文件"的宿主上也能跑（DSH 之外）。

### 先做仿真，再动代码

| 仿真场景 | 结果 |
| --- | --- |
| 全新解释器（macOS 系统 Python **3.9.6**）+ 干净克隆 + 仅 `pip install -r requirements.txt`（装到 Pillow **11.3.0**） | ✅ 跑通，`completed`，seam 0.97188 |
| 最小分发集 `skills/ + shared/ + Image_..._SPEC_v1.1/`（**4.6M**） | ✅ 跑通，seam 0.94641 |
| **只搬技能目录** | ❌ `ModuleNotFoundError: No module named 'shared'`，**裸 traceback** |

结论：跑是能跑的（整仓或最小分发集），但"只给技能目录"会崩，而且崩法违反第 1 条硬规则
（失败必须显式、可判断）。于是补三件事。

### 1) 技能目录自足：`requirements.txt` + `DEPLOY.md`

- `skills/image-variations/requirements.txt`：技能内依赖声明（与仓库根同源）
- `skills/image-variations/DEPLOY.md`：最小分发集、依赖、自检、两种布局、退出码、常见 stage

### 2) 路径可配置：复用已有变量，不新增第三个

发现 `shared/baseline.py` **本来就支持** `IV_REPO_ROOT` 与 `IV_BASELINE_ROOT`。
所以没有引入 `IV_SPEC_ROOT`——**同一件事只留一个变量名**，避免两个名字长期漂移。
`run_task.py` 的 bootstrap 现在按 `IV_REPO_ROOT` → 整仓布局 的顺序找 `shared/baseline.py`。

实测（技能放在非标准深度 `app/skills/...`，依赖放 `vendor/`）：

```
IV_REPO_ROOT 未设  → {"ok": false, "deployment_error": {"stage": "bootstrap", "tried": [...]}} 退出码 4
IV_REPO_ROOT=vendor → status=completed, seam=0.94641
```

### 3) 启动自检：`--self-check`，六项一次说清

`python scripts/run_task.py --self-check --pretty` 检查：Python 版本 / Pillow / 仓库根 /
基线 238 文件哈希 / 技能配置与 12 方向 / workspace 可写。

**部署错误不占业务码**（用户确认的取舍）：业务码只有"能力不足 / 预算不足 / 校验不通过"三类，
"装错了"不属于它们。混进业务码会让宿主把部署问题误判成模型问题、触发无意义重试。因此：

| 退出码 | 含义 | 输出形状 |
| --- | --- | --- |
| 0 / 2 / 3 | 业务面 | `{"ok": false, "error": {"code": <注册表码>}}` |
| **4** | **部署不完整** | `{"ok": false, "deployment_error": {"stage": ...}}` |

九项 stage：`python` / `dependencies` / `bootstrap` / `import` / `baseline` / `skill_config` /
`workspace`。**任何失败都不打印堆栈**（此前只搬技能目录会甩 traceback）。

### 4) 第 10 个测试套件 + CI dogfood

- `tests/test_deploy.py`：**13 项**，覆盖自检正常/异常、分离部署、只搬技能目录、无 `--input`、
  依赖声明与文档在位、技能目录内不得带 `.venv`
- `run_all.py` 套件数 9 → 10（CI 与 PR 模板的计数同步）
- CI 两个 job 各加一步 `--self-check`（同时覆盖 3.12 与 3.9 两个解释器）
- CI 冒烟里新增断言：模拟只搬技能目录时必须 **退出码 4 + 有 `deployment_error` + 无业务 `error`
  + 无 traceback** —— 把这次的教训固化成回归

### 未做（仍待外部输入）

- 真实模型阈值校准（尤其近重复 0.9）
- 两个兼容 profile 的字段名与真实响应比对
- 宿主若要求非 `SKILL.md` 的清单名，需要一层极薄映射（`SKILL.md` 不含任何宿主细节）

## 青水（qingshui）接入 + 接缝判据 A 方案 + 12 方向跑测台 — 2026-11-17

### 1. 一个必须记录在案的规范矛盾（已获用户裁决）

实测一张**质量良好**的 2×2 真实整板，接缝评分却是 `0.000`，被 `GRID_VALIDATION_FAILED` 阻断：

| 指标 | 实测值 |
| --- | --- |
| `boundary_means` | `[48.6, 75.1]` |
| `interior_means` | `[2.9 ~ 14.5]` |
| `seam_ratio` | `8.57` |
| `seam_score` | `1 − (8.57−1)/2 = 0.000` |
| `border_lines` | `[]`（无框线，"无框"这项通过） |

矛盾双方都在冻结包内：
- `references/grid-prompt.md` §3 模板正文要求「**每一格都是完整构图**」「主体不得跨格」「第 k 格：<不同变化点>」→ 产物是**接触表**；
- `specs/01-…-v1.2.md` 第 267–272 行 D-24 要求 `boundary_diff ≈ interior_diff`（`score ≥0.85` 需 `ratio ≤ 1.3`）→ 要求**跨格连续**。

接触表在数学上不可能满足 D-24；且 `specs/01-…-v1.2.md` 第 597 行本就标注阈值「**待实测校准**」。这是第一个真实校准数据点。

**用户裁决（2026-11-17）：A 方案** —— 连续性评分降为披露警告，伪影检测保持硬门禁。

### 2. 实现：`seam.grade_mode`

- `config/iv.config.json` 新增 `seam.grade_mode`，默认 `artifact_only`（可随时切回 `continuity_gate`）。
- `artifact_only` 语义：结构判 `pass`（等大格子、无框线、无留白带仍由原有规则硬拦），但**强制**带一条 `GRID_SEAM_RISK` 披露警告，措辞含「本产物不是无缝整板，不得宣称无缝」；`validation.seam_score` / `seam_check_method` / `seam_ratio` 照写（满足 AC-28），并新增 `validation.seam_gate_mode` 记录当时用的是哪种判据。
- `continuity_gate`：规范原文的阈值分级（`<0.60` 阻断），保留可用。
- 测试：`test_grid.py` 两种模式都覆盖；硬接缝触发重试的用例显式切到 `continuity_gate`；新增 `test_contact_sheet_*` 三例（披露、框线仍拦、留白带仍拦）。

### 3. 青水适配器（`provider/qingshui_compat.py`）

提交→轮询→下载三段式异步；参考图**上传换 URL 并在 `finally` 回收**（实测内联 data URL 会被上游拒绝，实测上传 URL 可用）；错误归一全部走 `IVError` + 既有错误码表：

| 上游现实 | 归一结果 |
| --- | --- |
| `AIMS … [502] UPSTREAM_PROVIDER_ERROR` | `PROVIDER_TIMEOUT`（可重试） |
| `image size must be at least 3686400 pixels` | `UNSUPPORTED_RESOLUTION`（**不重试**） |
| 内容安全/审核拒绝 | `MODEL_REFUSAL`（不重试） |
| 任务 `failed` 且无匹配特征 | `IMAGE_VALIDATION_FAILED`（可重试） |
| 轮询超时 / 连续错误达上限 / 下载失败 | `PROVIDER_TIMEOUT` |

重试收口：执行器传 `max_extra_retries=0`，适配器单次尝试，避免算力翻倍；新增不变量用例「重试复用已上传 URL，不重复上传」。

### 4. 能力档案（按 `provider-compat.md` §2 + 第 104 行「必须实测回填」）

`config/provider-profiles.json` 三个档案，全部带 `measured_at` 与 `evidence`：

| 档案 | 模型 | 能力声明要点 |
| --- | --- | --- |
| `qingshui_image2.5` | `gpt-image-2.5-flare` | 纯文本可用、1:1 恒 1024²、**参考图/编辑不可用**（3/3 次 502） |
| `qingshui_gemini3.1` | `gemini-3.1-flash-image-preview` | 纯文本 + **参考图可用**、3:4 输出 896×1200 |
| `qingshui_doubao45` | `Doubao-Seedream-4.5` | **不可用**（平台侧尺寸映射恒定 1.05M < AIMS 下限 3.69M），不声明任何档位，直接拦 |

### 5. 12 方向跑测台（`scripts/run_matrix.py`）

用户诉求：结果全存（供 pages）、提示词可追溯、默认 image2.5。实现：

- 产物：`campaign.json`、`index.json`（pages 直接用）、`ledger.jsonl`（一行一 cell，含 `images[]`）、每 cell 的 `task.json` / `prompts/*.md` / `result.json` + 技能正式产物（`manifest.json`、`board.png`、`images/NN.png`、ZIP）。
- 台账字段以 `manifest.json` 为唯一权威来源（`model_id`、尺寸、`seam_*`、`tile_edge_*`），并对老产物用能力档案回填 `model_id` 且标 `model_id_source`。
- 提示词原文经 `capture_prompt=1` 回传给调用方后落 `prompts/*.md`，**不进 `manifest.json` / ZIP**。
- `--dry-run` 预估算力、`--max-calls` 硬上限、默认跳过已成功 cell（保护算力）、`--reindex` 零成本重建台账。

### 6. 真实端到端证据（第 1 轮，`light` 方向 / Grid 2×2 / image2.5）

- `calls_used=2`、`attempts=2`、耗时 212.35s、`status=completed`；
- 产物：`board.png` 1024×1024 → 4 张 `images/NN.png` 各 512×512；
- `warnings`：`TILE_RESOLUTION_RISK`（512px 单格低于推荐 1024，已记降档）+ `GRID_SEAM_RISK`（接触表披露）；
- `validation.seam_gate_mode=artifact_only`、`tile_edge_ok=false`、`grid_attempts=2`；
- **真实 `manifest.json` 与 `delivery-package.json` 均通过冻结契约校验（0 问题）**；
- ZIP 3.37 MB。

### 7. 本轮未做 / 已知缺口

- 3×3、4×4 Grid 未实测（1024 整板 → 单格 341/256，低于硬下限 512，按策略明确不可做）。
- 失败尝试的**逐次**失败原因未落盘（manifest 只记 `grid_attempts`），排查时缺一层证据。
- `openai_image_compat` / `gemini_image_compat` 字段名仍是未验证假设。
- 成本口径：`cost_estimate` 走配置里的 USD 单价，而青水实际计费是「算力/张」，两者未打通。

## image_only 真实跑通 + 参考图收回失败不再静默 — 2026-11-17

### 1. `image_only`（带参考图）真实产物

- 任务：`light` 方向、Grid 2×2、`input_type=image_only`、能力档案 `qingshui_gemini3.1`、参考图 = 用户原图（1663×2048，`_reference/reference.jpg` 已入档）。
- 结果：`status=completed`、`calls_used=1`（**一次成板，未重试**）、`elapsed_sec=151.77`、`grid_attempts=1`。
- 产物：`board.png` 1024×1024 → `images/01..04.png` 各 512×512；`model_id=gemini-3.1-flash-image-preview`（`model_id_source=manifest`）。
- `warnings`：`TILE_RESOLUTION_RISK`×2（512px 单格，已记降档）+ `GRID_SEAM_RISK`（接触表披露）。
- **真实 `manifest.json` 与 `delivery-package.json` 再次通过冻结契约校验（0 问题）**。
- 观察（如实记录）：四格彼此一致、场景/姿态/服装接近原图，但**细节有漂移**——鞋从黑色穆勒高跟鞋变成露趾高跟凉鞋、裙摆变长且有拖尾。属于提示词/模型保真度问题，不是流水线缺陷。

### 2. 修掉两个"image_only 从没真跑过"才会暴露的 bug

| bug | 影响 | 修复 |
| --- | --- | --- |
| 能力档案名写成 `qingshui_gemini31`（实为 `qingshui_gemini3.1`） | image_only 直接 `MODEL_CAPABILITY_UNSUPPORTED` | 跑测台常量改正 |
| 参考图键名写成 `reference_image`（契约要求 `reference_path` 且**相对 workspace**） | image_only 直接 `INVALID_INPUT` | 跑测台改为把参考图复制进存档目录并用相对路径 |

### 3. 参考图收回失败不再静默（授权前提的兜底）

原实现 `except Exception: pass` —— 删失败完全不可见，而"用完即删"是用户授权上传的前提，属**静默降级**。

- 现在：DELETE 的**状态码也检查**（此前连状态码都没看），异常与非 2xx 都记成警告 `REFERENCE_CLEANUP_FAILED`（`severity=warning`），文案含 asset 前缀、"URL 可能仍公开可达，请到青水后台手动删除"。
- 生成结果不丢：收回失败不抛错，图照常交付；警告经 `take_warnings()` 由 `run_task` 并入产物 `warnings[]`；生成失败时并入 `IVError.detail["warnings"]`。
- 404 视为"本来就不存在"，不算失败。
- **注册表缺口**：冻结的 `error-registry.json` 里 image_variations 的 23 个码**没有覆盖"资产收回失败"**，规范包只读、不得擅自新增，故该码仅作警告并在文案里标注"该码不在冻结错误码表内，待在规范中补充"。需要用户在下一版规范里补一个正式码。
- 测试：新增 7 例（成功无警告、500 有警告但交付保留、请求异常、取走即清、404 不算失败、密钥不外泄、drain 兼容无此方法的适配器）。

### 4. 待规范补充清单（用户裁决：先记录，需补正式码）

新增 `docs/SPEC_BACKLOG.md`，集中记录 7 条必须由冻结包下一版处理的问题（每条含现象、实测证据、
当前实现如何不静默降级、建议动作）：

| 编号 | 问题 | 用户裁决 |
| --- | --- | --- |
| R-1 | 缺错误码：参考图临时分享收回失败 | 需补正式码，先记录 |
| R-2 | D-24 接缝判据与接触表模板矛盾 | A 方案（连续降为警告，伪影仍硬拦） |
| R-3 | 错误码总数 36 vs 37 | 待核 |
| R-4 | 单格下限"长边 vs 短边"口径不一 | 待规范明确 |
| R-5 | `tier_to_board_side_px` 标量无法表达按比例的真实边长 | 待规范扩展 |
| R-6 | 失败尝试的逐次原因未进产物 | 待规范扩展 |
| R-7 | 成本口径 USD vs 算力/张未打通 | 待规范扩展 |

### 5. 用户裁定的不做项（2026-11-17）

- 锚点文本**不加强**：实测带参考图时出现细节漂移（鞋型由穆勒高跟变露趾凉鞋、裙摆变长），
  用户裁定"就这样"，因此不改 `anchor.py` 的锚点描述文案。
- 12 方向批量测试**暂停**，不再投入算力；跑测台与能力档案已就绪，随时可 `--dry-run` 复核计划后再开跑。

## 模型探测：gpt-image-2.5-sunburst + 探针自身的两个 bug — 2026-11-17

### 1. 探测结果（走生产适配器，各 1 算力）

| 模式 | 结果 | 证据 |
| --- | --- | --- |
| `image_only`（带参考图） | ❌ 失败 | `AIMS 图片编辑失败 [503] UPSTREAM_CAPACITY_UNAVAILABLE`，5.9s，task_id `2df38e9d-58e2-4999-b5f9-7803ddc6be63`，扣 1 算力 |
| `text_only` | ✅ 成功 | 1024×1024、1.67MB、**136.8s**（同 prompt 下 flare 约 20~90s，sunburst 明显更慢） |

`gpt-image-2.5-flare` 5 次（502×4、503×1）+ `sunburst` 1 次（503）= **6 次全失败、6 算力零产出**，
故两个档案都声明 `reference_image_input=false` / `image_edit=false`；
新增能力档案 `qingshui_image2.5_sunburst`（含实测依据）。
`error_detail.upstream_status=503` 说明刚修的"状态码取自响应原文"在真实故障上生效了。

### 2. 探针脚本自身的两个 bug（已修，如实记录）

| bug | 后果 | 修复 |
| --- | --- | --- |
| 给 `generate()` 传了 `sleep=lambda _s: None` | **3 秒轮询间隔变成死循环**，一次成功任务打出 **482 次轮询**（约 3.5 次/秒） | 去掉该参数；`single_attempt` 本就保证只提交一次，等待交给适配器按 `poll_interval_sec` |
| `[SUBJECT_ANCHOR]` 槽位误填"锚点规则文本"而非主体描述 | 模型无主体可依，自己编了只小狗，该图不能用于主体保真度评估 | 改为真实主体描述（与正式任务一致） |

生产路径（`run_task` → 执行器）本就不传 `sleep`，因此**不受第一个 bug 影响**；
该 bug 只存在于我临时写的探针里。整板结构质量本身很好（4 格同一主体、4 种光照、无框可切）。
