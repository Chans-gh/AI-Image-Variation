# 命令与宿主流程

Python ≥3.11；仓库用 `uv sync --extra dev` 安装，打包后的 Skill 可用 `python -m pip install -r scripts/requirements.txt`。入口 `python scripts/run_task.py`；仓库安装后也可用 `image-variations`。

宿主先按 SKILL.md 使用 P1-01/02/03/04 或 06 产生 request.json 与 creative.json。示例结构在仓库 examples/mock-grid 中，内容仅为合成测试。

```sh
image-variations create --request request.json --creative creative.json \
  --profile profile.json --environment builtin --job .work/job-01 --output artifacts
```

`create` 只预检并持久化，不生图。外部接口还需 `--endpoint https://your-selected-host/v1`。create 不保存 Provider 凭据；每次调用由本地配置选择同一档案绑定的接口。

## 内置工具

宿主读取 job.json 的 compiled.prompts 和 dimensions。Grid 每次调用只生成一张整板，Sequence 每次只处理下一个待生成索引；真实附图按 task/input 和固定锚点传入。按 DimensionPlan 的参数调用所选工具，原始文件保留。

```sh
image-variations step --job .work/job-01 --generated-image /path/to/raw-output.png
```

`codex-image` 0.4.0 的模型、路由和尺寸能力验证后，可通过 `--config config.json` 调用。配置包含 `kind=codex_skill`、`script_path` 和独立的 `quality`。档案固定 `tool_script_sha256`、GPT Image 2.6 的具体型号及 Images 路由。兼有文生图与参考图的计划使用 `api_route=codex.images.generations` 和 `reference_api_route=codex.images.edits`。如选用 OpenCodex，档案的 `base_url` 填入对应本机代理地址。

## 外部接口

配置只有 `base_url`、`api_key_env`、可选 `api_revision`；不包含明文秘密。确保模型与路由已经验证后：

```sh
image-variations step --job .work/job-01 --config provider.local.json
```

每次 step 至多发起一个请求，返回 validating 后停止。不能在等待检查时继续生成更多图。

## 视觉检查

读取 `.work/job-01/review-request.json`，打开 image_path 指向的真实输出和 reference_path 指向的参考。用其中的 P1-09 提示词输出 result。将 request 中 task_id、artifact_revision、plan_sha256、asset_sha256、index、attempt 原样放入报告，再增加 `result` 对象：

```json
{
  "task_id": "FROM_REQUEST", "artifact_revision": "r1",
  "plan_sha256": "FROM_REQUEST", "asset_sha256": "FROM_REQUEST",
  "index": 0, "attempt": 1,
  "result": {
    "pass": false, "grid_structure_ok": null,
    "subject_consistency_ok": false, "direction_distinctness_ok": false,
    "sequence_order_ok": null, "issues": [], "retry_suggestion": ""
  }
}
```

视角、场景、时间天气、镜头语言和用途的新版计划，每个格位或帧都要有 `distinctive_cue` 和方向规则列出的 `direction_evidence`。例如，时间天气写明 `condition`、`environment_effect`、`subject_response`，直接从画面找雨具、地面和人物行为的关系。

当 `review-request.json` 的 `item_review_indices` 非空，`result.item_reviews` 必须恰好覆盖这些索引。每条包含 `index`、`observed`、`identity_ok`、`target_ok`、`direction_only_ok`。后三项可填 `true`、`false` 或 `null`，交付通过要求每个索引三项均为 `true`。Grid 填所有格位，Sequence 填本次送审的帧。相似格位在 `issues` 里点名。用途包含裁切或排版时，在预览上实际套用计划中的裁切框或文字区，再判断 `target_ok`，并记录保住的画面信息。失败、缺项或不确定的结果留在待处理流程中。旧尝试的图片与报告不能复用。

```sh
image-variations review --job .work/job-01 --report report.json
image-variations status --job .work/job-01
```

若允许重试，使用 P1-10 后可 `repair --job ... --report repair.json`；然后继续 step/review。超时且结果不确定时需人工核对，使用 `--confirm-uncertain-retry`；进程中断可用 `recover --generated-image ...` 接回实际结果（不重复调用），或先核对再显式 `recover --confirm-no-result`。所有费用未知时记录 unknown，不当作免费。

```sh
image-variations resume --source .work/job-01 --job .work/job-02 --indices 3 7
image-variations validate artifacts/TASK/r1
image-variations pack artifacts/TASK/r1 --zip artifacts/TASK/r1.zip
```

新修订保留成功帧字节和原索引，不覆盖 r1。阶段二仅在用户选择后消费固定修订；本运行时没有阶段二的自动调用。
