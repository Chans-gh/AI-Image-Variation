# Image Variations 双 Skill 实现仓库

[![CI](https://github.com/Chans-gh/AI-Image-Variation/actions/workflows/ci.yml/badge.svg)](https://github.com/Chans-gh/AI-Image-Variation/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](requirements.txt)

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
- 本轮全链路证据用 Mock / 合成 Provider（`tests/mock_provider.py`、`iv_skill/synthetic.py`）打通；
  真实密钥到位后只补适配器实现（字段名待真实响应确认）。

## 现在的状态

五个阶段全部落地并有测试证据（阈值校准仍待真实模型）：

| 阶段 | 内容 | 入口 |
|---|---|---|
| 0–1 | 仓库骨架、契约与夹具、48 项契约测试 | `tests/run_contract_tests.py` |
| 2 | Provider 能力抽象、两协议适配、错误归一、SSRF 防护、排产预检 | `iv_skill/preflight.py` |
| 3 | Grid 一次成板、D-24 接缝评分、确定性切图、整板重试与预算 | `iv_skill/executor.py` |
| 4 | Sequence 计划先行、图 1 锚点、失败保槽与续跑、近重复处理 | `iv_skill/sequence.py` |
| 5 | 产物清单、交付包与 ZIP、`SKILL.md` 入口、JSON CLI | `scripts/run_task.py` |

用法见 [`skills/image-variations/SKILL.md`](skills/image-variations/SKILL.md)。最小示例：

```bash
echo '{"task_id":"demo","output_mode":"grid","grid_size":2,"direction":"light",
       "input_type":"text_only","subject":"一只浅灰陶瓷杯","provider":{"type":"mock_synthetic"}}' \
  > /tmp/task.json
.venv/bin/python skills/image-variations/scripts/run_task.py --input /tmp/task.json \
  --workspace ./workspace --pretty
```

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

## 环境准备

```bash
git clone git@github.com:Chans-gh/AI-Image-Variation.git
cd AI-Image-Variation
python3 -m venv .venv && . .venv/bin/activate
python -m pip install -r requirements.txt        # 只需要 Pillow
```

实测环境：Python 3.12.14 + Pillow 13.0.0；Python 3.9.6 + Pillow 11.3.0 亦全绿。

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

> **发布检查清单**：`git status` 干净不代表仓库完整。发布前必须 `git clone` 到空目录，
> 在**干净克隆**里跑一遍 `shared/check_baseline.py` 与 `tests/run_all.py`——
> 本项目第一次发布就因此发现 `.gitignore` 的 `iv_*/` 把整个 `iv_skill/` 核心包静默漏掉了。

## 持续集成

`.github/workflows/ci.yml` 在每次 push / PR 上跑两个任务：

| 任务 | 内容 |
|---|---|
| `tests`（Python 3.12） | 基线只读校验 → 基线自带契约 24/24 → Skill 01 全量 9 套件 → Grid/Sequence 端到端冒烟 |
| `tests-py39`（Python 3.9，ubuntu-24.04） | 基线只读校验 → Skill 01 全量 9 套件 |

`main` 分支已开启保护：**两个任务必须全绿、分支必须与 main 同步、禁止强推与删除**，
改动请走「开分支 → 提 PR → 等 CI → 合并」。

## 许可

[MIT](LICENSE) © 2026 Chans-gh
