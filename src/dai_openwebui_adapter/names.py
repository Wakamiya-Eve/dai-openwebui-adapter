"""Shared display formatting; names never become request model IDs."""

from datetime import datetime, timezone


def runtime_label(runtime):
    try:
        age = (
            datetime.now(timezone.utc) - datetime.fromisoformat(runtime["observed_at"])
        ).total_seconds()
        if (
            runtime.get("schema_version") == "m3.runtime-status.v1"
            and runtime.get("current") is True
            and -5 <= age < 35
        ):
            return {"online": "Online", "offline": "Offline"}.get(runtime.get("state"), "Unknown")
    except (KeyError, ValueError, TypeError, AttributeError):
        pass
    return "Unknown"


def display_name(base, kind, label=None):
    return (
        base
        + (" · Chat" if kind == "workspace_preset" else "")
        + (" (" + label + ")" if label else "")
    )
