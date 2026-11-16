# Image Variations Skill — Phase 1 SPEC

**版本：** v1.1（双 Skill 集成兼容版）  
**日期：** 2026-11-16  
**目标：** 定义一款全新构建的、可复用的多图变体生成 Skill；不是对原网站或原功能代码的修改。  
**状态标识：** **[已确认]** 来自本轮需求确认；**[设计方案]** 是为开发可落地而补充的建议，未被用户单独逐项拍板；**[待二期]** 暂不实施。

> **交付性质**：本文是供开发 AI / 工程团队使用的产品与技术行为规范，不是已经实现的 Skill，不包含可用的模型凭据或实测生成效果。本 v1.1 版以 v1.0 为功能基线，**只补充与第二个 Skill 的结果交接和完成后推荐**；第二阶段的内容生产行为以 SPEC 02 为准。

## v1.1 变更摘要

- **保留**原 v1.0 的三个输入模式、12 种方向、Grid/Sequence 两种输出、默认比例与分辨率、两类模型兼容协议、切图与质量检查。
- **补充**结果 Artifact 版本标识、统一 `images[]` 清单的 `kind` 类型、图片哈希及供第二阶段读取的只读消费视图。
- **补充**第一阶段完成后推荐使用第二阶段的交互行为；**推荐不自动执行 Skill 02**。
- **调整**Skill 包部署目录，作为一个 Git 仓库里的 `skills/image-variations/`；所有交接字段详见 SPEC 03。
- **维持**第一阶段不生成公众号或小红书正文；旧功能包的笔记逻辑仍不进入第一阶段。

## 0. 执行摘要

Image Variations Skill 将用户输入的 **单张参考图 / 文本 / 单张参考图+文本**，转换为围绕一个主体、一个变化方向生成的系列图片。支持两种完全独立的出图模式：

- **Grid Mode（网格图）**：2×2、3×3、4×4，**一次生图请求**生成一张完整、无缝、等格的作品板；第一阶段**必须**提供整图与自动切出的 N² 张单图。
- **Sequence Mode（连续出图）**：4–16 张，系统先生成一组按序且互不雷同的 **N 条独立提示词**，再生成 N 张独立图片；三种序列分支（离散变体 / 渐进演变 / 情节叙事）每次任务只选一种，不混合。

两种输出均支持原包的全部 12 个变化方向；用户单次任务只选一个方向。优先调用宿主提供的生图能力，或按用户配置调用 **GPT Image 兼容协议 / Gemini Nano Banana 兼容协议**；缺能力时明确报错，不静默降级。**[已确认]**

## 1. 产品目标、非目标与交付边界

### 1.1 产品目标（P0）

1. 用一套 Skill 指令入口统一完成参数解析、主体锚定、变化规划、提示词生成、模型调用和交付。
2. 保持主体身份/核心特征的稳定性；把差异明确限定在选中的一个变化方向内。
3. 网格图采用单次生成，后处理保证输出单图在**像素尺寸和比例上一致**，具备规则切图坐标。
4. 序列图每张图具有独立且有序的变化点；纯文本模式以首次生成的图 1 作为后续一致性锚点。
5. 支持可扩展的方向规则库、协议适配器和任务输出清单；提供透明错误及有限重试。

### 1.2 第一阶段明确不做

- 不修改、迁移或依赖原网站 `/tools/image-variations/`、原后端或鉴权系统。
- 不把「多方向混合」作为功能；不在单次任务中混合多种序列分支。
- 不支持 2×3、1×9 等非 NxN 网格；不支持超过 16 张的序列。
- 不自动生成小红书笔记、视频、动态 GIF、故事脚本或营销发布流程（原包的笔记生成属于历史参考，**不进入 Phase 1**）。
- 不承诺所有供应商均支持任意比例和 4K；不可用时返回可操作的错误。
- 不把网格生成改成先生成若干单图后拼接；**必须维持一次模型生成完整网格**。

### 1.3 架构假设

**[设计方案]** 采用 Agent Skills 风格的 `SKILL.md` + `references/` + `scripts/` 文件夹封装工作流。运行依赖宿主提供文件、图片模型、密钥保管及任务执行能力；如果宿主没有这些能力，需要另行接入运行适配器，Skill 文件本身不等于一个生图模型服务。该封装方案可更换，不影响本文业务规则。

## 2. 核心术语与约束优先级

| 术语 | 定义 |
| --- | --- |
| Subject / 主体 | 本任务中始终被识别为同一人、动物、产品、物件、空间或主题的核心对象。 |
| Consistency Anchor / 一致性锚点 | 原图或稳定主体设定；纯文本序列在图 1 出来后，图 1 成为实际图像锚点。 |
| Direction / 方向 | `state` 等 12 种之一；每任务**恰好一个**。 |
| Branch / 序列分支 | `variants`（离散变体）、`progression`（渐进演变）、`narrative`（情节叙事）；仅用于 Sequence。 |
| Grid Board / 作品板 | 单个模型输出文件，内含 N 行 N 列，后续确定性切分。 |
| Resolution Tier / 分辨率档位 | 产品层的 1K / 2K / 4K 质量/目标大小档位；**不等于固定的像素宽高**。 |
| Tile / 单格 | 网格板中的一个矩形单元，所有 Tile 具有完全相同的像素宽高。 |
| Provider Adapter / 模型适配器 | 将统一生成请求映射为特定服务商参数，并解析返回内容。 |

