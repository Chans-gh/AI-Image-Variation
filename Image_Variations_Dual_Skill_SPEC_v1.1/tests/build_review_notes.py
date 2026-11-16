"""生成 review/Review-Notes-v1.1.docx（v1.0 → v1.1 评审说明）。

用法：python tests/build_review_notes.py
依赖：python-docx（仅用于生成审阅文档，不参与契约校验）。
"""
from __future__ import annotations
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "review" / "Review-Notes-v1.1.docx"
CJK = "微软雅黑"

A_ITEMS = [
    ("A1", "小红书条目封面约束存在漏洞：`not/anyOf` 写法允许两个封面同时出现",
     "改为 `not{anyOf[required cover_wide, required cover_square]}`；新增负例 `xhs-with-cover` 回归", "content-artifact"),
    ("A2", "`actual_aspect_ratio` 既标必填又允许缺失，自相矛盾", "改为 `status=completed` 条件必填（if/then）", "image-artifact"),
    ("A3", "`completed_at` 无条件必填，运行中产物无法落盘", "同上，仅完成态必填", "image-artifact"),
    ("A4", "网格验收字段不在契约中，校验结果无处存放", "新增 `validation{tile_edge_px,tile_edge_ok,seam_score,seam_check_method,grid_attempts}`", "image-artifact / SPEC 03"),
    ("A5", "文件名标准与示例不一致（`grid_board.png`、`tiles/`）", "统一为 `board.png` + `images/NN.png`", "SPEC 01 §7.4"),
    ("A6", "`index` 语义未定义：整板是否计入、从 0 还是 1 开始", "明确 board=0、tile/frame=1..N 连续唯一、Grid 行优先", "image-artifact / AC-32"),
]

B_ITEMS = [
    ("B1", "只改第 2 篇时没有字段表达“修订了哪一版”", "新增 `content_revision` + `revises{content_id,content_revision,change_scope}`", "P2-23"),
    ("B2", "“严禁张冠李戴”无法校验：事实断言与证据无链接", "内容侧 `claim_ids[]` 与证据侧 `used_by_content_ids[]` 双向比对", "P2-24 / 负例 evidence-link-mismatch"),
    ("B3", "选题计划不落盘，事后无法复核", "每条目强制 `topic_plan_file` + `profile_file`，并计入包清单", "P2-25"),
    ("B4", "包内图片副本没有哈希，交付包自身不可校验", "新增 `package_images[]` 覆盖包内全部图片", "P2-26"),
    ("B5", "研究证据字段过薄，无法证明“确实检索过”", "增 `search_performed/search_provider/tools/queries/searched_at` 与 claim 级 `source_type/verified_at/contradicted_by`", "research-evidence"),
    ("B6", "缺少 4 个必要契约", "补 `direction-registry`、`asset-profile`、`job-state`、`delivery-package`", "contracts/"),
]

C_ITEMS = [
    ("C1", "12 个方向只有摘要表，没有可执行规则正文", "`references/directions/`：registry.json + 12 份生成文档 + 索引", "AC-25 / INT-18"),
    ("C2", "没有 Prompt 模板，实现只能各自发挥", "`grid-prompt.md`（grid-prompt-v1）、`sequence-prompt.md`（三分支）", "AC-12 / AC-16"),
    ("C3", "没有 Provider 能力矩阵与字段映射", "`provider-compat.md`：能力声明、底线、映射、错误映射、待实测", "AC-21 / AC-22"),
    ("C4", "平台规格散落在正文，无法配置化", "`platform-config.json`：产品建议与平台要求分离，未验证项为 null", "O-03 / O-04"),
    ("C5", "错误码分散在两份 SPEC，数量与命名不一致", "`error-registry.json`：36 个错误码唯一真相源", "两份 SPEC §错误处理"),
    ("C6", "没有可交付的评审材料", "本文件（Word 版评审说明）", "review/"),
]

