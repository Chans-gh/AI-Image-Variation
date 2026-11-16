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
