from copy import deepcopy
import json
from pathlib import Path

import pytest

from dai_openwebui_adapter.capabilities import NATIVE, platform_snapshot
from dai_openwebui_adapter.errors import AdapterError, Conflict
from dai_openwebui_adapter.planner import observe
from dai_openwebui_adapter.publisher import apply, rollback
from conftest import MODELS


def complete(server, **states):
    caps = {name: "verified" for name in NATIVE}
    caps.update(states)
    server.m3[0]["capabilities"] = caps
    server.gateway[0]["capabilities"] = deepcopy(caps)


def test_maximal_per_model_intersection_enables_declared_features(config, session, server):
    complete(server)
    server.tools = [
        {"id": "calculator", "specs": [{"secret": "not-saved"}]},
        {"id": "server:api"},
        {"id": "server:mcp:ready", "authenticated": True},
        {"id": "server:mcp:needs-login", "authenticated": False},
        {"id": "disabled", "is_active": False},
    ]
    plan = observe(config, session).plan
    first = next(a["patch"] for a in plan["actions"] if a["id"] == MODELS[0])
    flags = first["meta"]["capabilities"]
    assert all(
        flags[k]
        for k in (
            "vision",
            "builtin_tools",
            "usage",
            "web_search",
            "image_generation",
            "code_interpreter",
            "terminal",
        )
    )
    assert flags["file_context"] is False  # Native file tools are exclusive with preloading.
    assert first["meta"]["builtinTools"]["files"] is True
    assert first["meta"]["builtinTools"]["automations"] is True
    assert first["meta"]["toolIds"] == ["calculator", "server:api", "server:mcp:ready"]
    assert first["meta"]["defaultFeatureIds"] == [
        "code_interpreter",
        "image_generation",
        "web_search",
    ]
    assert first["params"]["parallel_tool_calls"] is True
    assert first["params"]["reasoning_tags"] is True
    assert "response_format.json_schema" in first["meta"]["dai"]["supported_request_parameters"]
    assert "not-saved" not in json.dumps(plan)
    second = next(a["patch"] for a in plan["actions"] if a["id"] == MODELS[1])
    assert second["meta"]["capabilities"]["vision"] is False
    assert second["meta"]["capabilities"]["builtin_tools"] is False
    assert second["meta"]["toolIds"] == []
    apply(config, session, plan)
    assert observe(config, session).plan["actions"] == []


@pytest.mark.parametrize(
    "feature",
    [
        "vision",
        "tool_calling",
        "parallel_tool_calls",
        "json_mode",
        "json_schema",
        "reasoning_content",
        "usage",
    ],
)
def test_gateway_missing_feature_is_explicit_todo(config, session, server, feature):
    complete(server)
    del server.gateway[0]["capabilities"][feature]
    value = observe(config, session).plan["negotiation"][MODELS[0]]
    assert value["features"][feature]["candidate"] is True
    assert value["features"][feature]["enabled"] is False
    assert any("gateway.capabilities." + feature in row["missing"] for row in value["todos"])


@pytest.mark.parametrize("state", ["unverified", "unsupported"])
def test_model_or_gateway_denial_is_not_overridden(config, session, server, state):
    complete(server)
    server.m3[0]["capabilities"]["vision"] = state
    server.gateway[0]["capabilities"]["tool_calling"] = state
    value = observe(config, session).plan["negotiation"][MODELS[0]]
    assert not value["features"]["vision"]["enabled"]
    assert not value["features"]["tool_calling"]["enabled"]
    assert not value["features"]["parallel_tool_calls"]["enabled"]


def test_runtime_disabled_service_is_removed_from_intersection(config, session, server):
    complete(server)
    server.platform["features"].update(
        enable_web_search=False,
        enable_code_interpreter=False,
        enable_terminal_servers=False,
        enable_memories=False,
    )
    plan = observe(config, session).plan
    flags = plan["actions"][0]["patch"]["meta"]["capabilities"]
    assert flags["builtin_tools"] and flags["vision"]
    assert not flags["web_search"] and not flags["code_interpreter"]
    assert not flags["terminal"] and not flags["memory"]
    assert plan["actions"][0]["patch"]["meta"]["defaultFeatureIds"] == ["image_generation"]


