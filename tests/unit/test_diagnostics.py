from __future__ import annotations

import pytest

from nzbget_sab_bridge.diagnostics import sanitize_capture_path


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/", "/"),
        ("/jsonrpc", "/jsonrpc"),
        ("/jsonrpc/", "/jsonrpc/"),
        ("/jsonprpc", "/jsonprpc"),
        ("/xmlrpc", "/xmlrpc"),
        ("/jsonrpc/version", "/jsonrpc/version"),
        ("/jsonrpc/status", "/jsonrpc/status"),
        ("/jsonrpc/status/", "/jsonrpc/status/"),
        ("/jsonrpc/private-token", "/jsonrpc/<redacted>"),
        ("/jsonrpc/status/extra", "/jsonrpc/status/<redacted>"),
        ("/nzbget/jsonrpc", "/<redacted>/jsonrpc"),
        ("/a/b/c", "/<redacted>"),
        ("/healthz", "/<redacted>"),
    ],
)
def test_sanitize_capture_path(path: str, expected: str) -> None:
    assert sanitize_capture_path(path) == expected


def test_sanitize_capture_path_does_not_echo_secret_segment() -> None:
    rendered = sanitize_capture_path("/jsonrpc/private-secret-value")
    assert "private-secret-value" not in rendered
    assert rendered == "/jsonrpc/<redacted>"
