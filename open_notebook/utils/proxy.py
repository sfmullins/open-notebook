"""HTTP proxy bypass helpers for local Open Notebook services."""

import os

# Local service endpoints should never be sent through a configured external
# HTTP proxy. PostgreSQL itself is not HTTP-based and therefore needs no proxy
# exception derived from DATABASE_URL.
INTERNAL_NO_PROXY_HOSTS = (
    "host.docker.internal",
    "localhost",
    "127.0.0.1",
)
_NO_PROXY_ENV_VARS = ("no_proxy", "NO_PROXY")


def _split_hosts(value: str) -> list[str]:
    return [host.strip() for host in value.split(",") if host.strip()]


def _internal_hosts() -> list[str]:
    return list(INTERNAL_NO_PROXY_HOSTS)


def ensure_internal_no_proxy() -> None:
    """Merge local-service hosts into no_proxy/NO_PROXY without clobbering users."""
    existing: list[str] = []
    seen: set[str] = set()
    for var in _NO_PROXY_ENV_VARS:
        for host in _split_hosts(os.environ.get(var, "")):
            if host == "*":
                return
            key = host.lower()
            if key not in seen:
                seen.add(key)
                existing.append(host)

    merged = list(existing)
    for host in _internal_hosts():
        if host.lower() not in seen:
            seen.add(host.lower())
            merged.append(host)

    combined = ",".join(merged)
    for var in _NO_PROXY_ENV_VARS:
        os.environ[var] = combined
