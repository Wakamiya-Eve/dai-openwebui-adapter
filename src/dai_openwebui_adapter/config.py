"""Small, strict configuration; credentials exist only in environment variables."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import yaml

from .errors import AdapterError
from .model import digest


@dataclass(frozen=True)
class Endpoint:
    url: str
    api_key_env: str

    def token(self) -> str:
        value = os.environ.get(self.api_key_env, "").strip()
        if not value or any(c in value for c in "\r\n"):
            raise AdapterError(
                "Missing or invalid credential environment variable: " + self.api_key_env
            )
        return value


@dataclass(frozen=True)
class Config:
    source_id: str
    m3: Endpoint
    gateway: Endpoint
    openwebui: Endpoint
    connection_index: int
    expected_upstream_url: str
    workspace_prefix: str
    state_dir: Path
    timeout_seconds: float = 30
    allow_empty_catalogue: bool = False

    def document(self) -> dict:
        value = asdict(self)
        value["state_dir"] = str(self.state_dir)
        return value

    @property
    def fingerprint(self) -> str:
        return digest(self.document())

    @property
    def scope(self) -> str:
        return digest(
            {
                "source": self.source_id,
                "server": self.openwebui.url,
                "connection": self.connection_index,
                "upstream": self.expected_upstream_url,
                "prefix": self.workspace_prefix,
            }
        )


def _mapping(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise AdapterError("Invalid configuration fields in " + label)
    return value


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AdapterError("Invalid configuration value: " + label)
    return value


def _url(value, label):
    value = _text(value, label).rstrip("/")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise AdapterError("Invalid endpoint URL: " + label) from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or port == 0
    ):
        raise AdapterError("Invalid endpoint URL: " + label)
    if any(c.isspace() for c in value):
        raise AdapterError("Invalid endpoint URL: " + label)
    return value


def _endpoint(value, label):
    value = _mapping(value, {"url", "api_key_env"}, label)
    name = _text(value["api_key_env"], label + ".api_key_env")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise AdapterError("Invalid credential environment variable name")
    return Endpoint(_url(value["url"], label), name)


def load_config(path: str | Path) -> Config:
    try:
        raw = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError):
        raise AdapterError("Cannot read adapter YAML configuration") from None
    required = {
        "schema_version",
        "source_id",
        "sources",
        "openwebui",
        "publication",
        "state_dir",
    }
    if (
        not isinstance(raw, dict)
        or not required <= set(raw)
        or set(raw) - required - {"timeout_seconds", "allow_empty_catalogue"}
    ):
        raise AdapterError("Invalid top-level configuration fields")
    if raw["schema_version"] != "dai.openwebui-adapter.v1":
        raise AdapterError("Unsupported configuration schema_version")
    source = _text(raw["source_id"], "source_id")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", source):
        raise AdapterError("Invalid source_id")
    sources = _mapping(raw["sources"], {"m3", "gateway"}, "sources")
    web = _mapping(
        raw["openwebui"],
        {"url", "api_key_env", "connection_index", "expected_upstream_url"},
        "openwebui",
    )
    publication = _mapping(raw["publication"], {"workspace_prefix", "profile"}, "publication")
    if publication["profile"] != "chat":
        raise AdapterError("This version publishes the plain text chat profile only")
    prefix = _text(publication["workspace_prefix"], "workspace_prefix")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.", prefix):
        raise AdapterError("workspace_prefix must be a namespace ending in a dot")
    index = web["connection_index"]
    if type(index) is not int or index < 0:
        raise AdapterError("connection_index must be a non-negative integer")
    timeout = raw.get("timeout_seconds", 30)
    if type(timeout) not in (int, float) or not 0 < timeout <= 300:
        raise AdapterError("timeout_seconds must be between 0 and 300")
    empty = raw.get("allow_empty_catalogue", False)
    if type(empty) is not bool:
        raise AdapterError("allow_empty_catalogue must be boolean")
    state = Path(_text(raw["state_dir"], "state_dir")).expanduser()
    if not state.is_absolute():
        raise AdapterError("state_dir must be absolute")
    return Config(
        source,
        _endpoint(sources["m3"], "sources.m3"),
        _endpoint(sources["gateway"], "sources.gateway"),
        _endpoint({k: web[k] for k in ("url", "api_key_env")}, "openwebui"),
        index,
        _url(web["expected_upstream_url"], "expected_upstream_url"),
        prefix,
        state,
        float(timeout),
        empty,
    )
