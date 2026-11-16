# 交付包规范（`packaging-v1`）

**适用范围**：Skill 01 的 Grid/Sequence 出图包、Skill 02 的内容发布包，以及两者的 ZIP。
**配套契约**：`contracts/delivery-package.schema.json`。
**关联**：SPEC 00 §00.5、SPEC 03 §3.4、`references/error-registry.json`。

## 1. 目录布局

### 1.1 Grid

```text
iv_<task_id>_<revision>/
├── manifest.json            # image-artifact 1.1
├── delivery-package.json    # 本包清单（必填）
├── board.png                # kind=board, index=0
└── images/
    ├── 01.png               # kind=tile, index=1..N²，行优先
    └── ... NN.png
```

### 1.2 Sequence

```text
iv_<task_id>_<revision>/
├── manifest.json
├── delivery-package.json
└── images/
    ├── 01.png               # kind=frame, index=1..N，索引不因缺帧前移
    └── ... NN.png
```

### 1.3 内容包

```text
cp_<content_job_id>_<revision>/
├── manifest.json                    # content-artifact 1.1
├── delivery-package.json
├── research/                        # 可选；evidence.json + 索引
├── wechat-article-001/
│   ├── article.json / article.md / article.html
│   ├── topic-plan.json
│   ├── profile.json
│   ├── covers/cover-wide.<ext> / cover-square.<ext>
│   └── images/…                     # 全部登记进 package_images[]
└── xiaohongshu-note-001/
    ├── note.json / note.md
    ├── topic-plan.json
    ├── profile.json
    └── images/…                     # 全部登记进 package_images[]
```

## 2. 命名规则

| 对象 | 规则 |
|---|---|
| 任务目录 | `iv_` / `cp_` + 任务 ID + `_` + 修订号 |
| Grid 整板与切图 | `board.png`、`images/NN.png`（两位数字，行优先） |
| Sequence 帧 | `images/NN.png`（两位数字，`index` 对应） |
| 内容正文 | `article.json/md/html`、`note.json/md` |
| 选题与画像 | `topic-plan.json`、`profile.json` |
| 封面 | `covers/cover-wide.<ext>`、`covers/cover-square.<ext>` |
| 包清单 | 根目录 `delivery-package.json` |
| ZIP | 与目录同名 + `.zip` |

- 图片格式保持源格式；转换格式时必须记录 `media_type`，不得仅改扩展名。
- 早期草案的 `grid_board.png`、`tiles/` 已废弃。

## 3. 包清单（`delivery-package.json`）

必填字段：`package_id`、`package_type`、`root`、`entries[]`、`manifest_path`、`total_files`、`created_at`。

`entries[]` 每条：`path`（相对根、禁止绝对路径与 `..`）、`kind`（`image/metadata/text/research/cover/other`）、`sha256`、`bytes`、可选 `index`。

**硬要求**：

1. 覆盖包内**全部**文件（含 `manifest.json` 自身之外的所有产物；ZIP 场景下 ZIP 文件本身记录在 `archive`）。
2. 每条 `sha256` 必须与真实文件一致；不一致即 `PACKAGE_MANIFEST_INVALID`。
3. 不允许存在未登记文件；发现多余文件同样判失败。
4. 图片类条目必须与 manifest 中 `images[]` / `package_images[]` 的路径集合一致。

## 4. ZIP 规则

- 使用标准 ZIP；条目路径为相对路径，禁止 `..`、绝对路径、盘符、符号链接。
- 导入前逐条校验：路径规范化后必须位于目标根内（防 ZIP Slip）；遇到已存在文件不得静默覆盖。
- 限额：单条目解压后大小、总解压大小、条目数量、压缩比都要设上限（防压缩炸弹）；默认建议：单文件 ≤200MB、总量 ≤2GB、条目 ≤5000、压缩比 ≤200:1，可配置。
- 导出时先写目录与清单，再打包；ZIP 的 SHA256 记入 `archive.sha256`。
- 拒绝在文件名中出现控制字符或空字节（`PATH_TRAVERSAL_BLOCKED` / `ZIP_SLIP_BLOCKED`）。

## 5. 敏感信息

- 包内不得出现：明文 API Key、`secret://` 解析结果、研究服务凭据、登录 Cookie、内网 URL、上传令牌、临时签名 URL。
- 不在包内导出用户原始私人参考图（除非用户明确要求另存，且单独标注）。
- `execution` 只保留次数与成本估计，不保留请求头。

## 6. 校验顺序（实现建议）

```text
1. schema 校验（manifest + delivery-package）
2. 路径安全检查（规范化、根内、无控制字符）
3. 哈希与字节数比对（逐条 entries）
4. 覆盖率检查（目录内文件集合 == entries 集合）
5. 结构语义校验（Grid 切图覆盖 / Sequence 索引 / 正文块语法 / 封面同源比例）
6. 通过后才允许交付或推荐下游
```

任一步失败都必须返回错误码，**不得把未通过校验的包标记为完成**。
