from __future__ import annotations

import pytest

from nzbget_sab_bridge.config import Settings
from nzbget_sab_bridge.handlers import dispatch
from nzbget_sab_bridge.rpc import (
    InvalidParams,
    InvalidProcedure,
    NotImplementedMethod,
    RpcError,
    RpcRequest,
)


def _request(method: str, params: list[object] | None = None) -> RpcRequest:
    return RpcRequest(
        protocol="jsonrpc",
        method=method,
        params=params or [],
        callback=None,
        has_id=False,
        request_id=None,
    )


async def test_version_and_parameter_validation(bridge_settings: Settings) -> None:
    assert (
        await dispatch(_request("version"), settings=bridge_settings, idmap=None, sab=None)
        == "21.1"
    )
    with pytest.raises(InvalidParams, match="count"):
        await dispatch(_request("version", [1]), settings=bridge_settings, idmap=None, sab=None)


async def test_unimplemented_and_unknown_methods(bridge_settings: Settings) -> None:
    with pytest.raises(NotImplementedMethod, match="not implemented"):
        await dispatch(
            _request("listfiles", [0, 0, 1]),
            settings=bridge_settings,
            idmap=None,
            sab=None,
        )
    with pytest.raises(NotImplementedMethod, match="postqueue"):
        await dispatch(_request("postqueue", []), settings=bridge_settings, idmap=None, sab=None)
    with pytest.raises(InvalidProcedure):
        await dispatch(_request("unknown"), settings=bridge_settings, idmap=None, sab=None)


async def test_sab_unavailable_is_explicit(bridge_settings: Settings) -> None:
    with pytest.raises(RpcError, match="SABnzbd unavailable"):
        await dispatch(_request("config"), settings=bridge_settings, idmap=None, sab=None)
