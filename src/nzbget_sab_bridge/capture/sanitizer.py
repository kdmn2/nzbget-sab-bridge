"""Shape-only capture inspection.

Records never contain request/response bodies. JSON scalar values are omitted
except booleans and numbers in request parameters; strings are reduced to type,
length, and an allowlisted enum where useful. XML text is read only for
``methodName`` and ``struct/member/name``, and DTD/entity declarations are
rejected before inspection.
"""

from __future__ import annotations

import json
import re
from typing import Any
from xml.parsers import expat

from ..rpc import KNOWN_RPC_METHODS

_SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
_MAX_DEPTH = 16
_MAX_KEYS = 200
_MAX_ARRAY_SAMPLE = 3
_MAX_PARAMS = 64
_MAX_BATCH_CALLS = 16
_MAX_XML_DEPTH = 32
_MAX_XML_NODES = 5_000
_MAX_XML_TEXT = 128

EDITQUEUE_COMMANDS = frozenset(
    {
        "FileDelete",
        "FileMoveBottom",
        "FileMoveOffset",
        "FileMoveTop",
        "FilePause",
        "FilePauseAllPars",
        "FilePauseExtraPars",
        "FileReorder",
        "FileResume",
        "FileSplit",
        "GroupApplyCategory",
        "GroupDelete",
        "GroupDupeDelete",
        "GroupFinalDelete",
        "GroupMerge",
        "GroupMoveBottom",
        "GroupMoveOffset",
        "GroupMoveTop",
        "GroupPause",
        "GroupPauseAllPars",
        "GroupPauseExtraPars",
        "GroupResume",
        "GroupSetCategory",
        "GroupSetDupeKey",
        "GroupSetDupeMode",
        "GroupSetDupeScore",
        "GroupSetName",
        "GroupSetParameter",
        "GroupSetPriority",
        "GroupSort",
        "HistoryDelete",
        "HistoryFinalDelete",
        "HistoryMarkBad",
        "HistoryMarkGood",
        "HistoryMarkSuccess",
        "HistoryProcess",
        "HistoryRedownload",
        "HistoryReturn",
        "HistorySetCategory",
        "HistorySetDupeBackup",
        "HistorySetDupeKey",
        "HistorySetDupeMode",
        "HistorySetDupeScore",
        "HistorySetName",
        "HistorySetParameter",
        "PostDelete",
        "PostMoveBottom",
        "PostMoveOffset",
        "PostMoveTop",
    }
)

_SAFE_ERROR_CLASSES = {
    "Parse error": "parse_error",
    "Invalid procedure": "invalid_procedure",
    "Invalid parameter": "invalid_parameter",
    "Invalid action": "invalid_action",
    "Access denied": "access_denied",
}


class XmlInspectionError(ValueError):
    """The XML document is unsafe or too large to inspect."""


def _safe_name(value: str) -> str:
    if _SAFE_NAME.fullmatch(value):
        return value
    return "<redacted>"


def _shape(
    value: Any,
    *,
    depth: int = 0,
    include_scalar_values: bool,
    include_string_length: bool,
    max_depth: int = _MAX_DEPTH,
) -> dict[str, Any]:
    if depth >= max_depth:
        return {"type": "truncated", "reason": "depth"}
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        result: dict[str, Any] = {"type": "boolean"}
        if include_scalar_values:
            result["value"] = value
        return result
    if isinstance(value, int):
        result = {"type": "integer"}
        if include_scalar_values:
            result["value"] = value
        return result
    if isinstance(value, float):
        result = {"type": "number"}
        if include_scalar_values:
            result["value"] = value
        return result
    if isinstance(value, str):
        result = {"type": "string"}
        if include_string_length:
            result["length"] = len(value)
        return result
    if isinstance(value, list):
        result = {
            "type": "array",
            "length": len(value),
            "items": [
                _shape(
                    item,
                    depth=depth + 1,
                    include_scalar_values=include_scalar_values,
                    include_string_length=include_string_length,
                    max_depth=max_depth,
                )
                for item in value[:_MAX_ARRAY_SAMPLE]
            ],
        }
        if len(value) > _MAX_ARRAY_SAMPLE:
            result["truncated"] = True
        return result
    if isinstance(value, dict):
        fields: dict[str, Any] = {}
        for index, key in enumerate(value):
            if index >= _MAX_KEYS:
                fields["<truncated>"] = {"type": "truncated", "reason": "keys"}
                break
            safe_key = _safe_name(key) if isinstance(key, str) else "<redacted>"
            fields[safe_key] = _shape(
                value[key],
                depth=depth + 1,
                include_scalar_values=include_scalar_values,
                include_string_length=include_string_length,
                max_depth=max_depth,
            )
        return {"type": "object", "fields": fields}
    return {"type": type(value).__name__}