**优先级（从高到低）**：平台安全/模型政策与不可违反的接口能力 → 用户显式选择的模式、方向、张数、比例、档位、分支和主体限制 → Skill 的一致性与输出结构要求 → 方向规则的允许变/必须稳/禁止项 → 通用默认值 → 原始文本中的非结构化平台参数。用户在自由文本里明确写出的规格应被解析为产品参数；与已选结构化参数冲突时，结构化参数优先，必要时提示冲突。**[设计方案]**

> **变化规则覆盖原则**：主体**核心识别**始终不变；选中方向允许变化的非核心元素可以变化。例如 `styling` 允许换服饰，不能被通用的“服装保持不变”误锁；`style` 可以换美术风格，但不能换主体。Sequence 的分支规则可覆盖原包仅针对 Grid 的“不能连续动作/不必连续”限制。

## 3. 输入、参数与缺省规则

### 3.1 输入类型 [已确认]

1. **image_only**：一张 PNG/JPEG/WebP 参考图，可无文字；由图像理解生成基础主体设定。
2. **text_only**：仅有非空文本，先抽取并固定主体设定。
3. **image_plus_text**：一张图 + 文字；图像锁定主体，文字用于补充指定变化与允许的细节。

至少有一张图或有效文本；V1 仅接收**一张原图**，不支持多参考图的角色融合。**[设计方案：输入张数限制与原包一致]**

### 3.2 参数矩阵

| 参数 | Grid Mode | Sequence Mode | 状态 |
| --- | --- | --- | --- |
| `output_mode` | `grid` | `sequence` | [已确认] |
| `grid_size` | 2 / 3 / 4（对应 4/9/16 格） | 不适用 | [已确认] |
| `image_count` | N²（推导，不可单独设） | **4–16** 整数 | [已确认] |
| `direction` | 12 类之一 | 12 类之一 | [已确认] |
| `branch` | 不适用（离散网格） | 3 类中的一种 | [已确认：存在3种且不混合；代码命名是设计方案] |
| `aspect_ratio` | 有原图继承，无图 3:4 | 同左 | [已确认] |
| `resolution` | 2×2→1K；3×3→2K；4×4→4K | 默认 1K | [已确认] |
| 分辨率覆盖 | 可指定 1K/2K/4K | 可指定 1K/2K/4K | [已确认] |
| 图像输出 | 一张网格板 + N² 张自动切图 | N 张独立图片 | [已确认] |
| 二次打包 | 支持整板与切图打包 | 支持序列打包 | [已确认] |
| 模型来源 | 内置/用户配置 | 内置/用户配置 | [已确认] |

**[设计方案：未显式指定时的启动默认]** `output_mode=grid`、`grid_size=3`、`direction=state`、`image_count=4`（仅 sequence）、`branch=所选方向的默认偏向`。这几个启动默认是方便自动化的**建议**，不同于用户已确认的「网格档位映射、文本默认 3:4、序列默认 1K」。

### 3.3 比例继承

- 有图：读取已纠正 EXIF 朝向后的原图 **宽/高**，作为目标单图比例与整张 NxN 网格板比例；用户明确要求新比例时以其为准。
- 无图：默认 **3:4**；若明确指定其他比例，用显式比例。
- 不得为了适配模型**悄悄裁剪主体、改变比例或换档位**。不支持该比例时在调用前报 `UNSUPPORTED_RATIO`，说明模型能力和可选的替代比例。
- **[设计方案]** 比例以宽:高表示，支持规范化如 `3:4`、`4:3`、`1:1`、`16:9`、`9:16`；其他比例在对应模型支持精确尺寸时可以使用。模型返回比例必须实测；误差超阈值不得冒充满足规格。

### 3.4 分辨率语义（必须避免误读）

- **Grid 的 1K / 2K / 4K 指完整作品板的请求档位**，不能宣称每格分别具有 1K / 2K / 4K。
- **Sequence 的 1K / 2K / 4K 指每张独立图片的请求档位**。
- Adapter 负责把档位翻译为服务商的 `size`、`imageSize`、`quality` 等实际字段；`requested_resolution` 与 `actual_width/height` 都记录在结果里。
- 不允许把「1K」武断固定成 1024×1024，也不允许承诺两个模型的 4K 输出像素完全相同。模型支持能力先查询/声明，再做生成前校验。

## 4. 用户工作流及路由

### 4.1 交互主链

```text
用户输入 → 识别输入类型 → 解析显式要求与默认参数 → 校验模型能力
        → 构建主体锚点 → 选择唯一方向 → 制定变化计划
        ├─ Grid：N² 个格位变化点 → 一条完整网格 Prompt → 一次生成网格板
        │        → 验证网格 → 规范化边长 → 自动切出 N² 张 → 返回/打包
        └─ Sequence：一个分支、N 个有序变化点 → N 条独立 Prompt
                 → 生成图 1（无图时建立锚点） → 其余按图 1 / 原图锚定
                 → 逐张验证 → 返回有序图片组 → 按需打包
```

### 4.2 指令识别 [设计方案]

- “3×3 网格、九宫格” → `grid_size=3`；“连续输出 9 张、九张独立图片” → `sequence,image_count=9`。
- 仅说“九张”而无法判断拼板还是独立文件时，优先询问一次，不擅自把 9 张解释为九宫格。
- 指定“按动作推进/逐渐变亮/一个故事的分镜”分别优先解析为 `progression` / `progression` / `narrative`；未指定时取方向默认分支。
- 用户选择多个主方向时提示只可选一个；**不自动混用**。不应仅因文本中提到“摄影+雨天”等自然描述，就认定用户选择了两个变化方向；自然描述可作为固定背景约束。

### 4.3 提交与进度 [设计方案]

