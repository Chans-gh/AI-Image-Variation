# AI Image Variation

基于 [SPEC 01 v1.3](specs/01-Image-Variations-Skill-v1.3.md) 的 Phase 1 开发实现。输入单张参考图、文本或图文，生成同一主体的 Grid 作品板或 Sequence 图片组，并完成原生尺寸验收、视觉检查交接、切图、清单与打包。

当前发布 **Skill 01 · 0.1.0 开发预览**。Grid 支持 2×2、3×3、4×4，Sequence 支持 4–16 张独立图片。两种模式共享十二个变化方向。正式生图需要对应型号和路由的已验证能力档案，以及宿主的实际视觉检查。 [验证记录](docs/validation.md)

提示词库为 **1.4.3**。视角、场景、时间天气、镜头语言和用途采用可逐格或逐帧验收的方向字段。 [提示词修订规范](specs/01-Image-Variations-Prompt-Revision-v1.4.md)

## 安装 Skill 01

从 Releases 下载 `image-variations-0.1.0.zip`，解压并将完整的 `image-variations/` 目录放入宿主的 Skill 搜索路径。包内包含 `SKILL.md`、方向规则、提示词、运行时和契约。 [GitHub Releases](https://github.com/Chans-gh/AI-Image-Variation/releases)

在解压目录使用 Python 3.11 或以上版本安装依赖，再运行离线演示。

```sh
python -m pip install -r image-variations/scripts/requirements.txt
python image-variations/scripts/run_task.py demo --mode grid --grid-size 3
```

WorkBuddy 与 DeepSeek Harness 通过标准 `SKILL.md` 和文件桥接接入，两端的实机安装验证尚待完成。 [宿主流程](skills/image-variations/references/host-workflow.md)

## 从源码运行

需要 Python 3.11 或以上版本。使用 uv 安装锁定的依赖。

```sh
uv sync --locked --extra dev
uv run --locked image-variations --help
uv run --locked pytest -q
```

离线演示不需要凭据或网络生图：

```sh
uv run image-variations demo --mode grid --grid-size 3
uv run image-variations demo --mode sequence --count 4 --branch progression
```

返回状态包含 artifact_path，其中有整板及 tiles，或 sequence、manifest.json 和 delivery-package.json。示例默认另建 ZIP；所有演示产物标记 synthetic_fixture=true。

## 实际使用

入口是 [SKILL.md](skills/image-variations/SKILL.md)。宿主使用完整规范提示词完成参数解析、主体锚定与创意计划；程序不会凭空生成一套通用画面冒充模型规划。

```text
P1-01 参数解析 → 按需 P1-02 归一化 → P1-03 主体锚点
→ Grid P1-04 / Sequence P1-06 计划
→ create 预检 → step 单次生图 → 宿主 P1-09 检查 → review
→ 下一帧 / 有限重试 / 图片与包清单交付
```

[宿主流程与命令](skills/image-variations/references/host-workflow.md) 说明完整输入与操作；[Provider 接入](skills/image-variations/references/provider-compat.md) 说明 GPT Image、Gemini、内置工具和能力验证。`examples/mock-grid/` 提供可运行的结构示例。

本机 `codex-image` 0.4.0 可通过 Images 路由请求 GPT Image 2.6。接入时按模型和端点验证原生尺寸，再创建能力档案。外部兼容接口使用用户选定的 HTTPS 端点，凭据通过环境变量名称注入。

## 原生分辨率

- Grid：2×2/3×3/4×4 默认向**整板**请求 1K/2K/4K，再切成 4/9/16 张。
- Sequence：4–16 张，每张默认原生 1K，可指定 2K/4K。
- K 档由具体模型与路由决定。缺少有效档位/比例组合时，在生图前报错；不将 auto、quality 或放大视为原生高分辨率。
- 原始返回像素与几何处理后像素分开记录。Grid 只允许每轴 ≤1% 的整除微调，原始与有效比例分别检查。
- 有图序列始终引用原图；纯文本序列首帧通过后，其余帧始终引用首帧。

## 构建安装包

```sh
uv run --locked python scripts/build_skill.py
```

产出 `dist/image-variations-0.1.0.zip`。安装包可在仓库之外独立运行。

## 仓库布局

```text
skills/image-variations/   独立 Skill、规范提示词、Python 运行时
shared/                   相对路径、图像产物和交付包校验器
contracts/                1.0 / 1.1 图像产物及能力档案契约
specs/                    00 架构、01 生图、02 内容生产、03 集成及提示词修订
tests/                    协议、尺寸、执行、恢复及安全测试
docs/                     验证记录、实现决策和开发待办
examples/mock-grid/       脱敏合成结构示例
.work/ artifacts/ dist/   本地任务、产物和构建包（不入 Git）
```

## 后续开发

Skill 02 将基于已完成的图片清单生成公众号和小红书素材包。当前仓库提供其规格及开发顺序。Skill 01 成功后可以推荐内容生产，由用户主动启动。 [开发待办](docs/ROADMAP.md) · [SPEC 02](specs/02-Image-Content-Publishing-Skill-v1.1.md) · [SPEC 03](specs/03-Integration-and-Artifact-Contract-v1.1.md)

本仓库沿用 MIT 许可。 [LICENSE](LICENSE)
