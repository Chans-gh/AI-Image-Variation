# Image Variations 双 Skill 实现仓库

本仓库是 `Image_Variations_Dual_Skill_SPEC_v1.1` 规范包的**实现层**，按 SPEC 00 §00.4 的单仓库双 Skill 结构落地。

| 目录 | 作用 | 写入权限 |
|---|---|---|
| `Image_Variations_Dual_Skill_SPEC_v1.1/` | 冻结规范基线（只读） | **禁止修改**。`shared/baseline.py` 用哈希清单校验 |
| `skills/image-variations/` | Skill 01 实现（本次范围） | 可写 |
| `skills/image-content-publishing/` | Skill 02（不在本次范围） | 预留 |
| `shared/` | 跨 Skill 通用件：哈希、安全路径、交付包、错误码 | 可写 |

## O-02 决议（本轮已确认）

**方案 B：宿主无关 Python 核心库 + `SKILL.md` 编排形态**

- 核心库：`skills/image-variations/scripts/iv_skill/`，纯 Python + Pillow，不绑定任何宿主 API。
- 入口壳：`run_task.py`（JSON 进 / JSON 出）、`SKILL.md`（DSH 发现与编排，只写流程不写业务规则）。
- `builtin` 仅提供可注入接口；DSH 无内置生图时显式返回 `BUILTIN_UNAVAILABLE`，不静默降级。
- 本轮全链路证据用 Mock Provider（`tests/mock_provider.py`）打通；真实密钥到位后只补适配器实现。

## 单一真相源（不复制基线）

契约、方向注册表、错误码**不在本仓库复制**，运行时从基线读取：

| 数据 | 基线位置 |
|---|---|
| 契约 Schema | `contracts/*.schema.json` |
| 12 方向规则 | `references/directions/registry.json` |
| 错误码 | `references/error-registry.json` |
| Prompt 模板 | `references/grid-prompt.md` / `sequence-prompt.md` |

`shared/baseline.py` 负责路径解析与只读校验，并提供哈希清单防止基线被隐式修改。

## 阈值全部外置

`skills/image-variations/config/iv.config.json` 是唯一阈值来源（`tile_edge_min_px=1024`、`tile_edge_warn_px=768`、`tile_edge_hard_min_px=512`、`seam_pass=0.85`、`seam_risk=0.60`、Grid 尝试上限、单帧重试上限、预算）。规范已声明这些值**未经真实模型校准**，代码不得内联这些数值。

## 快速自检

```bash
# 基线只读性与基线测试（保持 24/24 全绿）
.venv/bin/python shared/check_baseline.py
.venv/bin/python Image_Variations_Dual_Skill_SPEC_v1.1/tests/validate_example_contracts.py

# Skill 01 契约与夹具
.venv/bin/python skills/image-variations/tests/run_contract_tests.py

# Skill 01 全量
.venv/bin/python skills/image-variations/tests/run_all.py
```

## 证据与阶段

每阶段的测试输出与已知缺口记录在 [`docs/PHASE_EVIDENCE.md`](docs/PHASE_EVIDENCE.md)。
