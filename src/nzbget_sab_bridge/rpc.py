"""NZBGet JSON-RPC transport, envelopes, positional parameters, and errors."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

Protocol = Literal["jsonrpc", "jsonprpc"]

_CALLBACK_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$.]{0,127}$")
_METHOD_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")

# Methods documented by NZBGet v13+ that are intentionally not implemented in
# Unsupported bridge-mode methods. They receive an explicit protocol error, never fake data.
KNOWN_UNIMPLEMENTED_METHODS = frozenset(
    {
        "checkupdates",
        "clearlog",
        "deleteextension",
        "downloadextension",
        "dump",
        "editserver",
        "fetchfeed",
        "listfiles",
        "loadconfig",
        "loadextensions",
        "loadlog",
        "log",
        "logscript",
        "logupdate",
        "pausepost",
        "pausescan",
        "postqueue",
        "previewfeed",
        "readurl",
        "reload",
        "resumepost",
        "resumescan",
        "saveconfig",
        "scan",
        "scheduleresume",
        "servervolumes",
        "shutdown",
        "startscript",
        "startupdate",
        "sysinfo",
        "systemhealth",
        "system.multicall",
        "testdiskspeed",
        "testextension",
        "testnetworkspeed",
        "testserver",
        "testserverspeed",
        "updateextension",
        "urlqueue",
        "viewfeed",
        "writelog",
        "resetservervolume",
    }
)

IMPLEMENTED_READ_METHODS = frozenset(
    {
        "version",
        "config",
        "configtemplates",
        "status",
        "listgroups",
        "history",
    }
)

IMPLEMENTED_MUTATION_METHODS = frozenset(
    {
        "append",
        "appendurl",
        "editqueue",
        "pause",
        "pausedownload",
        "pausedownload2",
        "rate",
        "resume",
        "resumedownload",
        "resumedownload2",
    }
)

KNOWN_RPC_METHODS = (
    KNOWN_UNIMPLEMENTED_METHODS | IMPLEMENTED_READ_METHODS | IMPLEMENTED_MUTATION_METHODS
)


class RpcError(Exception):
    code = 4
    message = "Internal error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message


class RpcParseError(RpcError):
    code = 4
    message = "Parse error"


class InvalidProcedure(RpcError):
    code = 1
    message = "Invalid procedure"


class InvalidParams(RpcError):
    code = 2
    message = "Invalid parameter"


class InvalidAction(RpcError):
    code = 3
    message = "Invalid action"


class NotImplementedMethod(RpcError):
    code = 1

    def __init__(self, method: str) -> None:
        safe_method = method if _METHOD_RE.fullmatch(method) else "<invalid>"
        super().__init__(f"Method '{safe_method}' is not implemented in bridge mode")


@dataclass(frozen=True, slots=True)
class RpcRequest:
    protocol: Protocol
    method: str
    params: list[Any]
    callback: str | None
    has_id: bool
    request_id: Any


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _load_json(body: bytes) -> Any:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RpcParseError() from exc
    try:
        return json.loads(text, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise RpcParseError() from exc


def peek_jsonp_callback(body: bytes) -> str | None:
    """Best-effort callback extraction for error responses."""

    try:
        payload = _load_json(body)
    except RpcError:
        return None
    if not isinstance(payload, dict):
        return None
    params = payload.get("params")
    if not isinstance(params, list) or not params:
        return None
    callback = params[0]
    if isinstance(callback, str) and _CALLBACK_RE.fullmatch(callback):
        return callback
    return None


def parse_rpc_request(body: bytes, protocol: Protocol) -> RpcRequest:
    payload = _load_json(body)
    if not isinstance(payload, dict):
        raise RpcParseError()

    method = payload.get("method")
    if not isinstance(method, str) or not _METHOD_RE.fullmatch(method):
        raise InvalidProcedure()

    raw_params = payload.get("params", [])
    if not isinstance(raw_params, list):
        raise InvalidParams("Invalid parameter (params must be an array)")

    params = list(raw_params)
    callback: str | None = None
    if protocol == "jsonprpc":
        if not params or not isinstance(params[0], str):
            raise InvalidParams("Invalid parameter (callback)")
        callback = params[0]
        if not _CALLBACK_RE.fullmatch(callback):
            raise InvalidParams("Invalid parameter (callback)")
        params = params[1:]

    has_id = "id" in payload
    request_id = payload.get("id")
    if has_id and not isinstance(request_id, (str, int, float, type(None))):
        raise InvalidParams("Invalid parameter (id)")

    return RpcRequest(
        protocol=protocol,
        method=method,
        params=params,
        callback=callback,
        has_id=has_id,
        request_id=request_id,
    )


def _payload(
    *,
    result: Any = None,
    error: RpcError | None = None,
    has_id: bool,
    request_id: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"version": "1.1"}
    if has_id:
        payload["id"] = request_id
    if error is not None:
        payload["error"] = {
            "name": "JSONRPCError",
            "code": error.code,
            "message": error.message,
        }
    else:
        payload["result"] = result
    return payload


def _render(payload: dict[str, Any], callback: str | None) -> bytes:
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if callback is not None:
        # JSON-P is loaded as JavaScript. Keep payload text from terminating a
        # surrounding script element or introducing JavaScript line separators.
        for character, escaped in (
            ("<", "\\u003c"),
            (">", "\\u003e"),
            ("&", "\\u0026"),
            ("\u2028", "\\u2028"),
            ("\u2029", "\\u2029"),
        ):
            serialized = serialized.replace(character, escaped)
        serialized = f"{callback}({serialized})"
    return serialized.encode("utf-8")


def success_response(request: RpcRequest, result: Any) -> bytes:
    return _render(
        _payload(
            result=result,
            has_id=request.has_id,
            request_id=request.request_id,
        ),
        request.callback,
    )


def error_response(
    error: RpcError,
    *,
    has_id: bool = False,
    request_id: Any = None,
    callback: str | None = None,
) -> bytes:
    return _render(
        _payload(error=error, has_id=has_id, request_id=request_id),
        callback,
    )


def request_error_response(error: RpcError, request: RpcRequest) -> bytes:
    return error_response(
        error,
        has_id=request.has_id,
        request_id=request.request_id,
        callback=request.callback,
    )