创建任务后返回 `job_id`，支持 `queued → planning → generating → validating → postprocessing → completed`；失败使用 `failed`，序列有可用部分结果而仍有失败项时为 `partial_failed`。长任务展示 `completed_count / total_count`、每张生成状态和失败原因。缺模型能力或缺 Key 应在实际生成前同步发现。

**[已确认：v1.1 新增联动规则]** 仅在图片生成、验证、Grid 自动切图（如适用）和结果 Manifest 全部成功后，可以在结果交付时**推荐一次**继续使用 `Image Content Publishing Skill` 生成公众号文章或小红书笔记。推荐动作**不得创建内容任务、执行研究或产生第二阶段模型调用**；用户主动选择后才调用第二个独立 Skill。若用户明确只要图片，不反复推荐；若网格校验/切图未通过、序列部分失败，则默认不做“完整内容生产”推荐。历史产物可以在后续单独触发第二阶段。

## 5. 变化方向体系（12种，全部支持双输出）

12 种方向来自参考包 `02-完整提示词.md`，保留其核心分类与规则，作为独立、可增量扩展的数据注册表（版本化），而不是写死于任务控制流程。**[已确认：首批全保留]**

| 方向 key | 名称 | 主要可变项 | 主体/内容稳定锚点（简写） |
| --- | --- | --- | --- |
| `state` | 状态拓展 | 动作、姿态、表情、操作阶段、使用状态 | 身份、造型、场景主题、光影/画面风格 |
| `view` | 视角拓展 | 正侧背、俯仰、机位、景别 | 身份、服饰/结构、场景、光照时间 |
| `scene` | 场景拓展 | 地点、环境、道具、使用情境 | 主体核心、视觉风格、镜头语言 |
| `detail` | 细节拓展 | 整体—局部、工艺、材质、微观部件 | 同一来源、材质逻辑、色彩/美术体系 |
| `light` | 光影拓展 | 光源方向、软硬、色温、明暗/阴影 | 主体、动作、场景结构、主构图 |
| `composition` | 构图拓展 | 重心、留白、占比、层次、对称 | 主体、状态、场景、光影和风格 |
| `style` | 风格拓展 | 摄影、电影感、插画、3D、艺术材质 | 身份、核心结构、主体内容/世界观 |
| `styling` | 造型拓展 | 服饰、妆发、配饰、包装、陈列 | 身份五官、核心结构、场景主视觉 |
| `weather` | 时间天气拓展 | 时间、季节、天气、空气感 | 主体、场景逻辑、世界观和画面质感 |
| `lens` | 镜头语言拓展 | 焦段、景深、透视、摄影质感 | 身份、造型、场景主题、时间/美术风格 |
| `usage` | 用途拓展 | 封面感、主图感、留白、视觉中心 | 身份、内容、品牌感；不生成文案/UI |
| `narrative` | 情节拓展 | 故事节点、互动关系、事件阶段 | 身份、世界观、造型、整体风格 |

每条方向记录建议至少含：`key`、`label`、`version`、`allowed_changes[]`、`must_preserve[]`、`avoid[]`、`variation_examples`、`branch_defaults`、`grid_rules`、`sequence_rules`。保留原资料的细粒度文字作为 `references/directions/*.md`，但将原提示词里“Grid 专属不连续”内容放到 `grid_rules`，避免误伤 Sequence。

### 5.1 Sequence 的三种分支

| 分支 | 目标 | 排列逻辑 | 检查重点 |
| --- | --- | --- | --- |
| `variants` 离散变体 | 同一主题下的 N 个独立且明显不同方案 | 变化点覆盖面最大化，不要求因果顺序 | 差异显著、不重复、不换主体 |
| `progression` 渐进演变 | 让一个可控指标逐步变化 | 起点→过渡→终点，阶段顺序明确 | 阶段单调/合理、连续、跨度足够 |
| `narrative` 情节叙事 | 同一世界观中的有序情节/分镜 | 起因→发展→转折→结果（按 N 扩展） | 人物/物品关系、空间/动作逻辑连贯 |

**[已确认]** 所有 12 方向在 Sequence 下均可使用这 3 个分支；每个任务只选**一个**分支。不存在「情节方向才能叙事」或「风格方向不能渐进」的硬限制。

**[设计方案：各方向的默认偏向，可配置]**：`state=variants`、`view=variants`、`scene=variants`、`detail=progression`、`light=variants`、`composition=variants`、`style=variants`、`styling=variants`、`weather=progression`、`lens=variants`、`usage=variants`、`narrative=narrative`。用户明确选择另一分支时准许覆盖，不混合。

### 5.2 分支在非典型方向中的应用示例

- `style + progression`：同一主体从写实到逐步加强的插画化，不是一开始就随机切换 16 种互不相关的风格。
- `view + narrative`：故事推进主要由镜头顺序表达，环境、服饰、事件保持统一，不能悄悄变成 `scene` 方向。
- `light + narrative`：一个事件中的光照情绪层次推进，但主变化仍是光影，不用引入全新的场景。
- `narrative + variants`：来自同一故事世界的多个独立片段，允许非线性，保持核心角色与世界观。

## 6. 主体一致性与文本归一化

### 6.1 主体设定

- 人物/动物：身份、脸型五官、发型外轮廓、体态、年龄感、识别性特征（不得在未要求时替换身份）。
- 产品/物件：核心外形、比例、材质识别、品牌感、关键部件；方向允许的包装/陈列等可变化。
- 空间/场景：空间布局逻辑、主视觉元素、色彩体系、世界观/整体美术方向。
- 用户明示的外观要求优先进入 `identity_anchor`；模型补足的缺失属性必须形成单一稳定设定，不能每图重抽。

