from copy import deepcopy

import httpx
import pytest

from dai_openwebui_adapter.catalogue import semantic_model
from dai_openwebui_adapter.clients import Session
from dai_openwebui_adapter.errors import Conflict
from dai_openwebui_adapter.model import digest
from dai_openwebui_adapter.planner import observe
from dai_openwebui_adapter.publisher import apply
from dai_openwebui_adapter.sync import sync


def report(created):
    description = {
        "model": {"id": "model", "created": created, "object": "model"},
        "capabilities": {"streaming": {"supported": True}},
        "unknown_extension": {"limit": 123},
    }
    return {
        "description": description,
        "description_digest": digest(description),
        "source_documents": {
            "models": {"data": [{"id": "model", "created": created, "unknown_extension": 42}]}
        },
    }


def source(created):
    return {"providers": [{"worker_response": {"result": {"capabilities": report(created)}}}]}


def test_creation_timestamp_only_is_stable_without_changing_raw_evidence():
    a, b = source(100), source(200)
    raw = deepcopy(a)
    assert semantic_model(a) == semantic_model(b)
    assert a == raw
    b["providers"][0]["worker_response"]["result"]["capabilities"]["description"][
        "unknown_extension"
    ]["limit"] = 124
    assert semantic_model(a) != semantic_model(b)


def test_unknown_model_extensions_and_standalone_digests_are_not_ignored():
    a, b = source(100), source(200)
    b["providers"][0]["worker_response"]["result"]["capabilities"]["source_documents"]["models"][
        "data"
    ][0]["unknown_extension"] = 43
    assert semantic_model(a) != semantic_model(b)
    for value in [a, b]:
        del value["providers"][0]["worker_response"]["result"]["capabilities"]["description"]
    assert semantic_model(a) != semantic_model(b)


def test_live_timestamp_drift_allows_apply_but_real_description_changes_still_conflict(
    config, server
):
    calls = [0]
    extension = [123]

    def handle(request):
        response = server.handle(request)
        if request.url.host == "gateway.test" and request.url.path == "/v1/model-capabilities":
            body = response.json()
            calls[0] += 1
            for row in body["upstream"]["data"]:
                observation = row["providers"][0]["worker_response"]["result"]["capabilities"]
                observation.update(report(calls[0]))
                observation["description"]["unknown_extension"]["limit"] = extension[0]
                observation["description_digest"] = digest(observation["description"])
            return httpx.Response(200, json=body)
        return response

    with Session(config, transport=httpx.MockTransport(handle)) as session:
        assert sync(config, session)["status"] == "updated"
        assert sync(config, session)["apply_called"] is False
        assert len(server.writes) == 4
        plan = observe(config, session).plan
        extension[0] = 124
        with pytest.raises(Conflict, match="changed after review"):
            apply(config, session, plan)
        assert len(server.writes) == 4
