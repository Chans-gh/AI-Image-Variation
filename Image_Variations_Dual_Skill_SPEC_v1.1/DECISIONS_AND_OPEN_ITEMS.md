# 决策记录与待确认清单（v1.1）

**版本**：1.1　|　**日期**：2026-11-16
**基线**：`Image_Variations_Dual_Skill_SPEC_v1.0`（冻结，不修改）。
**说明**：D-01..D-20 为 v1.0 已确认决策，本版**含义不变**，仅在必要时补充落地方式；D-21..D-30 是本版新增的修订决策（依据评审 A–E 与用户「D2/D6 按建议」的授权）。O 清单为仍未定项。

## A. 已确认产品决策（继承 v1.0）

| ID | 决策 | v1.1 落地补充 |
|---|---|---|
| D-01 | 第一阶段 Skill 名称为 `Image Variations Skill` | 不变 |
| D-02 | 图 / 文 / 图+文三类输入 | `input_type` 三值，见 SPEC 01 §3.1 |
| D-03 | 文本无原图默认 3:4；有图默认继承原图比例 | 明确「继承」按 EXIF 修正后的宽高比 |
| D-04 | Grid 2×2/3×3/4×4，单次提示词生成，档位默认 1K/2K/4K | **默认档位改为按单格下限推导**（D-21），用户仍可覆盖 |
| D-05 | Grid 必须无缝、比例一致、可切，自动切图 | 「无缝」改为可计算评分（D-24），不再只靠人眼 |
| D-06 | Sequence 4–16 张，N 条不同 Prompt，独立输出 | 张数与预算、锚点规则联动（D-25） |
| D-07 | 纯文本图 1 锚定后续；有图用原图锚定 | 锚点写入 `consistency_anchor`，禁链式接力 |
| D-08 | 12 个方向、双模式、一任务一方向 | 方向规则正文落地为 `references/directions/`（C1） |
| D-09 | 每方向三类分支，默认一类不混 | `branch` 单值，默认映射写进 registry |
| D-10 | 支持自配 GPT Image / Gemini 兼容 API 与宿主模型 | 增补能力声明与字段映射（C3/D-26） |
| D-11 | 能力不支持明确报错，不静默降级 | 底线清单前置到排产前（D-26） |
| D-12 | 一个 Git 仓库两个独立 Skill，02 依赖 01 产物 | 契约升到 1.1，`content_assets` 视图单点定义 |
| D-13 | 第二阶段发散公众号文章或小红书笔记，支持多主题方案 C | 选题对象落盘（B3）、评分卡（D-30） |
| D-14 | 公众号文—图—文—图交替，文字开篇收尾 | **改为块语法**，允许 2–4 张图集块（D-22） |
| D-15 | 两份封面是不同比例规格，不是两种创意 | 补同源校验、放大幅度上限（D-23） |
| D-16 | 文章篇幅智能自适应 | 与图集块配合，不按图片数凑字 |
| D-17 | 小红书需标题、含标签正文、首评 | 增补 `hashtags/claim_ids/ai_disclosure` |
| D-18 | 按需研究 R0/R1/R2 | 证据字段扩充，检索记录强制（B5） |
| D-19 | 智能交互：直出/先选题/阻塞才问 | 平台「必须确定」不等于「必须先问」，见 SPEC 02 §2.1 |
| D-20 | Skill 01 完成后推荐而不自动启动 Skill 02 | 不变，明确不创建 `content_job_id` |

## B. v1.1 新增修订决策

