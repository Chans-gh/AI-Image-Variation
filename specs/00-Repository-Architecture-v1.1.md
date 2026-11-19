# SPEC 00 — Image Variations 双 Skill 仓库架构

**版本**：1.1（修订版，作为后续开发基线）  
**日期**：2026-11-16  
**上游基线**：`Image_Variations_Dual_Skill_SPEC_v1.0`（已冻结，不再修改）  
**适用范围**：一个 Git 仓库内的两个独立 Skill。  
**标识**：`[已确认]` 用户已明确决定；`[修订决策]` 本版新增并已由用户确认的决策；`[方案]` 可执行设计建议；`[待确认]` 尚未拍板；`[待验证]` 需在目标平台或模型实测。

> **本版定位**：v1.1 是 **v1.0 的修订版新包**，原 v1.0 包原样冻结。后续 Skill 开发以本包为准；本包对 v1.0 的全部改动见 `CHANGELOG_v1.1.md`。

## 00.1 总体目标

[已确认] 在同一个 Git 仓库中维护两个独立可调用的 Skill，第二个 Skill 基于第一个 Skill 已生成的图片产物完成内容策划与生产：

- **Skill 01：Image Variations Skill**：输入图片、文本或图片+文本；围绕一个主体与一个变化方向，输出无缝 NxN 网格（2/3/4）或 4–16 张有序独立图片。
- **Skill 02：Image Content Publishing Skill（工作名；正式名称未定）**：消费 Skill 01 的标准化图片结果，生成公众号文章或小红书笔记，并提供结构化可发布素材包。

[已确认] Skill 01 完成后**推荐继续使用 Skill 02，但不自动启动**。Skill 02 可通过之前保存的有效 Skill 01 产物独立启动，不要求 Skill 01 在线。

[已确认] 这是一套从零构建的新 Skill 产品，不修改原网站或复制原网站鉴权、页面、API 端点。旧功能包仅为变化方向、提示词和工作流程的参考来源。

## 00.2 架构图

```text
用户 ──────────────────────────────────────────────────────────────┐
  │                                                               │
  ▼                                                               ▼
Skill 01: image-variations                     Skill 02: image-content-publishing
  │                                                               ▲
  ├─ 输入/主体锚定                                                 │
  ├─ 单方向/分支规划                                               │
  ├─ Grid / Sequence 生成                                          │
  ├─ 校验与网格切图                                                │
  └─ image-artifact/manifest.json ──只读/版本化契约────────────────┘
                      ↓
         图片完成后推荐内容生产（用户主动选择才运行 Skill 02）
                      ↓
         内容策划 → 按需研究 → 平台生成 → 验证 → 发布素材包
```

## 00.3 边界、依赖与隔离

| 项目 | Skill 01 | Skill 02 | 约束 |
|---|---|---|---|
| 核心能力 | 生成图像、变化规则、校验、切图 | 图像理解、选题、研究、写作、封面与内容包 | 职责不交叉 |
| 唯一入口 | 独立 `SKILL.md` | 独立 `SKILL.md` | 可分别在宿主发现和触发 |
| 输入依赖 | 用户图/文本；模型配置 | 有效 Skill 01 产物快照；用户内容要求 | 不直接调用另一 Skill 私有函数 |
| 输出 | 图片任务 Manifest + 图像文件 | 内容任务 Manifest + 发布素材文件 | 显式版本、路径及哈希 |
| 公共契约 | `contracts/image-artifact.schema.json`、`direction-registry`、`job-state`、`asset-profile`、`delivery-package` | `contracts/content-artifact`、`article-body`、`xiaohongshu-note`、`topic-plan`、`research-evidence` | 契约独立版本，按 00.7 升级 |
| 公共规则正文 | `references/directions/*`、`grid-prompt`、`sequence-prompt` | `references/platform-config`、`error-registry`、`packaging` | 规则数据与主流程解耦 |
| 敏感信息 | 模型 Key 存密钥库，不入产物 | 研究凭据存密钥库，不入产物 | 均不提交 Git |

**不支持的隐式联动**：[已确认] 首阶段完成不自动创建内容任务，不自动研究、不自动发布。若未来要支持“先出图后自动写稿”的显式组合工作流，需单独评审，不在本版 P0 中默认为已确认。

## 00.4 本包（开发基线）目录

