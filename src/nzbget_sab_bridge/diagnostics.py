"""Safe capture-route diagnostics.

Only approved RPC endpoint names and known NZBGet method names may appear in
diagnostic path output. Everything else is redacted. Query strings, headers,
bodies, and credentials are never accepted by this helper.
"""

from __future__ import annotations

from .rpc import KNOWN_RPC_METHODS

_ENDPOINTS = {"jsonrpc", "jsonprpc", "xmlrpc"}
_KNOWN_METHODS = KNOWN_RPC_METHODS
_REDACTED = "<redacted>"


def sanitize_capture_path(path: str) -> str:
    """Return a bounded, redacted representation of an HTTP path."""

    if not path or path == "/":
        return "/"

    trailing_slash = path.endswith("/")
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return "/"

    endpoint_index = next(
        (index for index, segment in enumerate(segments) if segment.lower() in _ENDPOINTS),
        None,
    )
    if endpoint_index is None:
        return f"/{_REDACTED}" + ("/" if trailing_slash else "")

    prefix = segments[:endpoint_index]
    endpoint = segments[endpoint_index].lower()
    remainder = segments[endpoint_index + 1 :]

    safe_prefix = "".join(f"/{_REDACTED}" for _ in prefix)
    rendered = f"{safe_prefix}/{endpoint}"

    if remainder:
        method = remainder[0].lower()
        rendered += "/" + (method if method in _KNOWN_METHODS else _REDACTED)
        for _extra in remainder[1:]:
            rendered += f"/{_REDACTED}"

    if trailing_slash:
        rendered += "/"
    return rendered
