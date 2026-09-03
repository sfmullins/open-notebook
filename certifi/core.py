from __future__ import annotations

import os
import ssl
from pathlib import Path


def where() -> str:
    candidates = [
        os.getenv("SSL_CERT_FILE"),
        ssl.get_default_verify_paths().cafile,
        "/etc/ssl/certs/ca-certificates.crt",
        "/etc/pki/tls/certs/ca-bundle.crt",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise RuntimeError("No operating-system CA certificate bundle was found")


def contents() -> str:
    return Path(where()).read_text(encoding="utf-8")
