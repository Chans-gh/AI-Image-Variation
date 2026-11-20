---
name: image-content-publishing
description: 从 Image Variations 的已交付 Tile 或 Sequence 图片策划选题、生成公众号图文及同源双封面、生成小红书笔记及首评。用于基于已完成的 Skill 01 产物发散选题、直接成稿或批量制作两平台发布素材。
---

# Image Content Publishing

1. 定位用户指定的 Skill 01 成品目录或 `manifest.json`。运行 `image-content-publishing inspect` 获取固定来源引用和可读独立图片。部分序列需用用户选定的 ID 预检。旧版验证记录不足时，按用户明确决定处理。
2. 打开独立图片建立画像。观察写可见元素，解释写构图判断或主观感受。按索引保留渐进和叙事顺序。把生成画面标为示意。
3. 按用户意图确定平台、主题、篇数、语气和研究范围。先看选题用 `explore_topics`，通过 `plan` 后展示候选便停下。直接成稿用 `generate_single`、`generate_batch` 或 `generate_mixed`，在内部完成选题。
4. 按 [宿主工作流](references/host-workflow.md) 写策划和成稿 JSON。先检查同批读者价值、论点和用图是否有实质差异。事实型选题按需要实际核验并记录证据。
5. 公众号正文以文字起止，每张图之间写一段承接正文。运行 `build` 和 `validate`，交付公众号 HTML、Markdown、双封面和按序图片，或小红书笔记、首评和按序图片。用户需要打包时运行 `pack`。出现部分成功时保留已交付文件，针对失败项提交补做。

发布素材供用户编辑和使用。Skill 01 的推荐文本仅用于提示用户选择。
