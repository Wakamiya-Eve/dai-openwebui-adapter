"""Consume the versioned L4 catalogue without direct M3 credentials or calls."""

from copy import deepcopy

from .errors import AdapterError, Conflict
from .model import digest, valid_id

SCHEMA = "m4.capability-catalogue.v1"
STATES = {"verified", "unverified", "unsupported"}


def semantic_model(value):
    """Exclude only known polling bookkeeping; retain all capability extensions."""
    value = deepcopy(value)
    for provider in value.get("providers", []):
        provider.pop("last_known_collected_at", None)
        for key in ("attempted_at", "age_seconds"):
            provider.get("collection", {}).pop(key, None)
        for key in ("worker_response", "last_known_worker_response"):
            envelope = provider.get(key)
            if not isinstance(envelope, dict):
                continue
            envelope.pop("request_id", None)
            report = (envelope.get("result") or {}).get("capabilities")
            if isinstance(report, dict):
                for timestamp in ("observed_at", "last_attempt_at"):
                    report.pop(timestamp, None)
    return value


def states(value):
    if not isinstance(value, dict) or any(
        not isinstance(v, str) or v not in STATES for v in value.values()
    ):
        raise AdapterError("L4 capability states are invalid")
    return deepcopy(value)


def read_catalogue(api, *, allow_empty=False):
    models, gateways, pages, raw_pages = {}, {}, [], []
    offset, revision, total = 0, None, None
    while True:
        page = api.request(
            "GET", "model-capabilities", params={"offset": offset, "limit": 100, "refresh": "true"}
        )
        if not isinstance(page, dict) or page.get("schema_version") != SCHEMA:
            raise AdapterError(
                "L4 must provide m4.capability-catalogue.v1; legacy /models is insufficient"
            )
        upstream = page.get("upstream")
        views = page.get("data")
        if (
            not isinstance(upstream, dict)
            or upstream.get("schema_version") != "m3.capability-catalogue.v1"
            or not isinstance(upstream.get("data"), list)
            or not isinstance(views, list)
            or len(upstream["data"]) != len(views)
        ):
            raise AdapterError("L4 did not preserve the complete M3 capability page")
        if (
            type(page.get("total")) is not int
            or not 0 <= page["total"] <= 10000
            or page.get("offset") != offset
        ):
            raise AdapterError("L4 capability pagination is invalid")
        if total is None:
            total, revision = page["total"], page.get("registry_revision")
        if total != page["total"] or revision != page.get("registry_revision"):
            raise Conflict("L4 registry changed during pagination")
        raw_pages.append(deepcopy(page))
        stable = deepcopy(page)
        stable["upstream"].pop("generated_at", None)
        stable["upstream"]["data"] = [semantic_model(row) for row in upstream["data"]]
        pages.append(stable)
        for index, (view, raw) in enumerate(zip(views, upstream["data"])):
            if (
                not isinstance(view, dict)
                or not isinstance(raw, dict)
                or not valid_id(view.get("id"))
                or view["id"] != raw.get("id")
                or view.get("source_pointer") != f"/upstream/data/{index}"
                or view["id"] in models
            ):
                raise AdapterError("L4 model identity or source pointer is invalid")
            mid = view["id"]
            gateway = view.get("gateway")
            if not isinstance(gateway, dict) or type(gateway.get("available")) is not bool:
                raise AdapterError("L4 serving declaration is invalid")
            model_caps = states(gateway.get("model_capabilities"))
            route_caps = states(gateway.get("capabilities"))
            name = (raw.get("model") or {}).get("display_name") or mid
            if not isinstance(name, str):
                raise AdapterError("Invalid model display name")
            gateway = deepcopy(gateway)
            gateway["capabilities"] = route_caps
            gateways[mid] = gateway
            routed = [
                p
                for p in raw.get("providers", [])
                if p.get("normalized", {}).get("inference_routed") is True
            ]
            interfaces = (
                routed[0].get("normalized", {}).get("interfaces", {}) if len(routed) == 1 else {}
            )
            models[mid] = {
                "id": mid,
                "name": name,
                "capabilities": model_caps,
                "interfaces": deepcopy(interfaces),
                "limits": deepcopy(gateway.get("limits", {})),
                "parameters": deepcopy(gateway.get("parameters", {})),
                "evidence": deepcopy(gateway.get("evidence", {})),
                "capability_revision": digest({"source": semantic_model(raw), "gateway": gateway}),
                "source": deepcopy(
                    raw
                ),  # Full data remains available to the adapter, not published as prompts.
            }
        next_offset = page.get("next_offset")
        if next_offset is None:
            if len(models) != total:
                raise AdapterError("L4 pagination ended before the complete catalogue")
            break
        if (
            type(next_offset) is not int
            or next_offset <= offset
            or next_offset != len(models)
            or next_offset >= total
        ):
            raise AdapterError("L4 pagination cursor is invalid")
        offset = next_offset
    if not models and not allow_empty:
        raise Conflict(
            "Empty source catalogue requires explicit allow_empty_catalogue configuration"
        )
    return {
        "models": models,
        "gateway": gateways,
        "gateway_ids": sorted(mid for mid, row in gateways.items() if row["available"]),
        "revision": digest(pages),
        "catalogue": raw_pages,
    }