### 6.2 有图场景

将原图作为全组稳定视觉基准，每次独立生图请求都携带原图（若适配器支持的图像编辑/参考图输入）。不得通过“前一张图→后一张图”的默认接力造成漂移。明确需要阶段推进时通过**变化计划/文字**控制顺序，而不是替换主锚点。**[已确认：保持统一锚点；具体请求组织是设计方案]**

### 6.3 纯文本 Grid

归一化描述（需要时）→ 构建稳定的文本身份锚点 → 单条 Grid Prompt 中要求所有 N² 格共享统一主体设定。无原图时不先生成单图，也不为网格拆分单独请求。**[已确认]**

### 6.4 纯文本 Sequence

1. 首先使用固定的 `identity_anchor + frame_01_change` 生成第 1 张；第 1 张同时是**序列的第一张结果**，不得作为额外隐藏的第 0 张。
2. 校验图 1 有效后，将图 1 设为 `anchor_image`；图 2–N 全部引用图 1，而不是图 i−1。
3. 每张 Prompt 都携带相同的 `identity_anchor`、统一世界观与对应 `frame_change`。若 `branch=progression/narrative`，加入阶段摘要确保前后关系。
4. 图 1 失败：不能继续；按重试策略重生图 1。图 i 失败：保留既有成功图，允许针对 i 补生成。
5. 若所选模型不支持参考图输入：**预校验报 `REFERENCE_REQUIRED`**，不得悄悄把后续帧降级成独立文生图。

> 这样“以第一张图为一致性”既是用户确认的业务规则，也是 Sequence 纯文本模式的硬性能力要求。

### 6.5 输入归一化 [设计方案]

参考旧包的 `normalized_prompt`、`subject_type`、`subject`、`identity_anchor`、`scene`、`style`、`lighting`、`composition`、`quality`、`keep_stable`、`discarded` 字段。对于明显冗长或含别家模型语法的纯文本输入，尝试调用宿主文本模型归一化；保留原文与归一化输出，使用 JSON schema 检验；解析失败不静默清掉用户的必要内容。简单输入可直接提取。不得把用户的 3:4 等有效显式需求当作应忽略的垃圾参数；应先解析结构化需求，再清理生图平台语法。

## 7. Grid Mode：生成、无缝网格与自动切图

### 7.1 单次生成要求 [已确认]

1. 生成前先计划 N² 个不重复主变化点，按**从左到右、从上到下**依次关联画格。
2. 在**一条**生成 Prompt 中同时表达网格总规模、主锚点、每格变化点、方向规则、禁止内容与目标比例；模型调用 `n=1`（语义上的单个最终输出）。
3. 完整输出必须为单张规则 NxN 网格图，不是 N² 张模型输出，也不是先分别生成再拼接。
4. **无缝**在本 SPEC 中指：**零可见间距、零边框、零衬底、零投影、零序号标签；画格尺寸统一且边界可按坐标直接切分**。画格内容各自完整，主体不得跨格。这条**覆盖**原包“纤细白色间距”的旧约束。
5. 格内独立比例 = 整张网格板比例（因为 N×N 且等格）；不得生成每格 3:4 却让整体成为 1:1 的拼贴版。

### 7.2 尺寸整齐性与像素规范化

- 设网格输出宽高为 `W,H`，要进行均匀无损几何切割，必须有 `W % N == 0` 与 `H % N == 0`；各格为 `tileW=W/N`、`tileH=H/N`。
- **[设计方案]** 当模型输出边长不整除 N，但画板结构/边缘检查通过时，可通过**极小幅的整图重采样**调整到最近的 `N` 倍数，单轴调整幅度不得超过原边长的 1%；同时检验比例误差不超过 1%。记录原始/规范化尺寸；禁止以裁剪画格中的主体来凑数。
- 当明显网格线偏移、某格空缺、跨格、出现厚边/白缝、总行列数错误时：**重采样并不能修复内容结构**，必须视为质量失败并有限重试，不允许假装切图成功。
- 所有自动切图必须**使用规范化后单张网格板的确定性整数边界**，输出第 `row* N+col +1` 张。保证格子的**像素宽高完全相同**；与目标比例的允许误差必须被记录且不得高于阈值。
- 只允许保真重采样进行几何规格对齐；不得通过拉伸明显错误比例来“满足”用户比例。

### 7.3 网格品质检查（P0）

**硬条件**：NxN 结构、N² 个可识别画格、无框无缝、无遮挡跨格、分割区域均等、输出图像可解码、真实比例满足容差。**语义检查**：所有画格主体一致、对应方向差异显著、无大面积重复、无文字/UI/水印等意外元素。

**[设计方案]** 采用两级验证：①确定性像素/尺寸/切图检验；②可用图像理解模型时进行语义视觉核验。最多进行 **2 次额外整板重试**（即总计最多 3 次网格生成请求，参数可配置）。若最终无法通过硬条件，返回 `GRID_VALIDATION_FAILED`，不把不合格文件标为合格切图。视觉一致性属于概率性评价，SPEC 约定验证与失败处理，不声称单次模型出图能数学保证完美结构。

### 7.4 Grid 返回物

- `grid_board.png`：最终合格的完整网格图。
- `tiles/01.png` … `tiles/NN.png`：按行优先的 N² 张自动切图。
- `manifest.json`：模式、方向、模型、档位、实际像素、切图坐标、验证结果、提示词版本等元数据。
- 用户可请求 `.zip` 二次打包；包内保留原作品板、切图、清单。**[设计方案：具体文件名/格式]**

