# 上游问题反馈：`gpt-image-2.5-flare` 的图片编辑通路持续 502

> 可直接转发给青水（qingshui）技术支持。以下均为 2026-11-17 实测原文，未做改写。
> **2026-11-17 17:23 复测补充**：仍然失败，但上游故障码已从 `502 UPSTREAM_PROVIDER_ERROR`
> 变为 `503 UPSTREAM_CAPACITY_UNAVAILABLE`（见下表 R 行）。

## 一句话

同一个 API Key、同一个接口下，`gpt-image-2.5-flare` **纯文本生图正常**，但只要带参考图
（`reference_images` 非空）就 **100% 失败**，返回 `AIMS API 错误 [502] UPSTREAM_PROVIDER_ERROR`，
且**每次失败仍扣 1 算力**。同一张参考图换 `gemini-3.1-flash-image-preview` 就成功，
因此故障被隔离在该模型的图片编辑通路上。

## 报错原文（4 次尝试逐字完全相同）

```
image gen failed: AIMS 图片编辑失败: AIMS API 错误 [502]: {"error":{"message":"模型服务异常，请稍后重试","type":"api_error","param":"","code":"UPSTREAM_PROVIDER_ERROR"}}
```

任务级明细（轮询 `GET /api/v1/images/{task_id}` 的返回）：

```json
{
  "task_type": "image",
  "model": "gpt-image-2.5-flare",
  "status": "failed",
  "progress": 0,
  "quota_cost": 1,
  "error_msg": "image gen failed: AIMS 图片编辑失败: AIMS API 错误 [502]: {\"error\":{\"message\":\"模型服务异常，请稍后重试\",\"type\":\"api_error\",\"param\":\"\",\"code\":\"UPSTREAM_PROVIDER_ERROR\"}}"
}
```

提交（`POST /api/v1/images/generate`）本身**是成功的**，返回
`{"success": true, "status": "pending", "task_id": "...", "quota_cost": 1, "message": "task submitted, ..."}`，
失败发生在异步任务里。

## 复现清单（5 个 task_id，可按时间查服务端日志）

| # | 参考图传法 | task_id | 提交时间 | 耗时 | 结果 |
| --- | --- | --- | --- | --- | --- |
| B | 内联 `data:image/jpeg;base64,...` | `d64e6f4b-2042-49aa-8093-a1adad201075` | 2026-11-17 11:13:36 +08 | 4.8s | failed / 扣 1 算力 |
| B1 | 内联（重试 1） | `ac900fa1-213e-4368-b601-7ae272095f0f` | 2026-11-17 11:15:44 +08 | 18.7s | failed / 扣 1 算力 |
| B2 | 内联（重试 2） | `219a4757-452d-4d1e-be3b-a9fdc8ccb519` | 2026-11-17 11:16:02 +08 | 17.5s | failed / 扣 1 算力 |
| F | **平台上传后的公网 URL** | `b5caa59b-621a-4e5c-b981-e66a1eedbbe3` | 2026-11-17 11:19:12 +08 | 10.5s | failed / 扣 1 算力 |
| R | 平台上传后的公网 URL（**6 小时后复测**） | `72e71e20-ab42-44c0-94f6-d1d732721975` | 2026-11-17 17:23:57 +08 | 2.0s | failed / 扣 1 算力，**码变成 503** |
| A（对照） | 无参考图，纯文本 | — | 2026-11-17 11:11 | — | **成功 1024×1024** |
| L（对照） | 同一张图 + `gemini-3.1-flash-image-preview` | — | 2026-11-17 11:27 | 20.2s | **成功 1024×1024** |

F 例用的参考图资产：`POST /api/v1/assets` 上传得到
`hash=977e01055b2bcaea2e069a6975d90d68`、`url=https://qingshui.hqwx.com/2/6/977e01055b2bcaea2e069a6975d90d68.jpg`，
测试后已 `DELETE /api/v1/assets/977e01055b2bcaea2e069a6975d90d68` 取消分享（返回 `{"success": true, "deleted": true}`）。

**已排除"传法不对"**：内联 data URL 与平台上传 URL 两种传法都试过，报错完全相同。

## 复测发现：换了一个上游故障码，仍然不是参数问题

