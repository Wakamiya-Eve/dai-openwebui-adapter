from copy import deepcopy
from datetime import datetime, timezone

import pytest

from dai_openwebui_adapter.errors import Conflict
from dai_openwebui_adapter.names import display_name, runtime_label
from dai_openwebui_adapter.planner import observe
from dai_openwebui_adapter.publisher import apply, rollback
from dai_openwebui_adapter.runtime_names import sync_names


def set_runtime(server, state="online"):
    server.gateway = [
        {
            "id": row["id"],
            "base_name": row["name"],
            "runtime": {
                "schema_version": "m3.runtime-status.v1",
                "state": state,
                "current": True,
                "observed_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        for row in server.m3
    ]


def test_name_only_sync_is_idempotent_preserves_params_and_keeps_ids(config, session, server):
    apply(config, session, observe(config, session).plan)
    before = deepcopy(server.records)
    set_runtime(server)
    reviewed = sync_names(config, session)
    assert reviewed["actions"] == 4 and not reviewed["apply_called"]
    assert server.records == before
    first = sync_names(config, session, apply=True)
    assert first["actions"] == 4 and first["apply_called"]
    assert sync_names(config, session, apply=True)["actions"] == 0
    assert set(server.records) == set(before)
    for mid, row in server.records.items():
        expected = deepcopy(before[mid])
        expected["name"] = row["name"]
        assert row == expected
        assert row["name"].endswith(" (Online)")
        assert (" · Chat (Online)" in row["name"]) == bool(row["base_model_id"])
    rollback(config, session, first["journal"])
    assert server.records == before


def test_offline_unknown_and_partial_write_recovery(config, session, server):
    apply(config, session, observe(config, session).plan)
    set_runtime(server, "offline")
    server.fail_write = len(server.writes) + 2
    with pytest.raises(Exception):
        sync_names(config, session, apply=True)
    server.fail_write = None
    with pytest.raises(Conflict, match="incomplete"):
        sync_names(config, session, apply=True)
    journals = list((config.state_dir / "journals").glob("runtime-names-*.json"))
    rollback(config, session, journals[0])
    assert sync_names(config, session, apply=True)["actions"] == 4
    assert all(row["name"].endswith(" (Offline)") for row in server.records.values())
    set_runtime(server, "unknown")
    sync_names(config, session, apply=True)
    assert all(row["name"].endswith(" (Unknown)") for row in server.records.values())


def test_missing_runtime_model_or_ambiguous_origin_prevents_updates(config, session, server):
    apply(config, session, observe(config, session).plan)
    set_runtime(server)
    server.gateway.pop()
    writes = len(server.writes)
    with pytest.raises(Conflict):
        sync_names(config, session, apply=True)
    assert len(server.writes) == writes


def test_display_formatter_has_no_status_accumulation():
    assert display_name("Muse", "workspace_preset", "Online") == "Muse · Chat (Online)"
    assert runtime_label({}) == "Unknown"


def test_full_publication_and_name_sync_agree_and_timestamps_do_not_cause_writes(config, session, server):
    set_runtime(server)
    apply(config, session, observe(config, session).plan)
    assert sync_names(config, session, apply=True)["actions"] == 0
    set_runtime(server)
    assert observe(config, session).plan["actions"] == []
    set_runtime(server, "offline")
    sync_names(config, session, apply=True)
    assert observe(config, session).plan["actions"] == []
