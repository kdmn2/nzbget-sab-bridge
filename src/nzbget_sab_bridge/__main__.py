"""Container entry point: load configuration, then run one uvicorn worker."""

from __future__ import annotations

import sys

import uvicorn

from .config import ConfigError, Settings
from .logging import configure_logging
from .main import create_app


def main() -> int:
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    configure_logging(settings.log_level, settings.log_format)
    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.listen_host,
        port=settings.listen_port,
        log_config=None,
        access_log=False,
        workers=1,
        proxy_headers=False,
        server_header=False,
        timeout_graceful_shutdown=10,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