def test_runtime_or_callable_tool_drift_invalidates_reviewed_plan(config, session, server):
    complete(server)
    plan = observe(config, session).plan
    server.platform["features"]["enable_web_search"] = False
    with pytest.raises(Conflict):
        apply(config, session, plan)
    server.platform["features"]["enable_web_search"] = True
    server.tools.append({"id": "new-tool"})
    with pytest.raises(Conflict):
        apply(config, session, plan)
    assert not server.writes


def test_upgrade_from_v1_and_downgrade_remove_only_automatic_bindings(config, session, server):
    apply(config, session, observe(config, session).plan)
    for row in server.records.values():
        row["meta"]["dai"]["policy"] = "chat.v1"
    complete(server)
    server.tools = [{"id": "automatic"}]
    result = apply(config, session, observe(config, session).plan)
    assert server.records[MODELS[0]]["meta"]["toolIds"] == ["automatic"]
    rollback(config, session, result["journal"])
    assert server.records[MODELS[0]]["meta"]["dai"]["policy"] == "chat.v1"
    apply(config, session, observe(config, session).plan)
    server.m3[0]["capabilities"]["tool_calling"] = "unsupported"
    apply(config, session, observe(config, session).plan)
    assert not server.records[MODELS[0]]["meta"]["capabilities"]["builtin_tools"]
    assert server.records[MODELS[0]]["meta"]["toolIds"] == []


def test_native_audio_is_not_confused_with_stt_text_tts(config, session, server):
    complete(server)
    plan = observe(config, session).plan
    value = plan["negotiation"][MODELS[0]]
    assert value["application_audio"]["standard_voice"]["available"]
    assert value["application_audio"]["standard_voice"]["native_model_audio"] is False
    for name in ("audio_input", "audio_output", "realtime_audio"):
        assert not value["features"][name]["enabled"]
        assert "openwebui.per_model_" + name + "_binding" in value["features"][name]["missing"]
    for action in plan["actions"]:
        assert "audio" not in action["patch"]["meta"]["capabilities"]
        assert "voice" not in action["patch"]["meta"]


def test_slim_does_not_claim_missing_local_speech_runtimes(config, session, server):
    server.platform["audio"]["stt"]["engine"] = "whisper"
    server.platform["audio"]["tts"]["engine"] = "transformers"
    value = observe(config, session).plan["negotiation"][MODELS[0]]
    assert not value["application_audio"]["standard_voice"]["available"]
    server.platform["audio"]["stt"]["engine"] = "web"
    server.platform["audio"]["tts"]["engine"] = ""
    value = observe(config, session).plan["negotiation"][MODELS[0]]
    assert value["application_audio"]["standard_voice"]["available"]


def test_auxiliary_audio_model_gets_role_binding_todo_not_a_fake_chat_preset(
    config, session, server
):
    complete(server, chat="unsupported")
    plan = observe(config, session).plan
    assert not any(t["logical_model_id"] == MODELS[0] for t in plan["targets"])
    value = plan["negotiation"][MODELS[0]]
    assert value["features"]["audio_transcription"]["candidate"]
    assert (
        "instance_service_binding_selection" in value["features"]["audio_transcription"]["missing"]
    )
    assert any(row["id"] == MODELS[0] for row in plan["pending"])


def test_undeclared_streaming_publishes_nonstream_default(config, session, server):
    del server.m3[0]["capabilities"]["streaming"]
    plan = observe(config, session).plan
    action = next(a for a in plan["actions"] if a["id"] == MODELS[0])
    assert action["patch"]["params"]["stream_response"] is False
    assert any(
        "model.capabilities.streaming" in row["missing"]
        for row in plan["negotiation"][MODELS[0]]["todos"]
    )


