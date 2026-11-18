"""N2830-only Compose DNS override, preserving the current image and data mount.

Run as root with apply, verify or rollback. Never prints resolved environment.
"""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT = Path("/opt/open-webui-slim")
BASE = ROOT / "compose.yaml"
OVERRIDE = ROOT / "compose.override.yaml"
STATE = ROOT / "dns-no-aaaa-state.json"
HERE = Path(__file__).resolve().parent
CONTAINER = "open-webui-slim"
SERVICE = "open-webui"
PREFIX = ["docker", "compose", "--project-directory", str(ROOT), "-p", CONTAINER,
          "-f", str(BASE)]


def run(args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=180, **kwargs)
    if result.returncode:
        # Compose may include credentials in stderr; keep output private.
        raise RuntimeError("External command failed; exit=" + str(result.returncode))
    return result.stdout


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def inspect():
    return json.loads(run(["docker", "inspect", CONTAINER]))[0]


def runtime_signature(current):
    return {
        "image_id": current["Image"],
        "mounts": sorted((x["Type"], x["Source"], x["Destination"], x["RW"])
                         for x in current["Mounts"]),
        "networks": sorted(current["NetworkSettings"]["Networks"]),
        "ports": current["HostConfig"]["PortBindings"],
        "environment_sha256": digest(sorted(current["Config"]["Env"])),
    }


def compose_config(extra=()):
    return json.loads(run([*PREFIX, *extra, "config", "--format", "json"]))


def assert_only_dns_changed(before, after):
    expected = copy.deepcopy(before)
    service = expected["services"][SERVICE]
    assert not service.get("dns_opt"), "Existing DNS options need manual review"
    service["dns_opt"] = ["no-aaaa"]
    assert after == expected, "Merged Compose changes more than DNS options"


def assert_runtime_preserved(before, after):
    assert runtime_signature(before) == runtime_signature(after), "Runtime image/data/env drift"


def preflight(require_running=True):
    current = inspect()
    labels = current["Config"]["Labels"]
    assert labels["com.docker.compose.project"] == CONTAINER
    assert labels["com.docker.compose.service"] == SERVICE
    assert labels["com.docker.compose.project.working_dir"] == str(ROOT)
    if require_running:
        assert current["State"]["Running"], "OpenWebUI not running"
    assert any(x["Source"] == "/srv/n2830-data/app-data/open-webui-slim/data"
               and x["Destination"] == "/app/backend/data" for x in current["Mounts"])
    for name in ["compose.override.yml", "docker-compose.override.yml",
                 "docker-compose.override.yaml"]:
        assert not (ROOT / name).exists(), "Another default override needs manual review"
    base = compose_config()
    assert set(base["services"]) == {SERVICE}, "Unexpected services"
    image = base["services"][SERVICE]["image"]
    local = json.loads(run(["docker", "image", "inspect", image]))[0]
    assert local["Id"] == current["Image"], "Local image tag differs from running image"
    env = dict(value.split("=", 1) for value in current["Config"]["Env"] if "=" in value)
    assert not env.get("RES_OPTIONS"), "RES_OPTIONS override requires review"
    return current, base


def recreate(extra=()):
    run([*PREFIX, *extra, "up", "-d", "--no-deps", "--pull", "never", "--no-build", SERVICE])
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:13000/health", timeout=5) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(3)
    raise RuntimeError("OpenWebUI health timeout")


def write_state(state):
    temporary = STATE.with_suffix(".tmp")
    with temporary.open("w") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(state, stream, indent=2)
    temporary.replace(STATE)


def verify(state):
    current, base = preflight()
    assert digest(base) == state["base_config_sha256"], "Base Compose configuration drift"
    assert digest(runtime_signature(current)) == state["runtime_sha256"], "Runtime drift"
    assert OVERRIDE.read_bytes() == (HERE / "compose.override.yaml").read_bytes()
    assert current["HostConfig"].get("DnsOptions") == ["no-aaaa"]
    resolver = run(["docker", "exec", CONTAINER, "cat", "/etc/resolv.conf"])
    assert any(line.startswith("options ") and "no-aaaa" in line.split()[1:]
               for line in resolver.splitlines()), "Resolver option not present"
    # No docker exec -e override: exercise actual container startup configuration.
    probe = json.loads(run(["docker", "exec", "-i", CONTAINER, "python", "-"],
                           input=(HERE / "verify-fetch.py").read_text()))
    assert probe["ok"]
    return {"ok": True, "image_id": current["Image"], "runtime_preserved": True,
            "dns_options": current["HostConfig"]["DnsOptions"], "fetch_probe": probe}


def rollback(state):
    current, base = preflight(require_running=False)
    assert digest(base) == state["base_config_sha256"], "Base Compose configuration drift"
    actual = runtime_signature(current)
    expected = copy.deepcopy(state["runtime"])
    # A failed start can leave network attachments absent; restore from the
    # unchanged original Compose definition, while still protecting image/data/env.
    if not current["State"]["Running"]:
        actual.pop("networks")
        expected.pop("networks")
    assert digest(actual) == digest(expected), "Runtime drift"
    assert OVERRIDE.read_bytes() == (HERE / "compose.override.yaml").read_bytes()
    destination = ROOT / ("compose.override.disabled-" + str(time.time_ns()))
    OVERRIDE.rename(destination)
    recreate()
    assert digest(runtime_signature(inspect())) == state["runtime_sha256"]
    assert not inspect()["HostConfig"].get("DnsOptions")
    state.update(status="rolled_back", disabled_override=str(destination))
    write_state(state)
    return {"ok": True, "rolled_back": True, "database_mount_preserved": True}


def apply():
    if STATE.exists():
        state = json.loads(STATE.read_text())
        if state.get("status") == "applied":
            return verify(state)
        raise RuntimeError("Existing state needs review; do not overwrite transaction")
    assert not OVERRIDE.exists() and not OVERRIDE.is_symlink(), "Existing override needs review"
    current, base = preflight()
    assert not current["HostConfig"].get("DnsOptions"), "Existing runtime DNS options"
    assert current["Config"]["Labels"]["com.docker.compose.project.config_files"] == str(BASE)
    source = HERE / "compose.override.yaml"
    # Validate privately before installing anything in production.
    assert_only_dns_changed(base, compose_config(["-f", str(source)]))
    state = {"status": "prepared", "base_config_sha256": digest(base),
             "runtime": runtime_signature(current),
             "runtime_sha256": digest(runtime_signature(current)),
             "image_id": current["Image"], "override_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    write_state(state)
    with OVERRIDE.open("x") as stream:
        os.fchmod(stream.fileno(), 0o644)
        stream.write(source.read_text())
    try:
        recreate(["-f", str(OVERRIDE)])
        assert_runtime_preserved(current, inspect())
        result = verify(state)
    except Exception as error:
        try:
            rollback(state)
        except Exception as rollback_error:
            raise RuntimeError("Activation failed; automatic rollback requires host review: "
                               + type(rollback_error).__name__) from None
        raise RuntimeError("Activation failed; original configuration restored: "
                           + type(error).__name__) from None
    state["status"] = "applied"
    write_state(state)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["apply", "verify", "rollback"])
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run in the N2830 root shell")
    try:
        if args.action == "apply":
            result = apply()
        else:
            state = json.loads(STATE.read_text())
            result = verify(state) if args.action == "verify" else rollback(state)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as error:
        # Only our own assertions/errors have safe messages, not dependency exceptions.
        message = str(error) if isinstance(error, (AssertionError, RuntimeError)) else "Details suppressed"
        print(json.dumps({"ok": False, "error_type": type(error).__name__, "reason": message}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
