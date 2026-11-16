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
