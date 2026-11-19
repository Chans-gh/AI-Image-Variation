# 参数与执行契约

输入：`input.user_prompt` / `input.reference_image_ref`（本地单张 PNG/JPEG/WebP）；至少一项。宿主的受控附件应先解析为可读文件，不从自由文本自动下载任意 URL。

`output.mode=grid|sequence`；`variation.direction` 读取方向注册表。Grid：`grid_size=2|3|4`，图数 N²，禁止设置分支。Sequence：`image_count=4..16`，`branch=variants|progression|narrative`，不设 grid_size。

已确认默认：纯文本比例 3:4；有图按 EXIF 归正后的比例；Grid 2/3/4 对应整板 1K/2K/4K；Sequence 每帧 1K。用户显式规格优先；档位可覆盖。宽高比统一约分，保留实际非主流比例。不支持即预检报错。

可配置设计默认：grid、3×3、state、序列 4 张、注册表里的方向默认分支。每个字段保留 explicit / inferred / proposed_default / confirmed_default / inherited 来源。结构化选择优于冲突的自由文本，并保留冲突记录。复杂自然语言解释由 P1-01 完成，程序只识别简单规格语法。

所有计划先于图像调用：P1-02 完整字段放 `creative.normalized`，P1-03 放 `creative.anchor`，P1-04 或 P1-06 放 `creative.plan`，P1-01 放 `creative.task_parse`。索引、数量、方向、分支和变化点唯一性由代码检查；视觉实质差异由宿主检查。

## 分辨率

采用 Provider-Native Resolution。精确能力档案给出档位、比例、API 原生参数与输出像素表或经验证范围。`quality` 独立于尺寸，`auto` 不能证明 K 档。生成后针对原始返回字节验收，保留真实尺寸和 SHA256。

比例误差相对目标 ≤1%；Grid 为整除 N 所作的每轴微调 ≤1%，两项分别检查。设计容差尚需真实模型回归后冻结。微调不能修复白缝、跨格、错行列或错误比例。只有视觉检查通过才切图，切图坐标严格等分且行优先。

## 状态与失败

`queued → planning → generating → validating → postprocessing → completed`。可用帧不齐为 `partial_failed`，无可用结果为 `failed`。本地逐步执行时，事件记录保留创建与 planning，生成后等待宿主提交检查。

每单元最多 2 次额外重试，总调用数受 call_budget_limit 限制；不会自动换模型。参数、安全拒绝、鉴权错误不做同类请求重试。失败帧保留索引，成功帧不可被 resume 选择。重建文本首帧需要新的整组锚点。

关键错误：INVALID_INPUT、INVALID_MODE_OR_COUNT、CLARIFICATION_REQUIRED、MULTIPLE_DIRECTIONS、REFERENCE_REQUIRED、UNSUPPORTED_RATIO、UNSUPPORTED_RESOLUTION、UNVERIFIED_NATIVE_TIER、RESOLUTION_RATIO_COMBINATION_UNSUPPORTED、NATIVE_RESOLUTION_VALIDATION_FAILED、MODEL_REFUSAL、GRID_VALIDATION_FAILED、IMAGE_VALIDATION_FAILED、SEQUENCE_ANCHOR_FAILED、CALL_BUDGET_EXCEEDED。错误只含安全元数据。

## 产物版本

新产物 `schema_version=1.0`，可选扩展 `dimension_execution` 按 v1.3 写入。读取兼容 1.0 / 1.1，旧清单缺原生尺寸证据标为 unverified，不补造通过记录。`images[]` 是唯一资产列表；`content_assets` 从已完成 tile/frame 派生，永不包含 board。

部分失败清单仅列真实可用图片，并在 `failed_frames[]` 中记录缺失索引。未产生任何可用图片的失败任务保留 job.json，不能捏造资产来满足旧 schema 的 minItems=1。新修订以新的目录和 parent_artifact_revision 交付。

`delivery-package.json` 清点交付目录的全部文件，排除清单自身以避免自哈希循环。清单与 ZIP 位于不同层级；导入会拒绝穿越、冲突、符号链接、异常压缩比及不匹配哈希。
