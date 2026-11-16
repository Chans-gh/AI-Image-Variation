"""Mock Provider：把上游各类失败归一化为错误码，并演示调用预算/拆批。

用途：在没有真实 API Key 的情况下验证 SPEC 01 §9.2 / §9.7 与
references/provider-compat.md 的错误映射、重试与预算逻辑。

用法：
    python tests/mock_provider.py --selftest
    python tests/mock_provider.py --scenario rate_limited
"""
from __future__ import annotations
import io
import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema_lite  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
JOB_SCHEMA = json.loads((ROOT / "contracts" / "job-state.schema.json").read_text(encoding="utf-8"))

SCENARIOS = {
    "success": {"status": 200, "body": "image/png", "note": "正常返回"},
    "auth": {"status": 401, "body": "unauthorized", "note": "密钥无效"},
    "forbidden": {"status": 403, "body": "forbidden", "note": "无权限"},
    "rate_limited": {"status": 429, "retry_after": 5, "body": "slow down", "note": "限流"},
    "timeout": {"status": None, "error": "ReadTimeout", "note": "网络超时"},
    "bad_param": {"status": 400, "body": "unsupported aspect_ratio 5:3", "note": "参数不支持"},
    "moderation": {"status": 400, "body": "content policy violation", "note": "内容拒绝"},
    "empty": {"status": 200, "body": "application/json", "note": "返回体无图片"},
    "corrupt": {"status": 200, "body": "image/png", "note": "字节损坏"},
    "wrong_ratio": {"status": 200, "body": "image/png", "note": "返回图比例不符"},
}

RATIO_TABLE = {"1:1": (1, 1), "3:4": (3, 4), "4:3": (4, 3), "2.35:1": (2.35, 1), "16:9": (16, 9)}


def normalize(provider_response: dict, expected_ratio: str, adapter_supports=("1:1", "3:4", "4:3"),
              image_bytes: bytes | None = None) -> tuple[str | None, dict]:
    """把上游响应归一化为 (错误码或 None, 补充信息)。"""
    if provider_response.get("error") == "ReadTimeout":
        return "PROVIDER_TIMEOUT", {"retryable": True}
    status = provider_response.get("status")
    body = str(provider_response.get("body", ""))
    if status in (401, 403):
        return "AUTH_FAILED", {"retryable": False}
    if status == 429:
        return "RATE_LIMITED", {"retryable": True,
                                "retry_after": provider_response.get("retry_after")}
    if status == 400 and "policy" in body:
        return "MODEL_REFUSAL", {"retryable": False}
    if status == 400:
        if "aspect_ratio" in body:
            return "UNSUPPORTED_RATIO", {"retryable": False, "detail": body}
        return "MODEL_CAPABILITY_UNSUPPORTED", {"retryable": False, "detail": body}
    if status != 200:
        return "MODEL_CAPABILITY_UNSUPPORTED", {"retryable": False, "detail": f"status={status}"}
    if provider_response.get("body") != "image/png":
        return "IMAGE_VALIDATION_FAILED", {"retryable": True, "detail": "响应中没有可解码图片"}
    if expected_ratio not in adapter_supports:
        return "UNSUPPORTED_RATIO", {"retryable": False,
                                     "detail": f"{expected_ratio} 不在 {list(adapter_supports)}"}
    if image_bytes is None:
        return "IMAGE_VALIDATION_FAILED", {"retryable": True, "detail": "空字节"}
    try:
        with Image.open(io.BytesIO(image_bytes)) as im:
            actual = im.size
    except Exception as exc:  # noqa: BLE001
        return "IMAGE_VALIDATION_FAILED", {"retryable": True, "detail": f"解码失败 {exc}"}
    num, den = RATIO_TABLE[expected_ratio]
    target = num / den
    error_ratio = abs((actual[0] / actual[1]) - target) / target
    if error_ratio > 0.02:
        return "IMAGE_VALIDATION_FAILED", {"retryable": True, "aspect_ratio_error_ratio": round(error_ratio, 4)}
    return None, {"size": actual, "aspect_ratio_error_ratio": round(error_ratio, 4)}


