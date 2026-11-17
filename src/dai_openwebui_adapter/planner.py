"""Reviewable plans contain managed patches and hashes, never existing prompts or keys."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from .clients import Session
from .capabilities import negotiate
from .config import Config
from .errors import AdapterError, Conflict
from .model import digest, marker, form
from .policy import assert_owned, merge, owned, patch

SCHEMA = "dai.openwebui-plan.v1"


@dataclass
class Observation:
    plan: dict
    records: dict


def observe(config: Config, session: Session) -> Observation:
    source = session.sources.read(allow_empty=config.allow_empty_catalogue)
    connection = session.web.connection(config)
    platform = session.web.platform()
    listed = session.web.stored()
    records = {}

    def get(mid):
        if mid not in records:
            records[mid] = session.web.get(mid)
        return records[mid]

    available = set(source["gateway_ids"])
    eligible, pending, negotiations = [], [], {}
    for mid, model in sorted(source["models"].items()):
        caps = model["capabilities"]
        negotiations[mid] = negotiate(
            model,
            source["gateway"].get(mid, {"capabilities": {}, "interfaces": {}, "available": False}),
            platform,
        )
        if mid not in available:
            pending.append({"id": mid, "reason": "not_available_in_agent_gateway"})
        elif caps.get("chat") != "verified":
            pending.append(
                {
                    "id": mid,
                    "reason": "chat_must_be_verified; auxiliary_service_model_requires_role_binding",
                }
            )
        elif not negotiations[mid]["features"]["chat"]["enabled"]:
            pending.append({"id": mid, "reason": "gateway_chat_capability_unavailable"})
        else:
            eligible.append(model)
    features = platform["features"]
    tools = (
        session.web.tools()
        if any(
            negotiations[model["id"]]["features"]["tool_calling"]["enabled"] for model in eligible
        ) and (features.get("enable_tools", False) or features.get("enable_tool_servers", False))
        else {"available": [], "excluded": []}
    )
    disabled = [mid for mid in tools["available"]
                if not features.get("enable_tool_servers" if mid.startswith("server:") else "enable_tools", False)]
    tools["available"] = sorted(set(tools["available"]) - set(disabled))
    tools["excluded"] = sorted(set(tools["excluded"]) | set(disabled))
    for value in negotiations.values():
        value["tool_ids"] = (
            tools["available"] if value["features"]["tool_calling"]["enabled"] else []
        )
    desired_ids, actions, targets = set(), [], []
    for model in eligible:
        mid = model["id"]
        if mid not in connection["selected_ids"]:
            raise Conflict("Eligible model is absent from the selected OpenWebUI connection")
        base = connection["prefix"] + "." + mid if connection["prefix"] else mid
        if connection["ownership"].get(base) != [config.connection_index]:
            raise Conflict("Base model ID is ambiguous across OpenWebUI connections")
        workspace = config.workspace_prefix + mid
        if workspace in connection["ownership"]:
            raise Conflict("Workspace preset ID collides with an upstream base model")
        for kind in ("base_override", "workspace_preset"):
            desired = patch(config, model, base, kind, negotiations[mid])
            target = desired["id"]
            if target in desired_ids:
                raise Conflict("Publication IDs collide")
            desired_ids.add(target)
            current = get(target)
            updated = form(merge(config, current, desired))
            targets.append(
                {
                    "id": target,
                    "base_model_id": updated["base_model_id"],
                    "kind": kind,
                    "logical_model_id": mid,
                }
            )
            if current != updated:
                actions.append(
                    {
                        "operation": "create" if current is None else "update",
                        "id": target,
                        "before_hash": digest(current),
                        "after_hash": digest(updated),
                        "patch": desired,
                    }
                )
    retired = []
    for mid, summary in sorted(listed.items()):
        if mid in desired_ids or not owned(config, summary):
            continue
        current = get(mid)
        if current is None:
            raise Conflict("Managed model disappeared during planning")
        assert_owned(config, current)
        if current["is_active"]:
            after = deepcopy(current)
            after["is_active"] = False
            retired.append(
                {
                    "operation": "disable",
                    "id": mid,
                    "before_hash": digest(current),
                    "after_hash": digest(after),
                    "patch": {"is_active": False},
                    "kind": marker(current)["kind"],
                }
            )
    # Disable presets before their bases; creation above always writes a base before its preset.
    actions.extend(sorted(retired, key=lambda row: (row["kind"] != "workspace_preset", row["id"])))
    value = {
        "schema_version": SCHEMA,
        "config_hash": config.fingerprint,
        "scope": config.scope,
        "source_hash": source["revision"],
        "connection_hash": digest(connection),
        "platform_hash": digest(platform),
        "negotiation": negotiations,
        "tools_hash": digest(tools),
        "pending": pending,
        "targets": targets,
        "actions": actions,
    }
    value["plan_hash"] = digest(value)
    return Observation(value, records)


def check_plan(value):
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA:
        raise AdapterError("Unsupported plan schema")
    raw = {key: item for key, item in value.items() if key != "plan_hash"}
    if value.get("plan_hash") != digest(raw):
        raise Conflict("Plan integrity check failed")


def materialize(config, action, current):
    if action["operation"] == "disable":
        if current is None:
            raise Conflict("Cannot disable a missing managed model")
        assert_owned(config, current)
        result = deepcopy(current)
        result["is_active"] = False
        return result
    return merge(config, current, action["patch"])
