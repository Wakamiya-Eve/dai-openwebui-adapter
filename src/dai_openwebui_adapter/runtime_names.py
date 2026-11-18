"""Name-only synchronization from L4. Never reads model capabilities or runs inference."""

from copy import deepcopy
import uuid

from .clients import rows
from .errors import Conflict
from .model import digest, marker
from .policy import assert_owned, owned
from .publisher import INCOMPLETE, JOURNAL, journal_dir
from .state import locked, read_json, write_json
from .names import display_name, runtime_label


def sync_names(config, session, *, apply=False):
    with locked(config.state_dir):
        directory = journal_dir(config)
        for file in directory.glob("*.json"):
            prior = read_json(file)
            if prior.get("scope") == config.scope and prior.get("status") in INCOMPLETE:
                raise Conflict("Roll back the incomplete operation before synchronizing names")
        connection = session.web.connection(config)
        catalogue = rows(session.sources.gateway.request("GET", "models"))
        source = {row["id"]: row for row in catalogue}
        actions = []
        for mid, summary in session.web.stored().items():
            if not owned(config, summary):
                continue
            current = session.web.get(mid)
            assert_owned(config, current)
            tag = marker(current)
            row = source.get(tag["logical_model_id"])
            if row is None:
                raise Conflict("Managed model is absent from L4 runtime catalogue")
            base = row.get("base_name")
            runtime = row.get("runtime") or {}
            if not isinstance(base, str) or not base or len(base) > 256:
                raise Conflict("L4 runtime catalogue has no valid base name")
            if connection["ownership"].get(tag["base_id"]) != [config.connection_index]:
                raise Conflict("Runtime-name model origin is ambiguous")
            name = display_name(base, tag["kind"], runtime_label(runtime))
            if current["name"] == name:
                continue
            after = deepcopy(current)
            after["name"] = name
            actions.append(
                {
                    "id": mid,
                    "before": current,
                    "before_hash": digest(current),
                    "after": after,
                    "after_hash": digest(after),
                    "status": "pending",
                }
            )
        if not apply or not actions:
            return {
                "actions": len(actions),
                "apply_called": False,
                "names": [{"id": row["id"], "name": row["after"]["name"]} for row in actions],
            }
        path = directory / ("runtime-names-" + uuid.uuid4().hex + ".json")
        journal = {
            "schema_version": JOURNAL,
            "scope": config.scope,
            "plan_hash": digest(actions),
            "status": "applying",
            "entries": actions,
        }
        write_json(path, journal)
        try:
            for action in actions:
                if digest(session.web.get(action["id"])) != action["before_hash"]:
                    raise Conflict("Model changed before name-only synchronization")
                session.web.write(action["after"], create=False)
                if digest(session.web.get(action["id"])) != action["after_hash"]:
                    raise Conflict("Name-only update failed read-back verification")
                action["status"] = "applied"
                write_json(path, journal, replace=True)
            session.web.refresh()
            journal["status"] = "completed"
            write_json(path, journal, replace=True)
        except BaseException:
            journal["status"] = "failed"
            write_json(path, journal, replace=True)
            raise
        return {"actions": len(actions), "apply_called": True, "journal": str(path)}
