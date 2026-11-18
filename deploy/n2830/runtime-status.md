# Name-only runtime status synchronization

Development templates only: this service/timer has not been installed or enabled.
The existing 24-hour capability timer is unchanged.

L4 Gateway `/v1/models` owns canonical base names and runtime observations.
OpenWebUI saves both base overrides and workspace presets, whose names override
upstream names. `sync-status` updates **only `name`** on adapter-owned records:
`Muse Glimmer 30B BF16 (Online)` and
`Muse Glimmer 30B BF16 · Chat (Online)`, or Offline/Unknown as appropriate.
Model IDs, base mappings, parameters, access grants, active flags, tools and
capabilities are retained. Unknown includes stale evidence and unconfirmed
loading/unhealthy states; it is never treated as a confirmed stopped model.

The command defaults to review mode:

```bash
python -m dai_openwebui_adapter --config /path/to/adapter.yaml sync-status
```

An explicit `--apply` enables writes. The future installed service obtains the
existing private credential-bound host snapshot using the standard root
pre-step, then performs HTTP reads/writes as the existing service user. It does
not create credentials or call inference/Worker lifecycle endpoints. Connection
origin, identity and ownership are revalidated. Only existing managed records
are updated; unknown/unmanaged records are never renamed or created.

Name changes use the same local process lock and compatible rollback journal
format as full publication. Every before/after image is recorded before writes,
and results are read back. Incomplete operations block further publication until
the existing rollback command resolves them. No-difference runs do not write or
refresh the OpenWebUI model list.

`dai-openwebui-status.timer` proposes a 30-second interval after the previous
oneshot finishes. M3 polls local runtime health at 15-second intervals with a
35-second observation TTL. The names reflect the most recent successful
synchronization, not an instantaneous subscription. If L4 or OpenWebUI itself is
unreachable, names cannot be rewritten; monitor the oneshot result and refresh
the browser's model list after recovery. M3 transport failure while L4 is healthy
is displayed as Unknown. No online label is invented from capability history.

Full capability publication uses the same formatter and excludes runtime
observation timestamps from capability digests, so the two paths agree and
polling alone does not cause capability rewrites. Neither path removes an offline
model or prevents a conversation from using the standard cold-start chain.

A future deployment needs Worker 0.4.0, the matching M3 runtime collector,
L4 Gateway runtime names and adapter 0.4.0. Older Workers produce Unknown.
Keep the two new units disabled until that rollout is explicitly authorized.
Use the existing `rollback --journal` command for failed name-only updates.
