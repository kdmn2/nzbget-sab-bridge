from __future__ import annotations

import json

from nzbget_sab_bridge.capture.sanitizer import (
    error_message_class,
    request_metadata,
    response_metadata,
)
from tests.conftest import FAKE_INDEXER_URL, FAKE_NZB_BASE64


def _dump(value: object) -> str:
    return json.dumps(value, sort_keys=True)


def test_json_request_shape_has_no_string_values() -> None:
    body = json.dumps(
        {
            "method": "append",
            "params": [
                "Private.Release.Name.nzb",
                FAKE_NZB_BASE64,
                "tv",
                0,
                False,
                True,
                "",
                0,
                "SCORE",
                [],
            ],
            "id": 123,
        }
    ).encode()
    meta = request_metadata(
        body, protocol="jsonrpc", content_type="application/json", inspect_limit=1_000_000
    )
    rendered = _dump(meta)
    assert meta["method"] == "append"
    assert meta["param_count"] == 10
    assert meta["params"][0]["type"] == "string"
    assert meta["params"][3]["value"] == 0
    assert meta["params"][4]["value"] is False
    assert meta["params"][8]["enum"] == "SCORE"
    assert "Private.Release" not in rendered
    assert "secret-nzb-body" not in rendered
    assert FAKE_NZB_BASE64 not in rendered


def test_editqueue_command_enum_is_recorded_without_other_strings() -> None:
    body = json.dumps(
        {
            "method": "editqueue",
            "params": ["GroupSetPriority", "Private Param Value", [1, 2]],
            "id": 1,
        }
    ).encode()
    meta = request_metadata(
        body, protocol="jsonrpc", content_type="application/json", inspect_limit=1_000_000
    )
    rendered = _dump(meta)
    assert meta["params"][0]["enum"] == "GroupSetPriority"
    assert "Private Param Value" not in rendered


def test_json_batch_preserves_structure_without_values() -> None:
    body = json.dumps(
        [
            {"method": "version", "params": [], "id": 1},
            {
                "method": "status",
                "params": [False, "private-value"],
                "id": 2,
            },
            {"method": "private-method", "params": ["secret"], "id": 3},
        ]
    ).encode()
    meta = request_metadata(
        body, protocol="jsonrpc", content_type="application/json", inspect_limit=1_000_000
    )
    rendered = _dump(meta)
    assert meta["parse"] == "batch"
    assert meta["batch"]["count"] == 3
    assert meta["batch"]["calls"][0]["method"] == "version"
    assert meta["batch"]["calls"][1]["method"] == "status"
    assert meta["batch"]["calls"][2]["method"] == "<redacted>"
    assert "private-value" not in rendered
    assert "private-method" not in rendered
    assert "secret" not in rendered


def test_system_multicall_records_nested_methods_and_param_shapes() -> None:
    body = json.dumps(
        {
            "method": "system.multicall",
            "params": [
                [
                    {"methodName": "version", "params": []},
                    {"methodName": "history", "params": [True]},
                    {"methodName": "private-method", "params": ["secret"]},
                ]
            ],
            "id": 1,
        }
    ).encode()
    meta = request_metadata(
        body, protocol="jsonrpc", content_type="application/json", inspect_limit=1_000_000
    )
    rendered = _dump(meta)
    assert meta["multicall"]["count"] == 3
    assert meta["multicall"]["calls"][0]["method"] == "version"
    assert meta["multicall"]["calls"][1]["method"] == "history"
    assert meta["multicall"]["calls"][2]["method"] == "<redacted>"
    assert "private-method" not in rendered
    assert "secret" not in rendered


def test_jsonp_callback_is_summarized_not_recorded() -> None:
    body = json.dumps({"method": "version", "params": ["secretCallbackName"], "id": 1}).encode()
    meta = request_metadata(
        body, protocol="jsonprpc", content_type="application/json", inspect_limit=1_000_000
    )
    assert meta["callback"]["type"] == "string"
    assert meta["param_count"] == 0
    assert "secretCallbackName" not in _dump(meta)


def test_json_response_shape_has_field_names_but_no_values() -> None:
    secret = "private-release-name"
    body = json.dumps(
        {
            "version": "1.1",
            "id": 1,
            "result": {
                "NZBID": 42,
                "Name": secret,
                "URL": FAKE_INDEXER_URL,
                "FileSizeLo": 123,
                "Parameters": [{"Name": "password", "Value": "hunter2"}],
            },
        }
    ).encode()
    meta = response_metadata(body, content_type="application/json", inspect_limit=1_000_000)
    rendered = _dump(meta)
    assert meta["parse"] == "ok"
    assert meta["envelope"]["type"] == "object"
    assert "result" in meta["envelope"]["fields"]
    assert "NZBID" in rendered
    assert "FileSizeLo" in rendered
    assert secret not in rendered
    assert "indexer-secret" not in rendered
    assert "hunter2" not in rendered


