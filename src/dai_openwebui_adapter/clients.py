"""HTTP-only clients. No inference proxy, SSH, database access or service control."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import httpx

from .config import Config, Endpoint
from .errors import AdapterError, Conflict
from .model import form, valid_id

MAX_BYTES = 8 * 1024 * 1024
MAX_MODELS = 10000


class API:
    def __init__(self, endpoint: Endpoint, timeout: float, *, transport=None):
        self._client = httpx.Client(
            base_url=endpoint.url + "/",
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            headers={"Authorization": "Bearer " + endpoint.token()},
            transport=transport,
        )

    def close(self):
        self._client.close()

    def request(self, method: str, path: str, *, missing=False, **kwargs):
        try:
            response = self._client.request(method, path.lstrip("/"), **kwargs)
        except httpx.HTTPError:
            raise AdapterError("API transport failed; no response details are logged") from None
        if missing and response.status_code == 404:
            return None
        if response.status_code != 200:
            raise AdapterError("API request failed with HTTP " + str(response.status_code))
        if len(response.content) > MAX_BYTES:
            raise AdapterError("API response exceeds supported size")
        try:
            return response.json()
        except ValueError:
            raise AdapterError("API response is not valid JSON") from None


def rows(value: Any) -> list[dict]:
    if not isinstance(value, dict) or not isinstance(value.get("data"), list):
        raise AdapterError("Model catalogue must contain a data list")
    result = value["data"]
    if len(result) > MAX_MODELS:
        raise AdapterError("Model catalogue exceeds supported count")
    seen = set()
    for row in result:
        if not isinstance(row, dict) or not valid_id(row.get("id")) or row["id"] in seen:
            raise AdapterError("Model catalogue has invalid or duplicate IDs")
        seen.add(row["id"])
    return result


class Sources:
    def __init__(self, m3: API, gateway: API):
        self.m3, self.gateway = m3, gateway

    def read(self, *, allow_empty=False):
        raw = rows(self.m3.request("GET", "models"))
        available = rows(self.gateway.request("GET", "models"))
        if not allow_empty and (not raw or not available):
            raise Conflict(
                "Empty source catalogue requires explicit allow_empty_catalogue configuration"
            )
        models = {}
        for row in raw:
            caps = row.get("capabilities")
            if not isinstance(caps, dict) or any(
                not isinstance(value, str) or value not in {"verified", "unverified", "unsupported"}
                for value in caps.values()
            ):
                raise AdapterError("M3 model capabilities must use tri-state declarations")
            name = row.get("name") or row["id"]
            if not isinstance(name, str):
                raise AdapterError("M3 model display name must be text")
            models[row["id"]] = {"id": row["id"], "name": name, "capabilities": caps}
        return {"models": models, "gateway_ids": sorted(row["id"] for row in available)}


class OpenWebUI:
    def __init__(self, api: API):
        self.api = api
        self.owner_id = None

    def identity(self):
        if self.owner_id is None:
            value = self.api.request("GET", "api/v1/auths/")
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("id"), str)
                or not value["id"]
                or value.get("role") != "admin"
            ):
                raise AdapterError("OpenWebUI publishing requires an authenticated administrator")
            self.owner_id = value["id"]
        return self.owner_id

    def connection(self, config: Config) -> dict:
        """Prove origin before overriding globally named OpenWebUI base records."""
        owner_id = self.identity()
        raw = self.api.request("GET", "openai/config")
        if not isinstance(raw, dict) or raw.get("ENABLE_OPENAI_API") is not True:
            raise AdapterError(
                "OpenWebUI OpenAI connections are disabled or have an unsupported schema"
            )
        urls, configs = raw.get("OPENAI_API_BASE_URLS"), raw.get("OPENAI_API_CONFIGS")
        if not isinstance(urls, list) or not isinstance(configs, dict):
            raise AdapterError("Unsupported OpenWebUI connection-config schema")
        if config.connection_index >= len(urls) or not isinstance(
            urls[config.connection_index], str
        ):
            raise Conflict("Selected OpenWebUI connection does not exist")
        if urls[config.connection_index].rstrip("/") != config.expected_upstream_url:
            raise Conflict(
                "Selected OpenWebUI connection URL does not match the configured gateway"
            )
        ownership: dict[str, list[int]] = {}
        selected_ids = set()
        selected_prefix = ""
        public_connections = []
        for index, url in enumerate(urls):
            if not isinstance(url, str):
                raise AdapterError("Invalid OpenWebUI connection URL")
            settings = configs.get(str(index), configs.get(url, {}))
            if not isinstance(settings, dict):
                raise AdapterError("Invalid OpenWebUI per-connection configuration")
            enabled = settings.get("enable", True)
            if type(enabled) is not bool:
                raise AdapterError("Invalid OpenWebUI connection enable flag")
            prefix = settings.get("prefix_id") or ""
            if not isinstance(prefix, str) or any(c.isspace() for c in prefix):
                raise AdapterError("Invalid OpenWebUI connection model prefix")
            public_connections.append(
                {"index": index, "url": url, "enable": enabled, "prefix": prefix}
            )
            if not enabled:
                if index == config.connection_index:
                    raise Conflict("Selected OpenWebUI connection is disabled")
                continue
            value = self.api.request("GET", "openai/models/" + str(index))
            values = value.get("data") if isinstance(value, dict) else None
            if not isinstance(values, list) or len(values) > MAX_MODELS:
                raise AdapterError("Unsupported OpenWebUI connection-model schema")
            ids = set()
            for entry in values:
                mid = (
                    entry
                    if isinstance(entry, str)
                    else entry.get("id")
                    if isinstance(entry, dict)
                    else None
                )
                if not valid_id(mid) or mid in ids:
                    raise AdapterError("Invalid or duplicate connection model ID")
                ids.add(mid)
                effective = prefix + "." + mid if prefix else mid
                ownership.setdefault(effective, []).append(index)
            if index == config.connection_index:
                selected_ids, selected_prefix = set(ids), prefix
        return {
            "owner_id": owner_id,
            "connections": public_connections,
            "ownership": ownership,
            "selected_ids": sorted(selected_ids),
            "prefix": selected_prefix,
        }

    def get(self, mid: str):
        record = self.api.request("GET", "api/v1/models/model", params={"id": mid}, missing=True)
        if record is None:
            return None
        if not isinstance(record, dict):
            raise AdapterError("Invalid OpenWebUI model response")
        if record.get("write_access") is False:
            raise Conflict("OpenWebUI model is not writable by the authenticated account")
        if record.get("user_id") != self.identity():
            raise Conflict("Existing OpenWebUI model belongs to a different owner")
        result = form(record)
        if result["id"] != mid:
            raise AdapterError("OpenWebUI returned a different model ID")
        return result

    def stored(self):
        """List both saved base overrides and workspace presets, then fetch full forms."""
        base = self.api.request("GET", "api/v1/models/base")
        if not isinstance(base, list) or len(base) > MAX_MODELS:
            raise AdapterError("Unsupported saved-base-model response")
        result = {}
        for record in base:
            if not isinstance(record, dict) or not valid_id(record.get("id")):
                raise AdapterError("Invalid saved base model")
            if record["id"] in result:
                raise AdapterError("Duplicate saved base model")
            result[record["id"]] = record
        page, count, total = 1, 0, None
        while True:
            value = self.api.request("GET", "api/v1/models/list", params={"page": page})
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("items"), list)
                or type(value.get("total")) is not int
                or not 0 <= value["total"] <= MAX_MODELS
            ):
                raise AdapterError("Unsupported workspace-model list response")
            if total is None:
                total = value["total"]
            if total != value["total"]:
                raise Conflict("OpenWebUI model list changed during pagination")
            for record in value["items"]:
                if not isinstance(record, dict) or not valid_id(record.get("id")):
                    raise AdapterError("Invalid workspace model")
                if record["id"] in result:
                    raise Conflict("Duplicate or overlapping OpenWebUI model-list records")
                result[record["id"]] = record
            count += len(value["items"])
            if count >= total:
                if count != total:
                    raise AdapterError("Workspace pagination count is inconsistent")
                break
            if not value["items"]:
                raise AdapterError("Workspace pagination ended early")
            page += 1
        return result

    def write(self, body: dict, *, create: bool):
        path = "api/v1/models/create" if create else "api/v1/models/model/update"
        result = self.api.request("POST", path, json=deepcopy(body))
        if not isinstance(result, dict) or result.get("id") != body["id"]:
            raise AdapterError("OpenWebUI did not confirm the written model")

    def refresh(self):
        return rows(self.api.request("GET", "api/models", params={"refresh": "true"}))


class Session:
    def __init__(self, config: Config, *, transport=None):
        self.apis = []
        try:
            for endpoint in (config.m3, config.gateway, config.openwebui):
                self.apis.append(API(endpoint, config.timeout_seconds, transport=transport))
        except BaseException:
            self.close()
            raise
        self.sources = Sources(*self.apis[:2])
        self.web = OpenWebUI(self.apis[2])

    def close(self):
        for api in self.apis:
            api.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
