"""Bounded HTTP readiness check; systemd ordering alone does not prove readiness."""

import argparse
from dataclasses import replace
import json
import sys
import time

from .clients import Session
from .config import load_config
from .errors import AdapterError


def wait_ready(config, *, timeout=120, interval=3, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + timeout
    while True:
        try:
            with Session(
                replace(config, timeout_seconds=min(config.timeout_seconds, 5))
            ) as session:
                # No model inference and no model/config writes.
                session.sources.gateway.request("GET", "models")
                session.web.identity()
                session.web.platform()
            return
        except AdapterError:
            remaining = deadline - clock()
            if remaining <= 0:
                raise AdapterError("Gateway/OpenWebUI HTTP readiness deadline exceeded") from None
            sleep(min(interval, remaining))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    try:
        wait_ready(load_config(args.config))
        print('{"status":"http_ready"}')
        return 0
    except AdapterError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError):
        print('{"error":"Invalid readiness configuration"}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
