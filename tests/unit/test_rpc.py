from __future__ import annotations

import json

import pytest

from nzbget_sab_bridge.rpc import (
    InvalidParams,
    InvalidProcedure,
    RpcParseError,
    error_response,
    parse_rpc_request,
    peek_jsonp_callback,
    success_response,
)


def _request(method: str, params: list[object] | None = None, request_id: object = 1) -> bytes:
    payload: dict[str, object] = {"method": method, "params": params or []}
    if request_id is not None:
        payload["id"] = request_id
    return json.dumps(payload).encode()


def test_parse_and_success_envelope() -> None:
    request = parse_rpc_request(_request("version", request_id=42), "jsonrpc")
    assert request.method == "version"
    assert request.params == []
    payload = json.loads(success_response(request, "21.1"))
    assert payload == {"version": "1.1", "id": 42, "result": "21.1"}


def test_absent_id_is_omitted() -> None:
    request = parse_rpc_request(_request("version", request_id=None), "jsonrpc")
    payload = json.loads(success_response(request, "21.1"))
    assert "id" not in payload


def test_jsonp_callback_is_first_param_and_wrapped() -> None:
    request = parse_rpc_request(_request("version", ["cb123"]), "jsonprpc")
    assert request.callback == "cb123"
    assert request.params == []
    rendered = success_response(request, "21.1").decode()
    assert rendered.startswith("cb123(")
    assert rendered.endswith(")")


def test_jsonp_callback_is_extracted_for_error_response() -> None:
    body = _request("version", ["safeCallback"])
    assert peek_jsonp_callback(body) == "safeCallback"
    request = parse_rpc_request(body, "jsonprpc")
    error = error_response(
        InvalidProcedure(),
        has_id=request.has_id,
        request_id=request.request_id,
        callback=request.callback,
    )
    assert error.decode().startswith("safeCallback(")


def test_malformed_json() -> None:
    with pytest.raises(RpcParseError):
        parse_rpc_request(b"{", "jsonrpc")


def test_nan_is_rejected() -> None:
    with pytest.raises(RpcParseError):
        parse_rpc_request(b'{"method":"version","params":[],"id":NaN}', "jsonrpc")


def test_named_params_are_not_supported() -> None:
    with pytest.raises(InvalidParams):
        parse_rpc_request(b'{"method":"version","params":{"x":1},"id":1}', "jsonrpc")


def test_invalid_method_name() -> None:
    with pytest.raises(InvalidProcedure):
        parse_rpc_request(b'{"method":"bad method","params":[],"id":1}', "jsonrpc")


def test_jsonp_requires_callback() -> None:
    with pytest.raises(InvalidParams):
        parse_rpc_request(_request("version"), "jsonprpc")


def test_jsonp_rejects_unsafe_callback() -> None:
    with pytest.raises(InvalidParams):
        parse_rpc_request(_request("version", ["</script>"]), "jsonprpc")


def test_error_envelope_matches_nzbget_shape() -> None:
    payload = json.loads(
        error_response(InvalidParams("Invalid parameter (x)"), has_id=True, request_id=7)
    )
    assert payload == {
        "version": "1.1",
        "id": 7,
        "error": {
            "name": "JSONRPCError",
            "code": 2,
            "message": "Invalid parameter (x)",
        },
    }
