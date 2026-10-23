from __future__ import annotations

import base64
from typing import Any, cast

from pydantic import SecretStr

from nzbget_sab_bridge.auth import (
    BasicCredentials,
    credentials_match,
    decode_basic_authorization,
    is_authenticated,
)
from nzbget_sab_bridge.config import Settings
from tests.conftest import (
    FAKE_BRIDGE_PASSWORD,
    FAKE_BRIDGE_USER,
    basic_auth_header,
)


def test_decode_basic_with_colon_in_password() -> None:
    token = base64.b64encode(b"user:pa:ss").decode()
    credentials = decode_basic_authorization(f"Basic {token}")
    assert credentials == BasicCredentials("user", "pa:ss")


def test_decode_rejects_malformed_headers() -> None:
    assert decode_basic_authorization(None) is None
    assert decode_basic_authorization("Bearer abc") is None
    assert decode_basic_authorization("Basic !!!") is None
    assert decode_basic_authorization("Basic " + base64.b64encode(b"nocolon").decode()) is None


def test_credentials_match_both_fields(bridge_settings: Settings) -> None:
    assert credentials_match(
        BasicCredentials(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD), bridge_settings
    )
    assert not credentials_match(BasicCredentials(FAKE_BRIDGE_USER, "wrong"), bridge_settings)
    assert not credentials_match(BasicCredentials("wrong", FAKE_BRIDGE_PASSWORD), bridge_settings)


def test_is_authenticated() -> None:
    settings = Settings(
        mode="bridge",
        nzbget_username="u",
        nzbget_password=SecretStr("p"),
        sabnzbd_url="http://sab:8080",
        sabnzbd_api_key=SecretStr("k"),
    )

    class Request:
        def __init__(self, header: str | None) -> None:
            self.headers = {"authorization": header} if header else {}

    authenticated = Request("Basic " + base64.b64encode(b"u:p").decode())
    missing = Request(None)
    wrong = Request("Basic " + base64.b64encode(b"u:x").decode())
    assert is_authenticated(cast(Any, authenticated), settings)
    assert not is_authenticated(cast(Any, missing), settings)
    assert not is_authenticated(cast(Any, wrong), settings)


def test_insecure_mode_skips_auth() -> None:
    settings = Settings(
        mode="bridge",
        allow_insecure_no_auth=True,
        sabnzbd_url="http://sab:8080",
        sabnzbd_api_key=SecretStr("k"),
    )
    assert not settings.auth_required

    class Request:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

    assert is_authenticated(cast(Any, Request()), settings)


def test_bridge_auth_fixture_header() -> None:
    header = basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD)
    assert header["Authorization"].startswith("Basic ")
