import ipaddress
from urllib.parse import parse_qs, urlsplit

from .contracts import check_schema, nonblank, public_payload, require


def is_public_source(url):
    try:
        parts = urlsplit(url)
        hostname = (parts.hostname or "").rstrip(".")
        if (
            parts.scheme not in {"https", "http"}
            or parts.username is not None
            or parts.password is not None
            or hostname.endswith((".local", ".internal", ".localhost", ".lan", ".invalid"))
        ):
            return False
        if {"token", "access_token", "key", "api_key", "sig", "signature", "x-amz-signature"} & {
            key.lower() for key in parse_qs(parts.query, keep_blank_values=True)
        }:
            return False
        try:
            return ipaddress.ip_address(hostname).is_global
        except ValueError:
            return "." in hostname and not hostname.replace(".", "").isdigit()
    except ValueError:
        return False


def validate_evidence(level, content_id, claim_ids, pack=None):
    link_code = "EVIDENCE_LINK_MISMATCH"
    require(
        isinstance(claim_ids, list)
        and all(nonblank(x) for x in claim_ids)
        and len(set(claim_ids)) == len(claim_ids),
        link_code,
        "claim_ids 须为一组唯一断言 ID",
    )
    if level == "R0" and pack is None:
        pack = {"research_level": "R0", "search_performed": False, "status": "not_required", "claims": []}
    require(pack is not None, "RESEARCH_UNAVAILABLE", "该选题需要由宿主提交检索和核验记录")
    check_schema("research-evidence", pack, "EVIDENCE_INSUFFICIENT")
    public_payload(pack)
    require(pack["research_level"] == level, "EVIDENCE_INSUFFICIENT", "证据包与选题研究层级不一致")
    if level == "R0":
        require(not claim_ids and not pack["claims"], link_code, "R0 正文不得引用外部断言")
        require(
            not pack["search_performed"]
            and pack["status"] == "not_required"
            and not any(pack.get(key) for key in ("search_provider", "tools", "queries", "searched_at")),
            "EVIDENCE_INSUFFICIENT",
            "R0 不能记录完成检索",
        )
        return pack
    require(pack["search_performed"], "RESEARCH_UNAVAILABLE", "缺少实际检索记录，无法完成事实型稿件")
    require(
        nonblank(pack.get("search_provider"))
        and nonblank(pack.get("searched_at"))
        and all(pack.get(key) and all(nonblank(x) for x in pack[key]) for key in ("tools", "queries")),
        "EVIDENCE_INSUFFICIENT",
        "检索记录必须包含来源、工具、查询词和日期",
    )
    require(
        pack["status"] in {"verified", "partially_verified"}
        and (level != "R2" or pack["status"] == "verified"),
        "EVIDENCE_INSUFFICIENT",
        "事实稿件的关键证据尚未通过核验",
    )
    claims = {}
    reverse = set()
    for claim in pack["claims"]:
        claim_id, used_by = claim["claim_id"], claim.get("used_by_content_ids", [])
        require(
            claim_id not in claims and used_by in ([], [content_id]),
            link_code,
            "证据包重复或引用了错误的内容项",
        )
        if used_by:
            reverse.add(claim_id)
        if claim["support_status"] == "verified":
            require(
                claim.get("source_type") in {"primary", "secondary", "community"}
                and nonblank(claim.get("source_title"))
                and nonblank(claim.get("retrieved_at"))
                and is_public_source(claim.get("source_url", ""))
                and not claim.get("contradicted_by"),
                "EVIDENCE_INSUFFICIENT",
                "已核验断言缺少有效的外部来源，或存在未消解的冲突",
            )
        claims[claim_id] = claim
    require(set(claim_ids) == reverse, link_code, "正文断言与证据反向引用不一致")
    require(
        bool(claim_ids)
        and all(
            claims[key]["support_status"] == "verified"
            and (level != "R2" or claims[key].get("source_type") in {"primary", "secondary"})
            for key in claim_ids
        ),
        "EVIDENCE_INSUFFICIENT",
        "正文包含缺少可靠证据的事实断言",
    )
    return pack
