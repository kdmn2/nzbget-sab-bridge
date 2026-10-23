"""HTTP Basic authentication with constant-time credential comparison."""

from __future__ import annotations

import base64
import binascii
import secrets
from dataclasses import dataclass

from fastapi import Request, Response

from .config import Settings

_AUTH_SCHEME = "Basic"


@dataclass(frozen=True, slots=True)
class BasicCredentials:
    username: str
    password: str


def decode_basic_authorization(header: str | None) -> BasicCredentials | None:
    """Decode a Basic header, returning ``None`` for any malformed input."""

    if not header:
        return None
    scheme, separator, encoded = header.partition(" ")
    if not separator or scheme.lower() != _AUTH_SCHEME.lower() or not encoded:
        return None

    try:
        raw = base64.b64decode(encoded.strip(), validate=True)
    except (binascii.Error, ValueError):
        return None
    if b":" not in raw:
        return None
    username_bytes, password_bytes = raw.split(b":", 1)
    try:
        username = username_bytes.decode("utf-8")
        password = password_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return BasicCredentials(username=username, password=password)


def credentials_match(provided: BasicCredentials, settings: Settings) -> bool:
    """Compare both fields without short-circuiting on username mismatch."""

    expected_username = settings.client_username.encode("utf-8")
    expected_password = settings.client_password.encode("utf-8")
    username_ok = secrets.compare_digest(provided.username.encode("utf-8"), expected_username)
    password_ok = secrets.compare_digest(provided.password.encode("utf-8"), expected_password)
    return bool(username_ok & password_ok)


def is_authenticated(request: Request, settings: Settings) -> bool:
    if not settings.auth_required:
        return True
    provided = decode_basic_authorization(request.headers.get("authorization"))
    if provided is None:
        return False
    return credentials_match(provided, settings)


def auth_challenge(settings: Settings) -> Response:
    """Return the same kind of empty 401 NZBGet sends for failed RPC auth."""

    headers = {"Cache-Control": "no-store"}
    if settings.auth_required:
        headers["WWW-Authenticate"] = 'Basic realm="NZBGet"'
    return Response(status_code=401, headers=headers)