## 8. Sequence Mode：分支规划与逐张提示词

### 8.1 计划必须先于模型调用

`SequencePlanner` 在调用图像模型前产出一个包含 N 个 `FramePlan` 的结构化计划，每个 `FramePlan` 至少包含：`index`、`change_point`、`visual_description`、`locked_elements`、`phase_label`（分支需要时）、`prompt`。全组的 `direction`、`branch`、`identity_anchor` 保持唯一。

### 8.2 生成规则 [已确认 + 实现方案]

- 单次任务只允许一种 `direction` 与一种 `branch`；`N ∈ [4,16]`。
- **N 条独立提示词**，每张图 `n=1` 的独立出图请求；不能只用同一个 Prompt 调 N 次，也不能通过一次 API 的 `n=N` 取巧代替差异化规划。
- `variants`：N 个离散不重复点，覆盖强弱层次；输出有索引顺序但不要求因果关系。
- `progression`：先定义单一主变量的阶段轴；N 个节点由起点渐进到终点，不允许大幅跳变又回退，除非用户明确要求往返过程。
- `narrative`：定义同一故事世界内的 N 个关键时刻；前后动作、物品、空间、服装等需可解释地连续，不能每张另起一个故事。
- 序列的统一画幅比例、分辨率目标和主体锚点在所有 N 张图上一致；实际像素必须分别记录，必要时仅在允许的轻微阈值内对齐，不得静默改变比例。
- 独立的每张生成失败时有限重试；保留成功图，最终返回完整成功或部分失败，不伪造成功数量。

### 8.3 Prompt 的共用与差异部分

每张图由下面的逻辑组件组装：

```text
GLOBAL_RULES（主体核心识别 + 单方向 + 禁止换主体 + 画幅/档位目标）
+ IDENTITY_ANCHOR（原图描述或图 1 锚点描述）
+ DIRECTION_RULES（允许变、必须稳、避免；当前唯一方向）
+ BRANCH_RULES（variants / progression / narrative 其中之一）
+ FRAME_PLAN[i]（第 i 张独有的主变化点与画面目标）
+ ORDER_CONTEXT（阶段摘要，仅用于 progression / narrative）
+ OUTPUT_RULES（单张成图，不要编号、分镜边框、UI或水印）
```

禁止把 `FramePlan[i]` 的变化点复制成完全相同的 N 条 Prompt。模型拒绝某一帧时记录实际错误，保持它在序列中的原索引，以便补生成。

### 8.4 结果呈现与打包

返回一个**有序图片列表**，每张具备稳定索引、可单独下载、可查看是否通过验证；可打包输出 `sequence/01.png…N.png + manifest.json` 的 zip。失败帧保留槽位信息，不能让后续序号前移而导致故事顺序错乱。

## 9. Provider Adapter 与用户配置模型

### 9.1 两类来源 [已确认]

- `builtin`：宿主已有的生图工具。使用宿主工具原生参数及认证；缺少工具时返回 `BUILTIN_UNAVAILABLE`。
- `external`：用户填写兼容类型、接口地址、API Key、模型 ID；Skill 通过适配器调用并解析结果。首批协议为 `openai_image_compatible`、`gemini_image_compatible`。

### 9.2 能力抽象

`ModelCapability` 至少包含：`text_to_image`、`reference_image_input`、`image_edit`、`supported_ratios`、`supported_resolution_tiers`、`custom_dimensions`、`supports_async_result`、`max_input_bytes`、`response_image_formats`。能力信息来自适配器已知声明和连接/预检；不能仅因为接口叫“OpenAI compatible”就假定所有 GPT Image 参数均支持。

**必须检查**：纯文本 Sequence 要求能接受图 1 作为参考的接口；图生图要求参考图能力；Grid/Sequence 选定比例、档位必须可表达。不满足则返回 `MODEL_CAPABILITY_UNSUPPORTED` / `REFERENCE_REQUIRED` / `UNSUPPORTED_RATIO` / `UNSUPPORTED_RESOLUTION`，**不自动静默降级**。**[已确认]**

### 9.3 GPT Image 兼容适配器

- 使用对应实现的**图像生成**和**图像编辑/参考图**路由；对于官方 API，其调用及参数随模型不同而不同。参考图输入不能被强行假设为所有供应商均支持 `image_urls` JSON 字段。
- 将统一的 `prompt`、实际目标尺寸、参考图片、格式等映射到该服务支持的字段；保存服务响应图片字节或授权访问的 URL，支持 base64 结果解码。
- GPT Image 服务的 `size` 可能采用宽×高字符串且与质量 `quality` 独立；`1K/2K/4K` 不等同于一个通用的官方 `resolution` 参数。

### 9.4 Gemini Nano Banana 兼容适配器

- 使用 Gemini 的原生图片生成/编辑接口、文本与图片输入结构，支持其当前 API 形态（例如 `generateContent` / 新版 `interactions`，由适配器版本声明决定）。
- 将 `aspect_ratio`、`image_size` / `imageSize` 等转换到选定 Gemini API 的真实字段；从模型返回的图像 part 中解析图片字节。
- Nano Banana 是**模型族**而非单个固定型号；模型能力、4K 与可选比例依具体模型版本判定，不绑定过时模型名。

### 9.5 配置、安全与失败策略 [设计方案]

用户配置建议字段：`provider_type`、`base_url`、`api_key_secret_ref`、`model_id`、`request_timeout_sec`、`capability_override`（仅管理员/开发环境）。连接测试只验证配置和权限，不触发无谓的正式批量生成。

