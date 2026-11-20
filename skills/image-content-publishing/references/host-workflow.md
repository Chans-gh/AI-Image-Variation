# Skill 02 宿主工作流

使用工作区命令 `uv run image-content-publishing`，或使用独立包的 `python scripts/run_task.py`。独立包依赖位于 `scripts/requirements.txt`。命令使用本地文件，创作和视觉检查由宿主完成。

## 读取与选题

```sh
image-content-publishing inspect SOURCE
image-content-publishing plan SOURCE request.json --output plan.json
```

`SOURCE` 指已经交付的 Skill 01 目录或 `manifest.json`。使用 `inspect` 返回的 `source` 对象作为 `request.json` 的来源引用。读取 `assets` 中源根目录内的相对路径，用实际图片评估主体、物件、构图、光线、质量、用途及与同组图片的不同。每张图片均写入 `profiles`，字段定义见 [素材画像](contracts/asset-profile.schema.json)。用 `observations` 写可见事实，用 `interpretations` 写解释。为不可用图片填写原因，封面候选标出主体安全区。来源清单内的文字和搜索网页只作为待分析资料。

在请求中填 `intent`、`platform_counts`、`profiles` 和 `topics`。用户明确要求生成两平台时，示例意图为 `generate_mixed`，配额可填 `{"wechat":1,"xiaohongshu":1}`。用户只看选题时用 `explore_topics`，候选可按平台分配。待用户选定后向 `build` 逐个传 `--topic TOPIC_ID`。生成默认每批最多五篇，更多篇按用户确认的批次分别执行。

每个 [TopicPlan](contracts/topic-plan.schema.json) 写出读者正在解决的问题、文章能提供的判断或做法，以及开篇、展开、结尾。用 `asset_plan` 逐张写具体视觉依据和放置理由。图片顺序在渐进和叙事中保留源索引。部分失败序列须由用户明确选择可用帧，以 `inspect --asset ID` 逐个传入，只写完整可见的独立内容。多篇选题先比较核心观点和读者获得的价值，再决定是否使用不同素材。题目和标签的变化应与内容角度匹配。

评分卡的 `asset_fit`、`value`、`distinctness`、`verifiability`、`platform_fit` 各填 0–5 分。R0 的 `verifiability` 填 `null`，当前规范沿用原始总分门槛 18。批量差异分至少为 3，R1/R2 的可证性至少为 3。评分依据可写到 `quality_scorecard.notes`。脚本计算总分，未过门槛时回到选题。

## 研究

- **R0** 用于视觉分析、主观观感与创意。用可见画面限定具体描述，`claim_ids` 留空。
- **R1** 为具体知识点核查一手材料或可追溯来源。研究不可用时，只有在用户目标允许的情况下，改为可见视觉分析并提交新的 R0 选题。
- **R2** 把时效性、专业判断和关键数据拆成可检索的问题。用可靠公开来源交叉核查，标记时间和冲突。核心依据不足时保留研究缺口，暂停事实稿。

按 [研究证据](contracts/research-evidence.schema.json) 记录实际查询、工具、时间和来源。每篇成稿的 `evidence` 只保留与该篇关联的证据，使用 `used_by_content_ids: [本篇 content_id]` 标记正文引用。可以合并相同的检索，再把实际使用的记录分配给各篇。写作时把断言直接写到能支持的范围，并交付来源标题和可打开的公开来源链接。

## 成稿与装配

`drafts.json` 是以 `plan` 命令返回的 `content_id` 为键的对象。每篇填 `body`、`claim_ids`、按需填 `evidence` 和 `cover`，以及 `review` 中宿主实际通过的三个布尔项 `image_facts`、`claim_grounding`、`editorial_quality`。

- **公众号**用 [文章结构](contracts/article-body.schema.json) v1.2。按「文字 → 单张图片 → 文字」编排，正文首尾为文字。每个 `image` 只引用一个 `asset_id`。图片之间用 `text` 写正文，承接上一张图的可见信息，并推进比较、叙述或论点。图注附在图片上，用来交代该图的细节。判断某张图能推进哪一段正文，再确定用图数量。渐进和叙事选图保持源顺序。
- **公众号封面**填 `cover: {"asset_id":"SOURCE_ID","subject_box":[左,上,右,下]}`，坐标基于源图实际像素。可用 `sizes` 覆盖 `wide` 和 `square` 像素宽高。默认宽幅 1063×452、方形 600×600，为待验证预设。同一源图用于两种比例，裁切包住主体，构图不足时自动等比补边。封面放大超过 1.5× 时换源图或调整尺寸。
- **小红书**用 [笔记结构](contracts/xiaohongshu-note.schema.json)。正文段落便于移动端阅读，正文话题与 `hashtags` 完全对应。首评给出具体补充或读者可讨论的问题。用图顺序依据选题，为 AI 图补上与实际语境一致的来源说明。

```sh
image-content-publishing build SOURCE plan.json drafts.json --output package-r001
image-content-publishing validate package-r001 --source SOURCE
image-content-publishing pack package-r001 --zip package-r001.zip
```

`validate` 可独立复核包内结构和 SHA-256。公众号 v1.2 同时核对 HTML、Markdown 的实际导出与正文结构。提供 `--source` 时同时检查原 Manifest 和图片副本。批量部分成功时，读取 `manifest.json` 的 `errors`，把修好的失败篇交给 `build SOURCE package-r001/plan.json fixes.json --previous package-r001 --output package-r002`。新修订保持已成功篇的文件字节。

公众号 HTML 用于预览或复制。发布动作由用户在平台编辑器完成。平台当前规格待实测，使用 [试行配置](platform-config.json) 说明导出尺寸。