D_ITEMS = [
    ("D1", "“无缝”是硬闸门但不可测量", "D-24：seam_score（boundary_gradient_v1），≥0.85 通过 / 0.60–0.85 风险 / <0.60 失败", "AC-13 / AC-28"),
    ("D2", "Grid 默认档位不合理：2×2 用 1K，单格仅 512px", "D-21：按满足单格长边下限（默认 1024px）的最低档位推导", "AC-06 / AC-27"),
    ("D3", "没有调用次数与成本模型，30 张图与 16 帧成本无约束", "D-25：Grid ≤3 次尝试、单帧 +2 次重试、批量 ≤5 篇；超预算报码并拆批", "AC-29 / INT-25"),
    ("D4", "模型能力底线隐含在正文，运行期才发现", "D-26：显式底线清单，声明缺失即不达标", "AC-30"),
    ("D5", "质量类验收项不可判定（“质量好”“差异化”）", "D-30：0–5 评分卡，总分 ≥18/25 且单项 ≥2；批量 distinctness ≥3", "P2-25"),
    ("D6", "“严格图文交替”与“不凑字数”互相冲突", "D-22：引入 image_group（2–4 张），块语法 TEXT (TEXT|IMAGE|GROUP)* TEXT", "P2-21 / INT-26"),
    ("D7", "“仅 HTTPS”会直接挡住本地自建兼容服务", "D-29：allow_local_endpoint 默认关闭、仅回环、状态入执行记录且不导出", "AC-22"),
]

E_ITEMS = [
    ("E1", "没有负例夹具，校验器只证明“好数据能过”", "examples/invalid/ 10 组负例 + test_negative_fixtures.py", "INT-23"),
    ("E2", "没有 Provider Mock，错误路径不可回归", "tests/mock_provider.py：19 项自测（鉴权/限流/超时/拒绝/坏图/预算）", "AC-20 / AC-29"),
    ("E3", "校验依赖 jsonschema，部分环境无法加载（rpds 签名问题）", "tests/schema_lite.py：标准库实现，全包离线可测", "全包"),
    ("E4", "缺少 Sequence → 内容示例，第二阶段链路未验证", "examples/valid-content-from-sequence/", "INT-22"),
    ("E5", "没有 AC 与证据的对应关系", "tests/ACCEPTANCE_MATRIX.md（AC-01..32、P2-01..26、INT-01..26）", "全量"),
]

NEW_DECISIONS = [
    ("D-21", "Grid 档位按单格长边下限推导", "2×2@1K 单格仅 512px，做正文图必然放大"),
    ("D-22", "image_group（2–4 张）作为一个图块", "避免 16 张图逼出 17 段凑字文字"),
    ("D-23", "封面放大 ≤1.0 正常 / 1.0–1.5 警示 / >1.5 拒绝", "“像素放大不等于细节”需要可执行阈值"),
    ("D-24", "seam_score 可测量接缝", "原“无可见缝隙”不可测、不可回归"),
    ("D-25", "调用预算前置与拆批", "成本差异巨大，原版无预算模型"),
    ("D-26", "能力底线显式清单", "能力问题不应留到运行期"),
    ("D-27", "ZIP Slip / 压缩炸弹 / 覆盖冲突防护", "包会跨机器传输"),
    ("D-28", "交付必须带 delivery-package 清单", "缺“包是否完整”的单一事实源"),
    ("D-29", "本地端点开发例外（默认关闭）", "仅 HTTPS 挡住本地兼容服务"),
    ("D-30", "选题/内容质量评分卡（≥18/25）", "质量验收项原本不可判定"),
]

OPEN_ITEMS = [
    ("O-03 / O-04", "公众号与小红书现行平台规格未实测", "platform-config.json 中 platform_requirement=null，上线前回填"),
    ("O-10", "模型实际分辨率、无缝成功率、阈值校准", "1024/768/512、0.85/0.60、1.5×、18/25 均为可配置默认值，需真实服务实测"),
    ("O-11", "12 方向正文与原参考包逐句一致性", "原包未随 v1.0 交付，本版按摘要表重建并标注来源"),
    ("O-12", "v1.0 旧产物迁移工具", "1.1 可读 1.0 清单但标记 validation_unverified，暂无批量升级工具"),
    ("O-13", "allow_local_endpoint 是否在发布版保留", "当前默认关闭、仅回环，发布前再确认"),
]


def style_doc(doc: Document):
    normal = doc.styles["Normal"]
    normal.font.name = CJK
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), CJK)
    for name in ("Heading 1", "Heading 2", "Heading 3"):
        st = doc.styles[name]
        st.font.name = CJK
        st.font.color.rgb = RGBColor(0x1F, 0x3B, 0x57)
        st.element.rPr.rFonts.set(qn("w:eastAsia"), CJK)


