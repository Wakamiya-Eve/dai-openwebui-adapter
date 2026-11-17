from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from dai_openwebui_adapter.errors import AdapterError, Conflict
from dai_openwebui_adapter.model import digest
from dai_openwebui_adapter.planner import observe
from dai_openwebui_adapter.publisher import apply, rollback, verify
from dai_openwebui_adapter.state import read_json, write_json, locked
from conftest import MODELS


def test_two_current_models_create_four_records_and_verify(config, session, server):
    plan = observe(config, session).plan
    assert len(plan["actions"]) == 4
    assert not server.writes and not config.state_dir.exists()
    result = apply(config, session, plan)
    assert result["writes"] == 4
    assert len(server.records) == 4
    for mid in MODELS:
        assert server.records[mid]["base_model_id"] is None
        assert server.records["dai.chat." + mid]["base_model_id"] == mid
    assert all(
        row["meta"]["capabilities"] == {"builtin_tools": False, "vision": False}
        for row in server.records.values()
    )
    assert verify(config, session)["inference_tested"] is False
    journal = Path(result["journal"])
    assert journal.stat().st_mode & 0o777 == 0o600
    assert observe(config, session).plan["actions"] == []
    assert apply(config, session, observe(config, session).plan)["writes"] == 0


def test_plan_preserves_private_prompts_and_does_not_expose_them(config, session, server):
    apply(config, session, observe(config, session).plan)
    mid = "dai.chat." + MODELS[0]
    row = server.records[mid]
    row["name"] = "User name"
    row["params"]["system"] = "private-prompt-never-print"
    row["params"]["temperature"] = 0.73
    row["meta"]["knowledge"] = [{"id": "user-knowledge"}]
    row["meta"]["capabilities"]["file_upload"] = True
    row["access_grants"] = [{"principal_id": "group", "permission": "read"}]
    server.m3[0]["capabilities"]["json_schema"] = "verified"
    plan = observe(config, session).plan
    assert "private-prompt-never-print" not in json.dumps(plan)
    assert "synthetic-admin-secret" not in json.dumps(plan)
    apply(config, session, plan)
    assert server.records[mid]["params"]["system"] == "private-prompt-never-print"
    assert server.records[mid]["name"] == "User name"
    assert server.records[mid]["meta"]["knowledge"] == [{"id": "user-knowledge"}]
    assert server.records[mid]["access_grants"] == [{"principal_id": "group", "permission": "read"}]
    assert server.records[mid]["meta"]["capabilities"]["file_upload"] is True


@pytest.mark.parametrize("where", ["source", "record"])
def test_reviewed_plan_rejects_source_or_record_drift_before_any_write(
    config, session, server, where
):
    apply(config, session, observe(config, session).plan)
    server.m3[0]["capabilities"]["json_schema"] = "verified"
    plan = observe(config, session).plan
    before = len(server.writes)
    if where == "source":
        server.m3[0]["name"] = "changed source"
    else:
        server.records[MODELS[0]]["params"]["system"] = "changed after review"
    with pytest.raises(Conflict):
        apply(config, session, plan)
    assert len(server.writes) == before


def test_plan_tampering_is_not_applied(config, session, server):
    plan = observe(config, session).plan
    plan["actions"][0]["patch"]["meta"]["capabilities"]["builtin_tools"] = True
    with pytest.raises(Conflict):
        apply(config, session, plan)
    # Recomputing a tampered plan hash cannot bypass the fresh policy comparison.
    plan["plan_hash"] = digest({k: v for k, v in plan.items() if k != "plan_hash"})
    with pytest.raises(Conflict):
        apply(config, session, plan)
    assert not server.writes


def test_new_model_requires_no_node_specific_code(config, session, server):
    apply(config, session, observe(config, session).plan)
    new = deepcopy(server.m3[0])
    new["id"] = "future-model"
    server.m3.append(new)
    server.gateway.append({"id": "future-model"})
    server.by_connection[0].append("future-model")
    plan = observe(config, session).plan
    assert len(plan["actions"]) == 2
    apply(config, session, plan)
    assert server.records["dai.chat.future-model"]["base_model_id"] == "future-model"


def test_removal_disables_presets_before_bases_and_can_rollback(config, session, server):
    apply(config, session, observe(config, session).plan)
    old = deepcopy(server.records)
    server.m3 = server.m3[1:]
    server.gateway = server.gateway[1:]
    plan = observe(config, session).plan
    assert [a["operation"] for a in plan["actions"]] == ["disable", "disable"]
    assert plan["actions"][0]["kind"] == "workspace_preset"
    result = apply(config, session, plan)
    assert not server.records[MODELS[0]]["is_active"]
    rollback(config, session, result["journal"])
    assert server.records == old


@pytest.mark.parametrize("ambiguous", [False, True])
def test_partial_or_ambiguous_write_has_durable_rollback(config, session, server, ambiguous):
    if ambiguous:
        server.ambiguous_write = 2
    else:
        server.fail_write = 2
    with pytest.raises(AdapterError, match="Publication stopped"):
        apply(config, session, observe(config, session).plan)
    journal = next((config.state_dir / "journals").glob("*.json"))
    assert read_json(journal)["status"] == "failed"
    with pytest.raises(Conflict, match="incomplete"):
        apply(config, session, observe(config, session).plan)
    server.fail_write = server.ambiguous_write = None
    result = rollback(config, session, journal)
    assert result["status"] == "rolled_back"
    assert all(not row["is_active"] for row in server.records.values())
    assert rollback(config, session, journal)["writes"] == 0


