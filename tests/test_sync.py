from copy import deepcopy

import pytest

from dai_openwebui_adapter.errors import AdapterError, Conflict
from dai_openwebui_adapter.publisher import apply
from dai_openwebui_adapter.state import locked, read_json
from dai_openwebui_adapter.sync import sync


def test_first_sync_then_no_change_does_not_invoke_apply(config, session, server, monkeypatch):
    result = sync(config, session)
    assert result["status"] == "updated"
    assert result["actions"] == len(server.writes) == 4
    calls = len(server.calls)

    def forbidden(*args):
        raise AssertionError("An empty plan must never invoke apply")

    monkeypatch.setattr("dai_openwebui_adapter.sync.apply", forbidden)
    result = sync(config, session)
    assert result["status"] == "unchanged"
    assert result["apply_called"] is False
    assert len(server.writes) == 4
    assert all(method == "GET" for method, _ in server.calls[calls:])
    assert read_json(config.state_dir / "last-success.json") == result


def test_sync_updates_real_differences_and_reaches_fixed_point(config, session, server):
    sync(config, session)
    before = len(server.writes)
    server.m3[0]["capabilities"]["streaming"] = "unsupported"
    result = sync(config, session)
    assert result["actions"] == 2
    assert len(server.writes) == before + 2
    assert server.records[server.m3[0]["id"]]["params"]["stream_response"] is False
    assert sync(config, session)["apply_called"] is False


def test_sync_rejects_source_drift_before_any_write(config, session, server, monkeypatch):
    def drift(*args):
        server.m3[0]["capabilities"]["streaming"] = "unsupported"
        return apply(*args)

    monkeypatch.setattr("dai_openwebui_adapter.sync.apply", drift)
    with pytest.raises(Conflict, match="changed after review"):
        sync(config, session)
    assert not server.writes


def test_incomplete_operation_blocks_even_if_current_configuration_matches(config, session, server):
    server.fail_write = 2
    with pytest.raises(AdapterError):
        sync(config, session)
    before = deepcopy(server.writes)
    server.fail_write = None
    with pytest.raises(Conflict, match="explicit recovery"):
        sync(config, session)
    assert server.writes == before
    assert not (config.state_dir / "last-success.json").exists()


def test_source_failure_does_not_apply_or_erase_last_success(config, session, server):
    sync(config, session)
    before = (config.state_dir / "last-success.json").read_bytes()
    server.fail_source = True
    with pytest.raises(AdapterError):
        sync(config, session)
    assert len(server.writes) == 4
    assert (config.state_dir / "last-success.json").read_bytes() == before


def test_complete_sync_has_its_own_lock(config, session, server):
    with locked(config.state_dir / "sync"):
        with pytest.raises(Conflict, match="lock"):
            sync(config, session)
    assert not server.writes
