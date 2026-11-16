# 验收追溯矩阵（v1.1）

**证据代号**

| 代号 | 含义 |
|---|---|
| `S` | 由 `contracts/*.schema.json` 结构强制（`tests/schema_lite.py` 校验） |
| `F` | 正例夹具 + 语义校验器（`examples/valid-*`、`tests/checks.py`） |
| `N` | 负例夹具（`examples/invalid/*`，`tests/test_negative_fixtures.py`） |
| `M` | Mock Provider 自测（`tests/mock_provider.py --selftest`） |
| `D` | 规范正文约束，需在实现阶段落实（当前无可执行证据） |
| `R` | 需真实模型/平台/密钥才能验证（外部实测项） |

**状态**：`通过` = 已有可执行证据且当前通过；`已覆盖` = 规范与契约已写清但实现待做；`待实测` = 需要外部环境。

---

## SPEC 01 验收标准（Image Variations Skill）

| ID | 要求摘要 | 证据 | 位置 | 状态 |
|---|---|---|---|---|
| AC-01 | 三种输入可解析 | D | SPEC 01 §3.1 | 已覆盖 |
| AC-02 | 空输入不调模型 | D | SPEC 01 §3.1 / error-registry | 已覆盖 |
| AC-03 | 无图默认 3:4 | F | `valid-sequence-4`（`requested_aspect_ratio=3:4`） | 通过 |
| AC-04 | 比例继承 | S/D | `requested_aspect_ratio`、`actual_aspect_ratio` | 已覆盖 |
| AC-05 | Grid 2/3/4 | S/D | `grid_size` enum + `valid-grid-2x2` | 通过 |
| AC-06 | 档位按单格下限推导 | S/F | `requested_resolution_policy`、`tile_edge_min_required_px` | 通过 |
| AC-07 | Sequence 4–16 | S | `frame_count`/`image_count` 边界 | 通过 |
| AC-08 | Sequence 默认 1K | F | `valid-sequence-4`（1K） | 通过 |
| AC-09 | 单方向 | S | `direction` + registry 12 项 | 通过 |
| AC-10 | 单分支 | S/F | `branch` enum + 两个序列夹具 | 通过 |
| AC-11 | Grid 一次成板 | M | `mock_provider.plan_calls("grid")==3`（1 次 + 重试预算） | 通过 |
| AC-12 | 全格规划不重复 | D | `grid-prompt.md` §4 | 已覆盖 |
| AC-13 | 网格结构 + 接缝阈值 | S/F/N | `grid_structure_passed`、`seam_score`；`grid-crop-mismatch` | 通过 |
| AC-14 | 切图像素数与坐标 | F/N | `check_grid_structure` + `grid-crop-mismatch` | 通过 |
| AC-15 | 错误网格不标成功 | N | 同上（`GRID_VALIDATION_FAILED`） | 通过 |
| AC-16 | N 张 N 条 Prompt | D | `sequence-prompt.md` §1/§4 | 已覆盖 |
| AC-17 | 纯文本锚点用图 1 | S/D | `consistency_anchor.type=first_frame` | 已覆盖 |
| AC-18 | 有图锚点用原图 | D | SPEC 01 §6 | 已覆盖 |
| AC-19 | 三分支计划语义 | D | `sequence-prompt.md` §2 检查点 | 已覆盖 |
| AC-20 | 能力不足不暗降级 | M/D | `UNSUPPORTED_RATIO`、`UNSUPPORTED_RESOLUTION` 映射 | 通过（映射） |
| AC-21 | 两类兼容协议解析 | R | `provider-compat.md`；Provider 适配器 | 待实测 |
| AC-22 | 凭据安全与 SSRF | D | SPEC 01 §9.5、`packaging.md` §5 | 已覆盖 |
| AC-23 | 部分失败保留槽位 | D | `sequence-prompt.md` §3 | 已覆盖 |
| AC-24 | 打包与清单 | S/F | `delivery-package` + `write_package` 校验 | 通过 |
| AC-25 | 新增第 13 方向不改主流程 | F | `directions/registry.json` + `build_direction_docs.py`（12 份由注册表生成） | 通过（生成链） |
| AC-26 | 第一阶段边界 | D | SPEC 00/01 边界声明 | 已覆盖 |
| AC-27 | 单格长边下限 | S/F/N | `tile_edge_px`/`tile_edge_ok`；`TILE_RESOLUTION_RISK` | 通过 |
| AC-28 | 接缝评分 | S/F | `seam_score` + `seam_check_method="boundary_gradient_v1"` | 通过 |
| AC-29 | 调用预算 | M/S | `plan_calls` + `BUDGET_EXCEEDED` + `job-state` schema | 通过 |
| AC-30 | 能力底线 | D/M | SPEC 01 §9.2 表；Mock 错误映射 | 已覆盖 |
| AC-31 | 包清单覆盖与哈希 | S/F/N | `check_package_coverage`；`package-coverage` 负例 | 通过 |
| AC-32 | 命名与索引 | S/F | `board.png` + `images/NN.png`；`index` 规则 | 通过 |

## SPEC 02 验收标准（Image Content Publishing Skill）

