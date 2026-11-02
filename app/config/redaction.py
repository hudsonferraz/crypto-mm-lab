"""Helpers that keep provider keys embedded in RPC URLs out of logs and responses."""

from urllib.parse import urlsplit

PUBLIC_RPC_HOSTS = frozenset({"ethereum.publicnode.com", "ethereum-rpc.publicnode.com"})


def redact_url(url: str | None) -> str | None:
    """Return scheme://host/*** — never the path, query, or credentials."""
    if not url:
        return None
    parts = urlsplit(url)
    if not parts.scheme or not parts.hostname:
        return "***"
    host = parts.hostname
    if parts.port:
        host = f"{host}:{parts.port}"
    has_secret_part = bool(parts.path.strip("/") or parts.query or parts.username)
    return f"{parts.scheme}://{host}/***" if has_secret_part else f"{parts.scheme}://{host}"


def rpc_provider_label(url: str | None) -> str:
    """Classify an RPC URL as the keyless public default or a custom endpoint."""
    if not url:
        return "none"
    host = urlsplit(url).hostname or ""
    return "public" if host in PUBLIC_RPC_HOSTS else "custom"
