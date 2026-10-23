"""Docker HEALTHCHECK entry point; no secrets or application state are read."""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    host = os.environ.get("LISTEN_HOST", "127.0.0.1")
    if host in {"0.0.0.0", "::", ""}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = os.environ.get("LISTEN_PORT", "6789")
    url = f"http://{host}:{port}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return 0 if response.status == 200 else 1
    except (urllib.error.URLError, OSError, ValueError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
