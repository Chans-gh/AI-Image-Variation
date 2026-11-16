---
name: image-variations
description: >
  把一句需求或一张参考图变成一组主体一致的图片变体。两种模式：Grid（一次生成 2×2/3×3/4×4
  整板再确定性切图，适合需要"同一主体、不同呈现"的成组图）与 Sequence（4–16 张逐帧生成，
  适合表现光照/视角/场景/叙事等渐进变化）。当用户想要"一张图的多组变体""同一主体的多角度/
  多风格/多场景""九宫格套图""分镜序列""同一产品换个说法的一组图"时使用。
  交付可校验的产物目录与 ZIP（manifest.json + delivery-package.json），不交付无法验证的散图。
---

# Image Variations（Skill 01）

**唯一入口。** 编排写在这里；12 个方向的全文、Prompt 模板、Provider 兼容矩阵、错误码、
阈值**不在这里**，一律在冻结基线内按需读取，避免出现两份互相漂移的真相。

- 业务规则：`Image_Variations_Dual_Skill_SPEC_v1.1/specs/01-Image-Variations-Skill-v1.2.md`
- 方向注册表：`.../references/directions/registry.json`
- Prompt 模板：`.../references/grid-prompt.md`、`.../references/sequence-prompt.md`
- Provider 兼容与安全：`.../references/provider-compat.md`
- 错误码：`.../references/error-registry.json`
- 打包规则：`.../references/packaging.md`
- 契约：`.../contracts/{image-artifact,job-state,delivery-package}.schema.json`

基线为**只读**：哈希清单在 `shared/baseline.manifest.json`，`shared/check_baseline.py` 必须常绿。
需要改规范请先说明理由并取得确认，不要在实现里悄悄"修正"基线。

## 一、五条不可违反的规则

1. **不静默降级**。能力不足、预算不足、校验不通过，一律显式返回
   `references/error-registry.json` 里的错误码；不允许"悄悄换个更小的尺寸/更低的档位/更少的张数"
   继续跑，也不允许把失败说成通过。
2. **阈值只从配置读**。`config/iv.config.json` 是唯一来源（单格边长 warn/hard、接缝 pass/risk、
   放大倍数、重试与预算）。它们尚未经真实模型校准，改之前先给数据。代码里不得出现魔法数字。
3. **单一事实来源**。方向、Prompt、错误码、契约都从基线运行时读取，**不复制**进本 Skill。
4. **密钥只走引用**。只接受 `secret://…` 或 `env:…`；密钥、原图、用户文本不得进入日志、
   manifest、错误信息或 ZIP。
5. **产物必须可验证**。每次交付都带 `manifest.json`（`image-artifact` 1.1）与
   `delivery-package.json`；清单里的每个条目都必须真实存在且哈希一致。

## 二、怎么用

```bash
# 1) 预检：只算计划，不调用模型、不落盘
python scripts/run_task.py --input task.json --preflight-only

# 2) 正式执行（无密钥时用 mock_synthetic 打通全链路）
python scripts/run_task.py --input task.json --workspace ./workspace --pretty

# 3) 只有部分帧失败时，按原索引补生成（产出 rev2，parent 指向 rev1）
python scripts/run_task.py --input task.json --retry-failed-of ./workspace/iv_<task>_rev1
```

任务描述（一份 JSON，字段与 `references/provider-compat.md` 的产品参数一致）：

```json
{
  "task_id": "cup_light_001",
  "output_mode": "grid",            // grid | sequence
  "grid_size": 3,                   // grid 必填：2 | 3 | 4
  "direction": "light",             // 12 个方向之一，见 registry.json
  "input_type": "text_only",        // text_only | image_only | image_plus_text
  "subject": "一只浅灰陶瓷杯",        // 主体描述，进入主体锚点
  "reference_path": "img/ref.png",  // 有图输入时必填，相对 workspace
  "aspect_ratio": "3:4",
  "resolution_tier": "1K",          // 1K | 2K | 4K；缺省按单格最小边长反推
  "provider": {"type": "mock_synthetic"},
  "pack_zip": true
}
```

`sequence` 额外需要 `branch`（方向支持的 3 个分支之一）与 `image_count`（4–16）。

真实 Provider：

