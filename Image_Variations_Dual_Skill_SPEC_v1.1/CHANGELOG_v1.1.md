# 变更记录：v1.0 → v1.1

**基线**：`Image_Variations_Dual_Skill_SPEC_v1.0`（2026-11-16，冻结不改）
**本版**：`Image_Variations_Dual_Skill_SPEC_v1.1`（2026-11-16，开发基线）
**性质**：修订版。契约向后兼容（1.0 清单可读，条件必填只作用于 `1.1` 文档）；不新增产品需求，只修正矛盾、补齐缺失交付物、把不可判定的验收项变成可判定。

## 1. 变更统计

| 类别 | v1.0 | v1.1 | 变化 |
|---|---|---|---|
| 契约 | 6 | 10 | +4（direction-registry、asset-profile、job-state、delivery-package） |
| SPEC | 4 | 4 | 全部改写，01 升至 1.2 |
| 参考文档 | 0（散落在 SPEC 正文） | 21 | 方向 14 + 模板/兼容/平台/错误/打包 7 |
| 示例 | 2 组（仅正例） | 4 组正例 + 10 组负例 | +2 正例、全部负例 |
| 测试 | 1 个脚本（依赖 jsonschema） | 6 个脚本 + 2 份文档 | 标准库校验器、Mock Provider、追溯矩阵 |

## 2. 按文件的变更

| 文件 | 变更 |
|---|---|
| `specs/00-Repository-Architecture-v1.1.md` | 重写：五级版本治理表、D-28 包清单强制、D-26 能力底线、D-27 包安全、DoD 可判定化 |
| `specs/01-Image-Variations-Skill-v1.2.md` | 重写 §3.4（档位推导）、§7.3（接缝评分）、§7.4（命名）、§9.2（能力底线）、§9.5（本地端点）、§9.7（调用预算）；AC 扩到 32 条 |
| `specs/02-Image-Content-Publishing-Skill-v1.1.md` | 新增 v1.1 摘要；§5.3 评分卡；§6.2 双向追溯；§7.3 块语法 + `image_group`；§7.4 放大幅度；§8 笔记字段；§10 四层检查；§13 扩到 P2-26 |
| `specs/03-Integration-and-Artifact-Contract-v1.1.md` | 新增 §1.1 `content_assets` 定义、§3.3 块语法、INT-21..26；字段表加「变更」列 |
| `contracts/*.schema.json` | 10 份全部校验通过；`image-artifact`/`content-artifact` 升 1.1；新增 4 份 |
| `references/directions/` | 新增：`registry.json`（12 方向结构化规则）+ 12 份生成文档 + 索引 |
| `references/grid-prompt.md` | 新增：一次成板的 Prompt 组装、格位规划规则、反例、与校验的对应 |
| `references/sequence-prompt.md` | 新增：三分支模板、锚点与失败恢复、预算、反例 |
| `references/provider-compat.md` | 新增：能力声明、底线清单、两类协议字段映射、错误映射、安全、待实测项 |
| `references/platform-config.json/.md` | 新增：产品建议与平台要求分离；未验证项显式 `null` |
| `references/error-registry.json` | 新增：36 个错误码唯一真相源 |
| `references/packaging.md` | 新增：目录、命名、包清单、ZIP 安全限额、校验顺序 |
| `examples/` | 重建 4 组正例（含 2048 整板、Sequence→内容链路）；新增 8 组负例 |
| `tests/` | 新增 `schema_lite.py`、`checks.py`、`validate_example_contracts.py`、`test_negative_fixtures.py`、`mock_provider.py`、`create_examples.py`、`build_direction_docs.py`、`ACCEPTANCE_MATRIX.md`、`QA_REPORT.md`、`requirements.txt` |
| `README.md` / `DECISIONS_AND_OPEN_ITEMS.md` / `review/*.docx` | 新增 |
| `archive/01-Image-Variations-Skill-v1.1.md` | 保留：v1.0 同名 SPEC 原文，便于逐句比对 |

## 3. 按评审编号的处置

### A. 契约矛盾与漏洞（全部修复）

| 编号 | 处置 |
|---|---|
| A1 | 小红书 `not/anyOf` 改为 `not{anyOf{required[cover_wide]}, required[cover_square]}`；负例 `xhs-with-cover` 回归 |
| A2 | `actual_aspect_ratio` 改为 `status=completed` 条件必填 |
| A3 | `completed_at` 同上 |
| A4 | 网格验收字段进入契约 `validation{}` |
| A5 | 命名统一为 `board.png` + `images/NN.png` |
| A6 | `index` 语义固定：board=0，tile/frame 1..N 连续唯一 |

### B. 跨 Skill 链路（全部补齐）

| 编号 | 处置 |
|---|---|
| B1 | `content_revision` + `revises{change_scope}` |
| B2 | `claim_ids[]` ↔ `used_by_content_ids[]` 双向校验 |
| B3 | `topic_plan_file` + `profile_file` 强制落盘并计入包清单 |
| B4 | `package_images[]` 覆盖包内全部图片副本 |
| B5 | 证据字段扩充 + `search_performed` 强制 |
| B6 | 新增 4 份契约 |

### C. 缺失交付物（全部补齐）

| 编号 | 交付物 |
|---|---|
| C1 | `references/directions/`（registry + 12 正文 + 索引） |
| C2 | `grid-prompt.md`、`sequence-prompt.md` |
| C3 | `provider-compat.md` |
| C4 | `platform-config.json/.md` |
| C5 | `error-registry.json` |
| C6 | `review/Review-Notes-v1.1.docx` |

### D. 决策缺口（转为 D-21..D-30，见 DECISIONS §B）

D1→D-24（接缝评分）、D2→D-21（档位推导）、D3→D-25（调用预算）、D4→D-26（能力底线）、
D5→D-30（评分卡）、D6→D-22（图集块）、D7→D-29（本地端点）。

### E. 测试与夹具（全部补齐）

E1→10 组负例；E2→Mock Provider；E3→标准库校验器；E4→Sequence→内容示例；E5→验收追溯矩阵。

## 4. 兼容性说明

1. **读取**：1.1 校验器接受 `schema_version=1.0` 的 `image-artifact`，但缺少 `tile_edge_ok` / `seam_score` / `delivery-package` 时标记 `validation_unverified`，**不得**伪造成通过（SPEC 02 §2.2 第 6 条、`TILE_RESOLUTION_UNVERIFIED`）。
2. **写入**：新产物一律写 `1.1`；`1.0` 不会再生成本。
3. **新增字段**：全部可选或条件必填，不存在「必须同时升级上游」的破坏性变更。
4. **重命名**：`grid_board.png` → `board.png`、`tiles/` → `images/` 属于**不兼容**的路径变更；旧包仍可读（路径来自清单），但新包不再生成旧名。
5. **不做的事**：不提供自动把 1.0 产物升级为 1.1 的转换器（O-12）。

## 5. 已知遗留

- O-03 / O-04：平台现行规格未实测，配置中为 `null`。
- O-10：模型实际分辨率、无缝成功率、1024/768/512 阈值需真实服务校准。
- O-11：12 方向正文与原参考包的逐句一致性待比对。
- O-13：`allow_local_endpoint` 是否在发布版保留待定。
