"""Root pre-step for installed OpenWebUI APIs that exclude API-key sessions.

Only this host integration reads container metadata. The publisher remains an
ordinary-user HTTP client; it never forges human sessions or changes API policy.
"""

import argparse
import json
import os
from pathlib import Path
import pwd
import subprocess
import tempfile
import time

from .config import load_config

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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        raise SystemExit("Host metadata pre-step requires root")
    config = load_config(args.config)
    target = Path("/run/dai-openwebui-adapter/snapshot.json")
    if config.snapshot_path != str(target):
        raise SystemExit("Unexpected host snapshot path")
    # The credential is sent only through stdin, never Docker argv or logs.
    deadline = time.monotonic() + 120
    while True:
        try:
            run = subprocess.run(
                ["docker", "exec", "-i", "open-webui-slim", "python", "-c", READER],
                input=json.dumps({"token": config.openwebui.token()}),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if run.returncode == 0:
                break
        except subprocess.TimeoutExpired:
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SystemExit(
                "Host metadata readiness deadline exceeded; private diagnostics suppressed"
            )
        time.sleep(min(3, remaining))
    data = json.loads(run.stdout)
    owner = pwd.getpwnam("kms0723online")
    parent = target.parent
    if parent.is_symlink() or parent.stat().st_uid != 0 or parent.stat().st_mode & 0o022:
        raise SystemExit("Host snapshot directory must be protected and root-owned")
    fd, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=parent)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o640)
            os.fchown(stream.fileno(), 0, owner.pw_gid)
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(json.dumps({"status": "host_metadata_ready", "version": data["version"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
