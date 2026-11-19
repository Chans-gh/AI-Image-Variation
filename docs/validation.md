# Skill 01 验证记录

开发预览 `0.1.1` · SPEC 01 `v1.3` · 提示词库 `1.4.3` · 2026-11-19

## 软件回归

- 全量 154 项 Python 测试覆盖请求、十二方向规则、能力档案、原生尺寸、两类适配器、视觉审核交接、切图、恢复、哈希及包校验。
- 合成 Grid 覆盖 2×2、3×3、4×4。合成 Sequence 覆盖 `variants`、`progression`、`narrative`，含原图或首帧引用及失败帧恢复。
- 独立安装 ZIP 已在仓库外运行 3×3 Grid 和 4 帧叙事 Sequence 的 CLI 演示。演示资产带 `synthetic_fixture` 标记。
- CI 的 `tests` 检查执行回归和安装包冒烟。保护规则沿用的 `tests-py39` 检查验证安装程序会按元数据拒绝 Python 3.9。运行版本要求为 Python 3.11 或以上。
- `0.1.1` 提升 Pillow 和 filelock 的最低修复版本，源码与安装 ZIP 使用同一份依赖声明。 [Pillow](https://pypi.org/project/pillow/13.0.0/) · [filelock](https://pypi.org/project/filelock/4.2.1/)

## 实际图像回归

- 参考图提示词回归已覆盖十二方向的 2×2 和 3×3。视角、场景、天气、镜头与用途有逐格复测，另有 3×3 网格和实际裁切检查。
- 3×3 回归共保留 22 张原始整板。构图样本仍出现细分隔线，部分镜头的画面边界、用途头像和方裁的余量需要改善。测试照片、原始整板和逐次提示词记录保存在本地。
- OpenCodex 参考图试验统一请求 `1024×1280 / low`，3×3 样本实际均为 `1122×1402`，服务端回报包括 `low`、`medium` 和 `high`。此路由的能力档案保持 `unverified`。

## 正式验收进度

真实 GPT Image 与 Gemini Nano Banana 的原生档位、真实 4×4 与 Sequence 全流程、实际费用，以及 WorkBuddy / DeepSeek Harness 实机安装仍待验收。详见 [开发待办](ROADMAP.md) 和 [SPEC 01 的尺寸验收矩阵](../specs/01-Image-Variations-Skill-v1.3.md#179-验收矩阵模拟适配器--真实模型--两种宿主)。
