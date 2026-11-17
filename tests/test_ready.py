import pytest

from dai_openwebui_adapter.errors import AdapterError
from dai_openwebui_adapter.ready import wait_ready


def test_ready_retries_startup_then_returns_without_writes(config, session, server, monkeypatch):
    clock = [0]
    attempts = []

    class Pending:
        def __init__(self, short_config):
            assert short_config.timeout_seconds <= 5

        def __enter__(self):
            attempts.append(clock[0])
            if len(attempts) < 3:
                raise AdapterError("not ready")
            return session

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("dai_openwebui_adapter.ready.Session", Pending)
    wait_ready(config, clock=lambda: clock[0], sleep=lambda t: clock.__setitem__(0, clock[0] + t))
    assert attempts == [0, 3, 6]
    assert not server.writes
    assert all(method == "GET" for method, _ in server.calls)


def test_ready_has_a_deadline(config, monkeypatch):
    clock = [0]

    def offline(_):
        raise AdapterError("private network diagnostics")

    monkeypatch.setattr("dai_openwebui_adapter.ready.Session", offline)
    with pytest.raises(AdapterError, match="deadline exceeded"):
        wait_ready(
            config,
            timeout=5,
            clock=lambda: clock[0],
            sleep=lambda t: clock.__setitem__(0, clock[0] + t),
        )
    assert clock[0] == 5