def test_error_response_records_safe_code_and_class() -> None:
    body = json.dumps(
        {
            "version": "1.1",
            "error": {
                "name": "JSONRPCError",
                "code": 2,
                "message": "Invalid parameter (private-value)",
            },
        }
    ).encode()
    meta = response_metadata(body, content_type="application/json", inspect_limit=1_000_000)
    assert meta["error"]["code"] == 2
    assert meta["error"]["message_class"] == "invalid_parameter"
    assert "private-value" not in _dump(meta)


def test_malformed_json_is_reported_without_body() -> None:
    body = b'{"password":"hunter2",'
    meta = request_metadata(
        body, protocol="jsonrpc", content_type="application/json", inspect_limit=1_000
    )
    assert meta["parse"] == "error"
    assert "hunter2" not in _dump(meta)


def test_oversized_body_is_not_inspected() -> None:
    body = b"x" * 20
    meta = request_metadata(
        body, protocol="jsonrpc", content_type="application/json", inspect_limit=10
    )
    assert meta["parse"] == "too_large"
    assert meta["body_bytes"] == 20


def test_shape_depth_is_bounded() -> None:
    value: object = "leaf"
    for _ in range(40):
        value = {"nested": value}
    body = json.dumps({"method": "x", "params": [value], "id": 1}).encode()
    meta = request_metadata(
        body, protocol="jsonrpc", content_type="application/json", inspect_limit=1_000_000
    )
    assert "truncated" in _dump(meta)


def test_xml_request_shape_and_field_names() -> None:
    body = b"""<?xml version="1.0"?>
    <methodCall>
      <methodName>append</methodName>
      <params>
        <param><value><string>secret-file-name.nzb</string></value></param>
        <param><value><base64>c2VjcmV0</base64></value></param>
      </params>
    </methodCall>"""
    meta = request_metadata(
        body, protocol="xmlrpc", content_type="text/xml", inspect_limit=1_000_000
    )
    rendered = _dump(meta)
    assert meta["parse"] == "ok"
    assert meta["method"] == "append"
    assert meta["param_types"] == ["string", "base64"]
    assert "secret-file-name" not in rendered
    assert "c2VjcmV0" not in rendered


def test_xml_response_struct_field_names() -> None:
    body = b"""<?xml version="1.0"?>
    <methodResponse><params><param><value><struct>
      <member><name>NZBID</name><value><i4>12</i4></value></member>
      <member><name>Name</name><value><string>private-name</string></value></member>
    </struct></value></param></params></methodResponse>"""
    meta = response_metadata(body, content_type="text/xml", inspect_limit=1_000_000)
    rendered = _dump(meta)
    assert meta["parse"] == "ok"
    assert {"name": "NZBID", "type": "i4"} in meta["fields"]
    assert {"name": "Name", "type": "string"} in meta["fields"]
    assert "private-name" not in rendered


def test_xml_dtd_and_entities_are_rejected_for_inspection() -> None:
    for body in (
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>',
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "secret">]><x>&e;</x>',
    ):
        meta = response_metadata(body, content_type="text/xml", inspect_limit=1_000_000)
        assert meta["parse"] == "error"
        assert "passwd" not in _dump(meta)
        assert "secret" not in _dump(meta)


def test_xml_malformed_is_reported() -> None:
    meta = response_metadata(b"<broken>", content_type="text/xml", inspect_limit=1_000)
    assert meta["parse"] == "error"


def test_xml_parser_rejects_deep_documents() -> None:
    body = b"<a>" * 40 + b"</a>" * 40
    meta = response_metadata(body, content_type="text/xml", inspect_limit=1_000_000)
    assert meta["parse"] == "error"
    assert "depth" in meta["reason"]


def test_error_message_class() -> None:
    assert error_message_class("Parse error") == "parse_error"
    assert error_message_class("Invalid parameter (x)") == "invalid_parameter"
    assert error_message_class("secret error text") == "redacted"
    assert error_message_class(None) == "unknown"


def test_deterministic_output() -> None:
    body = b'{"method":"version","params":[],"id":1}'
    first = request_metadata(
        body, protocol="jsonrpc", content_type="application/json", inspect_limit=1000
    )
    second = request_metadata(
        body, protocol="jsonrpc", content_type="application/json", inspect_limit=1000
    )
    assert first == second


def test_unsafe_field_names_are_redacted() -> None:
    body = json.dumps({"method": "x", "params": [{"<secret>": 1}], "id": 1}).encode()
    meta = request_metadata(
        body, protocol="jsonrpc", content_type="application/json", inspect_limit=1000
    )
    assert "<redacted>" in _dump(meta)