def test_rollback_will_not_clobber_later_manual_edits(config, session, server):
    result = apply(config, session, observe(config, session).plan)
    server.records[MODELS[0]]["name"] = "manual edit"
    before = len(server.writes)
    with pytest.raises(Conflict, match="drift"):
        rollback(config, session, result["journal"])
    assert len(server.writes) == before


def test_rollback_restores_updated_records(config, session, server):
    apply(config, session, observe(config, session).plan)
    before = deepcopy(server.records)
    server.m3[0]["capabilities"]["json_schema"] = "verified"
    result = apply(config, session, observe(config, session).plan)
    rollback(config, session, result["journal"])
    assert server.records == before


def test_unmanaged_collision_is_never_adopted(config, session, server):
    server.records[MODELS[0]] = {
        "id": MODELS[0],
        "base_model_id": None,
        "name": "user",
        "params": {},
        "meta": {},
        "access_grants": [],
        "is_active": True,
    }
    with pytest.raises(Conflict, match="unmanaged"):
        observe(config, session)
    assert not server.writes


def test_missing_metadata_does_not_inflate_capabilities(config, session, server):
    server.m3[0]["capabilities"] = {"chat": "verified"}
    plan = observe(config, session).plan
    assert len(plan["targets"]) == 2
    assert plan["pending"][0]["reason"] == "plain_chat_and_streaming_must_be_verified"


def test_verified_vision_or_tools_do_not_enable_an_untested_profile(config, session, server):
    server.m3[0]["capabilities"].update(tool_calling="verified", vision="verified")
    apply(config, session, observe(config, session).plan)
    assert all(not row["meta"]["capabilities"]["builtin_tools"] for row in server.records.values())
    assert all(not row["meta"]["capabilities"]["vision"] for row in server.records.values())


def test_wrong_connection_and_cross_connection_collision_stop_publication(config, session, server):
    with pytest.raises(Conflict, match="URL"):
        observe(replace(config, expected_upstream_url="http://other.test/v1"), session)
    server.urls.append("http://another.test/v1")
    server.by_connection[1] = [MODELS[0]]
    with pytest.raises(Conflict, match="ambiguous"):
        observe(config, session)
    assert not server.writes


def test_prefix_connection_maps_workspace_to_correct_effective_base(config, session, server):
    server.connections = {"0": {"prefix_id": "cluster"}}
    plan = observe(config, session).plan
    assert plan["targets"][0]["id"] == "cluster." + MODELS[0]
    apply(config, session, plan)
    assert server.records["dai.chat." + MODELS[0]]["base_model_id"] == "cluster." + MODELS[0]


def test_empty_or_failed_catalogues_never_cause_mass_disable(config, session, server):
    apply(config, session, observe(config, session).plan)
    before = len(server.writes)
    server.fail_source = True
    with pytest.raises(AdapterError):
        observe(config, session)
    server.fail_source = False
    server.m3 = []
    with pytest.raises(Conflict, match="Empty"):
        observe(config, session)
    assert len(server.writes) == before
    plan = observe(replace(config, allow_empty_catalogue=True), session).plan
    assert len(plan["actions"]) == 4


def test_external_tool_binding_is_not_silently_removed(config, session, server):
    apply(config, session, observe(config, session).plan)
    server.records[MODELS[0]]["meta"]["toolIds"] = ["human-selected-tool"]
    with pytest.raises(Conflict, match="tool bindings"):
        observe(config, session)


def test_verify_requires_actual_saved_and_visible_records(config, session, server):
    with pytest.raises(Conflict):
        verify(config, session)
    apply(config, session, observe(config, session).plan)
    server.by_connection[0].remove(MODELS[0])
    with pytest.raises(Conflict):
        verify(config, session)


def test_local_lock_and_exclusive_private_outputs(config, tmp_path):
    with locked(config.state_dir):
        with pytest.raises(Conflict, match="lock"):
            with locked(config.state_dir):
                pass
    path = tmp_path / "plan.json"
    write_json(path, {"example": True})
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(Conflict):
        write_json(path, {})
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(AdapterError):
        read_json(link)


def test_managed_marker_does_not_allow_foreign_owner_adoption(config, session, server):
    apply(config, session, observe(config, session).plan)
    server.records[MODELS[0]]["_owner"] = "another-user"
    with pytest.raises(Conflict, match="different owner"):
        observe(config, session)


def test_nonadministrator_cannot_plan_base_overrides(config, session, server):
    server.role = "user"
    with pytest.raises(AdapterError, match="administrator"):
        observe(config, session)
    assert not server.writes


@pytest.mark.parametrize("corruption", ["before", "after", "identity"])
def test_corrupt_rollback_snapshot_is_never_written(config, session, server, corruption):
    result = apply(config, session, observe(config, session).plan)
    path = Path(result["journal"])
    value = read_json(path)
    entry = value["entries"][0]
    if corruption == "before":
        entry["before"] = {"id": "different-model"}
    elif corruption == "after":
        entry["after"]["params"]["system"] = "tampered"
    else:
        entry["id"] = "different-model"
    write_json(path, value, replace=True)
    before = len(server.writes)
    with pytest.raises(Conflict):
        rollback(config, session, path)
    assert len(server.writes) == before
