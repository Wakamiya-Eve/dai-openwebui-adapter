"""Canonical documents and safe OpenWebUI model forms."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

from .errors import AdapterError

MANAGER = "dai-openwebui-adapter"
POLICY = "capabilities.v3"
COMPATIBLE_POLICIES = {"chat.v1", "capabilities.v2", POLICY}
FORM_KEYS = (
    "id",
    "base_model_id",
    "name",
    "meta",
    "params",
    "access_grants",
    "is_active",
)


def digest(value: Any) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def valid_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 256
        and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
    )


def form(record: dict[str, Any]) -> dict[str, Any]:
    """Strip server fields, preserve writable prompts, knowledge, ACLs and metadata."""
    if not isinstance(record, dict) or not valid_id(record.get("id")):
        raise AdapterError("OpenWebUI returned an invalid model record")
    result = {key: deepcopy(record[key]) for key in FORM_KEYS if key in record}
    if not isinstance(result.get("name"), str):
        raise AdapterError("OpenWebUI model is missing its name")
    for key in ("meta", "params"):
        if not isinstance(result.get(key), dict):
            raise AdapterError("OpenWebUI model has invalid " + key)
    # ModelMeta materializes these nullable defaults; omission and null are equivalent.
    for key in (
        "profile_image_url",
        "background_image_url",
        "description",
        "i18n",
        "knowledge",
        "subagents",
        "voice",
        "voice_avatar",
    ):
        if result["meta"].get(key, False) is None:
            result["meta"].pop(key, None)
    if result["params"].get("model_controls") == {}:
        result["params"].pop("model_controls")
    result.setdefault("base_model_id", None)
    result.setdefault("access_grants", [])
    if not isinstance(result["access_grants"], list):
        raise AdapterError("OpenWebUI model has invalid access grants")
    if type(result.get("is_active")) is not bool:
        raise AdapterError("OpenWebUI model has invalid activation state")
    return result


def marker(record: dict[str, Any]) -> dict[str, Any]:
    meta = record.get("meta")
    value = meta.get("dai") if isinstance(meta, dict) else None
    return value if isinstance(value, dict) else {}
