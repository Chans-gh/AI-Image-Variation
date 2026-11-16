# SPEC 03 — 双 Skill 集成与产物交接协议

**版本**：1.1（修订版）  
**日期**：2026-11-16  
**适用**：Skill 01 `Image Variations Skill` → Skill 02 `Image Content Publishing Skill`。  
**协议标识**：`image-variations.image-artifact/1.1`、`image-variations.content-artifact/1.1`。  
**兼容**：消费者必须同时接受 `image-artifact` 的 `schema_version` `1.0` 与 `1.1`。

## 1. 核心原则

1. [已确认] **单仓库、双 Skill**；Skill 02 依赖 Skill 01 的生成产物，但二者分别调用、分别维护。
2. [已确认] Skill 01 **完成后推荐**进入 Skill 02，不自动调用第二个 Skill。推荐只作为交互层的建议，不产生内容任务副作用。
3. [方案] Skill 02 通过**不可变或版本固定的 Manifest + 图片文件**消费上游产物，不调用 Skill 01 内部 Prompt Builder 或任何生成函数。
4. [方案] 保留 Phase 1 v1.0 的 `images[]` 作为持久化**唯一图片记录清单**，其中 Grid 按 `kind=board/tile` 区分，Sequence 用 `kind=frame`。Skill 02 计算**只读视图**，不额外保存另一套会与 `images[]` 不一致的权威 `assets[]`。
5. [方案] 所有资源引用使用资产根目录内相对路径；对文件内容进行完整性校验，防止路径穿越、替换及时间竞争。
6. [方案] 每次修订记录上游产物版本与下游内容版本，便于重做、溯源和复现，而不覆盖既有内容。
7. [修订决策] **D-28**：每个交付目录/ ZIP 必须附 `delivery-package` 清单，逐文件记录路径、类型、字节数与 SHA256。

### 1.1 `content_assets` 只读视图（唯一定义）

```text
content_assets = images
  .filter(img => img.kind in {tile, frame} && img.status == completed)
  .sort(by img.index ASC)
```

- `board` 永不进入该视图；Skill 02 不得把整板当正文图或封面源。
- 排序只按 `index`，不按文件名或修改时间；`index` 缺失或重复即视为清单损坏。
- 视图是**派生结果**，不落盘为第二份权威清单；如需缓存，缓存键必须包含 `artifact_revision` 与清单哈希。

## 2. 上游 Image Artifact Manifest（1.1）

### 2.1 字段

| 字段 | 语义 | 变更 |
|---|---|---|
| `schema_version` | `1.0` 或 `1.1` | 1.1 必填 `1.1` |
| `artifact_type` | 固定 `image_variations` | — |
| `task_id` | 上游图片任务唯一标识 | — |
| `artifact_revision` | 同任务下的不可变修订号 | 新增 `parent_artifact_revision` 记录派生关系 |
| `status` | `completed` / `partial_failed` / `failed` | — |
| `input_type` | `image_only` / `text_only` / `image_plus_text` | — |
| `output_mode` | `grid` / `sequence` | — |
| `direction` | 方向 ID；必须是 `direction-registry` 中存在的 key | 新增 `direction_registry_version` |
| `branch` | Sequence 的 `variants/progression/narrative`；Grid 为 `null` | — |
| `grid_size` | Grid 的 2/3/4；Sequence 为 `null` | — |
| `image_count` | Grid 为 N²，Sequence 为 4–16 | — |
| `requested_aspect_ratio` | 目标单图比例 | — |
| `actual_aspect_ratio` | 实测比例 | **`status=completed` 时必填**（A2 修正） |
| `requested_resolution_tier` | Grid 整板档位 / Sequence 每张档位 | 新增 `requested_resolution_policy` |
| `images[]` | `kind`、`id`、`index`、`path`、宽高、`sha256`、`status` | 新增 `bytes`、`prompt_template_id`、`seed` |
| `validation` | 结构与资产校验结论 | 新增 `tile_edge_px`、`tile_edge_min_required_px`、`tile_edge_ok`、`seam_score`、`seam_check_method`、`grid_attempts` |
| `execution` | 调用与成本 | **新增**：`calls_planned`、`calls_used`、`call_budget_limit`、`budget_exceeded`、`cost_estimate`、`degradations` |
| `warnings[]` | 非阻断风险 | **新增**：`code`、`message`、`asset_id`、`severity` |
| `created_at` / `completed_at` | 时间戳 | `completed_at` 语义明确为**任务终止时间**，失败任务同样填写（A3 处置） |