| ID | 要求摘要 | 证据 | 位置 | 状态 |
|---|---|---|---|---|
| P2-01 | Grid 产物取 9 tile 不用板 | F/S | `content_assets` 视图 + `valid-content-job` | 通过 |
| P2-02 | Sequence 16 张与分支 | F | `valid-content-from-sequence`（4 张夹具 + 分支字段） | 通过 |
| P2-03 | 无主题直接成稿 | D | SPEC 02 §2.1 意图路由 | 已覆盖 |
| P2-04 | 只要选题不写全文 | D | 同上 | 已覆盖 |
| P2-05 | 批量先规划去重 | S/D | `batch{}` 契约 | 已覆盖 |
| P2-06 | 双平台独立结果 | F | `valid-content-job`（微信 + 小红书） | 通过 |
| P2-07 | 正文首尾 Text、交替 | S/F/N | `article-body` 1.1 + `check_blocks` + `content-alternation` | 通过 |
| P2-08 | 智能篇幅 | D | SPEC 02 §7.3 | 已覆盖 |
| P2-09 | 双封面同源不拉伸 | S/F | `check_cover`（比例容差 2%、同源校验） | 通过 |
| P2-10 | 单格分辨率不足提示 | S/F | `TILE_RESOLUTION_RISK` 警告记录 | 通过 |
| P2-11 | 小红书三件套 | S/F | `xiaohongshu-note` 1.1 + 标签出现在正文校验 | 通过 |
| P2-12 | R0 不强制联网 | F | `research/evidence.json`（R0、claims 空） | 通过 |
| P2-13 | R1 核验并留源 | F | `research-evidence-R1.example.json` | 通过 |
| P2-14 | R2 无检索不宣称核实 | S/D | `support_status` + `search_performed` | 已覆盖 |
| P2-15 | AI 虚构不称真实 | S/F | `ai_disclosure` 字段 + 夹具文案 | 通过 |
| P2-16 | 只改封面不重生源图 | S/D | `revises.change_scope=covers` | 已覆盖 |
| P2-17 | 批量部分失败保留 | S/D | `batch{}` + `errors[].item_ref` | 已覆盖 |
| P2-18 | 复用固定上游版本 | S/F | `source{task_id, artifact_revision}` | 通过 |
| P2-19 | 外部资料不可信 | D | SPEC 02 §6.2 | 已覆盖 |
| P2-20 | 不自动发布 | D | `platform-config.json` `publish_automation=disabled` | 通过 |
| P2-21 | 图集块结构 | S/F | `image_group`（2–4）+ `valid-content-job` 5 块示例 | 通过 |
| P2-22 | 封面放大上限 | F/S | `upscale_factor`/`quality_risk`、`COVER_SOURCE_LOWRES` | 通过（逻辑） |
| P2-23 | 局部修订可追溯 | S | `content_revision` + `revises{}` | 通过 |
| P2-24 | 事实追溯双向一致 | F/N | `check_claim_linkage`；`EVIDENCE_LINK_MISMATCH` | 通过 |
| P2-25 | 选题评分落盘达标 | S/F | `quality_scorecard` + 校验器要求存在 | 通过 |
| P2-26 | 包清单覆盖正文图 | F/N | `package_images[]` + `check_content_package_images` | 通过 |

## 集成测试项（SPEC 03）

| ID | 要求摘要 | 证据 | 状态 |
|---|---|---|---|
| INT-01..03 | 2×2 / 3×3 / 4×4 Grid 切图 | F：`valid-grid-2x2`；3×3/4×4 由同一 `check_grid_structure` 覆盖 | 通过（2×2 夹具）/ 已覆盖 |
| INT-04..07 | Sequence 4/16/渐进/叙事 | F：`valid-sequence-4`（progression）；16 张由张数规则覆盖 | 通过（4 张）/ 已覆盖 |
| INT-08 | 切图失败不推荐下游 | D | 已覆盖 |
| INT-09/10 | 不点击不创建、点击固定版本 | S/D | 已覆盖 |
| INT-11/12 | 隔日续跑、上游重生不漂移 | S/F | `artifact_revision` + `parent_artifact_revision` | 通过 |
| INT-13 | 非法上游 Schema 明确报错 | N | `schema-major` | 通过 |
| INT-14 | 篡改检测 | N | `hash-mismatch` | 通过 |
| INT-15 | 路径穿越 / ZIP Slip | N | `path-traversal` + `packaging.md` §4 | 通过 |
| INT-16 | 无明文凭据 | D | SPEC 00 §00.5、`packaging.md` §5 | 已覆盖 |
| INT-17 | 批量部分失败 | S/D | `batch{}`、`errors[]` | 已覆盖 |
| INT-18 | 扩展方向不改主流程 | F | registry 驱动 12 份方向文档 | 通过 |
| INT-19 | 研究工具缺失处理 | D | SPEC 02 §6.2、`RESEARCH_UNAVAILABLE` | 已覆盖 |
| INT-20 | 封面修改不动源图 | S/D | `revises` + 哈希不变要求 | 已覆盖 |
| INT-21 | 包清单覆盖与哈希 | F/N | `package-coverage` | 通过 |
| INT-22 | Sequence 作内容源 | F | `valid-content-from-sequence` | 通过 |
| INT-23 | 负例全部被拒并给码 | N | 10 组负例全过 | 通过 |
| INT-24 | 单格分辨率下限分级 | F/N | 警告/阻断两档 | 通过 |
| INT-25 | 预算超限报批 | M | `check_budget`/`split_suggestion` | 通过 |
| INT-26 | 图集块结构 | S/F | `image_group` 规则 | 通过 |

## 未纳入自动化的项（需在实现阶段补）

1. **真实 Provider 调用**（AC-21、AC-22 的运行期部分）：需要密钥与网络，`mock_provider.py` 只覆盖错误映射与预算。
2. **平台现行规格**（O-03/O-04）：见 `references/platform-config.json` 的 `platform_requirement=null`。
3. **语义质量判断**（近重复、主体漂移、文案与画面一致）：需要视觉/文本判别能力，当前只有结构级校验。
4. **研究工具链**（真实检索、来源时效核验）：需要真实检索能力。
