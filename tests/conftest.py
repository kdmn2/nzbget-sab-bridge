from __future__ import annotations

import base64
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from nzbget_sab_bridge.config import Settings

FAKE_BRIDGE_USER = "bridge-user"
FAKE_BRIDGE_PASSWORD = "bridge-password-not-real-7f4c"
FAKE_SAB_API_KEY = "sab-api-key-not-real-9a1d"
FAKE_CAPTURE_USER = "capture-user"
FAKE_CAPTURE_PASSWORD = "capture-password-not-real-2b8e"
FAKE_NZB_BASE64 = base64.b64encode(b"<?xml version='1.0'?><nzb>secret-nzb-body</nzb>").decode()
FAKE_INDEXER_URL = "https://indexer.invalid/get?guid=abc&apikey=indexer-secret-55"
FAKE_COOKIE = "Auth-Token=cookie-secret-98"


@pytest.fixture
def bridge_settings(tmp_path: Path) -> Settings:
    return Settings(
        mode="bridge",
        listen_host="127.0.0.1",
        nzbget_username=FAKE_BRIDGE_USER,
        nzbget_password=SecretStr(FAKE_BRIDGE_PASSWORD),
        sabnzbd_url="http://sab.test:8080/sab",
        sabnzbd_api_key=SecretStr(FAKE_SAB_API_KEY),
        data_dir=tmp_path / "data",
        readyz_check_sab=False,
    )


@pytest.fixture
def capture_settings(tmp_path: Path) -> Settings:
    return Settings(
        mode="capture",
        listen_host="127.0.0.1",
        nzbget_username=FAKE_BRIDGE_USER,
        nzbget_password=SecretStr(FAKE_BRIDGE_PASSWORD),
        capture_nzbget_url="http://nzbget.test:6789/nzbget",
        capture_nzbget_username=FAKE_CAPTURE_USER,
        capture_nzbget_password=SecretStr(FAKE_CAPTURE_PASSWORD),
        data_dir=tmp_path / "unused-data",
        capture_dir=tmp_path / "captures",
        readyz_check_sab=False,
    )


def basic_auth_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def bridge_auth_headers() -> dict[str, str]:
    return basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD)


class RequestLog:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.bodies: list[bytes] = []

    def handler(
        self, response: Callable[[httpx.Request], httpx.Response]
    ) -> Callable[[httpx.Request], httpx.Response]:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            self.bodies.append(request.read())
            return response(request)

        return handle


def json_response(payload: dict[str, Any], status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        headers={"content-type": "application/json"},
    )
