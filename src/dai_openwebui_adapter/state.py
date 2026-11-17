"""Local process lock and private, crash-recoverable JSON journals."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile

from .errors import AdapterError, Conflict


def read_json(path):
    path = Path(path)
    if path.is_symlink():
        raise AdapterError("Refusing a symlinked JSON document")
    try:
        with path.open() as stream:
            return json.load(stream)
    except (OSError, ValueError):
        raise AdapterError("Cannot read JSON document") from None


def write_json(path, value, *, replace=False):
    path = Path(path)
    if path.is_symlink() or (path.exists() and not replace):
        raise Conflict("Output already exists or is a symlink")
    data = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()
    fd, name = tempfile.mkstemp(prefix=".dai-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(name, path)
        else:
            os.link(name, path)  # Exclusive publication even if another process creates the path.
            os.unlink(name)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except FileExistsError:
        raise Conflict("Output was created concurrently") from None
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def locked(state_dir: Path):
    if state_dir.is_symlink():
        raise AdapterError("State directory must not be a symlink")
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    fd = os.open(state_dir / "publisher.lock", flags, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Conflict("Another publisher operation holds the local lock") from None
        yield
    finally:
        os.close(fd)