- **API Key 不入提示词、不写进 Skill 包、不回显到日志/manifest/zip、不出现在 URL 参数中**；需要持久化时使用宿主密钥库加密，用户可更新/删除。
- 对自定义 Endpoint 仅允许 HTTPS，阻止内网、环回、元数据地址和重定向到内网的 SSRF；统一校验可访问目标及证书。
- 不把用户原图发送给未选定模型供应商；界面/任务结果明确所用 Provider 和模型，不记录 API Key。
- 第三方 HTTP 401/403 报鉴权错误，429 或网络超时可**有限重试**并遵循退避/`Retry-After`；参数不支持和安全拒绝不重试为同类请求，更不可自动换 Provider。
- 每个独立生成单元最大 **2 次额外重试**（设计默认，可配置）；生成次数与实际调用/成本需可观测。

### 9.6 协议参考（开发时以官方文档/选定模型最新能力为准）

- OpenAI Image Generation: https://developers.openai.com/api/docs/guides/image-generation
- OpenAI Image API Reference: https://developers.openai.com/api/reference/go/resources/images/methods/edit
- Gemini Image Generation: https://ai.google.dev/gemini-api/docs/image-generation
- Gemini Generate Content Image Config: https://ai.google.dev/api/generate-content

**注意**：这里约定“支持两类兼容协议”的**接口适配能力**，不表示第三方自称兼容就必然支持所有官方字段；具体端点、模型名、参数大小写与返回结构应由 Provider Adapter 版本锁定并在集成测试中验证。

## 10. 内部数据契约（实现建议）

### 10.1 统一任务请求示例

```json
{
  "task_id": "auto-generated",
  "input": {
    "type": "image_plus_text",
    "reference_image_ref": "secure://uploads/ref-001",
    "user_prompt": "保留这只杯子，探索不同办公场景"
  },
  "output": {
    "mode": "grid",
    "grid_size": 3,
    "image_count": null,
    "branch": null,
    "aspect_ratio": "inherited",
    "resolution_tier": "2K",
    "package_zip": true
  },
  "variation": {"direction": "scene"},
  "model": {"source": "external", "provider_type": "gemini_image_compatible", "model_id": "configured-model", "credential_ref": "secret://provider-01"}
}
```

**约束**：`image_count` 只供 Sequence 设置；Grid 从 N² 推导；`branch` 只供 Sequence 设置；`reference_image_ref` 为受保护的文件引用而非随意 URL；**任何请求/状态结构中均不得含明文 API Key**。对不适用字段可省略，不要求全部显式填 `null`。

### 10.2 Sequence 变化计划示例

```json
{
  "direction": "light",
  "branch": "progression",
  "identity_anchor": "同一只浅灰陶瓷杯，形状、把手、釉面不变",
  "frames": [
    {"index": 1, "change_point": "柔和清晨侧光", "phase": "起点"},
    {"index": 2, "change_point": "上午清晰自然光", "phase": "阶段2"},
    {"index": 3, "change_point": "正午高对比顶光", "phase": "阶段3"},
    {"index": 4, "change_point": "黄昏温暖低角度逆光", "phase": "终点"}
  ]
}
```

此示例仅展示数据契约；视觉内容由实际方向规则与用户输入决定，不能为所有任务固定同一四段计划。

### 10.3 任务结果 Manifest 必填字段

`schema_version`、`artifact_type`、`task_id`、`artifact_revision`、`status`、`input_type`、`output_mode`、`direction`、`branch`（如适用）、`grid_size/image_count`、`requested_aspect_ratio`、`actual_aspect_ratio`、`requested_resolution_tier`、`provider_type`、`model_id`、`direction_rules_version`、`prompt_template_version`、`created_at`、`completed_at`、`images[]`（稳定 asset ID、`kind=board/tile/frame`、索引、尺寸、格式、SHA256、受限相对路径、状态、验证记录）、`errors[]`、`retry_counts`。Grid 的 `images[]` 需区分 `board` 与 `tile` 并记录整数 `crop_box=(x0,y0,x1,y1)`。第二阶段从该清单派生只读 `content_assets` 视图，**不重复保存另一套权威图像列表**；详细格式见 SPEC 03。**[设计方案：v1.1 跨 Skill 扩展]**

默认不在交付包内公开原始私人参考图；如需可复现实验可按用户同意另存提示词/输入引用，但不能包含凭据或私有服务端内部路径。

## 11. 组件职责与 Skill 包结构 [设计方案]

建议一份 Skill 内部的逻辑职责：

1. `InputParser`：输入检查、提取图片元信息、优先级冲突处理。
2. `TaskPlanner`：路由两种模式、参数预检、调用预算估算。
3. `AnchorBuilder`：主体身份锚点与纯文本图 1 锚点。
4. `DirectionRegistry`：12 类规则和可扩展版本、3 分支偏向。
5. `GridPromptBuilder` / `SequencePromptBuilder`：单条网格 Prompt / N 条序列 Prompt。
6. `ProviderRouter` / `Adapters`：内置、GPT Image 兼容、Gemini 兼容调用。
7. `JobExecutor`：队列/进度/重试/失败恢复。
8. `GridValidator` / `SequenceValidator`、`TileSplitter`、`ResultPackager`：质检、切图、打包。

建议文件布局（**指开发交付目标，当前并未创建这些代码**）。v1.1 版要求将下述目录置于单仓库 `skills/image-variations/` 下，并将通用 Schema/验证器置于仓库 `shared/`，详见 SPEC 00：

