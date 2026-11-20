import re
from html import escape

from .contracts import check_schema, nonblank, platform_config, require
from .planning import check_selection, normalized_text

INVALID = "CONTENT_STRUCTURE_INVALID"
ARTICLE_BLOCKS_VERSION = "1.2"
TAG = re.compile(r"#([^\W_]\w*)#?")
MARKDOWN_ESCAPES = str.maketrans({char: "\\" + char for char in "\\[`~"})


def validate_article(article, selected_ids, assets, branch, *, allow_legacy=False):
    check_schema("article-body", article, INVALID)
    version = article.get("blocks_version", "1.1")
    require(
        version == ARTICLE_BLOCKS_VERSION or (allow_legacy and version == "1.1"),
        INVALID,
        "新公众号正文须采用逐张图文交替结构",
    )
    require(nonblank(article["title"]) and nonblank(article["summary"]), INVALID, "标题或摘要为空")
    kinds, used = [], []
    for block in article["blocks"]:
        kind = block["type"]
        kinds.append(kind)
        if kind == "text":
            require(nonblank(block["content"]), INVALID, "正文含空文字块")
        elif kind == "image":
            used.append(block["asset_id"])
        else:
            group = [image["asset_id"] for image in block["images"]]
            check_selection(group, assets, branch, consecutive=True)
            used.extend(group)
    require(
        len(kinds) >= 3
        and len(kinds) % 2 == 1
        and all((kind == "text") == (i % 2 == 0) for i, kind in enumerate(kinds)),
        INVALID,
        "公众号正文须以文字开头和结尾，每两张图之间需要正文段落",
    )
    require(used == selected_ids, INVALID, "正文实际用图须与选题的素材及顺序一致")


def validate_note(note, selected_ids):
    check_schema("xiaohongshu-note", note, INVALID)
    config = platform_config()["platforms"]["xiaohongshu"]
    require(
        all(nonblank(note[field]) for field in ("title", "body_with_hashtags", "first_comment")),
        INVALID,
        "笔记需要标题、正文和首评",
    )
    tags = note.get("hashtags", [])
    labels = [tag.strip("#") for tag in tags]
    require(
        len(tags) >= config["body"]["hashtag_min"]
        and len(labels) == len(set(labels))
        and all(TAG.fullmatch("#" + label) for label in labels)
        and set(labels) == set(TAG.findall(note["body_with_hashtags"])),
        INVALID,
        "正文实际话题须与 hashtags 一一对应",
    )
    prose = normalized_text(TAG.sub("", note["body_with_hashtags"]))
    comment = normalized_text(note["first_comment"])
    require(prose and comment and comment not in prose, INVALID, "首评需要补充独立内容")
    require(note["selected_asset_ids"] == selected_ids, INVALID, "笔记用图须与选题计划一致")
    for name, field in (("title", "title"), ("body", "body_with_hashtags")):
        maximum = config[name]["max_chars"]
        require(maximum is None or len(note[field]) <= maximum, INVALID, "笔记超过配置字数")
    maximum = config["images"]["max_count"]
    require(maximum is None or len(selected_ids) <= maximum, INVALID, "笔记超过配置图片数量")


def _paragraphs(text):
    return [
        "<p>" + "<br>\n".join(escape(line) for line in paragraph.splitlines()) + "</p>"
        for paragraph in re.split(r"\n\s*\n", text.strip())
    ]


def _markdown_text(text):
    return escape(text, quote=False).translate(MARKDOWN_ESCAPES)


def _article_image(block, paths):
    path = paths[block["asset_id"]]
    alt = block.get("alt", "")
    caption = block.get("caption", "")
    md_alt = _markdown_text(alt).replace("]", "\\]").replace("\n", " ")
    md = f"![{md_alt}]({path})"
    figure = ["<figure>", f'<img src="{escape(path)}" alt="{escape(alt)}" loading="lazy">']
    if caption:
        md += "\n" + _markdown_text(caption)
        figure.append("<figcaption>" + escape(caption) + "</figcaption>")
    return md, [*figure, "</figure>"]


def article_views(article, paths):
    title, summary = article["title"], article["summary"]
    markdown = ["# " + _markdown_text(title), _markdown_text(summary)]
    html = [
        "<article>",
        "<header>",
        "<h1>" + escape(title) + "</h1>",
        *_paragraphs(summary),
        "</header>",
        '<section class="article-body">',
    ]
    for block in article["blocks"]:
        if block["type"] == "text":
            html.append('<div class="text-block">')
            if block.get("heading"):
                level = block.get("level", "h2")
                markdown.append(("#" * int(level[-1])) + " " + _markdown_text(block["heading"]))
                html.append(f"<{level}>" + escape(block["heading"]) + f"</{level}>")
            markdown.append(_markdown_text(block["content"]))
            html.extend(_paragraphs(block["content"]))
            html.append("</div>")
        else:
            image_md, image_html = _article_image(block, paths)
            markdown.append(image_md)
            html.extend(image_html)
    html.append("</section>")
    if article.get("ai_disclosure"):
        markdown.append(_markdown_text(article["ai_disclosure"]))
        html.extend(["<footer>", *_paragraphs(article["ai_disclosure"]), "</footer>"])
    html.append("</article>")
    document = "\n".join(
        [
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width,initial-scale=1">',
            "<title>" + escape(title) + "</title>",
            (
                "<style>body{font:17px/1.75 system-ui,sans-serif;max-width:720px;margin:2rem auto;padding:0 1rem}"
                "figure{margin:1.5rem 0}img{display:block;max-width:100%;height:auto;margin:.6rem auto}"
                "figcaption{font-size:.9em;color:#555}</style>"
            ),
            *html,
            "</html>",
        ]
    )
    return {
        "article_md": ("article.md", "\n\n".join(markdown) + "\n"),
        "article_html": ("article.html", document),
    }


def note_views(note):
    text = "\n\n".join(
        [
            "# " + escape(note["title"], quote=False),
            escape(note["body_with_hashtags"], quote=False),
            *([escape(note["ai_disclosure"], quote=False)] if note.get("ai_disclosure") else []),
            "## 首条评论",
            escape(note["first_comment"], quote=False),
        ]
    )
    return {"note_md": ("note.md", text + "\n")}