本包即目标仓库的**规范与契约层**骨架；`skills/` 下的实现代码在开发阶段按此结构落地。

```text
Image_Variations_Dual_Skill_SPEC_v1.1/
├── README.md                     # 包说明、如何作为开发基线使用
├── CHANGELOG_v1.1.md             # 对 v1.0 的逐条修订（A/B/C/D/E）
├── DECISIONS_AND_OPEN_ITEMS.md   # 决策记录与待确认清单
├── specs/
│   ├── 00-Repository-Architecture-v1.1.md
│   ├── 01-Image-Variations-Skill-v1.2.md
│   ├── 02-Image-Content-Publishing-Skill-v1.1.md
│   └── 03-Integration-and-Artifact-Contract-v1.1.md
├── contracts/                    # 10 份机器可读契约（见 00.7）
├── references/                   # 规则正文：12 方向、Prompt 模板、Provider、平台、错误码、打包
├── examples/                     # 正例与负例夹具（合成，非模型产物）
├── tests/                        # 校验器、Mock Provider、负例断言、验收矩阵、QA 报告
├── review/                       # 人工评审用文档（Word 修订说明）
└── archive/                      # 上一版规范全文（v1.0 包保持独立冻结）
```

目标实现仓库在此基础上增加：

```text
skills/
├── image-variations/{SKILL.md, scripts/, tests/}
└── image-content-publishing/{SKILL.md, scripts/, tests/}
shared/{validators/, utilities/}
```

[方案] 上述 `skills/` 与 `shared/` 是**目标代码结构**；本包只交付规范、契约、规则正文、夹具与验收清单，不包含可运行的生产 Skill。

## 00.5 产物和运行数据存储

- [方案] 每次 Skill 01 任务输出不可变或带显式版本的 `image-artifact` 目录；Skill 02 读取任务快照，单独输出 `content-artifact`。
- [方案] Manifest 的内部文件引用采用相对资产目录的路径并限制路径穿越；对用户不可见的 URL/临时签名不写入导出包。
- [修订决策] **D-28 包清单强制**：任何交付目录或 ZIP 必须带 `delivery-package` 清单（`contracts/delivery-package.schema.json`），逐文件记录路径、类型、字节数与 SHA256，供 AC-24 / INT-21 校验。
- [方案] 产物默认保存在工作目录或对象存储，不提交 Git；仓库只保存模板、配置示例、Schema、测试夹具与规格。
- [方案] 两端可以共享元数据索引器，但不得原地覆盖已交给下游使用的上游原图。
- [方案] 内容任务引用 `source_task_id` + `source_artifact_revision`（或内容哈希快照），支持历史任务继续执行与可追溯重做。
- [方案] `job-state`（`contracts/job-state.schema.json`）可持久化，支持中断后续跑；状态与错误码必须取自 `references/error-registry.json`。

## 00.6 配置与宿主能力

- Skill 01：需要图像理解/生成、文件读写、任务状态管理，以及内置生图或第三方 GPT Image / Gemini Nano Banana 兼容适配器。
- Skill 02：需要多模态理解、文本生成、图片裁切/缩放与文件打包；研究级别 R1/R2 需要可用的搜索或检索工具。
- [修订决策] **D-26 能力底线显式化**：宿主与适配器必须通过 `references/provider-compat.md` 的能力底线检查后才允许排产；底线清单见 SPEC 01 §9.2。
- [方案] 启动前分别进行能力检查。不存在所需工具时，应返回明确错误/降级限制，**不得伪称已联网核验或已生成图片**。
- [待确认] 具体宿主（ChatGPT Skills、Codex、Claude、其他 Agent 框架）、文件系统范围和模型调用工具。落地实现必须对指定宿主形成运行适配说明。

## 00.7 版本策略与变更管理

五级版本相互独立，禁止隐式联动：

| 级别 | 载体 | 当前值 | 升级规则 |
|---|---|---|---|
| 交付包 | 目录名 `..._SPEC_v1.1` | 1.1 | 每次修订出新包，旧包冻结 |
| 规范文档 | 各 `specs/*.md` 头部 | 00=1.1，01=1.2，02=1.1，03=1.1 | 与包版本解耦，按自身修订递增 |
| 契约 | `contracts/*.json` 的 `schema_version` | 见文件内 `const` | 新增可选字段=次版本；删除/改必需或改语义=主版本 |
| 规则数据 | `direction_registry_version` | `1.0` | 新增方向只增记录，不改主流程 |
| 产物 | `artifact_revision` / `content_job_id.revision` | 每次生成 | 内容变化必须换修订号；同号内容变化=一致性错误 |