```text
image-variations-skill/
  SKILL.md                    # 唯一入口：触发条件、流程和分支规则
  references/
    product-contract.md       # 参数、优先级、状态、错误码
    directions/               # 12个方向，逐个可独立扩展
    grid-prompt.md            # 无缝Grid模板
    sequence-prompt.md        # 3种序列分支模板
    provider-compat.md        # 接口映射、能力矩阵与安全要求
  scripts/
    run_task.py               # 宿主入口示例
    prompt_planner.py         # 模式/分支计划
    image_validator.py        # 验证与规格化
    split_grid.py             # 确定性均匀切图
    package_result.py         # zip和manifest
    adapters/                 # builtin/openai_compat/gemini_compat
  tests/
    fixtures/                 # mock Provider响应与损坏网格
    test_grid.py
    test_sequence.py
    test_adapters.py
  README.md
```

**架构原则**：`SKILL.md` 只写编排和关键不可违反规则，长方向全文/模板/API 矩阵放在 `references`；可以被不同宿主移植，而不把业务逻辑绑在特定平台 API 上。

## 12. 错误码与恢复策略

| 错误码 | 触发条件 | 用户可见处理 |
| --- | --- | --- |
| `INVALID_INPUT` | 无图无文、格式损坏或非法参数 | 解释缺失/错误字段，不调用模型 |
| `INVALID_MODE_OR_COUNT` | 非 2/3/4 网格或序列不在 4–16 | 显示允许区间 |
| `MULTIPLE_DIRECTIONS` | 同一任务显式要求多个方向 | 请只选择其中一项 |
| `UNSUPPORTED_RATIO` | 模型无法满足有效比例 | 显示支持比例，不静默裁剪 |
| `UNSUPPORTED_RESOLUTION` | 模型不支持指定档位 | 显示可用档位，不静默降档 |
| `REFERENCE_REQUIRED` | 当前任务必须以图为锚点、模型却不支持参考图 | 明确提示换兼容模型 |
| `AUTH_FAILED` | 用户 Key 无效/无权限 | 提醒检查配置，不回显 Key |
| `MODEL_CAPABILITY_UNSUPPORTED` | 其他必需能力不可用 | 标明缺失能力 |
| `RATE_LIMITED` / `PROVIDER_TIMEOUT` | 429、超时 | 有限退避重试，失败后可针对失败项重跑 |
| `GRID_VALIDATION_FAILED` | 整板不满足 NxN/无缝/可切图 | 有限重试后失败，不输出“已通过”切图 |
| `SEQUENCE_ANCHOR_FAILED` | 纯文本序列第一张失败 | 停止后续张数，支持重试第一张 |
| `IMAGE_VALIDATION_FAILED` | 单图损坏/比例异常/主体明显偏移 | 重试该张，最终可部分失败 |
| `MODEL_REFUSAL` | 模型拒绝或安全策略禁止 | 明确展示拒绝，不通过改 Provider 绕过 |

### 12.1 Sequence 部分失败

保持同一 `task_id`、方向、分支、图 1 锚点与原帧索引。用户可以补生成失败索引，但不得重写已通过的成功帧；如图 1 重建，则后续全部旧结果不再默认视为同一锚点版本，需提示整组重建。**[设计方案]**

## 13. 验收标准与测试矩阵

以下 **AC** 建议作为 Phase 1 发版阻断项：

| ID | 用例 | 必须通过的判定 |
| --- | --- | --- |
| AC-01 | 三种输入 | image_only、text_only、image_plus_text 都能解析并提交 |
| AC-02 | 空输入 | 拒绝，不发起模型调用 |
| AC-03 | 无图默认 | 纯文本未指定比例时返回 3:4 请求计划 |
| AC-04 | 比例继承 | 参考图 EXIF 修正后的宽高比继承；用户明确比例可覆盖 |
| AC-05 | Grid 尺寸 | 2×2/3×3/4×4 三种都支持，其他网格报错 |
| AC-06 | Grid 默认档位 | 2→1K、3→2K、4→4K；用户覆盖时生效 |
| AC-07 | Sequence 张数 | 4 与 16 可通过，3 与 17 必须拒绝 |
| AC-08 | Sequence 默认档位 | 未指定 1K；显式 2K/4K 生效或预检报不支持 |
| AC-09 | 单方向 | 12方向双模式全可选；显式多方向被拒绝 |
| AC-10 | 单分支 | Sequence 支持3分支且单任务只选1个；Grid 不接收分支 |
| AC-11 | Grid 单次生成 | 1 个最终作品板的**一次**生成请求，不走 N² 次调用 |
| AC-12 | Grid 全格规划 | 每格有不重复主变化点，按行优先映射 |
| AC-13 | 网格结构 | 验证通过的整板无可见缝隙/框/编号，N² 格均可识别 |
| AC-14 | 切图像素 | 输出恰好 N² 张；宽高相同、坐标不重叠无缺漏、可单独打开 |
| AC-15 | 错误网格 | 行列错、跨格、白缝/厚边等不能被标记为成功切图 |
| AC-16 | 序列提示词 | N 张生成 N 条实质不同 Prompt，每张都有唯一变化点 |
| AC-17 | 纯文本锚点 | 图 1 成功后图 2–N 全部引入图 1 作为图片参考，不链式引用图 i−1 |
| AC-18 | 参考图锚点 | image_only 与 image_plus_text 的每张图均使用原参考图锚定 |
| AC-19 | 分支计划 | variants 无近重复；progression 有清楚阶段；narrative 有前后叙事 |
| AC-20 | 能力不足 | 缺图参考/4K/比例均事前明确报错，不暗降级 |
| AC-21 | 模型适配 | 官方/Mock 的 GPT Image 与 Gemini 两类兼容调用、响应解析均通过 |
| AC-22 | 凭据安全 | Key 不出现在日志、Prompt、响应、zip；自定义 URL 通过 SSRF 防护 |
| AC-23 | 部分失败 | 失败帧保持原槽位，可独立补生成，不丢已成功图 |
| AC-24 | 打包与清单 | 单图与 Grid 的 zip 文件数、命名、索引和尺寸元数据正确 |
| AC-25 | 方向扩展 | 新增第 13 个方向无需修改主流程分支判断，仅新增注册数据与测试 |
| AC-26 | 第一阶段边界 | 不自动触发原网站笔记功能；无需原网站任何运行时依赖 |

