# 契约示例（合成测试夹具，v1.1）

图片由本地绘图程序生成，**不是 AI 生图模型输出**，也不代表真实平台封面质量。用于验证 Manifest、
哈希、网格切分、块语法、封面同源比例、包清单与负例拒绝逻辑。

## 正例

- `valid-grid-2x2/`：2048×2048 整板 + 4 张 1024×1024 Tile（单格长边达标），含 `delivery-package.json`。
- `valid-sequence-4/`：4 张 768×1024 帧；纯文本默认 3:4；图 1 锚点；演示 `TILE_RESOLUTION_RISK` 警告记录。
- `valid-content-job/`：Grid 源 → 1 篇公众号（含 `image_group` 图集块 + 同源双封面 + 选题/画像 + 包清单）
  + 1 篇小红书。
- `valid-content-from-sequence/`：Sequence 源 → 1 篇小红书，保留阶段顺序（对应 INT-22）。
- `topic-plan.example.json` / `research-evidence-R1.example.json`：选题与 R1 证据结构示例。

## 负例（`invalid/`）

每组含 `case.json` 声明期望结果（`schema_fail` 与/或错误码）：

| 目录 | 期望 |
|---|---|
| `grid-crop-mismatch/` | `GRID_VALIDATION_FAILED` |
| `hash-mismatch/` | `SOURCE_ASSET_MISSING` |
| `path-traversal/` | schema 失败 + `PATH_TRAVERSAL_BLOCKED` |
| `schema-major/` | schema 失败 + `SCHEMA_MAJOR_UNSUPPORTED` |
| `sequence-gap/` | `SOURCE_ARTIFACT_INCOMPLETE` |
| `content-alternation/` | `CONTENT_STRUCTURE_INVALID` |
| `xhs-with-cover/` | schema 失败（A1 回归） |
| `package-coverage/` | `PACKAGE_MANIFEST_INVALID` |
| `cover-upscale/` | `COVER_SOURCE_LOWRES`（放大 2.0×） |
| `evidence-link-mismatch/` | `EVIDENCE_LINK_MISMATCH` |

## 运行校验

```bash
python tests/validate_example_contracts.py   # 正例 + 负例（24 项）
python tests/test_negative_fixtures.py       # 只跑负例
python tests/mock_provider.py --selftest     # Mock Provider 错误映射
python tests/build_direction_docs.py         # 由 registry 重新生成方向规则正文
```

依赖：Python 3.10+ 与 `Pillow`（JSON Schema 由包内 `tests/schema_lite.py` 提供，无需 `jsonschema`）。
