from dataclasses import replace
import hashlib
import json

import pytest

from dai_openwebui_adapter.clients import Session
from dai_openwebui_adapter.errors import AdapterError
from dai_openwebui_adapter.host_snapshot import READER
from dai_openwebui_adapter.snapshot import FEATURE_KEYS, current_platform, read_snapshot
from dai_openwebui_adapter.sync import sync


def test_installed_container_reader_compiles():
    compile(READER, "container-reader", "exec")


@pytest.fixture
def snapshot_file(tmp_path, monkeypatch, server):
    path = tmp_path / "snapshot.json"
    value = {
        "schema": "dai.openwebui-host-snapshot.v1",
        "issued_at": 1000,
        "token_sha256": hashlib.sha256(b"web-test-key").hexdigest(),
        "identity": {"id": "test-owner", "role": "admin"},
        "platform": server.platform,
    }
    path.write_text(json.dumps(value))
    monkeypatch.setattr("dai_openwebui_adapter.snapshot._check_permissions", lambda p: None)
    return path, value


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"issued_at": 1}, "expired"),
        ({"issued_at": 1031}, "future"),
        ({"token_sha256": "wrong"}, "different credential"),
        ({"identity": {"id": "test-owner", "role": "user"}}, "administrator"),
    ],
)
def test_snapshot_rejects_bad_binding_or_freshness(snapshot_file, change, reason):
    path, value = snapshot_file
    value.update(change)
    path.write_text(json.dumps(value))
    with pytest.raises(AdapterError, match=reason):
        read_snapshot(path, "web-test-key", clock=lambda: 1000)


def test_export_never_retains_secrets_or_unrelated_settings(snapshot_file):
    _, value = snapshot_file
    result = current_platform(
        value,
        {
            "openai.api_keys": ["never-persist-this"],
            "memories.enable": True,
            "audio.stt.engine": "openai",
        },
    )
    assert result["features"]["enable_memories"] is True
    assert "never-persist-this" not in json.dumps(result)


def test_real_api_key_contract_never_calls_human_endpoints(
    config, server, snapshot_file, monkeypatch
):
    import httpx
    from time import time

    path, value = snapshot_file
    value["issued_at"] = time()
    path.write_text(json.dumps(value))
    config = replace(config, snapshot_path=str(path))

    def handler(request):
        if request.url.path == "/api/v1/configs/export":
            server.calls.append((request.method, request.url.path))
            values = {
                key: server.platform["features"].get(name, False)
                for name, key in FEATURE_KEYS.items()
            }
            values.update({"audio.stt.engine": "openai", "audio.tts.engine": "openai"})
            return httpx.Response(200, json=values)
        assert request.url.path not in {"/api/v1/auths/", "/api/config"}
        return server.handle(request)

    with Session(config, transport=httpx.MockTransport(handler)) as session:
        assert sync(config, session)["status"] == "updated"
        assert sync(config, session)["apply_called"] is False
        server.platform["features"]["enable_memories"] = False
        assert sync(config, session)["status"] == "updated"
    assert len(server.writes) == 8
