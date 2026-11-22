"""Root pre-step for OpenWebUI APIs that exclude API-key sessions.

The daily capability publisher deliberately forces a fresh host snapshot.
The 30-second runtime-name reconciler may reuse a recent, credential-bound
snapshot. A root-only file lock serializes both paths.
"""

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import tempfile
import time

from .config import load_config
from .errors import AdapterError
from .snapshot import read_snapshot

READER = r"""
import asyncio,contextlib,hashlib,io,json,sys,time
credential=json.load(sys.stdin)['token']
with contextlib.redirect_stdout(io.StringIO()):
 from open_webui.models.users import Users
 from open_webui.env import ENABLE_TOOLS,ENABLE_TOOL_SERVERS,USE_SLIM,VERSION
 async def collect():
  user=await Users.get_user_by_api_key(credential)
  if user is None or user.role!='admin':raise RuntimeError('Expected publishing administrator')
  features={'slim':USE_SLIM,'enable_tools':ENABLE_TOOLS,'enable_tool_servers':ENABLE_TOOL_SERVERS}
  return {'schema':'dai.openwebui-host-snapshot.v1','issued_at':time.time(),
    'token_sha256':hashlib.sha256(credential.encode()).hexdigest(),
    'identity':{'id':user.id,'role':user.role},'platform':{'features':features},
    'version':VERSION,'source_sha256':{name:hashlib.sha256(open('/app/backend/open_webui/'+name,'rb').read()).hexdigest()
      for name in ['main.py','utils/auth.py','models/config.py','models/users.py']}}
 result=asyncio.run(collect())
print(json.dumps(result))
"""

SNAPSHOT_PATH = Path("/run/dai-openwebui-adapter/snapshot.json")
DEFAULT_REUSE_MAX_AGE_SECONDS = 360
# Keep at least 150 seconds before the reader's 600-second hard expiry.
# The status oneshot has TimeoutStartSec=150s.
MAX_REUSE_MAX_AGE_SECONDS = 450


def _check_snapshot_directory(parent: Path) -> None:
    if parent.is_symlink():
        raise SystemExit("Host snapshot directory must not be a symlink")
    try:
        info = parent.stat()
    except OSError:
        raise SystemExit("Host snapshot directory is unavailable") from None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise SystemExit("Host snapshot directory must be root-owned and protected")


@contextmanager
def _snapshot_lock(target: Path):
    """Serialize status and daily snapshot refreshes across their systemd units."""
    parent = target.parent
    _check_snapshot_directory(parent)
    lock_path = parent / "snapshot.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError:
        raise SystemExit("Cannot open protected host snapshot lock") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1:
            raise SystemExit("Host snapshot lock must be a root-owned regular file")
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _read_reusable_snapshot(
    target: Path,
    token: str,
    max_age_seconds: int,
    *,
    clock=time.time,
):
    """Return a validated cached snapshot only while it has ample TTL remaining."""
    if target.is_symlink():
        raise SystemExit("Host snapshot must not be a symlink")
    try:
        data = read_snapshot(target, token, clock=clock)
    except (AdapterError, OSError, ValueError, TypeError):
        return None
    stamp = data.get("issued_at") if isinstance(data, dict) else None
    if type(stamp) not in (int, float):
        return None
    age = clock() - stamp
    if not 0 <= age < max_age_seconds:
        return None
    return data, age


def _collect_snapshot(token: str):
    # The credential is sent only through stdin, never Docker argv or logs.
    deadline = time.monotonic() + 120
    while True:
        try:
            run = subprocess.run(
                ["docker", "exec", "-i", "open-webui-slim", "python", "-c", READER],
                input=json.dumps({"token": token}),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if run.returncode == 0:
                try:
                    return json.loads(run.stdout)
                except ValueError:
                    raise SystemExit("Host metadata reader returned invalid JSON") from None
        except subprocess.TimeoutExpired:
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SystemExit(
                "Host metadata readiness deadline exceeded; private diagnostics suppressed"
            )
        time.sleep(min(3, remaining))


def _write_snapshot(target: Path, data: dict, token: str) -> None:
    parent = target.parent
    _check_snapshot_directory(parent)
    if target.is_symlink():
        raise SystemExit("Host snapshot must not be a symlink")
    owner = pwd.getpwnam("kms0723online")
    fd, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=parent)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o640)
            os.fchown(stream.fileno(), 0, owner.pw_gid)
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        # Validate the new document before replacing the last known-good snapshot.
        read_snapshot(Path(temporary), token)
        os.replace(temporary, target)
        directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare_snapshot(
    config,
    target: Path = SNAPSHOT_PATH,
    *,
    reuse_if_fresh: bool = False,
    reuse_max_age_seconds: int = DEFAULT_REUSE_MAX_AGE_SECONDS,
    clock=time.time,
):
    if not 1 <= reuse_max_age_seconds <= MAX_REUSE_MAX_AGE_SECONDS:
        raise SystemExit(
            f"reuse-max-age-seconds must be between 1 and {MAX_REUSE_MAX_AGE_SECONDS}"
        )
    token = config.openwebui.token()
    with _snapshot_lock(target):
        if target.is_symlink():
            raise SystemExit("Host snapshot must not be a symlink")
        if reuse_if_fresh:
            cached = _read_reusable_snapshot(
                target, token, reuse_max_age_seconds, clock=clock
            )
            if cached is not None:
                data, age = cached
                return {
                    "status": "host_metadata_ready",
                    "cache": "reused",
                    "age_seconds": round(age, 1),
                    "reuse_max_age_seconds": reuse_max_age_seconds,
                    "version": data.get("version"),
                }

        data = _collect_snapshot(token)
        _write_snapshot(target, data, token)
        return {
            "status": "host_metadata_ready",
            "cache": "refreshed",
            "version": data.get("version"),
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--reuse-if-fresh",
        action="store_true",
        help="reuse a recent, valid root-issued snapshot instead of running Docker Exec",
    )
    parser.add_argument(
        "--reuse-max-age-seconds",
        type=int,
        default=DEFAULT_REUSE_MAX_AGE_SECONDS,
        help="refresh cached metadata when older than this age (default: 360; max: 450)",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.reuse_max_age_seconds <= MAX_REUSE_MAX_AGE_SECONDS:
        parser.error(
            f"--reuse-max-age-seconds must be between 1 and {MAX_REUSE_MAX_AGE_SECONDS}"
        )
    if os.geteuid() != 0:
        raise SystemExit("Host metadata pre-step requires root")
    config = load_config(args.config)
    target = SNAPSHOT_PATH
    if config.snapshot_path != str(target):
        raise SystemExit("Unexpected host snapshot path")
    result = prepare_snapshot(
        config,
        target,
        reuse_if_fresh=args.reuse_if_fresh,
        reuse_max_age_seconds=args.reuse_max_age_seconds,
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
