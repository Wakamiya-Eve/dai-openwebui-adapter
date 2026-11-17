"""Capability negotiation, independent of model names and physical GPU nodes.

A declaration is not an execution endpoint. Candidates are the model/OpenWebUI
intersection; activation also checks the serving Gateway contract. Missing
contracts remain explicit TODOs instead of invented capabilities or dummy UI keys.
"""

from __future__ import annotations

from .errors import AdapterError

STATES = {"verified", "unverified", "unsupported"}
NATIVE = (
    "chat",
    "streaming",
    "multi_turn",
    "vision",
    "tool_calling",
    "parallel_tool_calls",
    "json_mode",
    "json_schema",
    "reasoning_content",
    "reasoning_effort",
    "usage",
    "audio_input",
    "audio_output",
    "video_input",
    "realtime_audio",
    "audio_transcription",
    "audio_speech",
    "image_generation",
    "image_editing",
    "embeddings",
)
# These features already form the legacy, validated OpenAI Chat Completions path.
LEGACY_GATEWAY = {"chat", "streaming", "multi_turn"}
# Key names from the installed Model Editor and builtin-tool implementation.
CATEGORY_FEATURE = {
    "time": None,
    "user_input": None,
    "memory": "enable_memories",
    "chats": None,
    "notes": "enable_notes",
    "knowledge": None,
    "files": None,
    "channels": "enable_channels",
    "notifications": "enable_user_webhooks",
    "web_search": "enable_web_search",
    "image_generation": "enable_image_generation",
    "code_interpreter": "enable_code_interpreter",
    "tasks": None,
    "automations": "enable_automations",
    "calendar": "enable_calendar",
    "subagents": "enable_subagents",
    "skills": None,
    "tools": "enable_tools",
}


def platform_snapshot(value):
    if not isinstance(value, dict) or not isinstance(value.get("features"), dict):
        raise AdapterError("Unsupported OpenWebUI public capability-config schema")
    if any(
        type(value["features"].get(key)) is not bool
        for key in ("slim", "enable_tools", "enable_tool_servers")
    ):
        raise AdapterError("OpenWebUI config must expose authenticated feature flags")
    features = {k: v for k, v in value["features"].items() if type(v) is bool}
    # No credentials, provider URLs or arbitrary config values enter plans/markers.
    audio = value.get("audio") or {}
    if not isinstance(audio, dict):
        raise AdapterError("Invalid OpenWebUI audio configuration")
    safe_audio = {}
    for section in ("stt", "tts", "realtime"):
        raw = audio.get(section) or {}
        if not isinstance(raw, dict):
            raise AdapterError("Invalid OpenWebUI audio service declaration")
        safe_audio[section] = {
            k: v for k, v in raw.items() if k in {"engine", "voice", "model"} and isinstance(v, str)
        }
    realtime = audio.get("realtime") or {}
    if "enabled" in realtime and type(realtime["enabled"]) is not bool:
        raise AdapterError("Invalid OpenWebUI realtime enable flag")
    safe_audio["realtime"]["enabled"] = realtime.get("enabled", False)
    return {"features": features, "audio": safe_audio, "contract": "openwebui-slim-audited.v1"}


def _state(mapping, name):
    value = mapping.get(name, "unverified")
    if value not in STATES:
        raise AdapterError("Invalid capability state")
    return value


def _platform(name, platform):
    # Generic OpenAI request/renderer features are present in the audited slim code.
    if name in {
        "chat",
        "streaming",
        "multi_turn",
        "vision",
        "tool_calling",
        "parallel_tool_calls",
        "json_mode",
        "json_schema",
        "reasoning_content",
        "reasoning_effort",
        "usage",
    }:
        return True
    if name in {"realtime_audio", "audio_input", "audio_output"}:
        # In this installed image selected chat models have no native-audio binding.
        # Standard/Realtime voice uses an instance provider, not this chat model's audio API.
        return False
    if name == "audio_transcription":
        return bool(platform["audio"]["stt"].get("engine"))
    if name == "audio_speech":
        return bool(platform["audio"]["tts"].get("engine"))
    if name == "image_generation":
        return platform["features"].get("enable_image_generation", False)
    if name == "image_editing":
        return platform["features"].get("enable_image_editing", False)
    # A video/audio file upload is not a native video/audio chat input contract.
    return False


