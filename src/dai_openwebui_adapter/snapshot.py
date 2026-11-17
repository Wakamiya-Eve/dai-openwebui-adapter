"""Consume a fresh root-issued, credential-bound OpenWebUI metadata snapshot."""

import hashlib
import hmac
from pathlib import Path
import time

from .errors import AdapterError
from .model import valid_id
from .state import read_json

FEATURE_KEYS = {
    "enable_memories": "memories.enable",
    "enable_notes": "notes.enable",
    "enable_channels": "channels.enable",
    "enable_user_webhooks": "ui.enable_user_webhooks",
    "enable_web_search": "web.search.enable",
    "enable_image_generation": "image_generation.enable",
    "enable_code_interpreter": "code_interpreter.enable",
    "enable_automations": "automations.enable",
    "enable_calendar": "calendar.enable",
    "enable_subagents": "subagents.enable",
}


def current_platform(snapshot, values):
    """Immediately discard unrelated admin-export values, including credentials."""
    if not isinstance(values, dict):
        raise AdapterError("Unsupported authenticated OpenWebUI config export")
    features = {
        key: snapshot["platform"]["features"][key]
        for key in ("slim", "enable_tools", "enable_tool_servers")
    }
    features.update(
        {name: values[key] for name, key in FEATURE_KEYS.items() if type(values.get(key)) is bool}
    )
    features["enable_terminal_servers"] = bool(
        features["enable_tool_servers"]
        and any(
            row.get("enabled", True) for row in (values.get("terminal_server.connections") or [])
        )
    )
    audio = {
        section: {key: values.get(f"audio.{section}.{key}", "") for key in keys}
        for section, keys in {
            "stt": ("engine",),
            "tts": ("engine", "voice"),
            "realtime": ("model", "voice"),
        }.items()
    }
    audio["realtime"]["enabled"] = values.get("audio.realtime.enabled", False)
    return {"features": features, "audio": audio}


def _check_permissions(path):
    for item in (path, path.parent):
        info = item.stat()
        if item.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
            raise AdapterError("OpenWebUI snapshot and parent must be root-owned and protected")


def read_snapshot(path, token, *, clock=time.time):
    path = Path(path)
    _check_permissions(path)
    data = read_json(path)
    if not isinstance(data, dict) or data.get("schema") != "dai.openwebui-host-snapshot.v1":
        raise AdapterError("Unsupported OpenWebUI host snapshot")
    stamp = data.get("issued_at")
    if type(stamp) not in (int, float) or not -30 <= clock() - stamp < 600:
        raise AdapterError("OpenWebUI host snapshot is expired or from the future")
    fingerprint = data.get("token_sha256")
    if not isinstance(fingerprint, str) or not hmac.compare_digest(
        fingerprint, hashlib.sha256(token.encode()).hexdigest()
    ):
        raise AdapterError("OpenWebUI host snapshot belongs to a different credential")
    identity = data.get("identity")
    if (
        not isinstance(identity, dict)
        or not valid_id(identity.get("id"))
        or identity.get("role") != "admin"
    ):
        raise AdapterError("Host snapshot must identify the publishing administrator")
    return data
