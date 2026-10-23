"""Sanitized SABnzbd client exceptions.

None of these exceptions retain the original exception or request URL, because
both can contain the API key or credential-bearing indexer URLs.
"""

from __future__ import annotations


class SabError(RuntimeError):
    """Base class for safe, user-presentable SABnzbd failures."""


class SabTransportError(SabError):
    def __init__(self) -> None:
        super().__init__("SABnzbd connection failed")


class SabTimeoutError(SabError):
    def __init__(self) -> None:
        super().__init__("SABnzbd request timed out")


class SabHttpError(SabError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"SABnzbd returned HTTP {status_code}")
        self.status_code = status_code


class SabRedirectError(SabError):
    def __init__(self) -> None:
        super().__init__("SABnzbd redirected the request; redirects are disabled")


class SabMalformedResponseError(SabError):
    def __init__(self, detail: str = "invalid JSON or unexpected response shape") -> None:
        super().__init__(f"SABnzbd returned {detail}")


class SabApiError(SabError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


_SAFE_API_MESSAGES = {
    "API Key Incorrect",
    "API Key Required",
    "Configuration locked",
    "item does not exist",
    "No value given",
    "No such configuration",
}


def sanitize_api_error(raw: object) -> str:
    """Convert SAB's error text to a small allowlist, never echoing URLs/keys."""

    if not isinstance(raw, str):
        return "SABnzbd rejected the request"
    if raw in _SAFE_API_MESSAGES:
        return raw
    lowered = raw.lower()
    if "api key" in lowered:
        return "SABnzbd API key incorrect"
    if "authentication" in lowered or "login" in lowered:
        return "SABnzbd authentication failed"
    return "SABnzbd rejected the request"
