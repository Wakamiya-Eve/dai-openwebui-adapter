"""Noninteractive control-plane CLI; live changes require an explicit reviewed plan."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .clients import Session
from .config import load_config
from .errors import AdapterError
from .planner import observe
from .publisher import apply, rollback, verify
from .state import read_json, write_json


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--config",
        required=True,
        help="adapter YAML; credentials are environment references",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser(
        "plan", help="read APIs and produce a reviewable plan; no remote writes"
    )
    plan.add_argument(
        "--output",
        help="new private JSON plan file; existing files are never overwritten",
    )
    publish = commands.add_parser(
        "apply", help="apply exactly the reviewed plan after revalidation"
    )
    publish.add_argument("--plan-file", required=True)
    commands.add_parser(
        "verify",
        help="check model records, mappings and visible catalogue; no inference",
    )
    restore = commands.add_parser(
        "rollback", help="restore updates and disable newly created entries"
    )
    restore.add_argument("--journal", required=True)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        with Session(config) as session:
            if args.command == "plan":
                result = observe(config, session).plan
                if args.output:
                    write_json(args.output, result)
                    result = {
                        "status": "planned",
                        "actions": len(result["actions"]),
                        "plan_hash": result["plan_hash"],
                        "plan_file": args.output,
                    }
            elif args.command == "apply":
                result = apply(config, session, read_json(args.plan_file))
            elif args.command == "rollback":
                result = rollback(config, session, args.journal)
            else:
                result = verify(config, session)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except AdapterError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError):
        # Never expose file contents, HTTP diagnostics or Pydantic values through a traceback.
        print(
            '{"error":"Invalid data or local I/O failure; no sensitive details are logged"}',
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