6 小时后（17:23:57）用**同一套生产代码**（上传参考图换 URL → 提交 → 轮询 → 用完即删）复测：

```json
{
  "task_id": "72e71e20-ab42-44c0-94f6-d1d732721975",
  "model": "gpt-image-2.5-flare",
  "status": "failed",
  "progress": 0,
  "quota_cost": 1,
  "error_msg": "image gen failed: AIMS 图片编辑失败: AIMS API 错误 [503]: {\"error\":{\"message\":\"模型服务暂时不可用，请稍后重试\",\"type\":\"api_error\",\"param\":\"\",\"code\":\"UPSTREAM_CAPACITY_UNAVAILABLE\"}}"
}
```

资产 `15782dea4fb95187cb3876a8b9b79ec7` 上传成功、测试后 `DELETE` 返回 `{"deleted": true}`。

两次的对比：

| 时间 | 上游码 | 上游文案 |
| --- | --- | --- |
| 11:13–11:19（4 次） | `502` `UPSTREAM_PROVIDER_ERROR` | 模型服务异常，请稍后重试 |
| 17:23（复测，第 5 次） | `503` `UPSTREAM_CAPACITY_UNAVAILABLE` | 模型服务暂时不可用，请稍后重试 |

两次都是**上游服务/容量**层面的拒绝，与请求参数、参考图传法无关；且**5 次全部扣算力、零产出**。
说明该模型的图片编辑后端在这段时间内持续不可用（先是异常，后是容量不足）。

## 请求体（可原样重放）

内联那三次：

```json
{
  "prompt": "Keep the same woman, same outfit and same setting; apply soft warm evening light.",
  "model": "gpt-image-2.5-flare",
  "image_count": 1,
  "aspect_ratio": "1:1",
  "negative_prompt": "",
  "reference_images": ["data:image/jpeg;base64,<参考图 base64>"]
}
```

上传 URL 那次把 `reference_images` 换成 `["https://qingshui.hqwx.com/2/6/977e01055b2bcaea2e069a6975d90d68.jpg"]`，其余相同。

参考图：1663×2048 JPEG，450,269 字节。

对照实验（同模型不带图）：

```json
{
  "prompt": "A plain light-grey ceramic coffee cup on a clean warm-white background, ...",
  "model": "gpt-image-2.5-flare",
  "image_count": 1,
  "aspect_ratio": "1:1",
  "negative_prompt": "",
  "reference_images": [],
  "size": "2048x2048"
}
```

→ 成功出图 1024×1024（`size` 参数被忽略，另见下文）。

## 两个附带问题

### 1. 失败仍扣算力

4 次 `failed`（`progress: 0`、无任何图片产出）每次都记 `quota_cost: 1`。**共 4 算力未产出任何图**。
期望：上游 502 属平台/模型侧故障，应自动退回或不计费。

### 2. `Doubao-Seedream-4.5` / `5.0-lite` 的尺寸下限冲突

```
AIMS API 错误 [400]: {"error":{"message":"The parameter `size` specified in the request is not valid: image size must be at least 3686400 pixels.","code":"InvalidParameter"}}
```

3 次尝试（纯文本 2 次、带参考图 1 次）全部同一个错。`3686400 = 1920²`，
而平台按 `aspect_ratio` 映射出的尺寸大约是 `1024² = 1048576`，恒低于 Doubao 的下限，
因此该模型在本平台**完全不可用**。期望：要么由平台按模型设置合规的 `size`，要么在模型列表里标注不可用。

同样地，`gpt-image-2.5-flare` 传 `size: "2048x2048"` 被忽略（仍输出 1024×1024），
`gemini-3.1-flash-image-preview` 传 `image_size: "2K"` 也被忽略（仍 1024×1024）——
如果这两个模型本身支持更大尺寸，建议开放尺寸参数。

## 我们的期望

只要能明确其一即可，我们据此修改能力声明：

1. `gpt-image-2.5-flare` 支持 `reference_images` → 请修复该通路（并退回失败扣掉的算力）；
2. 或者该模型**不支持**参考图输入 → 请在模型列表/文档里明确标注，我们会把
   `reference_image_input` / `image_edit` 声明为 `false`，避免用户反复踩同一个坑。
