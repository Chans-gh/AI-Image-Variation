# QA 报告（v1.1）

**日期**：2026-11-16
**环境**：Python 3.12.14（arm64, macOS）；Pillow 12.3.0；无网络、无真实 API Key
**结论**：**包内全部可执行检查通过（24/24 + 19/19 + 10/10）**；未覆盖项为需外部环境的部分，已在第 4 节逐条列出。

## 1. 执行结果

| # | 检查 | 命令 | 结果 |
|---|---|---|---|
| 1 | 契约 JSON 可解析 | `python -c "json.loads(...)"` | **10/10 通过** |
| 2 | 契约与夹具校验 | `python tests/validate_example_contracts.py` | **24/24 通过**（14 正例、10 负例） |
| 3 | 负例单独回归 | `python tests/test_negative_fixtures.py` | **10/10 按预期被拒** |
| 4 | Provider 映射与预算 | `python tests/mock_provider.py --selftest` | **19/19 通过** |
| 5 | 方向文档生成 | `python tests/build_direction_docs.py` | **12/12 生成成功** |
| 6 | 夹具完整重建 | `python tests/create_examples.py` | 187 个文件，可重复生成 |

正例明细：`schema image-artifact (grid/sequence)`、`semantics valid-grid-2x2`、
`semantics valid-sequence-4`、`schema delivery-package`、`schema content-artifact (grid/sequence source)`、
`semantics valid-content-job`、`semantics valid-content-from-sequence`、`schema article-body`、
`blocks grammar (image_group)`、`schema xiaohongshu-note`、`schema topic-plan`、`schema research-evidence`。

负例明细（`examples/invalid/`）：

| 负例 | 期望 | 结果 |
|---|---|---|
| `grid-crop-mismatch` | `GRID_VALIDATION_FAILED` | 通过 |
| `hash-mismatch` | `SOURCE_ASSET_MISSING` | 通过 |
| `path-traversal` | schema 失败 + `PATH_TRAVERSAL_BLOCKED` | 通过 |
| `schema-major` | schema 失败 + `SCHEMA_MAJOR_UNSUPPORTED` | 通过 |
| `sequence-gap` | `SOURCE_ARTIFACT_INCOMPLETE` | 通过 |
| `content-alternation` | `CONTENT_STRUCTURE_INVALID` | 通过 |
| `xhs-with-cover` | schema 失败（A1 回归） | 通过 |
| `package-coverage` | `PACKAGE_MANIFEST_INVALID` | 通过 |
| `cover-upscale` | `COVER_SOURCE_LOWRES`（2.0×） | 通过 |
| `evidence-link-mismatch` | `EVIDENCE_LINK_MISMATCH` | 通过 |

## 2. 这些检查证明了什么

- 10 份契约是合法 JSON Schema 子集，且对正例夹具给出零错误。
- 图像产物的**哈希、尺寸、切图坐标、单格长边、接缝评分、索引语义**可被机械校验。
- 内容产物的**块语法**（首尾文字、图块不相邻、`TEXT = 图块数 + 1`、`image_group` 2–4）、
  **封面同源与比例**、**放大幅度**、**claim 双向追溯**、**包清单覆盖与哈希**可被机械校验。
- 10 类坏数据会被拒绝并给出注册表中的错误码，而不是静默通过。
- Provider 的鉴权/限流/超时/参数不支持/内容拒绝/坏图都能归一化为稳定错误码，预算与拆批逻辑可计算。

## 3. 本轮发现并修复的问题

| 问题 | 现象 | 处置 |
|---|---|---|
| 第三方校验器不可用 | `jsonschema` 的 `rpds` 扩展在受签名限制环境无法 dlopen | 改为包内 `tests/schema_lite.py`（标准库），移除 `.vendor` |
| 夹具单格过小 | v1.0 的 400×400 tile 在本版 §3.4 下限下必然失败 | 改为 2048×2048 整板 + 1024×1024 切图 |
| 预算公式错误 | 纯文本 Sequence 计划调用数算成 N+2 | 改为 N + 重试预算，并加断言约束 4–16 |
| 负例元数据写错目录 | `case.json` 被写进 `invalid/case.json/case.json` | 修正生成器 |
| Sequence 缺帧负例同时触发 schema 失败 | 期望“仅语义失败”，无法定位职责 | 改为索引 1,2,4,5，schema 通过、语义报缺口 |
| 包内图片归属检查误报 | 校验单条目时把另一条目的图片判为未登记 | `check_content_package_images` 支持传入整包登记集合 |

## 4. 未覆盖 / 未验证（重要）

1. **真实模型调用**：分辨率、比例矩阵、无缝成功率、限流阈值、计费口径均未实测（O-10）。
   因此 `tile_edge_min_px=1024`、`768/512` 分档、`0.85/0.60` 接缝阈值、`1.5×` 放大上限、
   `18/25` 评分阈值都是**可配置默认值**，不是实测结论。
2. **平台规格**：公众号封面像素与编辑器导入、小红书字符/图片数限制（O-03/O-04）在
   `references/platform-config.json` 中为 `null`；实现必须按“未知则提示、不硬校验”处理。
3. **语义质量**：近重复判定、主体漂移、文案与画面一致性需要视觉/文本判别能力，当前只有结构级校验。
4. **研究链路**：真实检索、来源时效与冲突核验未接入；`research-evidence-R1.example.json` 是结构示例，数据为占位。
5. **方向正文来源**：12 份方向文档按 v1.0 摘要表重建，与原参考包的逐句一致性未核对（O-11）。
6. **压缩炸弹限额**：`packaging.md` 给出建议上限，但本版没有真实 ZIP 解析实现可测（INT-15 只覆盖了路径穿越与 Slip 规则文本）。

## 5. 复现步骤

```bash
cd Image_Variations_Dual_Skill_SPEC_v1.1
python -m pip install Pillow                 # 唯一必需依赖
python tests/validate_example_contracts.py   # 期望：总计 24/24 通过
python tests/test_negative_fixtures.py       # 期望：负例 10/10 按预期被拒
python tests/mock_provider.py --selftest     # 期望：全部 PASS，exit 0
python tests/create_examples.py              # 可重复重建全部夹具
python tests/build_review_notes.py           # 重建 Word 评审说明（需 python-docx）
```
