# Provider 与能力档案

能力档案绑定 `provider_type + model_id + api_route + profile_version + environment`，外部档案还绑定 exact base_url。每档每比例分别列有效参数与原生输出表。配置样例都是 **unverified**，修改模型名不等于已支持。

- GPT Image 兼容：`images/generations` 为 JSON、`n=1`；`images/edits` 为包含实际图像字节的 multipart。档位映射使用已验证 `size=WIDTHxHEIGHT`，不把 quality 当分辨率。Sequence 在首帧前就要求已声明 edits 路由。可解析 b64_json 或从受控公共 HTTPS URL 获取图像。
- Gemini 兼容：`generateContent` 使用 `generationConfig.imageConfig.imageSize/aspectRatio` 与 `contents.parts.inlineData`；`interactions` 使用 `response_format.image_size/aspect_ratio` 与 typed image input。API revision 可在配置中显式提供；两种路由不可互换大小写。响应原始字节仍需真实尺寸验收。
- 内置宿主可用 `--generated-image` 导入实际生成的图片。`codex_skill` 调用本机 `codex-image` 0.4.0，核对脚本哈希、GPT Image 2.6 型号以及 `codex.images.generations` / `codex.images.edits` 路由。OpenCodex 使用档案中的本机 `base_url`。原生尺寸仍按返回图片像素单独验收。
- Mock：`environment=mock` 的档案与 Provider 只能互相使用；即使字段写着 4K，合成 fixture 的像素也不能证明任何真实模型的 4K 能力。

外部 HTTP 仅 HTTPS，DNS 解析的全部地址须为公共地址，直接连接已校验的 IP 后按原主机校验证书。不使用代理环境变量、不跟随重定向，不给返回图片 URL 附加 Provider 认证头。不要给 custom endpoint 开内网例外。宿主本地可信工具与外部 Endpoint 是不同接入边界。

凭据由配置中的 `api_key_env` 指向本地环境变量，或通过 Python 构造器注入宿主解析出的秘密。任务 JSON、提示词、日志、Manifest 与 ZIP 不含明文 Key。工具内部读取 OAuth 属于选定本地 Skill 的职责，不打印或打包 auth.json。

## 验证新档案

先依据实际接口声明填写选项，再由用户明确触发少量能力试验，记录请求尺寸参数、型号/路由、输出 SHA256、真实像素、日期、费用或费用未知。试验成功后只开放实际证明的组合；单次 1K 样本不证明 2K/4K。无法确认档位则保持 unverified，正常排产将被阻断。

参考核对（2026-11-17，仅协议资料；实际型号另行验证）：

- [OpenAI image generation](https://developers.openai.com/api/docs/guides/image-generation)
- [OpenAI Images API](https://developers.openai.com/api/reference/resources/images/methods/generate)
- [Gemini image generation](https://ai.google.dev/gemini-api/docs/image-generation)
- [Gemini generateContent ImageConfig](https://ai.google.dev/api/generate-content)