def test_openwebui_feature_snapshot_never_retains_credentials():
    value = platform_snapshot(
        {
            "features": {
                "slim": True,
                "enable_tools": True,
                "enable_tool_servers": True,
                "secret": "never-save",
            },
            "audio": {
                "stt": {"engine": "openai", "api_key": "never-save"},
                "tts": {},
                "realtime": {"enabled": False},
            },
        }
    )
    assert "never-save" not in json.dumps(value)
    with pytest.raises(AdapterError):
        platform_snapshot({"features": [], "audio": {}})


def test_negotiated_fields_match_installed_slim_contract(config, session, server):
    contract = json.loads(
        (Path(__file__).parent / "fixtures/openwebui_slim_contract.json").read_text()
    )
    complete(server)
    value = observe(config, session).plan["negotiation"][MODELS[0]]
    assert sorted(value["builtinTools"]) == sorted(contract["builtin_categories"])
    assert sorted(value["openwebui_capabilities"]) == sorted(contract["model_capability_fields"])
    assert value["openwebui_capabilities"]["citations"]
    assert value["openwebui_capabilities"]["status_updates"]


def test_unavailable_gateway_model_has_no_enabled_features(config, session, server):
    complete(server)
    server.gateway.pop(0)
    plan = observe(config, session).plan
    value = plan["negotiation"][MODELS[0]]
    assert not any(item["enabled"] for item in value["features"].values())
    assert not any(row["logical_model_id"] == MODELS[0] for row in plan["targets"])


@pytest.mark.parametrize("bad", ["false", 0, [], {}])
def test_invalid_callable_availability_does_not_inject_tools(config, session, server, bad):
    complete(server)
    server.tools = [{"id": "tool", "authenticated": bad}]
    with pytest.raises(AdapterError):
        observe(config, session)
    assert not server.writes


def test_interfaces_are_descriptive_and_do_not_trigger_requests(config, session, server):
    complete(server)
    server.m3[0]["interfaces"] = {"audio_speech": {"path": "http://untrusted.invalid/speech"}}
    plan = observe(config, session).plan
    assert "http://untrusted.invalid" not in json.dumps(plan)
    assert all("speech" not in path for _, path in server.calls)
    missing = plan["negotiation"][MODELS[0]]["features"]["audio_speech"]["missing"]
    assert "model.interfaces.audio_speech" not in missing
    assert "gateway.interfaces.audio_speech" in missing


def test_service_roles_still_report_missing_bindings_if_service_is_disabled(
    config, session, server
):
    complete(server)
    server.platform["audio"] = {}
    server.platform["features"]["enable_image_generation"] = False
    value = observe(config, session).plan["negotiation"][MODELS[0]]
    for name in ("audio_transcription", "audio_speech", "image_generation", "embeddings"):
        assert value["features"][name]["scope"] == "instance_service"
        assert "instance_service_binding_selection" in value["features"][name]["missing"]
        assert not value["features"][name]["enabled"]


def test_manual_tool_ownership_is_preserved_and_conflicts_require_review(config, session, server):
    complete(server)
    server.tools = [{"id": "manual"}, {"id": "automatic"}]
    apply(config, session, observe(config, session).plan)
    row = server.records[MODELS[0]]
    row["meta"]["dai"]["managed_tool_ids"] = ["automatic"]
    server.tools = [{"id": "manual"}]
    apply(config, session, observe(config, session).plan)
    assert server.records[MODELS[0]]["meta"]["toolIds"] == ["manual"]
    assert server.records[MODELS[0]]["meta"]["dai"]["managed_tool_ids"] == []
    server.tools = []
    with pytest.raises(Conflict, match="External tool bindings"):
        observe(config, session)


def test_public_config_without_authenticated_capabilities_cannot_downgrade_models(
    config, session, server
):
    server.platform = {"features": {"slim": True, "auth": True}}
    with pytest.raises(AdapterError, match="authenticated feature flags"):
        observe(config, session)
    assert not server.writes
