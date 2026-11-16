# Image Variations 双 Skill 规范包 v1.1

本包是 `Image_Variations_Dual_Skill_SPEC_v1.0` 的**修订版**，也是后续 Skill 开发的**唯一基线**。
v1.0 原包保持冻结、不修改；两者差异见 [CHANGELOG_v1.1.md](CHANGELOG_v1.1.md)。

**包含两个互相独立、通过产物契约解耦的 Skill**

| Skill | 作用 | 产出 |
|---|---|---|
| Skill 01 `Image Variations Skill` | 一张图（或一段文字）发散成多张变体 | Grid 整板 + N² 张切图；或 4–16 帧序列图 |
| Skill 02 `Image Content Publishing Skill`（工作名） | 消费 Skill 01 的产物，生成公众号文章 / 小红书笔记 | 正文块、双封面、笔记、选题、证据、交付包 |

**关键设计**：Skill 02 只读 Skill 01 的 `image-artifact` 清单，通过 `content_assets` 视图取图，从不读取中间状态、不重跑生图、不自动发布。

## 1. 目录结构

```text
Image_Variations_Dual_Skill_SPEC_v1.1/
├── README.md                       # 本文件
├── CHANGELOG_v1.1.md               # v1.0 → v1.1 全部变更（按 A–E 编号）
├── DECISIONS_AND_OPEN_ITEMS.md     # D-01..D-30 决策 + O-01..O-13 待定
├── specs/
│   ├── 00-Repository-Architecture-v1.1.md
│   ├── 01-Image-Variations-Skill-v1.2.md
│   ├── 02-Image-Content-Publishing-Skill-v1.1.md
│   └── 03-Integration-and-Artifact-Contract-v1.1.md
├── contracts/                      # 10 份 JSON Schema（Draft 2020-12）
│   ├── image-artifact.schema.json          # 图像产物 1.1
│   ├── content-artifact.schema.json        # 内容产物 1.1
│   ├── article-body.schema.json            # 正文块 1.1（含 image_group）
│   ├── xiaohongshu-note.schema.json
│   ├── topic-plan.schema.json
│   ├── research-evidence.schema.json
│   ├── direction-registry.schema.json
│   ├── asset-profile.schema.json
│   ├── job-state.schema.json
│   └── delivery-package.schema.json
├── references/
│   ├── directions/                 # 12 方向：registry.json + 12 份规则正文 + README
│   ├── grid-prompt.md              # grid-prompt-v1
│   ├── sequence-prompt.md          # sequence-prompt-v1（三分支）
│   ├── provider-compat.md          # 能力底线、字段映射、错误映射
│   ├── platform-config.json/.md    # 平台规格（产品建议 vs 平台要求分离）
│   ├── error-registry.json         # 36 个错误码唯一真相源
│   └── packaging.md                # 交付包与 ZIP 规则
├── examples/                       # 合成夹具：4 组正例 + 10 组负例 + 2 份示例对象
├── tests/                          # 校验器、Mock Provider、追溯矩阵
└── archive/01-Image-Variations-Skill-v1.1.md   # v1.0 同名 SPEC 的存档
```

## 2. 快速自检（零第三方依赖，除 Pillow）

```bash
python tests/validate_example_contracts.py   # 24 项：14 正例 + 10 负例
python tests/test_negative_fixtures.py       # 10 组负例单独回归
python tests/mock_provider.py --selftest     # 19 项 Provider 映射与预算
python tests/build_direction_docs.py         # 由 registry 重新生成 12 份方向文档
python tests/build_review_notes.py           # 重建 Word 评审说明（需 python-docx）
```

依赖说明见 [tests/requirements.txt](tests/requirements.txt)：仅需 Python 3.10+ 与 Pillow；
JSON Schema 校验由包内 `tests/schema_lite.py` 提供，**不需要** `jsonschema`。

## 3. 版本治理

| 层 | 版本 | 说明 |
|---|---|---|
| 包 | 1.1 | 本包 |
| SPEC 00 架构 | 1.1 | 版本治理、包清单、能力底线 |
| SPEC 01 图像 Skill | **1.2** | 档位推导、接缝评分、预算、命名 |
| SPEC 02 内容 Skill | 1.1 | 图集块、追溯、评分卡 |
| SPEC 03 集成 | 1.1 | `content_assets`、块语法、INT-01..26 |
| 契约 | 各文件 `$id` 内版本 | 1.0 旧清单仍可读取 |
| 方向注册表 | 1.0 | 12 方向 |
| 产物修订 | `artifact_revision` / `content_revision` | 每次重做递增，旧修订不被覆盖 |

## 4. 读这份规范时必须知道的五条硬约定

1. **`index`**：`board.index = 0`；`tile`/`frame` 为 `1..N` 连续唯一（Grid 行优先）。
2. **命名**：`board.png` + `images/NN.png` + `manifest.json` + `delivery-package.json`；`grid_board.png` / `tiles/` 已废弃。
3. **无缝**：不是形容词，而是 `seam_score`（`boundary_gradient_v1`）：≥0.85 通过、0.60–0.85 记 `GRID_SEAM_RISK`、<0.60 失败。
4. **预算**：Grid ≤3 次尝试、单帧 +2 次重试、批量 ≤5 篇；超预算必须 `BUDGET_EXCEEDED` 并拆批，不得静默继续。
5. **正文结构**：`TEXT (TEXT|IMAGE|GROUP)* TEXT`，图块不相邻，`TEXT 块数 = 图块数 + 1`，`image_group` 2–4 张。

## 5. 状态与限制（务必阅读）

- **合成夹具**：`examples/` 下全部图片由本地绘图生成，**不是模型输出**，只验证数据契约，不证明画质。
- **未实测项**：平台规格（O-03/O-04）、真实分辨率与无缝成功率（O-10）、两类兼容 API 的具体字段（AC-21）均标注待实测；相关数值在 `platform-config.json` 与 SPEC 中是可配置默认值。
- **方向正文来源**：12 方向规则按 v1.0 摘要表重建（原参考包未随 v1.0 交付），逐句一致性列为 O-11。
- **不含实现代码**：本包是规范与契约，不是 Skill 运行时；`tests/` 是校验工具，不是产品代码。

## 6. 验收追溯

- 全部 AC / INT 与证据的对应关系：[tests/ACCEPTANCE_MATRIX.md](tests/ACCEPTANCE_MATRIX.md)
- 本次校验结果与已知缺口：[tests/QA_REPORT.md](tests/QA_REPORT.md)
- 评审说明（Word）：[review/Review-Notes-v1.1.docx](review/Review-Notes-v1.1.docx)
