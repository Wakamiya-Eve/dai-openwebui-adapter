"""Public diagnostics deliberately exclude response bodies and credentials."""


class AdapterError(RuntimeError):
    """An actionable, credential-free adapter failure."""


class Conflict(AdapterError):
    """Reviewed state no longer matches or belongs to another owner."""
