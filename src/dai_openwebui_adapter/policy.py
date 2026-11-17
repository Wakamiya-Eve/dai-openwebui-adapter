"""Publish the maximal negotiated profile without model/node-specific branches."""

from __future__ import annotations

from copy import deepcopy

from .config import Config
from .errors import Conflict
from .model import MANAGER, POLICY, COMPATIBLE_POLICIES, digest, marker, valid_id


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
        or tag.get("policy") not in COMPATIBLE_POLICIES
        or kind not in {"base_override", "workspace_preset"}
    ):
        raise Conflict("Model is unmanaged or belongs to a different publication scope")
    expected_id = base if kind == "base_override" else config.workspace_prefix + mid
    expected_base = None if kind == "base_override" else base
    if record["id"] != expected_id or record.get("base_model_id") != expected_base:
        raise Conflict("Managed model identity or base mapping was changed externally")


def patch(config: Config, model: dict, base_id: str, kind: str, negotiated: dict) -> dict:
    mid = model["id"]
    mid_out = base_id if kind == "base_override" else config.workspace_prefix + mid
    if not valid_id(mid_out) or mid_out == (base_id if kind == "workspace_preset" else None):
        raise Conflict("Generated workspace identity is invalid or collides with its base")
    return {
        "id": mid_out,
        "base_model_id": None if kind == "base_override" else base_id,
        "name": model["name"] + (" · Chat" if kind == "workspace_preset" else ""),
        "meta": {
            "capabilities": deepcopy(negotiated["openwebui_capabilities"]),
            "builtinTools": deepcopy(negotiated["builtinTools"]),
            "defaultFeatureIds": sorted(
                key
                for key in ("web_search", "image_generation", "code_interpreter")
                if negotiated["openwebui_capabilities"][key]
            ),
            "toolIds": negotiated["tool_ids"][:],
            "dai": {
                "managed_by": MANAGER,
                "source_id": config.source_id,
                "scope": config.scope,
                "logical_model_id": mid,
                "base_id": base_id,
                "kind": kind,
                "profile": "auto",
                "policy": POLICY,
                "capability_digest": digest(
                    {"model": model["capabilities"], "effective": negotiated,
                     "source_revision": model.get("capability_revision")}
                ),
                "source_schema": "m4.capability-catalogue.v1",
                "source_revision": model.get("capability_revision"),
                "limits": deepcopy(model.get("limits", {})),
                "parameters": deepcopy(model.get("parameters", {})),
                "capability_evidence": deepcopy(model.get("evidence", {})),
                "enabled_features": sorted(
                    name for name, value in negotiated["features"].items() if value["enabled"]
                ),
                "application_audio": deepcopy(negotiated["application_audio"]),
                "managed_tool_ids": negotiated["tool_ids"][:],
                "supported_request_parameters": [
                    name
                    for name, cap in (
                        ("response_format.json_object", "json_mode"),
                        ("response_format.json_schema", "json_schema"),
                        ("reasoning_effort", "reasoning_effort"),
                    )
                    if negotiated["features"][cap]["enabled"]
                ],
            },
        },
        "params": {"function_calling": "native", **negotiated["params"]},
        "is_active": True,
    }


def merge(config: Config, current: dict | None, desired: dict) -> dict:
    if current is None:
        result = deepcopy(desired)
        result["access_grants"] = []  # Owner-only by default; never widen audience implicitly.
        result["meta"].setdefault("toolIds", [])
        return result
    assert_owned(config, current)
    if current["base_model_id"] != desired["base_model_id"]:
        raise Conflict("Managed model base changed; explicit migration is required")
    meta = current["meta"]
    if meta.get("tool_ids"):
        raise Conflict("Noncanonical external tool bindings require explicit migration")
    old_ids = meta.get("toolIds") or []
    old_managed = marker(current).get("managed_tool_ids") or []
    if (
        not isinstance(old_ids, list)
        or not isinstance(old_managed, list)
        or any(not valid_id(value) for value in old_ids + old_managed)
    ):
        raise Conflict("Invalid external tool bindings")
    manual = set(old_ids) - set(old_managed)
    wanted = set(desired["meta"]["toolIds"])
    if manual - wanted:
        raise Conflict("External tool bindings are no longer callable; review them explicitly")
    if not isinstance(meta.get("capabilities", {}), dict):
        raise Conflict("Managed capability configuration has an invalid shape")
    result = deepcopy(current)
    result["meta"].setdefault("capabilities", {}).update(desired["meta"]["capabilities"])
    result["meta"]["dai"] = deepcopy(desired["meta"]["dai"])
    result["meta"]["builtinTools"] = deepcopy(desired["meta"]["builtinTools"])
    previous_defaults = meta.get("defaultFeatureIds") or []
    if not isinstance(previous_defaults, list) or any(
        not isinstance(v, str) for v in previous_defaults
    ):
        raise Conflict("Invalid OpenWebUI default feature IDs")
    result["meta"]["defaultFeatureIds"] = sorted(
        (set(previous_defaults) - {"web_search", "image_generation", "code_interpreter"})
        | set(desired["meta"]["defaultFeatureIds"])
    )
    result["meta"]["toolIds"] = sorted(wanted | manual)
    result["meta"]["dai"]["managed_tool_ids"] = sorted(wanted - manual)
    result["params"]["function_calling"] = "native"
    for key in ("stream_response", "parallel_tool_calls", "reasoning_tags"):
        result["params"][key] = desired["params"][key]
    result["is_active"] = True
    # Existing name, unmanaged parameters/system prompt, knowledge, grants and other metadata remain intact.
    return result
