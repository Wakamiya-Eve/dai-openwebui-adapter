# OpenWebUI fetch_url behind the existing transparent proxy

Status: deployment configuration approved on 2026-11-18; activation and actual UI
fetch_url results are recorded separately. DAI capability configuration is already
deployed. No adapter application release needs to be switched for this change.

## Observed failure and successful isolated test

On 2026-11-18, both Muse and Swift completed real OpenWebUI `search_web` calls,
but `fetch_url` rejected valid public URLs before HTTP. Inside OpenWebUI 0.13.3
slim, docs.openwebui.com resolved to 28.0.0.37 and private IPv6 fc00::23.
The installed URL validator rejects a hostname if any resolved address violates
its private-address policy. Direct HTTPS to the same document returned 200.
The user's QWRT transparent proxy is the existing intended egress path; this
address pattern is consistent with synthetic DNS, without proving which device
generated each answer.

A fresh diagnostic process with `RES_OPTIONS=no-aaaa` used the installed
SafeWebBaseLoader successfully: one official document, 12,394 characters.
127.0.0.1, 169.254.169.254 and 10.0.0.1 remained blocked. This is an isolated
loader test; real UI `fetch_url` must still be verified after activation.
Evidence: CodexDesk history Q-20261118-001-fetch-ipv4-20261118T131430495143Z.json.

## Proposed persistent configuration

Install deploy/openwebui/compose.override.yaml beside the existing base file at
/opt/open-webui-slim/compose.yaml. The actual Compose project is open-webui-slim,
service open-webui, container open-webui-slim. Add only `dns_opt: [no-aaaa]`.
Docker writes this resolver option into this container's resolv.conf. The
installed glibc 2.41 supports it (available since 2.36).

This preserves QWRT, DNS servers, host routing, SearXNG, image contents, database
volume, model capabilities, URL filtering and TLS checks. It changes container
startup configuration, not image or application code. It suppresses DNS AAAA
lookups for the container's libc resolver; it does not disable IPv6 globally.
IPv6-only destinations will not work through this resolver setting. The libc
manual describes no-aaaa as a diagnostic option and notes incompatibility with
application EDNS0/DNSSEC validation; keep this host-specific workaround optional.

## Activation and rollback requirements

Before activation, verify the base Compose file, image ID and database bind
mount still match the running container. Refuse to overwrite an existing
unrelated override. Validate merged Compose configuration privately: only the
open-webui service's dns_opt may change. Preserve the existing local image;
do not pull, build, run `down`, remove volumes, or recreate dependencies.
Recreate only open-webui with `up -d --no-deps --pull never --no-build open-webui`.
The web interface will briefly disconnect; model processes and gateways remain
running. The standard default Compose invocation includes compose.override.yaml;
an explicit `-f compose.yaml` invocation must also include the override.

Afterwards, confirm health, identical image/mounts, actual resolver option,
official-page loading and private-address blocking without a process-only env
override. Then verify both real UI tools, search_web and fetch_url.

Rollback moves this specific newly installed override aside and recreates only
open-webui from the original base file with the same no-pull/no-build options.
Preserve the database directory throughout. Before activation, commit the
override/runbook and acceptance evidence to Git and publish the configuration
revision; this does not require deploying a new adapter application release.

The guarded deployment helper (stdlib, root shell on N2830) is:

```bash
python3 deploy/openwebui/manage-dns.py apply
python3 deploy/openwebui/manage-dns.py verify
# Only when rollback is needed:
python3 deploy/openwebui/manage-dns.py rollback
```

Keep all three files in deploy/openwebui together. `apply` validates the merged
configuration before installation, records only configuration hashes in a root
private state file, and automatically attempts rollback if activation checks
fail. It never prints resolved credentials. An already applied transaction is
read-only verified on repeat. A failed/rolled-back transaction requires review
before a new attempt. Configuration drift or an unrelated existing override
causes a refusal rather than replacement.

References:
- https://man7.org/linux/man-pages/man5/resolv.conf.5.html
- https://docs.docker.com/reference/compose-file/services/#dns_opt
