from __future__ import annotations

import pytest

from nzbget_sab_bridge.urls import join_base_path, normalize_base_url


def test_normalize_preserves_subpath_and_strips_trailing_slash() -> None:
    assert (
        normalize_base_url("http://host:8080/reverse/sab/", strip_api_suffix=False)
        == "http://host:8080/reverse/sab"
    )


def test_normalize_strips_api_suffix_only() -> None:
    assert (
        normalize_base_url("http://host:8080/reverse/sab/api/", strip_api_suffix=True)
        == "http://host:8080/reverse/sab"
    )
    assert (
        normalize_base_url("http://host:8080/apiary", strip_api_suffix=True)
        == "http://host:8080/apiary"
    )


@pytest.mark.parametrize(
    "url",
    [
        "",
        "ftp://host/path",
        "http://user:pass@host/path",
        "http://host/path?query=1",
        "http://host/path#fragment",
        "http://host:notaport",
        "http://host/a/../b",
        "http://host/\nnext",
    ],
)
def test_invalid_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        normalize_base_url(url, strip_api_suffix=False)


def test_join_base_path_rejects_query_and_relative_path() -> None:
    assert join_base_path("http://host/base", "/jsonrpc") == "http://host/base/jsonrpc"
    with pytest.raises(ValueError):
        join_base_path("http://host/base", "jsonrpc")
    with pytest.raises(ValueError):
        join_base_path("http://host/base", "/jsonrpc?x=1")
