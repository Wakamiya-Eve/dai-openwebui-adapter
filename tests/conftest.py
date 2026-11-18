from copy import deepcopy

import httpx
import pytest

from dai_openwebui_adapter.clients import Session
from dai_openwebui_adapter.config import Config, Endpoint

MODELS = ("muse-glimmer-30b-bf16", "swift15-q8-128k")


class Server:
    """Stateful fake for the audited M3/Gateway/OpenWebUI HTTP contracts."""

    def __init__(self):
        self.m3 = [
            {
                "id": mid,
                "name": mid,
                "capabilities": {
                    "chat": "verified",
                    "streaming": "verified",
                    "tool_calling": "unverified",
                    "vision": "unverified",
                },
            }
            for mid in MODELS
        ]
        self.gateway = [{"id": mid} for mid in MODELS]
        self.urls = ["http://gateway.test/v1"]
        self.connections = {}
        self.by_connection = {0: list(MODELS)}
        self.records = {}
        self.calls = []
        self.writes = []
        self.fail_write = None
        self.ambiguous_write = None
        self.fail_source = False
        self.secret_response = False
        self.extra_visible = []
        self.role = "admin"
        self.tools = []
        self.platform = {
            "features": {
                "slim": True,
                "enable_tools": True,
                "enable_tool_servers": True,
                "enable_memories": True,
                "enable_notes": True,
                "enable_channels": True,
                "enable_web_search": True,
                "enable_image_generation": True,
                "enable_code_interpreter": True,
                "enable_terminal_servers": True,
                "enable_automations": True,
                "enable_calendar": True,
                "enable_user_webhooks": True,
                "enable_subagents": True,
            },
            "audio": {
                "stt": {"engine": "openai"},
                "tts": {"engine": "openai"},
                "realtime": {"enabled": True, "model": "voice-model", "voice": "test"},
            },
        }

    def handle(self, request):
        path = request.url.path
        self.calls.append((request.method, path))
        token = {
            "m3.test": "m3-test-key",
            "gateway.test": "gateway-test-key",
            "web.test": "web-test-key",
        }[request.url.host]
        assert request.headers["authorization"] == "Bearer " + token
        if request.url.host == "m3.test":
            raise AssertionError("Adapter must only consume L4, never call M3 directly")
        if request.url.host == "gateway.test":
            if path == "/v1/models":
                return httpx.Response(200, json={"data": deepcopy(self.gateway)})
            assert path == "/v1/model-capabilities"
            if self.fail_source:
                return httpx.Response(503, text="synthetic-secret-never-print")
            raw, views = [], []
            gateway = {row["id"]: row for row in self.gateway}
            for index, row in enumerate(self.m3):
                caps = row.get("capabilities")
                serving = gateway.get(row["id"])
                raw.append({
                    "schema_version": "m3.capability-catalogue.v1", "id": row["id"],
                    "model": {"display_name": row.get("name", row["id"])},
                    "future": deepcopy(row.get("future", {})),
                    "providers": [{"provider_id": "provider-" + row["id"],
                                   "collection": {"current": True, "expired": False,
                                                  "age_seconds": len(self.calls), "attempted_at": str(len(self.calls))},
                                   "worker_response": {"request_id": str(len(self.calls)), "result": {
                                       "capabilities": {"observed_at": str(len(self.calls)), "future": 42}}},
                                   "normalized": {"inference_routed": serving is not None,
                                                  "interfaces": row.get("interfaces", {}),
                                                  "capabilities": caps}}],
                })
                route_caps = (serving.get("capabilities", {
                    name: "verified" for name in ("chat", "streaming", "multi_turn")
                }) if serving else {})
                views.append({"id": row["id"], "source_pointer": f"/upstream/data/{index}",
                              "gateway": {"available": serving is not None, "model_capabilities": caps,
                                          "capabilities": route_caps,
                                          "interfaces": (serving or {}).get("interfaces", {}),
                                          "limits": {"context_tokens": 131072},
                                          "parameters": {"defaults": {"repeat_penalty": 1.1}}}})
                if serving and "runtime" in serving:
                    raw[-1]["runtime"] = deepcopy(serving["runtime"])
                    views[-1].update(base_name=serving["base_name"], runtime=deepcopy(serving["runtime"]))
            return httpx.Response(200, json={
                "schema_version": "m4.capability-catalogue.v1", "registry_revision": "test-revision",
                "upstream": {"schema_version": "m3.capability-catalogue.v1", "data": raw,
                             "registry_revision": "test-revision", "generated_at": str(len(self.calls))},
                "data": views, "offset": 0, "limit": 100, "total": len(raw), "next_offset": None,
            })
        if path == "/api/v1/auths/":
            return httpx.Response(200, json={"id": "test-owner", "role": self.role})
        if path == "/api/v1/tools/":
            return httpx.Response(200, json=self.tools)
        if path == "/api/config":
            return httpx.Response(200, json=self.platform)
        if path == "/openai/config":
            return httpx.Response(
                200,
                json={
                    "ENABLE_OPENAI_API": True,
                    "OPENAI_API_BASE_URLS": self.urls,
                    "OPENAI_API_CONFIGS": self.connections,
                    "OPENAI_API_KEYS": ["synthetic-admin-secret-never-print"],
                },
            )
        if path.startswith("/openai/models/"):
            idx = int(path.rsplit("/", 1)[1])
            return httpx.Response(
                200, json={"data": [{"id": mid} for mid in self.by_connection[idx]]}
            )
        if path == "/api/v1/models/base":
            return httpx.Response(
                200,
                json=[
                    deepcopy(row) for row in self.records.values() if row["base_model_id"] is None
                ],
            )
        if path == "/api/v1/models/list":
            values = [deepcopy(row) for row in self.records.values() if row["base_model_id"]]
            page = int(request.url.params["page"])
            return httpx.Response(
                200,
                json={"items": values[(page - 1) * 2 : page * 2], "total": len(values)},
            )
        if path == "/api/v1/models/model":
            value = self.records.get(request.url.params["id"])
            if value is None:
                return httpx.Response(404, json={"detail": "not found"})
            output = deepcopy(value)
            # Real ModelMeta serializes nullable defaults, read routes add non-form fields.
            output["meta"].setdefault("description", None)
            output["meta"].setdefault("profile_image_url", None)
            output["params"].setdefault("model_controls", {})
            output.update(
                write_access=True,
                user_id=output.pop("_owner", "test-owner"),
                updated_at=100,
            )
            return httpx.Response(200, json=output)
        if path in {"/api/v1/models/create", "/api/v1/models/model/update"}:
            import json

            value = json.loads(request.content)
            n = len(self.writes) + 1
            if self.fail_write == n:
                return httpx.Response(500, text="synthetic-secret-never-print")
            if path.endswith("/create"):
                assert value["id"] not in self.records
            else:
                assert value["id"] in self.records
            self.records[value["id"]] = deepcopy(value)
            self.writes.append(deepcopy(value))
            if self.ambiguous_write == n:
                raise httpx.ReadTimeout("synthetic-secret-never-print", request=request)
            return httpx.Response(200, json=value)
        if path == "/api/models":
            visible = [{"id": mid} for mid in self.by_connection[0]]
            visible += [
                {"id": mid, "preset": True}
                for mid, row in self.records.items()
                if row["is_active"] and row["base_model_id"]
            ]
            return httpx.Response(200, json={"data": visible + self.extra_visible})
        raise AssertionError("Unexpected API request: " + path)


@pytest.fixture
def config(tmp_path, monkeypatch):
    for name, value in (
        ("M3_KEY", "m3-test-key"),
        ("GW_KEY", "gateway-test-key"),
        ("WEB_KEY", "web-test-key"),
    ):
        monkeypatch.setenv(name, value)
    return Config(
        "test-dai",
        Endpoint("http://m3.test/v1", "M3_KEY"),
        Endpoint("http://gateway.test/v1", "GW_KEY"),
        Endpoint("http://web.test", "WEB_KEY"),
        0,
        "http://gateway.test/v1",
        "dai.chat.",
        tmp_path / "state",
    )


@pytest.fixture
def server():
    return Server()


@pytest.fixture
def session(config, server):
    with Session(config, transport=httpx.MockTransport(server.handle)) as value:
        yield value