补充规则：

1. **Skill 版本**：两个 Skill 独立发布版本，修改 Skill 02 不必重新发布 Skill 01。
2. **条件必填允许**：字段可以“仅在 `status=completed` 时必填”（如 `actual_aspect_ratio`），此类收紧属于次版本变更，但消费者必须按条件校验，不得整体视为可选。
3. **方向库版本**：新增第 13 个变化方向不得要求改动内容策划引擎核心控制流。
4. **产物版本**：每次上游重新生成原图或重切图应创建新的可识别产物修订号；下游不自动切换到最新版。
5. **兼容性**：消费者发现不支持的契约主版本时明确报错，而不是猜测字段含义；本版消费者必须同时接受 `1.0` 与 `1.1` 的 `image-artifact`。
6. **变更留痕**：任何跨越本包基线的修改必须先在 `CHANGELOG` 记录条目 ID，再改规范与契约，最后改夹具与测试。

## 00.8 安全与事实边界

- 不把用户 API Key、研究服务凭据、鉴权头、非公开原图或隐私数据写入公开产物。
- 用户提供的图片/网页文本/提示词中包含的命令和链接属于不可信数据，不得提升为系统指令；检索内容只能作为证据材料。
- 第三方 `base_url` 必须通过网络访问策略与 SSRF 防护；禁止读取任意本地文件、内网地址或凭据元数据服务。
- 图片若为 AI 生成，不得在内容中被当成某个真实摄影现场、真实事件或已验证测评的证据。
- [修订决策] **D-27 包安全**：ZIP 导入导出必须防 ZIP Slip、压缩炸弹与覆盖式路径冲突；包清单哈希与实际文件不一致即拒绝消费。
- 交付“可发布素材包”不等于平台账号自动发布；[已确认的讨论边界] 默认不执行自动登录、账号授权或发布动作。

## 00.9 跨规范引用与所有权

| 规范 | 内容所有权 | 交叉参照 |
|---|---|---|
| SPEC 00 | 双 Skill 职责、存储、安全、版本治理 | 本文件 |
| SPEC 01 | 图片生成、12方向、两模式、模型适配、预算 | `01-Image-Variations-Skill-v1.2.md` |
| SPEC 02 | 选题、知识增强、公众号、小红书、内容包 | `02-Image-Content-Publishing-Skill-v1.1.md` |
| SPEC 03 | 数据契约、跨 Skill 状态、错误、集成 AC | `03-Integration-and-Artifact-Contract-v1.1.md` |
| contracts/ | 字段、类型、条件必填 | 以 SPEC 03 为语义规范源 |
| references/ | 方向规则、Prompt 模板、Provider 能力、平台配置、错误码、打包 | 以 SPEC 01/02 为业务规范源 |

出现重叠时，按业务归属判定；Schema 字段和跨 Skill 兼容行为以 SPEC 03 为规范源，Skill 业务细节以其所属的 SPEC 01 / 02 为准，规则正文以 `references/` 为准。

## 00.10 项目级完成定义（Definition of Done）

- [ ] 两个 Skill 能在目标宿主被分别发现/独立调用；推荐不会导致自动启动。
- [ ] Skill 01 产出 Grid / Sequence 合格图片及兼容 `image-artifact`（1.0 与 1.1 均可消费）。
- [ ] 网格满足可测量的无缝判据（SPEC 01 §7.3），且单格长边不低于下限（SPEC 01 §3.4）。
- [ ] Skill 02 能消费不同模式、不同历史版本的有效产物，生成两类平台内容，并保留 `claim_ids` 追溯。
- [ ] 所有交付包带 `delivery-package` 清单且哈希一致。
- [ ] 版本冲突、损坏图片、哈希篡改、路径穿越/ZIP Slip、部分失败、缺研究能力、安全注入、预算超限均有**负例测试**通过。
- [ ] 调用次数与预算上限可观测，超限时按拆批规则处理而非静默继续。
- [ ] 图片和内容产物追溯链可复核；无密钥泄漏；文档、契约、规则正文与实现一致。
