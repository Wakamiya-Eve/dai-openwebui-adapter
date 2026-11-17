"""Pure plain-chat policy; no model/node-specific branches or capability inflation."""

from __future__ import annotations

from copy import deepcopy

from .config import Config
from .errors import Conflict
from .model import MANAGER, POLICY, digest, marker, valid_id


def owned(config: Config, record: dict) -> bool:
    tag = marker(record)
    return tag.get("managed_by") == MANAGER and tag.get("source_id") == config.source_id


def assert_owned(config: Config, record: dict):
    tag = marker(record)
    mid, base, kind = tag.get("logical_model_id"), tag.get("base_id"), tag.get("kind")
    if (
        not owned(config, record)
        or tag.get("scope") != config.scope
        or not valid_id(mid)
        or not valid_id(base)
        or tag.get("policy") != POLICY
        or kind not in {"base_override", "workspace_preset"}
    ):
        raise Conflict("Model is unmanaged or belongs to a different publication scope")
    expected_id = base if kind == "base_override" else config.workspace_prefix + mid
    expected_base = None if kind == "base_override" else base
    if record["id"] != expected_id or record.get("base_model_id") != expected_base:
        raise Conflict("Managed model identity or base mapping was changed externally")


def patch(config: Config, model: dict, base_id: str, kind: str) -> dict:
    mid = model["id"]
    mid_out = base_id if kind == "base_override" else config.workspace_prefix + mid
    if not valid_id(mid_out) or mid_out == (base_id if kind == "workspace_preset" else None):
        raise Conflict("Generated workspace identity is invalid or collides with its base")
    return {
        "id": mid_out,
        "base_model_id": None if kind == "base_override" else base_id,
        "name": model["name"] + (" · Chat" if kind == "workspace_preset" else ""),
        "meta": {
            "capabilities": {"builtin_tools": False, "vision": False},
            "dai": {
                "managed_by": MANAGER,
                "source_id": config.source_id,
                "scope": config.scope,
                "logical_model_id": mid,
                "base_id": base_id,
                "kind": kind,
                "profile": "chat",
                "policy": POLICY,
                "capability_digest": digest(model["capabilities"]),
            },
        },
        "params": {"function_calling": "native"},
        "is_active": True,
    }


def merge(config: Config, current: dict | None, desired: dict) -> dict:
    if current is None:
        result = deepcopy(desired)
        result["access_grants"] = []  # Owner-only by default; never widen audience implicitly.
        result["meta"]["toolIds"] = []
        return result
    assert_owned(config, current)
    if current["base_model_id"] != desired["base_model_id"]:
        raise Conflict("Managed model base changed; explicit migration is required")
    meta = current["meta"]
    if any(meta.get(key) for key in ("toolIds", "tool_ids")):
        raise Conflict(
            "Managed plain-chat profile has external tool bindings; review them explicitly"
        )
    if not isinstance(meta.get("capabilities", {}), dict):
        raise Conflict("Managed capability configuration has an invalid shape")
    result = deepcopy(current)
    result["meta"].setdefault("capabilities", {}).update(desired["meta"]["capabilities"])
    result["meta"]["dai"] = deepcopy(desired["meta"]["dai"])
    result["is_active"] = True
    # Existing name, params/system prompt, knowledge, grants and unrelated metadata remain intact.
    return result