| ID | 决策 | 理由 | 落点 |
|---|---|---|---|
| D-21 | Grid 默认档位按「满足单格长边下限（默认 1024px）的最低档位」推导，不再固定 2×2→1K | 2×2 用 1K 时单格仅 512px，做正文图/封面必然放大 | SPEC 01 §3.4、`requested_resolution_policy`、AC-06/AC-27 |
| D-22 | 正文允许 `image_group`（2–4 张同源图）作为一个图块 | 16 张图强套「m 图 → m+1 段」会逼出凑字段落 | SPEC 02 §7.3、`article-body` 1.1、AC/P2-21 |
| D-23 | 封面放大幅度 ≤1.0 正常、1.0–1.5 需警示、>1.5 拒绝 | 「像素放大不等于细节」需要可执行阈值，否则等于没约束 | SPEC 02 §7.4、`upscale_factor`、P2-22 |
| D-24 | 「无缝」定义为 `seam_score`（边界/内部梯度比），≥0.85 通过、0.60–0.85 风险、<0.60 失败，并检测 ≥3px 高对比线 | 原「无可见缝隙」不可测、不可回归 | SPEC 01 §7.3、AC-13/AC-28 |
| D-25 | 调用预算前置：Grid ≤3 次尝试、单帧 +2 次重试、批量 ≤5 篇；超预算报 `BUDGET_EXCEEDED` 并拆批 | 30 张图和 16 帧序列的成本差异巨大，原版无预算模型 | SPEC 01 §9.7、AC-29、INT-25 |
| D-26 | 能力底线显式清单（生图、参考图、多次请求、比例、档位声明、错误可区分等），声明缺失即不达标 | 原版把能力问题留到运行期才发现 | SPEC 01 §9.2、`provider-compat.md` |
| D-27 | 包安全：防 ZIP Slip、压缩炸弹、覆盖冲突；清单哈希不符即拒绝 | 包会被跨机器传输与二次分发 | `packaging.md` §4、INT-15 |
| D-28 | 任何交付目录/ZIP 必须带 `delivery-package` 清单 | 原版没有「包是否完整」的单一事实源 | `delivery-package` 契约、AC-31/INT-21 |
| D-29 | 本地端点例外：`allow_local_endpoint` 默认关闭，仅 `127.0.0.1/localhost`，开启状态入执行记录且不导出 | 原「仅 HTTPS」会直接挡住本地自建兼容服务 | SPEC 01 §9.5 |
| D-30 | 选题/内容质量改为 0–5 评分卡，总分 ≥18/25 且单项 ≥2（批量 `distinctness` ≥3） | 原 AC「质量好」不可判定 | SPEC 02 §5.3、`quality_scorecard`、P2-25 |

## C. 评审发现与处置（A–E）

| 编号 | 问题 | 处置 | 落点 |
|---|---|---|---|
| A1 | 小红书条目用 `not/anyOf` 写法存在「两个封面都带」的漏洞 | 改为 `not: {anyOf: [{required:[cover_wide]}, {required:[cover_square]}]}`，并加负例 `xhs-with-cover` | `content-artifact` |
| A2 | `actual_aspect_ratio` 既标必填又允许缺失 | 改为 `status=completed` 时必填（`if/then`） | `image-artifact` |
| A3 | `completed_at` 无条件必填 | 同上，仅完成态必填 | `image-artifact` |
| A4 | 网格验收字段不在契约里 | 新增 `validation{tile_edge_px,tile_edge_ok,seam_score,seam_check_method,grid_attempts}` | `image-artifact`、SPEC 03 §2.2 |
| A5 | 文件名标准与示例不一致（`grid_board.png`/`tiles/`） | 统一为 `board.png` + `images/NN.png` | SPEC 01 §7.4、AC-32 |
| A6 | `index` 语义未定义（板是否算、从 0 还是 1） | 明确 `board.index=0`，`tile/frame.index≥1` 连续唯一、行优先 | `image-artifact`、AC-32 |
| B1 | 局部重做无血缘字段 | 新增 `content_revision` + `revises{content_id,content_revision,change_scope}` | `content-artifact`、P2-23 |
| B2 | 事实断言与证据无双向链接 | 内容侧 `claim_ids[]` + 证据侧 `used_by_content_ids[]`，双向校验 | `check_claim_linkage`、P2-24 |
| B3 | 选题计划不落盘 | 每个条目必须有 `topic_plan_file` 与 `profile_file`，计入包清单 | `content-artifact`、`topic-plan` |
| B4 | 包内图片副本无哈希 | 新增 `package_images[]`，覆盖包内全部图片 | `content-artifact`、P2-26 |
| B5 | 研究证据字段过薄 | 增 `search_performed/search_provider/tools/queries/searched_at`、claim `source_type/verified_at/contradicted_by` | `research-evidence` |
| B6 | 缺 4 个契约（方向、画像、任务状态、交付包） | 新增 `direction-registry`、`asset-profile`、`job-state`、`delivery-package` | `contracts/` |
| C1 | 12 方向只有摘要，无规则正文 | `references/directions/`：`registry.json` + 12 份生成文档 + README | AC-25、INT-18 |
| C2 | 无 Prompt 模板 | `grid-prompt.md`（`grid-prompt-v1`）、`sequence-prompt.md`（三分支） | AC-12/AC-16 |
| C3 | 无 Provider 能力矩阵 | `provider-compat.md`：能力声明、字段映射、错误映射、待实测项 | AC-21/AC-22 |
| C4 | 平台规格散落在正文 | `platform-config.json`（产品建议 vs 平台要求分离，未验证项为 `null`） | O-03/O-04 |
| C5 | 错误码分散在两份 SPEC | `error-registry.json`（36 码，唯一真相源） | 两份 SPEC 的 §错误处理 |
| C6 | 无可交付的评审说明 | `review/Review-Notes-v1.1.docx` | 本包 |
| D1 | 「无缝」是硬闸门但不可测 | → D-24 | AC-28 |
| D2 | Grid 档位默认值不合理 | → D-21 | AC-06/AC-27 |
| D3 | 无成本/调用次数模型 | → D-25 | AC-29/INT-25 |
| D4 | 能力底线隐含未写明 | → D-26 | AC-30 |
| D5 | 质量类 AC 不可判定 | → D-30 | P2-25 |
| D6 | 「严格交替」与「不凑字数」冲突 | → D-22 | P2-21/INT-26 |
| D7 | 仅 HTTPS 挡住本地端点 | → D-29 | AC-22 |
| E1 | 无负例夹具 | `examples/invalid/` 10 组 + `test_negative_fixtures.py` | INT-23 |
| E2 | 无 Provider Mock | `tests/mock_provider.py`（19 项自测） | AC-20/AC-29 |
| E3 | 校验依赖 jsonschema（部分环境无法加载） | `tests/schema_lite.py` 标准库实现 | 全包离线可测 |
| E4 | 缺 Sequence → 内容示例 | `examples/valid-content-from-sequence/` | INT-22 |
| E5 | 无验收追溯 | `tests/ACCEPTANCE_MATRIX.md` | 全量 AC/INT |