def negotiate(model, gateway, platform):
    declared, routed = model["capabilities"], gateway["capabilities"]
    report = {}
    todos = []
    for name in NATIVE:
        state = _state(declared, name)
        supported = _platform(name, platform)
        candidate = state == "verified" and supported
        route = _state(routed, name)
        legacy = False  # The versioned L4 contract must declare every executable feature.
        effective = candidate and gateway.get("available", True) and (route == "verified" or legacy)
        missing = []
        if name not in declared:
            missing.append("model.capabilities." + name)
        if candidate and not legacy and name not in routed:
            missing.append("gateway.capabilities." + name)
        report[name] = {
            "model": state,
            "openwebui": supported,
            "candidate": candidate,
            "gateway": "legacy_chat" if legacy else route,
            "enabled": effective,
            "missing": missing,
        }
        if missing:
            todos.append({"capability": name, "missing": missing})
    # A parallel-call declaration only has meaning when native tool calls can traverse the path.
    if not report["tool_calling"]["enabled"]:
        report["parallel_tool_calls"]["enabled"] = False
    native = report["tool_calling"]["enabled"]
    categories = {}
    for category, feature in CATEGORY_FEATURE.items():
        categories[category] = native and (
            feature is None or platform["features"].get(feature, False)
        )
    flags = {
        "builtin_tools": native,
        "vision": report["vision"]["enabled"],
        "usage": report["usage"]["enabled"],
        "web_search": categories["web_search"],
        "image_generation": categories["image_generation"],
        "code_interpreter": categories["code_interpreter"],
    }
    chat = report["chat"]["enabled"]
    features = platform["features"]
    flags.update(
        citations=chat,
        status_updates=chat,
        file_upload=chat,
        file_context=chat and not native,
        memory=chat and features.get("enable_memories", False),
        terminal=native and features.get("enable_terminal_servers", False),
    )
    audio = platform["audio"]
    slim = features.get("slim", False)
    stt = audio["stt"].get("engine", "")
    tts = audio["tts"].get("engine", "")
    stt_ready = bool(stt) and (not slim or stt not in {"whisper", "faster-whisper", "local"})
    tts_ready = not slim or tts not in {"transformers", "local"}
    application_audio = {
        "standard_voice": {
            "available": chat and stt_ready and tts_ready,
            "scope": "instance_stt_text_tts",
            "native_model_audio": False,
            "health_verified": False,
        },
        "realtime_voice": {
            "available": chat and audio["realtime"]["enabled"],
            "scope": "instance_realtime_provider",
            "native_model_audio": False,
            "health_verified": False,
        },
    }
    for name in ("audio_input", "audio_output", "video_input", "realtime_audio"):
        if _state(declared, name) == "verified" and not _platform(name, platform):
            report[name]["missing"].append("openwebui.per_model_" + name + "_binding")
            todos.append({"capability": name, "missing": report[name]["missing"][:]})
    for name, interface in (
        ("audio_transcription", "audio_transcriptions"),
        ("audio_speech", "audio_speech"),
        ("image_generation", "image_generation"),
        ("image_editing", "image_editing"),
        ("embeddings", "embeddings"),
    ):
        if report[name]["model"] == "verified":
            missing = []
            if interface not in model.get("interfaces", {}):
                missing.append("model.interfaces." + interface)
            if interface not in gateway.get("interfaces", {}):
                missing.append("gateway.interfaces." + interface)
            # These are instance-service contracts, not per-model capability switches.
            report[name]["enabled"] = False
            report[name]["scope"] = "instance_service"
            missing.append("instance_service_binding_selection")
            report[name]["missing"].extend(missing)
            todos.append({"capability": name, "missing": missing})
    params = {
        "stream_response": report["streaming"]["enabled"],
        "parallel_tool_calls": report["parallel_tool_calls"]["enabled"],
        "reasoning_tags": report["reasoning_content"]["enabled"],
    }
    for name, value in report.items():
        reasons = []
        if value["model"] != "verified":
            reasons.append("model_" + value["model"])
        if not value["openwebui"]:
            reasons.append("openwebui_configuration_or_model_binding_unavailable")
        if value["candidate"] and value["gateway"] not in {"verified", "legacy_chat"}:
            reasons.append("gateway_" + value["gateway"])
        if name == "parallel_tool_calls" and not report["tool_calling"]["enabled"]:
            reasons.append("requires_native_tool_calling")
        if not gateway.get("available", True):
            reasons.append("model_not_available_in_gateway")
        value["reasons"] = reasons
    return {
        "features": report,
        "openwebui_capabilities": flags,
        "params": params,
        "builtinTools": categories,
        "application_audio": application_audio,
        "todos": todos,
    }