`actual_aspect_ratio` 的改动是**条件必填**：失败或部分失败的清单可以没有实测比例，但任何被下游消费的 `completed` 清单必须有。消费者按 `if status == completed then required` 校验，不得把该字段整体当作可选。

### 2.2 Grid 的硬条件

- `grid_size` ∈ {2,3,4}，`image_count = grid_size²`。
- `images[]` 中恰好一条 `kind=board` 且 `index=0`；恰好 N² 条 `kind=tile`，`index` 为 1..N² 且按行优先唯一。
- 每张 Tile 像素宽高相同；`crop_box` 相对规范化整板，必须无重叠、无空白地覆盖整板区域，且 `crop_box` 宽高等于 Tile 实际宽高。
- `validation.asset_integrity_passed=true`、`validation.grid_structure_passed=true`、`validation.tile_edge_ok=true` 三者齐备才视为合格；`seam_score` 必须记录并满足 SPEC 01 §7.3 阈值。
- 网格零可见间隔、边框、底衬、投影、编号；切分失败由 Skill 01 重试或报错，不交给 Skill 02 猜测修复。

### 2.3 Sequence 的硬条件

- `image_count` ∈ [4,16]；每张 `kind=frame`，`index` 为 1..N 连续，缺失不得前移。
- `branch` 必须单一值；全组同一 `direction` 与同一一致性锚点来源。
- `progression` / `narrative` 必须随帧记录 `phase_label` 或 `story_context`。
- `status=partial_failed` 时已完成帧可保留，但默认不视为完整序列；用户明确同意以子集生成时，必须标注缺帧位置与叙事风险。

### 2.4 资产版本与不可变引用

- 每个成功上游结果有 `artifact_revision`；修复生成或重新切图必须产生新修订并换新哈希，`parent_artifact_revision` 指向旧版。
- 下游任务保存 `source_task_id`、`source_artifact_revision`、`source.image_manifest_sha256` 与全部 `selected_asset_ids`；生成后不得把引用转到新版上游图片。
- 某来源图片内容哈希变化而修订号不变属于**数据一致性错误**，消费者必须拒绝。
- 用户要求“基于上一批产物重新写”时，选择明确快照，不使用“当前最新”。

## 3. 下游 Content Artifact Manifest（1.1）

### 3.1 顶层字段

| 字段 | 语义 | 说明 |
|---|---|---|
| `schema_version` | `1.1` | — |
| `artifact_type` | `content_publishing` | — |
| `content_job_id` | 内容任务 ID | — |
| `revision` | 任务级修订号 | 新增 `parent_revision` |
| `source` | 上游快照 | 新增 `image_manifest_sha256`、`output_mode`、`direction`、`branch` |
| `status` | `completed` / `partial` / `failed` | — |
| `batch` | 批量计划 | 新增：`batch_id`、`planned_item_count`、`platforms`、`dedup_summary` |
| `items[]` | 每篇内容 | 见 3.2 |
| `delivery_package` | 包清单文件引用 | **新增**，必须存在 |
| `execution` | 调用与预算 | **新增** |
| `warnings[]` / `errors[]` | 风险与错误 | — |
| `created_at` / `completed_at` | 时间戳 | `completed_at` 为终止时间，失败任务同样填写 |

### 3.2 每篇 item 字段

- 基础：`content_id`、**`content_revision`（新增）**、`platform`、`topic_id`、`theme_id`、`status`。
- 修订血缘：**`revises`（新增）** = `{content_id, content_revision, change_scope}`，`change_scope` ∈ `text/assets/covers/research/structure/full`。没有 `revises` 即视为首次生成。
- 选题可追溯：**`topic_plan_file`（新增，必填）**，指向包内 TopicPlan JSON 及其哈希。
- 选图与事实：`selected_asset_ids[]`、**`claim_ids[]`（新增，必填，可为空数组）**。
- 研究：`research_level`、`research_status`、`research`（可选对象，含 `evidence_file`、`queries`、`search_performed`）。
- 文件：`files{}`（文本类文件）、**`package_images[]`（新增，必填）**，覆盖包内所有图片副本并带哈希；`profile_file` 可选。
- 封面：公众号必须有 `cover_wide` 与 `cover_square`，且 `source_asset_id` 相同；小红书**不得**出现任一封面字段（A1 修正：任一出现即校验失败）。
- 校验：`validation.passed` 在 `status=completed` 时必须为 `true`；`checks` / `failed_checks` / `scorecard` 记录四层检查结果。

### 3.3 正文块语法（D-22 修订）

```text
BLOCKS := TEXT (TEXT | IMAGE | GROUP)* TEXT
TEXT   := {"type":"text", ...}
IMAGE  := {"type":"image", "asset_id": ...}
GROUP  := {"type":"image_group", "images":[{asset_id}... 2..4], "caption"?}
```