## D. 未确认 / 待定事项

| ID | 事项 | 当前处置 |
|---|---|---|
| O-01 | 第二个 Skill 正式名称 | 暂称 `Image Content Publishing Skill` |
| O-02 | Skill 宿主、部署方式、文件与工具权限 | 先写宿主无关契约，落地前选择 |
| O-03 | 公众号封面现行比例/像素/编辑器导入 | `platform_requirement=null`，上线前实测回填 |
| O-04 | 小红书现行标题/正文/图片数/话题限制 | 同上 |
| O-05 | 批量内容任务最大篇数 | 默认 5 篇软限制 + 超限确认 |
| O-06 | 是否支持用户显式要求自动跨 Skill 联动 | 仍采用推荐式（D-20） |
| O-07 | 是否支持导入非 Skill 01 图片资产 | P0 不纳入 |
| O-08 | 公众号是否允许叠加封面标题文字 | 默认不叠字 |
| O-09 | 内容包能否一键导入平台编辑器 | 只保证本地素材包 |
| O-10 | 模型实际分辨率、Grid 严格无缝成功率 | 必须真实服务实测（D-24 阈值待校准） |
| **O-11** | **12 方向逐条正文与原始参考包《单图生成多图-功能与提示词包-20261114》的逐句一致性** | 该包未随 v1.0 交付；本版按摘要表重建并标注 `source_note`，拿到原包后需比对回填 |
| **O-12** | **v1.0 旧产物的迁移策略** | 1.1 接受 1.0 清单但标记 `validation_unverified`；是否提供批量升级工具待定 |
| **O-13** | **`allow_local_endpoint` 是否允许在发布版保留** | 当前默认关闭、仅回环；发布前需再确认是否彻底移除 |

## E. 变更原则

1. **v1.0 冻结**：原包不修改，任何差异以本包为准。
2. **主版本兼容**：1.1 新增字段均为可选或条件必填；1.0 清单可被读取但标记未验证。
3. **单一真相源**：方向来自 registry，错误码来自 error-registry，平台规格来自 platform-config，包清单来自 delivery-package；正文只引用不复制。
4. **不静默降级**：能力不足、预算不足、校验不通过都必须显式报码。
5. **数值须可校准**：1024/768/512、0.85/0.60、1.5×、18/25 均为**可配置默认值**，需真实服务实测后回填。
