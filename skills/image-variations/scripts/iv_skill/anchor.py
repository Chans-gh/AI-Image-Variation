"""一致性锚点：参考图锚定 / 纯文本图 1 锚定（D-07、SPEC 01 §6）。

限制：本仓库**不做图像理解**。有图时锚点描述来自调用方（宿主多模态能力）；
未提供的部分只描述"必须保持什么"，不编造主体的具体外观。
"""
from __future__ import annotations

from dataclasses import dataclass

from shared.errors import IVError

VALID_ANCHOR_TYPES = ("reference_image", "first_frame", "text_description")


@dataclass(frozen=True)
class Anchor:
    type: str
    description: str
    reference_path: str | None = None

    def to_dict(self) -> dict:
        payload = {"type": self.type, "description": self.description}
        if self.reference_path:
            payload["reference_path"] = self.reference_path
        return payload

    def prompt_block(self) -> str:
        if self.type == "first_frame":
            return f"以已生成的第 1 张图作为唯一形象锚点：{self.description}。后续每张都只引用它，不引用上一张。"
        if self.type == "reference_image":
            return f"以用户原图为唯一形象锚点：{self.description}。每张都必须与原图为同一主体。"
        return f"主体锚点：{self.description}"


def build_anchor(*, input_type: str, user_description: str | None = None,
                 reference_path: str | None = None) -> Anchor:
    """按输入类型建立锚点；纯文本必须先出图 1 才能建立，因此这里只登记类型。"""
    if input_type not in ("image_only", "text_only", "image_plus_text"):
        raise IVError("INVALID_INPUT", f"input_type 非法: {input_type!r}")
    description = (user_description or "").strip()
    if input_type == "text_only":
        if not description:
            raise IVError("INVALID_INPUT",
                          "纯文本模式必须给出主体描述（用于生成第 1 张锚点图），否则无法保证一致性")
        return Anchor("first_frame", description)
    if not reference_path:
        raise IVError("INVALID_INPUT", "图片输入必须登记参考图路径")
    if not description:
        description = "主体身份、外形结构、材质与标志性细节必须与原图完全一致"
    return Anchor("reference_image", description, reference_path)
