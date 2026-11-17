from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path

import httpx
import pytest
import yaml

from dai_openwebui_adapter import cli
from dai_openwebui_adapter.clients import Session
from dai_openwebui_adapter.config import load_config
from dai_openwebui_adapter.errors import AdapterError
from dai_openwebui_adapter.planner import observe


def document(config):
    return {
        "schema_version": "dai.openwebui-adapter.v1",
        "source_id": config.source_id,
        "sources": {"m3": asdict(config.m3), "gateway": asdict(config.gateway)},
        "openwebui": {
            **asdict(config.openwebui),
            "connection_index": config.connection_index,
            "expected_upstream_url": config.expected_upstream_url,
        },
        "publication": {"workspace_prefix": config.workspace_prefix, "profile": "auto"},
        "state_dir": str(config.state_dir),
    }


def config_file(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(value))
    return path


def test_config_roundtrip_contains_environment_names_not_values(config, tmp_path):
    loaded = load_config(config_file(tmp_path, document(config)))
    assert loaded == config
    assert "m3-test-key" not in json.dumps(loaded.document())


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("sources.m3", "url", "http://user:password@host/v1"),
        ("sources.m3", "url", "http://host/v1?api_key=secret"),
        ("sources.gateway", "api_key_env", "INVALID-NAME"),
        ("openwebui", "connection_index", True),
        ("openwebui", "connection_index", -1),
        ("publication", "profile", "tools"),
        ("publication", "profile", "chat"),
        ("publication", "workspace_prefix", "bad namespace"),
        ("", "state_dir", "relative/state"),
        ("", "timeout_seconds", float("nan")),
        ("", "allow_empty_catalogue", "yes"),
        ("", "unknown_field", "ignored"),
    ],
)
def test_invalid_config_fails_closed(config, tmp_path, section, key, value):
    data = document(config)
    target = data
    for part in section.split(".") if section else []:
        target = target[part]
    target[key] = value
    with pytest.raises(AdapterError):
        load_config(config_file(tmp_path, data))


def test_unsafe_yaml_is_never_executed(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text('!!python/object/apply:os.system ["false"]')
    with pytest.raises(AdapterError):
        load_config(path)


def test_shipped_example_is_valid():
    loaded = load_config(Path(__file__).resolve().parents[1] / "config/adapter.example.yaml")
    assert loaded.workspace_prefix == "dai.chat."


def test_missing_credentials_error_is_safe(config, monkeypatch):
    monkeypatch.delenv("M3_KEY")
    with pytest.raises(AdapterError, match="M3_KEY"):
        with Session(config):
            pass


@pytest.mark.parametrize("mutation", ["duplicate", "invalid_capability", "missing_data"])
def test_malformed_source_cannot_publish(config, session, server, mutation):
    if mutation == "duplicate":
        server.m3.append(deepcopy(server.m3[0]))
    elif mutation == "invalid_capability":
        server.m3[0]["capabilities"]["chat"] = {"not": "a status"}
    else:
        server.m3[0]["capabilities"] = None
    with pytest.raises(AdapterError):
        observe(config, session)
    assert not server.writes


def test_cli_plan_apply_verify_rollback_and_redaction(
    config, server, tmp_path, monkeypatch, capsys
):
    path = config_file(tmp_path, document(config))
    real_session = Session
    monkeypatch.setattr(
        cli,
        "Session",
        lambda cfg: real_session(cfg, transport=httpx.MockTransport(server.handle)),
    )
    plan = tmp_path / "review.plan.json"
    prefix = ["--config", str(path)]
    assert cli.main(prefix + ["plan", "--output", str(plan)]) == 0
    assert not server.writes
    assert cli.main(prefix + ["apply", "--plan-file", str(plan)]) == 0
    assert cli.main(prefix + ["verify"]) == 0
    journal = next((config.state_dir / "journals").glob("*.json"))
    assert cli.main(prefix + ["rollback", "--journal", str(journal)]) == 0
    output = capsys.readouterr()
    assert "test-key" not in output.out + output.err
    assert "synthetic-admin-secret" not in output.out + output.err


def test_cli_api_failure_does_not_print_response_or_traceback(
    config, server, tmp_path, monkeypatch, capsys
):
    path = config_file(tmp_path, document(config))
    server.fail_source = True
    monkeypatch.setattr(
        cli,
        "Session",
        lambda cfg: Session(cfg, transport=httpx.MockTransport(server.handle)),
    )
    assert cli.main(["--config", str(path), "plan"]) == 2
    output = capsys.readouterr()
    assert "synthetic-secret" not in output.err
    assert "Traceback" not in output.err
    assert not server.writes


def test_redirects_are_not_followed_with_credentials(config):
    calls = []

    def redirect(request):
        calls.append(request.url.host)
        return httpx.Response(302, headers={"Location": "http://other.test/steal"})

    with Session(config, transport=httpx.MockTransport(redirect)) as session:
        with pytest.raises(AdapterError, match="302"):
            observe(config, session)
    assert calls == ["m3.test"]