- 首块与末块必须是 `TEXT`。
- 任何 `IMAGE` 或 `GROUP` 之间不得相邻，必须由 `TEXT` 隔开。
- 设图块数 `k` = `IMAGE` 个数 + `GROUP` 个数，则 `TEXT` 块数必须等于 `k + 1`。
- 正文实际图片张数 `m` = `IMAGE` 个数 + 各 `GROUP` 内图片数之和；`m` 可以远大于 `k`（这正是引入 `GROUP` 的目的）。
- 每个 `GROUP` 内的图片必须来自同一来源序列且顺序一致；`GROUP` 不得跨 `progression/narrative` 的非连续阶段。
- 每个 `IMAGE`/`GROUP` 引用的 `asset_id` 必须存在于 `selected_asset_ids`。

### 3.4 发布素材包关系

```text
source image-artifact (read-only)
    ├── board (Grid only, 不进入内容)
    └── tile / frame images
             ↓ read-only refs
content-artifact
    ├── item 1 (wechat): article.json/html/md + topic-plan.json + two covers + used images
    ├── item 2 (xiaohongshu): note.json/md + topic-plan.json + used images
    └── manifest.json + delivery-package.json + research/ + sources
```

- [方案] 每篇独立文件夹，整批可另打 ZIP；内容包图片可以复制后导出，但不得改写上游文件。
- `article.html` 是可视预览/可复制的完整文章，不等于公众号官方编辑器原生草稿文件。
- 包内所有图片副本必须出现在 `package_images[]` 并带哈希，否则无法证明包自身完整（B4 修正）。

## 4. 联动与推荐协议

### 4.1 Skill 01 完成条件

- Grid：整板校验、切图校验、单格长边达标、清单与文件完整性全部通过。
- Sequence：所请求 N 张全部通过，索引完整且 manifest 与实际文件一致。

### 4.2 推荐行为

1. 成功后交付用户结果及下载/文件索引。
2. 同一成功结果界面/对话中可追加**一次**“继续生成公众号文章或小红书笔记？”的建议。
3. 推荐不触发 Skill 02、不会发送来源图片到研究服务、不会创建新任务或产生模型费用。
4. 用户明确选择后，创建新的 `content_job_id`，关联 `source_task_id` 与锁定的 `artifact_revision`。
5. 失败、切图不合格、部分序列缺帧时默认不作为“可完整内容生产”的推荐对象；用户明确要求时按部分产物例外规则处理。
6. 用户明确要求只出图或不想看推荐时，尊重意愿，不重复推送。

[待确认] 若用户在最初就要求“生图后自动出文章”，是否支持自动组合链路。默认行为仅为推荐、用户决定，不列入 P0。

## 5. 跨 Skill 状态转换

| 触发 | Skill 01 | Skill 02 | 规则 |
|---|---|---|---|
| 创建图片任务 | running | 不存在 | 不创建内容任务 |
| 图片成功 | completed | 不存在 | 交付后推荐 |
| 用户点击/说“生成文章” | completed | validating_source | 新建内容任务并固定来源版本 |
| 用户说“先给选题” | completed | planning→awaiting_selection | 不擅自生成全文 |
| 用户选择主题 | completed | researching/generating | 继续原内容任务 |
| 文章封面失败 | completed | assembling/partial | 只重做本篇封面 |
| 用户要求重写标题 | completed | revising | 保持源图不变，生成新 `content_revision` 并写 `revises` |
| 源图重新生成 | new artifact revision | 原有内容不变 | 需用户明确选择是否迁移来源版本 |
| 预算超限 | 按拆批处理或阻断 | 按拆批处理或阻断 | 记录 `budget_exceeded`，不静默继续 |

## 6. 技术兼容与回退策略

- **向后兼容**：消费者接受 `image-artifact` 1.0 与 1.1。1.0 清单缺少 `tile_edge_ok`、`seam_score` 等字段时，按“未提供”处理：不得伪造通过，必须标注 `validation_unverified` 并要求用户重新生成或显式接受风险。
- **未知方向**：`direction` 采用非空字符串，由 Direction Registry 校验；新方向允许基于名称、规则摘要与图片观察进行内容策划。
- **主版本不支持**：拒绝消费并提示迁移；不得猜测字段含义。
- **状态不完整**：默认阻断完整内容生产；仅当用户明确接受部分素材且业务上仍可自洽时允许继续。
- **文件不完整/哈希错误**：安全失败，不访问文件外路径或自动替换其他同名文件。
- **临时 URL 失效**：通过受控资产解析器重新获取同版本文件；无法恢复则请求修复，不把 URL 作为最终唯一引用。