def fake_png(size=(1024, 1024)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "#8FA9C4").save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------- budget
def plan_calls(mode: str, frame_count: int = 0, input_type: str = "text_only",
               retry_budget: int = 2) -> int:
    """排产时的计划调用数。

    Grid 一次成板（+重试预算）；Sequence 每帧一次独立请求（纯文本模式同样 N 次：
    图 1 先出，图 2..N 带参考图），输入类型只决定哪几次请求携带参考图，不改变次数。
    """
    if mode == "grid":
        return 1 + retry_budget
    assert 4 <= frame_count <= 16, "Sequence 张数必须在 4–16"
    assert input_type in ("text_only", "image_only", "image_plus_text")
    return frame_count + retry_budget


def check_budget(planned: int, limit: int | None) -> tuple[bool, str | None]:
    if limit is not None and planned > limit:
        return False, "BUDGET_EXCEEDED"
    return True, None


def split_suggestion(total: int, per_batch: int = 5) -> list[list[int]]:
    batches, start = [], 1
    while start <= total:
        end = min(start + per_batch - 1, total)
        batches.append(list(range(start, end + 1)))
        start = end + 1
    return batches


def job_state(job_id: str, skill: str, phase: str, completed: int, total: int,
              execution: dict, errors=None) -> dict:
    return {
        "job_id": job_id, "skill": skill, "status": "running", "phase": phase,
        "progress": {"completed_count": completed, "total_count": total, "unit": "image"},
        "execution": execution, "retry_counts": {}, "warnings": [],
        "errors": errors or [], "created_at": "2026-11-16T01:00:00Z",
        "updated_at": "2026-11-16T01:05:00Z",
    }


def selftest() -> int:
    failures = []

    def expect(name, got, want):
        ok = got == want
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: got={got!r} want={want!r}")
        if not ok:
            failures.append(name)

    expect("success", normalize(SCENARIOS["success"], "1:1", image_bytes=fake_png())[0], None)
    expect("auth", normalize(SCENARIOS["auth"], "1:1")[0], "AUTH_FAILED")
    expect("forbidden", normalize(SCENARIOS["forbidden"], "1:1")[0], "AUTH_FAILED")
    expect("rate_limited", normalize(SCENARIOS["rate_limited"], "1:1")[0], "RATE_LIMITED")
    expect("rate_limited.retry_after",
           normalize(SCENARIOS["rate_limited"], "1:1")[1]["retry_after"], 5)
    expect("timeout", normalize(SCENARIOS["timeout"], "1:1")[0], "PROVIDER_TIMEOUT")
    expect("bad_param", normalize(SCENARIOS["bad_param"], "1:1")[0], "UNSUPPORTED_RATIO")
    expect("moderation", normalize(SCENARIOS["moderation"], "1:1")[0], "MODEL_REFUSAL")
    expect("empty", normalize(SCENARIOS["empty"], "1:1")[0], "IMAGE_VALIDATION_FAILED")
    expect("corrupt", normalize(SCENARIOS["corrupt"], "1:1", image_bytes=b"not-a-png")[0],
           "IMAGE_VALIDATION_FAILED")
    expect("wrong_ratio", normalize(SCENARIOS["wrong_ratio"], "1:1",
                                    image_bytes=fake_png((1024, 512)))[0], "IMAGE_VALIDATION_FAILED")
    expect("ratio_supported", normalize({"status": 200, "body": "image/png"}, "2.35:1",
                                        adapter_supports=("1:1",),
                                        image_bytes=fake_png((1024, 436)))[0], "UNSUPPORTED_RATIO")

    # 预算
    expect("grid planned", plan_calls("grid"), 3)
    expect("sequence text_only planned", plan_calls("sequence", 4, "text_only"), 6)
    expect("sequence image planned", plan_calls("sequence", 16, "image_only"), 18)
    expect("budget ok", check_budget(3, 3), (True, None))
    expect("budget exceeded", check_budget(5, 3), (False, "BUDGET_EXCEEDED"))
    expect("split 16", split_suggestion(16, 5), [[1, 2, 3, 4, 5], [6, 7, 8, 9, 10],
                                                 [11, 12, 13, 14, 15], [16]])
    state = job_state("iv_demo_001", "image_variations", "generating", 2, 4,
                      {"calls_planned": 5, "calls_used": 2, "budget_exceeded": False})
    errs = schema_lite.validate(state, JOB_SCHEMA, JOB_SCHEMA)
    expect("job-state schema", errs, [])
    return 1 if failures else 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    if "--scenario" in sys.argv:
        name = sys.argv[sys.argv.index("--scenario") + 1]
        code, extra = normalize(SCENARIOS[name], "1:1", image_bytes=fake_png())
        print(json.dumps({"scenario": name, "code": code, "extra": extra}, ensure_ascii=False, indent=2))
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
