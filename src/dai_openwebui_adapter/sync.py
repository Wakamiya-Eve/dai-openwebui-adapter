"""Authorized unattended reconciliation; an empty plan never invokes apply."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sys
import uuid

from .clients import Session
from .config import load_config
from .errors import AdapterError, Conflict
from .planner import observe
from .publisher import apply, journal_dir, verify
from .state import locked, read_json, write_json


def sync(config, session):
    if config.state_dir.is_symlink():
        raise AdapterError("State directory must not be a symlink")
    config.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Serialize complete timer/manual runs without nesting the publisher's own lock.
    with locked(config.state_dir / "sync"):
        with locked(config.state_dir):
            for path in journal_dir(config).glob("*.json"):
                value = read_json(path)
                if value.get("scope") == config.scope and value.get("status") != "rolled_back":
                    if value.get("status") != "completed":
                        raise Conflict("Incomplete publication requires explicit recovery")
        plan = observe(config, session).plan
        journal = None
        plan_path = None
        if plan["actions"]:
            directory = config.state_dir / "plans"
            if directory.is_symlink():
                raise AdapterError("Plan directory must not be a symlink")
            directory.mkdir(mode=0o700, exist_ok=True)
            plan_path = directory / (uuid.uuid4().hex + ".json")
            write_json(plan_path, plan)
            # apply independently re-observes and compares every input before writing.
            journal = apply(config, session, plan)["journal"]
        validation = verify(config, session)
        result = {
            "status": "updated" if plan["actions"] else "unchanged",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "actions": len(plan["actions"]),
            "apply_called": bool(plan["actions"]),
            "plan_hash": plan["plan_hash"],
            "plan": str(plan_path) if plan_path else None,
            "journal": journal,
            "verification": validation,
        }
        write_json(config.state_dir / "last-success.json", result, replace=True)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        with Session(config) as session:
            result = sync(config, session)
        # Detailed capability evidence stays in private state, outside the journal.
        print(
            json.dumps(
                {
                    key: result[key]
                    for key in (
                        "status",
                        "observed_at",
                        "actions",
                        "apply_called",
                        "plan_hash",
                        "journal",
                    )
                }
            )
        )
        return 0
    except AdapterError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError):
        print(
            '{"error":"Invalid data or local I/O failure; no sensitive details are logged"}',
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