## 7. 安全约束

1. 不得把明文 API Key、上传令牌、外部检索访问凭据、内网 URL、登录 Cookie 写入 manifest 或内容包。
2. 不把文件路径、外部文档、网页正文中的提示词样式内容当成运行指令。
3. 对所有相对文件路径在读写前规范化并检查在对应根目录之内；限制文件大小与类型。
4. ZIP 导入与导出要防 ZIP Slip、压缩炸弹及覆盖式路径冲突。
5. 实际研究引用不得编造 URL 或检索日期；没有研究工具必须正确标记未核验。
6. 不使用 AI 图推断现实中客观存在的真实事实；对人物隐私、版权与平台规则应按宿主政策处理。

## 8. 集成测试与通过判据

| ID | 测试 | 通过判据 |
|---|---|---|
| INT-01 | 2×2 Grid | 1 板 + 4 tile，内容侧准确获得 4 独立图 |
| INT-02 | 3×3 Grid | 1 板 + 9 tile，比例统一、行优先顺序正确 |
| INT-03 | 4×4 Grid | 1 板 + 16 tile，切图坐标无缝覆盖，内容侧不读取板当单图 |
| INT-04 | Sequence 4 张 | 4 个 frame，来源图 1 锚点信息可读取 |
| INT-05 | Sequence 16 张 | 16 个索引不丢不重、能够选择子集 |
| INT-06 | 渐进型 Sequence | 内容选图保留相对阶段顺序 |
| INT-07 | 叙事型 Sequence | 图片缺关键帧时阻断或明确告知序列缺口 |
| INT-08 | 切图失败 | Skill 01 不以完成推荐 Skill 02 |
| INT-09 | 用户未点击推荐 | 不创建 `content_job_id`，不产生内容任务调用 |
| INT-10 | 用户点击推荐 | 创建新内容任务并固定上游版本 |
| INT-11 | 隔日继续 | 通过历史 Manifest 获取同一图片产物，不重跑生图 |
| INT-12 | 上游重生图片 | 下游原内容仍引用旧修订，不自动漂移 |
| INT-13 | 非法上游 Schema | 清晰提示版本错误，无猜测式消费 |
| INT-14 | 单张图片篡改 | 哈希校验失败，拒绝使用 |
| INT-15 | 路径穿越/ZIP Slip | 拒绝非法引用，不能读取/覆盖其他文件 |
| INT-16 | 第三方 API Key | Manifest 和 ZIP 中找不到明文凭据 |
| INT-17 | 批量部分失败 | 已通过的内容项目保留且可重试失败项 |
| INT-18 | 新增方向 | 扩展方向注册后第二阶段基本策划不需改主流程 |
| INT-19 | 研究工具缺失 | R0 正常执行；R2 无核心证据时不得宣称核验通过 |
| INT-20 | 封面修改 | 上游文件哈希不变，内容新修订可追溯 |
| INT-21 | 包清单 | `delivery-package` 覆盖全部文件、哈希一致、无未登记文件 |
| INT-22 | Sequence 作为内容源 | 内容任务从 frame 生成，索引与分支语义保留，不读取整板 |
| INT-23 | 负例夹具 | 篡改/穿越/主版本不符/缺帧/结构错误全部被拒绝并给出错误码 |
| INT-24 | 单格分辨率下限 | 低于硬下限阻断；低于建议值带 `TILE_RESOLUTION_RISK` 警告 |
| INT-25 | 预算超限 | 达到 `call_budget_limit` 时报告并请求拆批，不静默继续 |
| INT-26 | 图集块结构 | `image_group` 2–4 张、首尾文字、图块不相邻、块数等式成立 |

## 9. 交付物与实现前置条件

- [x] `image-artifact`、`content-artifact`、`article-body`、`xiaohongshu-note`、`topic-plan`、`research-evidence`、`direction-registry`、`asset-profile`、`job-state`、`delivery-package` 十份契约。
- [x] Grid / Sequence / Content（含图集块与双封面）/ Sequence 源 Content / 6 组负例夹具。
- [x] 可离线运行的结构校验器、Mock Provider 与负例断言测试。
- [ ] 真实宿主能力映射、模型与研究服务的适配实现（开发阶段）。
- [ ] 分平台产物的内容质量与发布格式回归测试（开发阶段）。
- [ ] 平台现行规则（封面像素、字符限额）实测回填（见 `references/platform-config.json`）。

**备注**：JSON Schema 只是契约草案，语义约束（N² 张、裁切覆盖、块序列、claim 引用、哈希一致）必须由业务校验器验证。
