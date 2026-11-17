# DEPLOY — 把 Skill 01 装到别的宿主（Workbuddy 等）

宿主需要的只有四样：**技能目录 + 能执行 shell + Python + 读写本地文件**。
本文件说明要搬什么、怎么装依赖、怎么自检、出问题怎么定位。

## 一、最小分发集（4.6M）

```text
<REPO_ROOT>/
├── shared/                                 # 跨 Skill 通用件（哈希/安全路径/交付包/错误码）
├── Image_Variations_Dual_Skill_SPEC_v1.1/  # 冻结基线（只读，238 个文件）
├── skills/image-variations/                # 技能本体（SKILL.md + scripts + config + tests）
└── requirements.txt
```

`skills/image-variations/requirements.txt` 是同一份依赖的技能内副本，宿主按技能目录安装时用它可以。

**不要搬 `.venv/`**：venv 里记着创建时的绝对路径，换机器或换系统就会坏。让宿主自己建环境。

## 二、依赖

Python ≥ 3.9，Pillow ≥ 10：

```bash
pip install -r requirements.txt
```

已实测组合：Python 3.12.14 + Pillow 13.0.0；Python 3.9.6 + Pillow 11.3.0。

## 三、装完第一步：自检

```bash
python "$SKILL_DIR/scripts/run_task.py" --self-check --pretty
```

- `"ok": true` + 退出码 **0** → 环境可用，可以跑任务
- `"ok": false` + `"deployment_error"` + 退出码 **4** → 部署问题，按 `hint` 修

自检覆盖六项：Python 版本 / Pillow / 仓库根 / 基线 238 文件哈希 / 技能配置与 12 个方向 / workspace 可写。

## 四、宿主怎么触发

```bash
python "$SKILL_DIR/scripts/run_task.py" \
  --input "$TASK_JSON" --workspace "$WORKSPACE" --pretty
```

- `$SKILL_DIR`：本技能目录（宿主以它为相对路径基准）；`SKILL.md` §〇 写明了两个根的分工
- `$WORKSPACE`：产物目录，宿主需可写
- `$TASK_JSON`：任务描述，字段见 `SKILL.md` §二

## 五、两种部署形态

| 形态 | 布局 | 需要设置 |
| --- | --- | --- |
| **整仓（推荐）** | 保持上面三件套的相对位置 | 无（零配置） |
| **分离** | 技能放别处，`shared/` + 基线放 `vendor/` | `IV_REPO_ROOT=<vendor>`；基线再单独存放时 `IV_BASELINE_ROOT=<基线目录>` |

分离部署的目录可以这样长：

```text
/opt/wb/app/skills/image-variations/        ← 技能（深度随意）
/opt/wb/vendor/shared/                      ← IV_REPO_ROOT 指向 /opt/wb/vendor
/opt/wb/vendor/Image_Variations_Dual_Skill_SPEC_v1.1/
```

## 六、退出码：业务面和安装面分开

| 码 | 含义 | 面 |
| --- | --- | --- |
| 0 | 完成（可能带风险警告） | 业务 |
| 2 | 被错误码阻断 | 业务 |
| 3 | 参数/任务描述非法 | 业务 |
| **4** | **部署不完整** | **安装** |

0/2/3 的错误是 `{"ok": false, "error": {"code": ...}}`，`code` 取自冻结基线的
`references/error-registry.json`；**4 用 `{"ok": false, "deployment_error": {...}}`，不占用业务错误码**
—— "装错了"不属于"能力不足 / 预算不足 / 校验不通过"这三类任务失败，混进业务码会让宿主
把部署问题误判成模型问题，甚至触发无意义的重试。所有失败都不打印堆栈。

## 七、常见部署错误

| `stage` | 含义 | 处理 |
| --- | --- | --- |
| `bootstrap` | 候选目录里都没有 `shared/baseline.py` | 恢复整仓结构，或设 `IV_REPO_ROOT` |
| `dependencies` | 缺 Pillow | `pip install -r requirements.txt` |
| `python` | 解释器低于 3.9 | 换解释器 |
| `import` | 代码/依赖不完整 | 确认 `shared/` 与冻结基线随技能一起部署 |
| `baseline` | 基线缺文件或被修改 | 重新部署基线；**不要修改基线内容** |
| `skill_config` | 技能 `config/` 或方向注册表不可用 | 检查技能目录是否完整 |
| `workspace` | 产物目录不可写 | 给宿主写权限 |

## 八、宿主清单文件约定

宿主若要求特定清单文件名（例如 `skill.json`），只需在技能目录里加一层**极薄映射**：
`SKILL.md` 只写编排与流程，不知道任何宿主细节；业务规则全部来自冻结基线，本技能不复制它们。
