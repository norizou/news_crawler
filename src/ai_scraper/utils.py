"""General utility functions for network and SSL."""

import os
import ssl
from pathlib import Path


def get_ssl_verify() -> ssl.SSLContext | bool:
    """
    Get appropriate SSLContext with CA bundle for SSL verification.
    Automatically detects system CA certificates (e.g. WSL / Linux ca-certificates.crt)
    or custom SSL_CERT_FILE / REQUESTS_CA_BUNDLE environment variables.
    """
    env_cert = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if env_cert and Path(env_cert).exists():
        return ssl.create_default_context(cafile=env_cert)

    # Standard Linux system certificate bundle location
    system_ca = Path("/etc/ssl/certs/ca-certificates.crt")
    if system_ca.exists():
        return ssl.create_default_context(cafile=str(system_ca))

    return True
