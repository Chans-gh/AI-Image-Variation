import argparse
import json
import sys
from pathlib import Path

from filelock import Timeout

from .contracts import PublishingError, json_bytes, read_json, require
from .delivery import build_content, pack_content, validate_content
from .planning import content_id, make_plan
from .source import assert_source, load_source


def _parser():
    parser = argparse.ArgumentParser(description="Image Content Publishing Skill")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("inspect", help="Validate and index a Skill 01 manifest or directory")
    command.add_argument("source")
    command.add_argument("--asset", action="append", help="Select an explicit subset by source asset ID")
    command.add_argument("--accept-unverified", action="store_true")
    command = commands.add_parser("plan", help="Validate and persist host profiles and topics")
    command.add_argument("source")
    command.add_argument("request")
    command.add_argument("--output", required=True)
    command = commands.add_parser("build", help="Assemble output from a validated plan and host drafts")
    command.add_argument("source")
    command.add_argument("plan")
    command.add_argument("drafts")
    command.add_argument("--output", required=True)
    command.add_argument("--topic", action="append", help="Approved topic ID from an exploration plan")
    command.add_argument("--previous", help="Previous partial content package to retry failed items")
    command = commands.add_parser("validate", help="Check the contents and hashes of a publishing package")
    command.add_argument("root")
    command.add_argument("--source", help="Also verify the original images and pinned source manifest")
    command = commands.add_parser("pack", help="Create a ZIP with the verified publishing files")
    command.add_argument("root")
    command.add_argument("--zip", required=True)
    return parser


def _run(args):
    if args.command == "inspect":
        return load_source(
            args.source, subset=args.asset, accept_unverified=args.accept_unverified
        ).inspection()
    if args.command == "plan":
        raw = read_json(args.request)
        require(isinstance(raw, dict), "TOPIC_PLAN_INVALID", "策划输入须为 JSON 对象")
        source = assert_source(args.source, raw.get("source"))
        plan = make_plan(source, raw)
        output = Path(args.output).absolute()
        require(
            not output.resolve().is_relative_to(source.root.resolve()),
            "UNSAFE_PATH",
            "策划文件须保存到来源目录外",
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as file:
            file.write(json_bytes(plan))
        return {
            "path": str(output),
            "status": plan["status"],
            "topics": [topic | {"content_id": content_id(topic)} for topic in plan["topics"]],
        }
    if args.command == "build":
        result = build_content(
            args.source,
            read_json(args.plan),
            read_json(args.drafts),
            args.output,
            topic_ids=args.topic,
            previous=args.previous,
        )
        return {"path": str(Path(args.output).absolute()), "manifest": result}
    if args.command == "validate":
        return {"manifest": validate_content(args.root, args.source)}
    return {"path": str(pack_content(args.root, args.zip))}


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        result = _run(args)
    except PublishingError as exc:
        print(json.dumps({"ok": False, **exc.as_dict()}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (ValueError, OSError, KeyError, TypeError, Timeout) as exc:
        code = "ARTIFACT_EXISTS" if isinstance(exc, FileExistsError) else "INVALID_INPUT"
        print(
            json.dumps(
                {"ok": False, "code": code, "message": "文件无法读取或写入，或输入结构无效"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