```json
"provider": {
  "type": "openai_image_compatible",     // 或 gemini_image_compatible
  "base_url": "https://api.example.com/v1",
  "model_id": "gpt-image-1",
  "api_key_secret_ref": "secret://IMAGE_API_KEY",
  "capability": { "...": "见 provider-compat.md §1 能力声明" }
}
```

`capability` 缺省即视为不达标并报 `MODEL_CAPABILITY_UNSUPPORTED`——它是"底线清单"，
不会被默认值悄悄补全。内置适配器未注入时报 `BUILTIN_UNAVAILABLE`，**不会**自动改走外网。

## 三、流程（分支规则）

```text
解析输入 → 预检（能力/比例/档位/张数/预算）
  ├─ 不过 → 报错误码，结束（不调用模型、不落盘）
  └─ 过 ─┬─ Grid：一次成板 → 校验（N×N / 无缝 / 可切图 / 比例）→ 有限整板重试
         │        → 规格化 → 行优先切图 → 每格边长判定（ok / risk / too_low）
         └─ Sequence：先出 N 条帧计划（帧数、每帧变化点、保持项、Prompt）
                  → 逐帧生成（图 1 打底，图 2–N 一律引用图 1；有原图则每帧都带原图）
                  → 单帧失败保槽继续；图 1 失败立即 SEQUENCE_ANCHOR_FAILED
                  → 近重复检测（重试耗尽后保留槽位并标注）
 → 落盘（manifest + delivery-package + 可选 ZIP）→ 返回 JSON 结果
```

关键判定：

| 情况 | 处理 |
| --- | --- |
| `grid_size` 非 2/3/4、序列不在 4–16 | `INVALID_MODE_OR_COUNT` |
| 单格边长低于 hard 阈值 | `TILE_RESOLUTION_TOO_LOW`，不出交付包 |
| 单格边长低于 warn 阈值 | 交付但记 `TILE_RESOLUTION_RISK` 警告（`tile_edge_ok=false`） |
| 接缝 < risk 阈值 | `GRID_VALIDATION_FAILED`，重试整板；不外发切图 |
| 接缝在 risk–pass 之间 | 交付并记 `GRID_SEAM_RISK` |
| 板面 N×N 结构/比例不达标 | `GRID_VALIDATION_FAILED` |
| 图 1 失败 | `SEQUENCE_ANCHOR_FAILED`，不继续烧预算 |
| 第 i 帧失败 | 保留原索引槽位，继续后续帧，最终 `partial_failed` |
| 预算将超 | `BUDGET_EXCEEDED`（可附拆分建议，但**不自动**拆） |

## 四、产物

```text
workspace/iv_<task_id>_<revision>/
├── manifest.json            # image-artifact 1.1（风格/尺寸/接缝/执行/警告/错误）
├── delivery-package.json    # 本包清单（逐文件 sha256）
├── board.png                # 仅 Grid：kind=board, index=0
└── images/NN.png            # Grid=切图 tile（行优先）；Sequence=frame（索引不前移）
```

同目录下 `iv_<task_id>_<revision>.zip` 为可选打包产物。ZIP 内不含 `delivery-package.json`
自身；`archive` 哈希写回 `delivery-package.json`。

**部分失败**时：只登记真实存在的图（失败帧不进 `images[]`），失败信息在
`errors[].asset_id` + `validation.failed_indices`，`status=partial_failed`。
续跑产出新修订（`parent_artifact_revision` 指向上一版），已成功帧逐字节不变。

## 五、边界

- 只做**出图变体**。文案写作、平台发布、封面排版属于 Skill 02（`image-content-publishing`），
  本 Skill 不越界。
- 用户显式要求多个方向时只挑一个并说明理由，或报 `MULTIPLE_DIRECTIONS`，不混排。
- 不做"风格迁移到他人作品"这类擦边请求；涉及真实人物/品牌时按 `provider-compat.md §4`
  的安全要求处理。
- 自检用的 `mock_synthetic` 产物固定标注 `provider_type=mock_synthetic`，**不得对外交付**。

## 六、开发与验证

```bash
.venv/bin/python skills/image-variations/tests/run_all.py     # 9 个套件（含基线 24/24 与契约 48/48）
.venv/bin/python shared/check_baseline.py                     # 基线只读校验
```

证据与已知偏差记录在 `docs/PHASE_EVIDENCE.md`。