def add_table(doc: Document, headers, rows, widths=None):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for i, head in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = ""
        run = cell.paragraphs[0].add_run(head)
        run.bold = True
    for row in rows:
        cells = table.add_row().cells
        for i, value in enumerate(row):
            cells[i].text = str(value)
    if widths:
        for row in table.rows:
            for i, width in enumerate(widths):
                row.cells[i].width = width
    doc.add_paragraph()
    return table


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    style_doc(doc)

    title = doc.add_heading("Image Variations 双 Skill 规范包 评审说明", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    meta = doc.add_paragraph()
    meta.add_run("版本：v1.0 → v1.1　|　日期：2026-11-16　|　结论：接受评审意见，出修订版，v1.0 冻结\n").bold = True
    meta.add_run("范围：只评审 v1.0 现有 SPEC，不新增产品需求；修订版成为后续 Skill 开发基线。")

    doc.add_heading("一、评审结论摘要", level=1)
    for text in [
        "v1.0 的产品决策与两个 Skill 的边界是清楚的，不需要推翻；问题集中在三处：契约自相矛盾、跨 Skill 链路缺少可追溯字段、以及若干验收标准不可判定。",
        "本次共提出 5 类 30 项处置（A 契约 6、B 链路 6、C 缺失交付物 6、D 决策缺口 7、E 测试 5），全部在本版落地。",
        "其中 D2（Grid 档位）与 D6（文—图交替）按评审建议处理：档位改为按单格长边下限推导；交替改为块语法并允许 2–4 张图集块。",
        "本版新增 10 项修订决策（D-21..D-30）与 13 项待定事项（O-01..O-13）。所有阈值均为可配置默认值，标注需真实服务校准。",
    ]:
        doc.add_paragraph(text)

    doc.add_heading("二、A 类：契约矛盾与漏洞", level=1)
    doc.add_paragraph("这一类是“说了但没说清”，会导致两个实现各自解读。")
    add_table(doc, ["编号", "问题", "处置", "落点"], A_ITEMS)

    doc.add_heading("三、B 类：跨 Skill 链路缺失", level=1)
    doc.add_paragraph("这一类会让“可追溯”“可局部重做”“严禁张冠李戴”变成无法校验的口号。")
    add_table(doc, ["编号", "问题", "处置", "验证"], B_ITEMS)

    doc.add_heading("四、C 类：缺失交付物", level=1)
    doc.add_paragraph("SPEC 正文引用了却不存在的材料，本次全部补齐。")
    add_table(doc, ["编号", "缺失", "交付物", "对应验收"], C_ITEMS)

    doc.add_heading("五、D 类：决策缺口", level=1)
    doc.add_paragraph("这一类是“规范里有要求，但没有可执行判据”。")
    add_table(doc, ["编号", "问题", "处置", "落点"], D_ITEMS)

    doc.add_heading("六、E 类：测试与夹具", level=1)
    doc.add_paragraph("v1.0 的校验器只能证明“好数据能过”，无法证明坏数据会被拒。")
    add_table(doc, ["编号", "问题", "处置", "验收"], E_ITEMS)

    doc.add_heading("七、新增修订决策", level=1)
    add_table(doc, ["编号", "决策", "理由"], NEW_DECISIONS)

    doc.add_heading("八、未决事项", level=1)
    add_table(doc, ["编号", "事项", "当前处置"], OPEN_ITEMS)

    doc.add_heading("九、验证结果", level=1)
    add_table(doc, ["检查", "结果"], [
        ("契约 JSON 解析", "10/10 通过"),
        ("契约与夹具校验（正例 + 负例）", "24/24 通过（14 正例、10 负例）"),
        ("Mock Provider 错误映射与预算", "19/19 通过"),
        ("方向文档由注册表生成", "12/12 生成成功"),
        ("第三方依赖", "仅 Pillow；JSON Schema 用包内标准库实现"),
    ])
    doc.add_paragraph("说明：examples/ 下图片为本地绘制的合成夹具，用于验证数据契约，不代表模型画质；"
                      "平台规格、真实分辨率与无缝成功率仍需实测（O-03/O-04/O-10）。")

    doc.add_heading("十、后续建议", level=1)
    for text in [
        "1. 开发阶段先落 D-26 能力底线与 D-25 预算，这两项决定“能不能开工”和“会不会烧钱”。",
        "2. 拿到真实模型后立即校准 D-21 的单格下限与 D-24 的接缝阈值，并把结果回填 platform-config 与 SPEC。",
        "3. 若后续能获得原方向参考包，按 O-11 做逐句比对，必要时升级 registry 到 1.1。",
        "4. 平台规格（O-03/O-04）实测回填后，再决定是否把小红书/公众号的字符与像素限制改为硬校验。",
    ]:
        doc.add_paragraph(text)

    doc.save(OUT)
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
