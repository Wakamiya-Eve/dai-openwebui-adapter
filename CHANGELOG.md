## 0.3.0 (development, not deployed)

- Consume complete, paginated L4 capability catalogues with no M3 credentials.
- Derive OpenWebUI features, callable tools, limits and parameters per model.
- Preserve unknown source data and invalidate plans on semantic contract changes.
- Keep polling timestamps from invalidating otherwise identical reviewed plans.
- Retain explicit plan/apply/verify/rollback and owner/access checks.

# Changelog

## 0.2.0

- Replace the fixed plain-chat profile with per-model maximal capability negotiation.
- Map vision, native/parallel tools, usage, streaming, reasoning and application capabilities to audited OpenWebUI fields.
- Discover callable local/OpenAPI/MCP tools; include instance/tool drift in reviewed-plan checks.
- Separate application voice from native model audio and expose missing lower-layer/service-binding contracts as TODOs.
- Support nonstream chat, managed tool downgrade, v1 policy migration and old-journal rollback.
- Add installed-slim contract fixtures, lower-layer schema proposal and synthetic integration coverage.

## 0.1.0

- Add independent M3/Gateway catalogue readers and OpenWebUI API clients.
- Publish managed base overrides and workspace presets under an explicit plain-chat policy.
- Add reviewable plans, source/owner/connection checks, durable apply journals and guarded rollback.
- Add configuration verification that explicitly does not claim inference acceptance.
- Keep deployment and live application mutation outside initial development.
