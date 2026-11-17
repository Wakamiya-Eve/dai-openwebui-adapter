from copy import deepcopy
import json

import httpx
import pytest
import yaml

from dai_openwebui_adapter.catalogue import read_catalogue, semantic_model
from dai_openwebui_adapter.clients import Session
from dai_openwebui_adapter.config import load_config
from dai_openwebui_adapter.errors import AdapterError, Conflict
from dai_openwebui_adapter.planner import observe
from dai_openwebui_adapter.publisher import apply
from conftest import MODELS
from test_capabilities import complete
from test_config_cli import document


def test_only_l4_credentials_and_versioned_capability_endpoint_are_used(
    config, server, monkeypatch
):
    monkeypatch.delenv("M3_KEY")
    with Session(config, transport=httpx.MockTransport(server.handle)) as session:
        source = session.sources.read()
    assert set(source["models"]) == set(MODELS)
    assert server.calls == [("GET", "/v1/model-capabilities")]


def test_gateway_only_v2_configuration(config, tmp_path, monkeypatch, server):
    value = document(config)
    value["schema_version"] = "dai.openwebui-adapter.v2"
    del value["sources"]["m3"]
    file = tmp_path / "adapter.yaml"
    file.write_text(yaml.safe_dump(value))
    loaded = load_config(file)
    assert loaded.m3 is None
    monkeypatch.delenv("M3_KEY")
    with Session(loaded, transport=httpx.MockTransport(server.handle)) as session:
        assert len(session.sources.read()["models"]) == 2


def test_unknown_source_fields_and_parameter_contract_survive(config, session, server):
    server.m3[0]["future"] = {"unknown_modality": [True, None, {"range": [1, 8]}]}
    source = session.sources.read()
    assert source["models"][MODELS[0]]["source"]["future"] == server.m3[0]["future"]
    plan = observe(config, session).plan
    record = next(row["patch"] for row in plan["actions"] if row["id"] == MODELS[0])
    assert record["meta"]["dai"]["limits"]["context_tokens"] == 131072
    assert record["meta"]["dai"]["parameters"]["defaults"]["repeat_penalty"] == 1.1
    assert "unknown_modality" not in json.dumps(
        plan
    )  # Full data is consumed, not copied into app prompts.
    server.m3[0]["future"]["unknown_modality"].append("changed")
    with pytest.raises(Conflict):
        apply(config, session, plan)


def test_poll_timestamps_do_not_make_reviewed_plans_permanently_stale(config, session, server):
    first = observe(config, session).plan
    second = observe(config, session).plan
    assert first == second
    apply(config, session, first)
    assert observe(config, session).plan["actions"] == []


def test_platform_disables_external_tools_without_disabling_supported_builtin_categories(
    config, session, server
):
    complete(server)
    server.tools = [{"id": "local-tool"}, {"id": "server:mcp:test"}]
    server.platform["features"].update(enable_tools=False, enable_tool_servers=False)
    plan = observe(config, session).plan
    first = next(row["patch"] for row in plan["actions"] if row["id"] == MODELS[0])
    assert first["meta"]["capabilities"]["builtin_tools"]
    assert not first["meta"]["builtinTools"]["tools"]
    assert first["meta"]["toolIds"] == []
    assert ("GET", "/api/v1/tools/") not in server.calls


class Pages:
    def __init__(self, pages):
        self.pages = pages

    def request(self, method, path, *, params):
        assert method == "GET" and path == "model-capabilities"
        return deepcopy(self.pages[params["offset"]])


def pages_from(session):
    full = session.sources.gateway.request("GET", "model-capabilities")
    pages = {}
    for i in range(2):
        page = deepcopy(full)
        page["data"] = [page["data"][i]]
        page["data"][0]["source_pointer"] = "/upstream/data/0"
        page["upstream"]["data"] = [page["upstream"]["data"][i]]
        page.update(offset=i, limit=1, next_offset=1 if i == 0 else None)
        pages[i] = page
    return pages


def test_pagination_reads_every_model_with_complete_source(session):
    result = read_catalogue(Pages(pages_from(session)))
    assert set(result["models"]) == set(MODELS)
    assert (
        result["models"][MODELS[1]]["source"]["providers"][0]["worker_response"]["result"][
            "capabilities"
        ]["future"]
        == 42
    )


@pytest.mark.parametrize(
    "mutation", ["revision", "cursor", "pointer", "schema", "missing", "state"]
)
def test_bad_or_changing_catalogue_cannot_publish(session, mutation):
    pages = pages_from(session)
    if mutation == "revision":
        pages[1]["registry_revision"] = "changed"
    elif mutation == "cursor":
        pages[0]["next_offset"] = 0
    elif mutation == "pointer":
        pages[0]["data"][0]["source_pointer"] = "/untrusted"
    elif mutation == "schema":
        pages[0]["schema_version"] = "legacy"
    elif mutation == "missing":
        pages[1]["upstream"]["data"] = []
    elif mutation == "state":
        pages[0]["data"][0]["gateway"]["capabilities"]["chat"] = True
    with pytest.raises((AdapterError, Conflict)):
        read_catalogue(Pages(pages))


def test_semantic_hash_removes_polling_fields_but_keeps_unknown_names():
    raw = {
        "future": {"observed_at": "producer-owned-data"},
        "providers": [
            {
                "collection": {"age_seconds": 4, "attempted_at": "time", "expired": False},
                "worker_response": {
                    "request_id": "poll",
                    "future": {"age_seconds": 999},
                    "result": {
                        "capabilities": {"observed_at": "time", "description": {"unknown": None}}
                    },
                },
            }
        ],
    }
    stable = semantic_model(raw)
    assert stable["future"]["observed_at"] == "producer-owned-data"
    assert stable["providers"][0]["worker_response"]["future"]["age_seconds"] == 999
    assert stable["providers"][0]["collection"] == {"expired": False}
    assert raw["providers"][0]["worker_response"]["request_id"] == "poll"


def test_complete_catalogue_pages_retain_top_level_unknown_fields_and_original_timestamps(session):
    pages = pages_from(session)
    pages[0]["upstream"]["future_catalogue_field"] = {"nested": [None, True]}
    source = read_catalogue(Pages(pages))
    assert source["catalogue"][0] == pages[0]
    assert source["catalogue"][0]["upstream"]["future_catalogue_field"] == {"nested": [None, True]}
    assert "generated_at" in source["catalogue"][0]["upstream"]