def _enum_for_param(method: str, index: int, value: str) -> str | None:
    lowered = method.lower()
    if lowered == "editqueue" and index == 0 and value in EDITQUEUE_COMMANDS:
        return value
    if (
        lowered in {"append", "appendurl"}
        and index == 8
        and value
        in {
            "SCORE",
            "ALL",
            "FORCE",
        }
    ):
        return value
    if lowered == "editqueue" and index == 1 and value in {"SCORE", "ALL", "FORCE"}:
        return value
    return None


def _param_shape(method: str, index: int, value: Any) -> dict[str, Any]:
    shape = _shape(
        value,
        include_scalar_values=True,
        include_string_length=True,
        max_depth=8,
    )
    if isinstance(value, str):
        enum = _enum_for_param(method, index, value)
        if enum is not None:
            shape["enum"] = enum
    return shape


def _safe_method_name(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    lowered = value.lower()
    return lowered if lowered in KNOWN_RPC_METHODS else "<redacted>"


def _call_shape(call: Any, method_key: str) -> dict[str, Any]:
    if not isinstance(call, dict):
        return {"type": type(call).__name__}
    method = _safe_method_name(call.get(method_key))
    params = call.get("params")
    if not isinstance(params, list):
        return {
            "method": method,
            "param_count": None,
            "params": {"type": type(params).__name__},
        }
    return {
        "method": method,
        "param_count": len(params),
        "params": [
            _param_shape(method or "", index, value)
            for index, value in enumerate(params[:_MAX_PARAMS])
        ],
    }


def _multicall_shapes(params: list[Any]) -> dict[str, Any]:
    calls: list[Any] = params
    if len(params) == 1 and isinstance(params[0], list):
        calls = params[0]
    return {
        "count": len(calls),
        "calls": [_call_shape(call, "methodName") for call in calls[:_MAX_BATCH_CALLS]],
        "truncated": len(calls) > _MAX_BATCH_CALLS,
    }


def _batch_shapes(payload: list[Any]) -> dict[str, Any]:
    return {
        "count": len(payload),
        "calls": [_call_shape(call, "method") for call in payload[:_MAX_BATCH_CALLS]],
        "truncated": len(payload) > _MAX_BATCH_CALLS,
    }


def request_metadata(
    body: bytes,
    *,
    protocol: str,
    content_type: str | None,
    inspect_limit: int,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "content_type": content_type,
        "body_bytes": len(body),
    }
    if not body:
        base["parse"] = "empty"
        return base
    if len(body) > inspect_limit:
        base["parse"] = "too_large"
        return base

    if protocol == "xmlrpc":
        try:
            base.update(_xml_metadata(body))
        except XmlInspectionError as exc:
            base["parse"] = "error"
            base["reason"] = str(exc)
        return base

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        base["parse"] = "error"
        base["reason"] = "invalid_json"
        return base
    if isinstance(payload, list):
        base["parse"] = "batch"
        base["batch"] = _batch_shapes(payload)
        return base
    if not isinstance(payload, dict):
        base["parse"] = "error"
        base["reason"] = "not_an_object"
        return base

    method = payload.get("method")
    if not isinstance(method, str):
        method = ""
    params = payload.get("params", [])
    base["method"] = _safe_name(method) if method else None
    base["parse"] = "ok"
    if not isinstance(params, list):
        base["params"] = {"type": "invalid", "reason": "not_an_array"}
        return base

    if method.lower() == "system.multicall":
        base["multicall"] = _multicall_shapes(params)

    if protocol == "jsonprpc":
        if params and isinstance(params[0], str):
            base["callback"] = {"type": "string", "length": len(params[0])}
            params = params[1:]
        else:
            base["callback"] = {"type": "missing"}

    base["param_count"] = len(params)
    base["params"] = [
        _param_shape(method, index, value) for index, value in enumerate(params[:_MAX_PARAMS])
    ]
    if len(params) > _MAX_PARAMS:
        base["params_truncated"] = True
    return base


def response_metadata(
    body: bytes,
    *,
    content_type: str | None,
    inspect_limit: int,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "content_type": content_type,
        "body_bytes": len(body),
    }
    if not body:
        base["parse"] = "empty"
        return base
    if len(body) > inspect_limit:
        base["parse"] = "too_large"
        return base

    content_type_lower = (content_type or "").lower()
    if "xml" in content_type_lower:
        try:
            base.update(_xml_metadata(body))
        except XmlInspectionError as exc:
            base["parse"] = "error"
            base["reason"] = str(exc)
        return base
    if "json" not in content_type_lower and not body.lstrip().startswith((b"{", b"[")):
        base["parse"] = "unstructured"
        return base

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        base["parse"] = "error"
        base["reason"] = "invalid_json"
        return base

    base["parse"] = "ok"
    if not isinstance(payload, dict):
        base["envelope"] = _shape(
            payload,
            include_scalar_values=False,
            include_string_length=False,
        )
        return base

    base["envelope"] = _shape(
        payload,
        include_scalar_values=False,
        include_string_length=False,
    )
    error = payload.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        base["error"] = {
            "code": code if isinstance(code, int) else None,
            "message_class": error_message_class(error.get("message")),
        }
    return base


def error_message_class(message: object) -> str:
    if not isinstance(message, str):
        return "unknown"
    if message in _SAFE_ERROR_CLASSES:
        return _SAFE_ERROR_CLASSES[message]
    if message.startswith("Invalid parameter"):
        return "invalid_parameter"
    if message.startswith("Method '") and message.endswith("is not implemented in bridge mode"):
        return "not_implemented"
    return "redacted"


class _XmlNode:
    __slots__ = ("children", "tag", "text")

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.children: list[_XmlNode] = []
        self.text: str | None = None


def _xml_metadata(body: bytes) -> dict[str, Any]:
    parser = expat.ParserCreate()
    root: _XmlNode | None = None
    stack: list[_XmlNode] = []
    text_parts: list[str] = []
    node_count = 0

    def reject_doctype(*_args: object) -> None:
        raise XmlInspectionError("DTD declarations are not allowed")

    def reject_entity(*_args: object) -> None:
        raise XmlInspectionError("entity declarations are not allowed")

    def reject_external(*_args: object) -> int:
        raise XmlInspectionError("external entities are not allowed")

    def start_element(name: str, _attrs: dict[str, str]) -> None:
        nonlocal root, node_count
        node_count += 1
        if node_count > _MAX_XML_NODES:
            raise XmlInspectionError("XML node limit exceeded")
        if len(stack) >= _MAX_XML_DEPTH:
            raise XmlInspectionError("XML depth limit exceeded")
        node = _XmlNode(_safe_name(name))
        if stack:
            stack[-1].children.append(node)
        else:
            root = node
        stack.append(node)
        text_parts.clear()

    def end_element(_name: str) -> None:
        node = stack.pop()
        if node.tag in {"name", "methodName"}:
            text = "".join(text_parts)[:_MAX_XML_TEXT]
            node.text = _safe_name(text) if text else None
        text_parts.clear()

    def char_data(data: str) -> None:
        if stack and stack[-1].tag in {"name", "methodName"}:
            text_parts.append(data)
            if sum(len(part) for part in text_parts) > _MAX_XML_TEXT:
                raise XmlInspectionError("XML text limit exceeded")

    parser.StartDoctypeDeclHandler = reject_doctype
    parser.EntityDeclHandler = reject_entity
    parser.ExternalEntityRefHandler = reject_external
    parser.StartElementHandler = start_element
    parser.EndElementHandler = end_element
    parser.CharacterDataHandler = char_data

    try:
        parser.Parse(body, True)
    except expat.ExpatError as exc:
        raise XmlInspectionError("malformed XML") from exc
    if root is None:
        raise XmlInspectionError("empty XML document")

    method: str | None = None
    fields: list[dict[str, str]] = []
    param_types: list[str] = []
    tag_counts: dict[str, int] = {}

    def visit(node: _XmlNode) -> None:
        nonlocal method
        tag_counts[node.tag] = tag_counts.get(node.tag, 0) + 1
        if node.tag == "methodName" and node.text:
            method = node.text
        if node.tag == "struct":
            for member in node.children:
                if member.tag != "member":
                    continue
                name_node = next((child for child in member.children if child.tag == "name"), None)
                value_node = next(
                    (child for child in member.children if child.tag == "value"), None
                )
                if name_node is None or value_node is None:
                    continue
                value_type = value_node.children[0].tag if value_node.children else "empty"
                fields.append(
                    {
                        "name": name_node.text or "<redacted>",
                        "type": _safe_name(value_type),
                    }
                )
        if node.tag == "param":
            value_node = next((child for child in node.children if child.tag == "value"), None)
            param_types.append(
                _safe_name(value_node.children[0].tag)
                if value_node and value_node.children
                else "empty"
            )
        for child in node.children:
            visit(child)

    visit(root)
    return {
        "parse": "ok",
        "root": root.tag,
        "method": method,
        "param_types": param_types[:64],
        "fields": fields[:200],
        "field_count": len(fields),
        "tags": dict(sorted(tag_counts.items())),
    }
