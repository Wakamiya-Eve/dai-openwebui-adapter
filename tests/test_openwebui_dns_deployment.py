"""Regression guards for preserving production state during DNS-only recreation."""

import copy
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "manage_dns", Path(__file__).resolve().parents[1] / "deploy/openwebui/manage-dns.py"
)
dns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dns)


def test_refuse_unrelated_environment_change():
    before = {"services": {"open-webui": {"image": "same", "environment": {"A": "1"}}}}
    after = copy.deepcopy(before)
    after["services"]["open-webui"].update(dns_opt=["no-aaaa"], environment={"A": "2"})
    with pytest.raises(AssertionError, match="more than DNS"):
        dns.assert_only_dns_changed(before, after)


def test_refuse_replacing_existing_resolver_options():
    before = {"services": {"open-webui": {"dns_opt": ["timeout:2"]}}}
    with pytest.raises(AssertionError, match="Existing DNS"):
        dns.assert_only_dns_changed(before, before)


@pytest.mark.parametrize("changed", ["Image", "Mounts", "HostConfig", "Config"])
def test_detect_runtime_data_image_ports_or_credentials_drift(changed):
    before = {
        "Image": "sha256:original", "Mounts": [
            {"Type": "bind", "Source": "/data", "Destination": "/app/backend/data", "RW": True}
        ],
        "NetworkSettings": {"Networks": {"ai-apps-net": {}}},
        "HostConfig": {"PortBindings": {"8080/tcp": [{"HostPort": "13000"}]}},
        "Config": {"Env": ["A=private-value"]},
    }
    after = copy.deepcopy(before)
    replacements = {
        "Image": "sha256:different", "Mounts": [],
        "HostConfig": {"PortBindings": {}}, "Config": {"Env": ["A=changed"]},
    }
    after[changed] = replacements[changed]
    with pytest.raises(AssertionError, match="Runtime image/data/env drift"):
        dns.assert_runtime_preserved(before, after)


def test_dns_only_change_allowed():
    before = {"services": {"open-webui": {"image": "same", "volumes": ["/data:/data"]}}}
    after = copy.deepcopy(before)
    after["services"]["open-webui"]["dns_opt"] = ["no-aaaa"]
    dns.assert_only_dns_changed(before, after)
