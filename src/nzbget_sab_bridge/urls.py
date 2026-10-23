"""Small URL helpers shared by configuration, the SAB client, and capture."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


def normalize_base_url(raw: str, *, strip_api_suffix: bool) -> str:
    """Validate and normalize a configured base URL.

    The result has no trailing slash, query, fragment, or embedded credentials.
    Reverse-proxy subpaths are preserved. When ``strip_api_suffix`` is true,
    a path ending in ``/api`` is reduced to its parent, because callers append
    their own API path.
    """

    candidate = raw.strip()
    if not candidate:
        raise ValueError("URL must not be empty")
    if any(char in candidate for char in ("\r", "\n", "\x00")):
        raise ValueError("URL must not contain control characters")

    try:
        parts = urlsplit(candidate)
        port = parts.port
    except ValueError as exc:
        raise ValueError("URL has an invalid host or port") from exc

    if parts.scheme.lower() not in {"http", "https"}:
        raise ValueError("URL scheme must be http or https")
    if not parts.hostname:
        raise ValueError("URL must include a host")
    if parts.username is not None or parts.password is not None:
        raise ValueError("URL must not contain credentials")
    if parts.query or parts.fragment:
        raise ValueError("URL must not contain a query string or fragment")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("URL port must be between 1 and 65535")

    path = parts.path or ""
    segments = [segment for segment in path.split("/") if segment]
    if any(segment in {".", ".."} for segment in segments):
        raise ValueError("URL path must not contain dot segments")

    if strip_api_suffix and segments and segments[-1].lower() == "api":
        segments = segments[:-1]

    normalized_path = "/" + "/".join(segments) if segments else ""
    normalized = urlunsplit((parts.scheme.lower(), parts.netloc, normalized_path, "", ""))
    return normalized.rstrip("/") or f"{parts.scheme.lower()}://{parts.netloc}"


def join_base_path(base_url: str, path: str) -> str:
    """Append a fixed, already-validated path to a normalized base URL."""

    if not path.startswith("/"):
        raise ValueError("path must start with '/'")
    if "?" in path or "#" in path:
        raise ValueError("path must not include a query string or fragment")
    return f"{base_url.rstrip('/')}{path}"
