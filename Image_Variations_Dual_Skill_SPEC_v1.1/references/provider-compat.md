# Provider 兼容与能力底线（`provider-compat-v1`）

**适用范围**：Skill 01 的 `builtin` 与 `external` 两类模型来源；Skill 02 只读取上游产物，不直接调用图像模型。
**关联**：SPEC 01 §9、`contracts/image-artifact.schema.json` 的 `execution`、`references/error-registry.json`。

## 1. 两类来源

| 来源 | 说明 | 缺少时 |
|---|---|---|
| `builtin` | 宿主自带的生图能力，使用宿主原生参数与认证 | `BUILTIN_UNAVAILABLE` |
| `external` | 用户填写 `provider_type`、`base_url`、`api_key_secret_ref`、`model_id`；首批协议 `openai_image_compatible`、`gemini_image_compatible` | `AUTH_FAILED` / `MODEL_CAPABILITY_UNSUPPORTED` |

> 「兼容」只表示**协议形状相似**，不表示对方实现了官方全部字段。适配器必须声明自己支持哪些能力，并由版本锁定字段名（SPEC 01 §9.6）。

## 2. 能力声明（`ModelCapability`）

```json
{
  "text_to_image": true,
  "reference_image_input": true,
  "image_edit": true,
  "supported_ratios": ["1:1", "3:4", "4:3", "16:9", "9:16"],
  "supported_resolution_tiers": ["1K", "2K"],
  "tier_to_board_side_px": {"1K": 1024, "2K": 2048},
  "custom_dimensions": false,
  "supports_async_result": false,
  "max_input_bytes": 10485760,
  "response_image_formats": ["png", "jpeg", "webp"],
  "distinguishable_errors": true,
  "adapter_version": "openai-image-compat/1.0"
}
```

`tier_to_board_side_px` 是 Grid 档位推导的**唯一输入**（SPEC 01 §3.4）：没有该声明就无法计算单格长边，视为能力不达标。

## 3. 底线清单（排产前逐项通过）

| 能力项 | 底线 | 不满足 |
|---|---|---|
| `text_to_image` | 必需 | `MODEL_CAPABILITY_UNSUPPORTED` |
| `reference_image_input` | 纯文本 Sequence 与有图输入必需 | `REFERENCE_REQUIRED` |
| 多次独立请求 | Sequence 需 N 次独立请求 | `MODEL_CAPABILITY_UNSUPPORTED` |
| `supported_ratios` | 覆盖用户目标比例 | `UNSUPPORTED_RATIO` |
| `supported_resolution_tiers` + `tier_to_board_side_px` | 至少一档满足 `tile_edge_min_px` | 按 SPEC 01 §3.4 分级：警告或 `UNSUPPORTED_RESOLUTION` |
| `max_input_bytes` | ≥ 用户原图字节 | 提示压缩或更换原图，不静默丢参考图 |
| `response_image_formats` | 可解码 PNG/JPEG/WebP | `IMAGE_VALIDATION_FAILED` |
| `distinguishable_errors` | 能区分鉴权/限流/超时/内容拒绝 | 视为不达标 |

## 4. 字段映射（骨架，具体取值由适配器版本锁定）

### 4.1 `openai_image_compatible`

| 统一字段 | 映射方向 | 备注 |
|---|---|---|
| `prompt` | 文本提示 | 不拼接明文密钥 |
| 目标尺寸 | 宽×高字符串（如 `1024x1024`） | 与质量参数相互独立；**1K/2K/4K 不等于官方 `resolution` 参数** |
| 参考图 | 图像编辑/参考图路由（多部分表单或等价结构） | 不得假定所有供应商都接受 `image_urls` JSON 字段 |
| 质量 | 质量参数 | 可选；缺失时按适配器默认 |
| 返回 | 图片字节或授权 URL | 两者都要能解析；URL 不得作为最终唯一引用 |

### 4.2 `gemini_image_compatible`

| 统一字段 | 映射方向 | 备注 |
|---|---|---|
| `prompt` | 文本 part | — |
| `aspect_ratio` | 比例字段 | 大小写由适配器版本锁定 |
| 档位/尺寸 | `image_size` / `imageSize` 等 | 4K 与可选比例依具体模型版本判定 |
| 参考图 | 图片 part | 从返回的图片 part 解析字节 |

### 4.3 通用要求

- 请求与响应的**结构、字段名、大小写**必须由适配器版本声明并在集成测试中固化；不得靠"叫兼容就应该支持"推断。
- 上传的用户原图只能发给用户选定的 Provider；结果中明确记录 `provider_type` 与 `model_id`。
- 不支持的参数不要"试一下"：能预检的必须预检。

## 5. 错误映射与重试

| 上游情况 | 归一化错误码 | 是否重试 |
|---|---|---|
| 401 / 403 | `AUTH_FAILED` | 否 |
| 429 / 明确限流 | `RATE_LIMITED` | 是，遵循 `Retry-After`，指数退避 + 抖动 |
| 网络超时 | `PROVIDER_TIMEOUT` | 是，最多 2 次额外重试 |
| 400 参数不支持 | `MODEL_CAPABILITY_UNSUPPORTED` / `UNSUPPORTED_RATIO` / `UNSUPPORTED_RESOLUTION` | 否（改参数后才可重试） |
| 内容策略拒绝 | `MODEL_REFUSAL` | 否，且不得换 Provider 绕过 |
| 返回无图片 / 无法解码 | `IMAGE_VALIDATION_FAILED` | 是，计入单帧重试 |
| 返回图比例不符 | `IMAGE_VALIDATION_FAILED`（记录 `aspect_ratio_error_ratio`） | 是 |

- 重试策略：每个独立生成单元最多 **2 次额外重试**；Grid 整板最多 3 次尝试（含首试）。
- 重试必须计入 `execution.calls_used` 与 `retry_counts`；超预算转 `BUDGET_EXCEEDED`。
- **不得自动切换 Provider**：换供应商属于用户决策。

## 6. 安全

1. API Key 只存宿主密钥库，引用形式 `secret://...`；**不入 Prompt、日志、manifest、ZIP、URL 参数**。
2. 自定义 Endpoint 默认仅允许 HTTPS，并阻断内网、环回、链路本地、云元数据地址及重定向到内网的 SSRF。
3. 本地开发例外：`allow_local_endpoint`（默认关闭），仅对 `127.0.0.1` / `localhost` 生效，开启状态记入执行记录且不导出。
4. 连接测试只验证配置与权限，不触发正式批量生成。

## 7. 待实测锁定项

| 项目 | 现状 |
|---|---|
| 各 Provider 的真实字段名与大小写 | 开发时按官方文档 + 实际响应锁定，写入适配器版本 |
| 各档位对应的真实边长 | 必须在真实服务上测量并回填 `tier_to_board_side_px` |
| 4K 与比例矩阵 | 依模型版本，需逐模型实测 |
| 限流阈值与计费口径 | 需真实账号验证，用于成本估算 |