**测试资源**：Mock Provider 必须覆盖成功、401、429、超时、错误 MIME、损坏图、比例不符、无图响应、网格错误、文字-only 图 1 失败、图 i 失败等；对真实模型至少做 2×2/3×3/4×4 和 3 个 Sequence 分支的人工视觉复核。视觉指标不可能单凭静态单元测试保证，验收包含实际样图检查。

## 14. 双 Skill 集成、版本策略与待确认项（v1.1）

### 14.1 新增：上游产物契约

- Skill 01 的持久化 Manifest 保持 `images[]` 为单一图片清单：Grid 记录 1 张 `board` 与 N² 张 `tile`；Sequence 记录 N 张 `frame`。
- 为每条图片记录稳定 `id`、`kind`、`index`、`path`、`width`、`height`、`sha256`、`status`；Grid Tile 记录规范化作品板对应的 `crop_box`。原 v1.0 字段（模式、方向、分支、比例、分辨率、模型等）继续保留。
- 新增 `schema_version`、`artifact_type=image_variations`、`artifact_revision`，修复/重切图形成新修订，不无声覆盖下游已经引用的图片。
- 第二阶段只能使用完成并通过验证的独立图片资产。Grid 不把完整作品板作为正文图像；Sequence 维持帧索引、分支顺序及缺帧槽位。
- API Key、用户私有原始附件、内部凭据不写入对外产物包。

### 14.2 新增：交接及推荐策略

1. Skill 01 成功后生成完整 Manifest，交付图片与打包能力，**然后推荐**继续进行内容生产。
2. 这个推荐是用户交互，不是 API 调用：**没有用户显式选择，不得创建 Skill 02 任务**。
3. Skill 02 读取此前保存的固定 `artifact_revision`；修改内容不要求 Skill 01 再次生成图片。
4. 上游部分失败或校验不通过时，不将产物冒充完整成功；具体消费例外和错误恢复交由 SPEC 03 约束。
5. 单仓库中的两个 Skill 均保留独立入口与版本号，不把用户研究/公众号/小红书生成塞入 Skill 01。

### 14.3 已确认需求（继续有效）

- 名称 `Image Variations Skill`；新建 Skill，原包仅供参考。
- 输入图片、文本、图片+文本；纯文本默认 **3:4**，其他默认继承原图。
- Grid：2×2/3×3/4×4；默认 1K/2K/4K，支持覆盖；一次完整网格出图；无缝等格；第一阶段自动切图。
- Sequence：**4–16** 张，默认 1K、可覆盖；每图独立 Prompt；以原图或文本第 1 张锚定，不逐张接力。
- 12 个方向均支持两种模式，一次任务一个方向；三类 Sequence 分支一次只选一种，全部方向均支持。
- 宿主内置图像能力或用户配置 GPT Image/Gemini Nano Banana 兼容接口及 Key；能力缺失明示，不暗中降级。
- 第一阶段完成后**推荐继续**第二阶段，用户主动选择方可运行。

### 14.4 设计方案及待验证（与 v1.0 同步保留）

| 项目 | 当前建议 | 性质 |
|---|---|---|
| 默认未指定模式 | Grid 3×3 | [设计方案] |
| 默认未指定方向 | `state` | [设计方案] |
| Sequence 默认张数 | 4 | [设计方案] |
| 各方向默认分支 | 原 §5.1 映射 | [设计方案] |
| 比例和尺寸的模型能力映射 | 预检并记录真实宽高 | [待验证] |
| 网格尺寸对齐容差 | 单轴 ≤1% 微调整且质量过关 | [设计方案/待实测] |
| 单次单元最大额外重试 | 2 次 | [设计方案] |
| Skill 宿主及接口 | `SKILL.md` + references/scripts | [待确认] |
| 联动推荐入口呈现 | 结果中一次建议或控件 | [设计方案] |

## 15. 参考来源与迁移说明

**用户资料（需求基础）**：`单图生成多图-功能与提示词包-20261114.zip`，其中 `01-功能说明.md`、`02-完整提示词.md`、`03-参数与开发说明.md`、`源码/prompt_builder.py`、`数据/prompt-library.json`。借鉴**主体一致性、12 类方向、单条 Grid Prompt、文本归一化**；不沿用原 HTTP 端点、用户鉴权、笔记功能、模型供应商默认值或“白色网格间距”。

**外部协议参考（2026-11-14 核对）**：OpenAI Image Generation、OpenAI Image API Reference、Google Gemini Image Generation、Google Generate Content Image Config，以及 OpenAI Skills 文档：https://developers.openai.com/api/docs/guides/tools-skills 。这些文档用于校准适配器抽象，**不等于所有第三方兼容商承诺实现所有功能**。

---

**结束语**：本 v1.1 SPEC 是第一阶段在双 Skill 单仓库中的开发基线；需要构建时，应先落实宿主、Provider Adapter、两套 Prompt Builder、标准结果 Manifest 与完成后推荐接口。第二阶段内容生成由独立的 SPEC 02 定义。
