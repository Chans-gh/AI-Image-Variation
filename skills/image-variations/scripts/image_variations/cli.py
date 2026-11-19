import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from jsonschema.exceptions import ValidationError

from shared.artifacts import validate_artifact, verify_delivery

from .adapters.gemini_compatible import GeminiCompatible
from .adapters.host import CODEX_IMAGES_BASE_URL, CodexSkillProvider, HostFileProvider
from .adapters.mock import MockProvider
from .adapters.openai_compatible import OpenAICompatible
from .demo import run_demo
from .dimensions import plan_dimensions
from .errors import VariationError, require
from .jobs import create_job, get_status, recover_interrupted, repair_prompt, resume_job, review_job, step_job
from .packaging import import_package, package_artifact
from .prompts import render_template
from .request import resolve_request
from .resources import contracts_root


def load(path):
    return json.loads(Path(path).read_text())


def provider_for(config, state):
    profile = state["profile"]
    if state["environment"] == "mock":
        return MockProvider()
    kind = config.get("kind")
    if kind == "codex_skill":
        base_url = profile.get("base_url", CODEX_IMAGES_BASE_URL).rstrip("/")
        require(
            config.get("base_url", base_url).rstrip("/") == base_url,
            "MODEL_CAPABILITY_UNSUPPORTED",
            "Codex Images 端点与能力档案不一致",
        )
        return CodexSkillProvider(
            config["script_path"],
            profile["tool_script_sha256"],
            config.get("quality", "low"),
            base_url=base_url,
        )
    require(
        state["environment"] == "external",
        "BUILTIN_UNAVAILABLE",
        "请使用 --generated-image 导入宿主生成的图片",
    )
    require(
        config.get("base_url", "").rstrip("/") == profile.get("base_url", "").rstrip("/"),
        "MODEL_CAPABILITY_UNSUPPORTED",
        "执行端点与能力档案不一致",
    )
    env_name = config.get("api_key_env")
    require(
        isinstance(env_name, str) and env_name.isidentifier(), "AUTH_FAILED", "请选择存放凭据的环境变量名称"
    )
    key = os.environ.get(env_name, "")
    if profile["provider_type"] == "openai_image_compatible":
        return OpenAICompatible(config["base_url"], key)
    if profile["provider_type"] == "gemini_image_compatible":
        return GeminiCompatible(config["base_url"], key, api_revision=config.get("api_revision"))
    raise VariationError("MODEL_CAPABILITY_UNSUPPORTED", "没有适配选定的协议类型")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Image Variations Skill — SPEC v1.3")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("create", help="Validate and prepare a job; does not generate images")
    for key in ("request", "creative", "profile", "job", "output"):
        p.add_argument("--" + key, required=True)
    p.add_argument("--environment", choices=["external", "builtin", "mock"], default="external")
    p.add_argument("--endpoint")
    p = commands.add_parser("step", help="Generate one unit, then wait for visual review")
    p.add_argument("--job", required=True)
    p.add_argument("--config")
    p.add_argument("--generated-image", help="Real image from the selected builtin tool")
    p.add_argument("--confirm-uncertain-retry", action="store_true")
    p = commands.add_parser("review", help="Submit a hash-bound P1-09 review")
    p.add_argument("--job", required=True)
    p.add_argument("--report", required=True)
    p = commands.add_parser("repair", help="Apply P1-10 to the failed unit")
    p.add_argument("--job", required=True)
    p.add_argument("--report", required=True)
    p = commands.add_parser("status")
    p.add_argument("--job", required=True)
    p = commands.add_parser("recover", help="Resolve an interrupted call after checking provider results")
    p.add_argument("--job", required=True)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--confirm-no-result", action="store_true")
    group.add_argument("--generated-image")
    p = commands.add_parser("resume", help="Create a new revision for failed sequence indices")
    p.add_argument("--source", required=True)
    p.add_argument("--job", required=True)
    p.add_argument("--indices", nargs="+", type=int)
    p = commands.add_parser("validate")
    p.add_argument("path")
    p.add_argument("--allow-partial", action="store_true")
    p = commands.add_parser("pack")
    p.add_argument("path")
    p.add_argument("--zip", required=True)
    p = commands.add_parser("unpack")
    p.add_argument("archive")
    p.add_argument("--output", required=True)
    p = commands.add_parser("template")
    p.add_argument("id")
    p.add_argument("--context", required=True)
    p = commands.add_parser("preflight")
    p.add_argument("--request", required=True)
    p.add_argument("--profile", required=True)
    p.add_argument("--environment", default="external", choices=["external", "builtin", "mock"])
    p.add_argument("--endpoint")
    p = commands.add_parser("demo", help="Offline synthetic fixture; never calls a real model")
    p.add_argument("--mode", choices=["grid", "sequence"], default="grid")
    p.add_argument("--grid-size", type=int, default=3)
    p.add_argument("--count", type=int, default=4)
    p.add_argument("--branch", choices=["variants", "progression", "narrative"], default="variants")
    p.add_argument("--job")
    p.add_argument("--output", default="artifacts/mock")
    args = parser.parse_args(argv)
    try:
        cmd = args.command
        if cmd == "create":
            result = create_job(
                args.job,
                load(args.request),
                load(args.creative),
                load(args.profile),
                args.output,
                environment=args.environment,
                endpoint=args.endpoint,
            )
        elif cmd == "step":
            state = load(Path(args.job) / "job.json")
            provider = (
                HostFileProvider(args.generated_image)
                if args.generated_image
                else provider_for(load(args.config) if args.config else {}, state)
            )
            result = step_job(args.job, provider, confirm_uncertain_retry=args.confirm_uncertain_retry)
        elif cmd == "review":
            result = review_job(args.job, load(args.report))
        elif cmd == "repair":
            result = repair_prompt(args.job, load(args.report))
        elif cmd == "status":
            result = get_status(args.job)
        elif cmd == "recover":
            result = recover_interrupted(
                args.job, image_path=args.generated_image, confirm_no_result=args.confirm_no_result
            )
        elif cmd == "resume":
            result = resume_job(args.source, args.job, args.indices)
        elif cmd == "validate":
            result = validate_artifact(args.path, contracts_root(), require_complete=not args.allow_partial)
            verify_delivery(args.path, contracts_root())
            result = {
                "valid": True,
                "assets": len(result["content_assets"]),
                "native_resolution_verified": result["native_resolution_verified"],
                "validation_unverified": result["validation_unverified"],
            }
        elif cmd == "pack":
            result = {"path": str(package_artifact(args.path, args.zip))}
        elif cmd == "unpack":
            result = {"path": str(import_package(args.archive, args.output))}
        elif cmd == "template":
            result = {"prompt": render_template(args.id, load(args.context))}
        elif cmd == "preflight":
            result = plan_dimensions(
                resolve_request(load(args.request)), load(args.profile), args.environment, args.endpoint
            )
        else:
            result = run_demo(
                args.job or ".work/demo-" + uuid.uuid4().hex[:8],
                args.output,
                args.mode,
                args.grid_size,
                args.count,
                args.branch,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except VariationError as error:
        print(json.dumps({"ok": False, **error.as_dict()}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError, ValidationError):
        # Do not echo user files or arbitrary exception messages to logs.
        print(
            json.dumps(
                {
                    "ok": False,
                    "code": "INVALID_LOCAL_DATA",
                    "message": "本地文件、配置或产物校验失败；请核对参数和格式",
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
