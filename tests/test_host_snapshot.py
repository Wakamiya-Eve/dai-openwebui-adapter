from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from dai_openwebui_adapter import host_snapshot
from dai_openwebui_adapter.errors import AdapterError


def config(path):
    return SimpleNamespace(
        snapshot_path=str(path),
        openwebui=SimpleNamespace(token=lambda: "test-token"),
    )


def test_reuses_fresh_snapshot_without_running_docker_or_writing(monkeypatch, tmp_path):
    target = tmp_path / "snapshot.json"
    data = {"version": "0.13.3"}
    monkeypatch.setattr(host_snapshot, "_snapshot_lock", lambda _target: nullcontext())
    monkeypatch.setattr(
        host_snapshot,
        "_read_reusable_snapshot",
        lambda *args, **kwargs: (data, 42.5),
    )
    monkeypatch.setattr(
        host_snapshot,
        "_collect_snapshot",
        lambda *_: pytest.fail("Docker Exec must not run for a fresh snapshot"),
    )
    monkeypatch.setattr(
        host_snapshot,
        "_write_snapshot",
        lambda *_: pytest.fail("A reused snapshot must not be rewritten"),
    )

    result = host_snapshot.prepare_snapshot(config(target), target, reuse_if_fresh=True)
    assert result == {
        "status": "host_metadata_ready",
        "cache": "reused",
        "age_seconds": 42.5,
        "reuse_max_age_seconds": 360,
        "version": "0.13.3",
    }


def test_stale_snapshot_is_refreshed_and_written(monkeypatch, tmp_path):
    target = tmp_path / "snapshot.json"
    data = {"version": "0.13.3", "schema": "dai.openwebui-host-snapshot.v1"}
    writes = []
    monkeypatch.setattr(host_snapshot, "_snapshot_lock", lambda _target: nullcontext())
    monkeypatch.setattr(host_snapshot, "_read_reusable_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(host_snapshot, "_collect_snapshot", lambda token: data)
    monkeypatch.setattr(host_snapshot, "_write_snapshot", lambda *args: writes.append(args))

    result = host_snapshot.prepare_snapshot(config(target), target, reuse_if_fresh=True)
    assert result["cache"] == "refreshed"
    assert result["version"] == "0.13.3"
    assert len(writes) == 1
    assert writes[0][0] == target
    assert writes[0][1] is data
    assert writes[0][2] == "test-token"


def test_daily_mode_forces_refresh_without_consulting_cache(monkeypatch, tmp_path):
    target = tmp_path / "snapshot.json"
    data = {"version": "0.13.3"}
    writes = []
    monkeypatch.setattr(host_snapshot, "_snapshot_lock", lambda _target: nullcontext())
    monkeypatch.setattr(
        host_snapshot,
        "_read_reusable_snapshot",
        lambda *a, **k: pytest.fail("Daily capability sync must force a refresh"),
    )
    monkeypatch.setattr(host_snapshot, "_collect_snapshot", lambda token: data)
    monkeypatch.setattr(host_snapshot, "_write_snapshot", lambda *args: writes.append(args))

    result = host_snapshot.prepare_snapshot(config(target), target, reuse_if_fresh=False)
    assert result["cache"] == "refreshed"
    assert len(writes) == 1


@pytest.mark.parametrize(
    "issued_at,expected_reuse",
    [(641, True), (640, False), (1001, False)],
)
def test_reuse_requires_nonnegative_age_strictly_below_threshold(
    monkeypatch, tmp_path, issued_at, expected_reuse
):
    target = tmp_path / "snapshot.json"
    data = {
        "schema": "dai.openwebui-host-snapshot.v1",
        "issued_at": issued_at,
        "version": "0.13.3",
    }
    monkeypatch.setattr(host_snapshot, "read_snapshot", lambda *a, **k: data)
    result = host_snapshot._read_reusable_snapshot(
        target, "test-token", 360, clock=lambda: 1000
    )
    assert (result is not None) is expected_reuse
    if expected_reuse:
        assert result == (data, 359)


def test_invalid_or_expired_snapshot_is_refreshed(monkeypatch, tmp_path):
    target = tmp_path / "snapshot.json"

    def invalid(*args, **kwargs):
        raise AdapterError("expired")

    monkeypatch.setattr(host_snapshot, "read_snapshot", invalid)
    assert host_snapshot._read_reusable_snapshot(
        target, "test-token", 360, clock=lambda: 1000
    ) is None


def test_symlinked_snapshot_is_rejected(monkeypatch, tmp_path):
    target = tmp_path / "snapshot.json"
    target.symlink_to(tmp_path / "other.json")
    with pytest.raises(SystemExit, match="must not be a symlink"):
        host_snapshot._read_reusable_snapshot(
            target, "test-token", 360, clock=lambda: 1000
        )


def test_reuse_age_is_capped_below_snapshot_expiry(monkeypatch, tmp_path):
    target = tmp_path / "snapshot.json"
    monkeypatch.setattr(host_snapshot, "_snapshot_lock", lambda _target: nullcontext())
    with pytest.raises(SystemExit, match="between 1 and 450"):
        host_snapshot.prepare_snapshot(
            config(target), target, reuse_if_fresh=True, reuse_max_age_seconds=451
        )
