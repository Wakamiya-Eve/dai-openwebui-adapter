"""Explicit apply/rollback with optimistic concurrency and durable per-write recovery."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import uuid

from .errors import AdapterError, Conflict
from .model import digest, form
from .planner import check_plan, materialize, observe
from .policy import assert_owned
from .state import locked, read_json, write_json

JOURNAL = "dai.openwebui-journal.v1"
INCOMPLETE = {"applying", "failed", "rolling_back", "rollback_failed"}


def journal_dir(config):
    path = config.state_dir / "journals"
    if path.is_symlink():
        raise AdapterError("Journal directory must not be a symlink")
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def apply(config, session, reviewed):
    check_plan(reviewed)
    if reviewed["config_hash"] != config.fingerprint:
        raise Conflict("Reviewed plan belongs to a different configuration")
    with locked(config.state_dir):
        directory = journal_dir(config)
        for file in directory.glob("*.json"):
            old = read_json(file)
            if old.get("scope") == config.scope and old.get("status") in INCOMPLETE:
                raise Conflict(
                    "An incomplete operation must be rolled back before applying another plan"
                )
        fresh = observe(config, session).plan
        if fresh != reviewed:
            raise Conflict("Sources or OpenWebUI state changed after review; create a new plan")
        path = directory / (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            + "-"
            + uuid.uuid4().hex
            + ".json"
        )
        journal = {
            "schema_version": JOURNAL,
            "scope": config.scope,
            "plan_hash": reviewed["plan_hash"],
            "status": "applying",
            "entries": [],
        }
        write_json(path, journal)
        try:
            for action in reviewed["actions"]:
                current = session.web.get(action["id"])
                if digest(current) != action["before_hash"]:
                    raise Conflict("Model changed immediately before publication")
                after = form(materialize(config, action, current))
                if digest(after) != action["after_hash"]:
                    raise Conflict("Materialized policy differs from reviewed plan")
                entry = {
                    "id": action["id"],
                    "before": current,
                    "before_hash": digest(current),
                    "after": after,
                    "after_hash": digest(after),
                    "status": "pending",
                }
                journal["entries"].append(entry)
                write_json(
                    path, journal, replace=True
                )  # Record intent before an ambiguous HTTP write.
                session.web.write(after, create=current is None)
                saved = session.web.get(action["id"])
                if saved is None or digest(saved) != entry["after_hash"]:
                    raise Conflict("Read-back differs from the expected writable model form")
                entry["status"] = "applied"
                write_json(path, journal, replace=True)
            session.web.refresh()
            journal["status"] = "completed"
            write_json(path, journal, replace=True)
        except BaseException:
            journal["status"] = "failed"
            write_json(path, journal, replace=True)
            raise AdapterError(
                "Publication stopped; inspect private journal and use rollback: " + str(path)
            ) from None
        return {
            "status": "completed",
            "writes": len(journal["entries"]),
            "journal": str(path),
        }


def validate_journal(config, value):
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != JOURNAL
        or value.get("scope") != config.scope
        or value.get("status") not in INCOMPLETE | {"completed", "rolled_back"}
        or not isinstance(value.get("entries"), list)
    ):
        raise Conflict("Invalid rollback journal or publication scope")
    seen = set()
    for entry in value["entries"]:
        if not isinstance(entry, dict) or entry.get("id") in seen:
            raise Conflict("Invalid or duplicate rollback entry")
        before, after = entry.get("before"), entry.get("after")
        if (
            not isinstance(after, dict)
            or after.get("id") != entry.get("id")
            or form(after) != after
            or digest(after) != entry.get("after_hash")
            or digest(before) != entry.get("before_hash")
        ):
            raise Conflict("Rollback snapshot integrity check failed")
        assert_owned(config, after)
        if before is not None:
            if (
                not isinstance(before, dict)
                or before.get("id") != entry["id"]
                or form(before) != before
            ):
                raise Conflict("Rollback before-image has an invalid model identity")
            assert_owned(config, before)
        target = deepcopy(before if before is not None else after)
        if before is None:
            target["is_active"] = False
        if "rollback_body" in entry and entry["rollback_body"] != target:
            raise Conflict("Rollback recovery target is inconsistent")
        seen.add(entry["id"])


def rollback(config, session, path):
    path = Path(path)
    if path.is_symlink() or path.parent.resolve() != (config.state_dir / "journals").resolve():
        raise AdapterError("Rollback journal must be inside this configured state directory")
    with locked(config.state_dir):
        value = read_json(path)
        validate_journal(config, value)
        if value.get("status") == "rolled_back":
            return {"status": "rolled_back", "writes": 0, "journal": str(path)}
        session.web.connection(config)
        # Preflight every record before the first rollback write; no clobbering later edits.
        for entry in value["entries"]:
            current = session.web.get(entry["id"])
            expected_rollback = entry.get("rollback_body", entry["before"])
            if current == expected_rollback:
                continue
            if current is None or digest(current) != entry["after_hash"]:
                raise Conflict("Model drift prevents automatic rollback")
            assert_owned(config, current)
        value["status"] = "rolling_back"
        write_json(path, value, replace=True)
        writes = 0
        try:
            for entry in reversed(value["entries"]):
                # A pre-existing record is restored; a newly created record is disabled, not deleted.
                target = deepcopy(entry["before"])
                if target is None:
                    target = deepcopy(entry["after"])
                    target["is_active"] = False
                entry["rollback_body"] = target
                current = session.web.get(entry["id"])
                if current == target or (entry["before"] is None and current is None):
                    continue
                if digest(current) != entry["after_hash"] and current != entry["before"]:
                    raise Conflict("Model changed immediately before rollback")
                write_json(path, value, replace=True)
                session.web.write(target, create=False)
                writes += 1
                if session.web.get(entry["id"]) != target:
                    raise Conflict("Rollback read-back differs from expected state")
                entry["status"] = "rolled_back"
                write_json(path, value, replace=True)
            session.web.refresh()
            value["status"] = "rolled_back"
            write_json(path, value, replace=True)
        except BaseException:
            value["status"] = "rollback_failed"
            write_json(path, value, replace=True)
            raise AdapterError(
                "Rollback stopped; resolve drift before retrying: " + str(path)
            ) from None
        return {"status": "rolled_back", "writes": writes, "journal": str(path)}


def verify(config, session):
    observation = observe(config, session)
    plan = observation.plan
    if plan["actions"]:
        raise Conflict("Published configuration does not match current policy; review a new plan")
    if not plan["targets"]:
        raise Conflict("No eligible published chat models to verify")
    visible = {row["id"]: row for row in session.web.refresh()}
    for target in plan["targets"]:
        if target["id"] not in visible:
            raise Conflict("Published model is missing from the authenticated OpenWebUI catalogue")
    return {
        "status": "verified",
        "models": sorted({row["logical_model_id"] for row in plan["targets"]}),
        "records": len(plan["targets"]),
        "inference_tested": False,
        "browser_acceptance_required": True,
        "capabilities": {
            mid: sorted(name for name, item in value["features"].items() if item["enabled"])
            for mid, value in plan["negotiation"].items()
        },
        "todos": {mid: value["todos"] for mid, value in plan["negotiation"].items()},
    }
