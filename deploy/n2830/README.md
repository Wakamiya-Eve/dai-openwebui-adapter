# N2830 scheduled publication

The service runs as `kms0723online`. The wrapper executes `sync`, which reads the
current catalogue and OpenWebUI configuration, computes a fresh plan, calls
`apply` **only when actions are nonempty**, then verifies the saved/visible model
records. An unchanged run never invokes `apply` or model update/create endpoints.
Read-only API requests and a local last-success record still occur.

`sync` is explicit authorization for unattended application of the computed
policy. It retains the same source revalidation, owner checks, private journals,
per-write conflict checks and explicit rollback as interactive `plan/apply`.
It never silently retries stale plans or overwrites an incomplete publication.

Install immutable Git-derived source under `/opt/dai-openwebui-adapter/releases`,
point `current` to the selected release, and install pinned dependencies in its
dedicated `/opt/dai-openwebui-adapter/venv`. Runtime imports use `current/src`;
there is no dependency on a development worktree or its virtual environment.

Configuration is `/etc/dai-openwebui-adapter/adapter.yaml`; credentials are loaded
by systemd from root-owned mode-600 `credentials.env` in that directory. Set
`AGENT_GATEWAY_API_KEY` and `OPENWEBUI_ADMIN_API_KEY`, never commit real values.
The mode-700 state directory belongs to the service user:
`/srv/n2830-data/app-data/dai-openwebui-adapter`. It retains plans, rollback
journals and `last-success.json`. State can contain private model metadata.

Enable the service for boot and enable the timer. HTTP checks wait up to roughly
120 seconds for Gateway/OpenWebUI authentication/config APIs, with five-second
request timeouts; systemd ordering also follows Docker and the L4 Gateway.
The timer activates every 24 hours, with one-minute scheduling accuracy. Boot
also runs the enabled oneshot. A completed oneshot normally reports `inactive
(dead)` with `Result=success`, while the timer remains `active (waiting)`.
No `RemainAfterExit` is used, so the timer can invoke it again.

Manual run: `systemctl start dai-openwebui-adapter.service`.
Inspect: `systemctl status dai-openwebui-adapter.timer` and
`journalctl -u dai-openwebui-adapter.service`.

To stop publication, disable the timer **and service**; an already running task
must finish or be stopped before rollback. Restore a publication through
`python -m dai_openwebui_adapter --config ... rollback --journal ...` with the
same credentials, source and dedicated venv. New records are disabled, not
deleted; subsequent manual edits make rollback stop for explicit resolution.

The publisher does not start models. Unavailable/expired runtime evidence cannot
enable unverified extended capabilities. Start a target model through the normal
inference lifecycle before validating model-specific tools/vision, then sync
again. Publication verification does not send inference; a separate acceptance
request through OpenWebUI is needed to prove the inference path.

## OpenWebUI 0.13.3 API-key boundary

This version's session identity endpoint requires a human JWT, and `/api/config`
returns only public fields for API keys. The host integration therefore adds a
root `ExecStartPre` that reads the installed container's actual API-key owner and
environment feature constants, and writes a root-owned, mode-640 snapshot under
`/run/dai-openwebui-adapter`. Its metadata is bound to the credential hash and
expires after ten minutes. No credential is printed or passed in Docker argv.
The publisher still runs as the ordinary user. A tmpfiles rule recreates the
protected runtime directory on boot.

For every observation/revalidation it reads the admin-only config-export API
with its API key, immediately selects the feature flags and audio declarations,
and discards all unrelated data. Exported secrets never enter plans, markers,
state or logs. The root snapshot supplies identity and environment-only flags;
it never fabricates a human session or relaxes OpenWebUI authentication.
