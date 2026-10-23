from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from nzbget_sab_bridge.config import Settings
from nzbget_sab_bridge.main import create_app
from tests.conftest import (
    FAKE_BRIDGE_PASSWORD,
    FAKE_BRIDGE_USER,
    FAKE_CAPTURE_PASSWORD,
    FAKE_INDEXER_URL,
    FAKE_NZB_BASE64,
    FAKE_SAB_API_KEY,
    basic_auth_header,
)

_SAB_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sab"


def _sab_fixture(name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((_SAB_FIXTURES / name).read_text(encoding="utf-8")),
    )


def _synthetic_sab_handler() -> tuple[Any, list[str]]:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.read().decode())
        mode = form.get("mode", [""])[0]
        calls.append(mode)
        fixtures = {
            "queue": "queue.json",
            "history": "history.json",
            "status": "status.json",
            "server_stats": "server_stats.json",
            "get_cats": "categories.json",
        }
        filename = fixtures.get(mode)
        if filename is None:
            return httpx.Response(
                400,
                json={"status": False, "error": "unexpected synthetic mode"},
            )
        return httpx.Response(200, json=_sab_fixture(filename))

    return handler, calls


def _jsonrpc(method: str, params: list[Any] | None = None, request_id: Any = 1) -> dict[str, Any]:
    payload: dict[str, Any] = {"method": method, "params": params or []}
    if request_id is not None:
        payload["id"] = request_id
    return payload


def _post_rpc(
    client: TestClient,
    path: str,
    payload: dict[str, Any] | bytes,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    request_headers = {
        "Content-Type": "application/json",
        **(headers or {}),
    }
    return cast(httpx.Response, client.post(path, content=body, headers=request_headers))


def test_bridge_version_and_auth(bridge_settings: Settings) -> None:
    app = create_app(bridge_settings)
    with TestClient(app) as client:
        unauthenticated = _post_rpc(client, "/jsonrpc", _jsonrpc("version"))
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers["www-authenticate"].startswith("Basic ")
        assert unauthenticated.content == b""

        response = _post_rpc(
            client,
            "/jsonrpc",
            _jsonrpc("version"),
            basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD),
        )
    assert response.status_code == 200
    assert response.json() == {"version": "1.1", "id": 1, "result": "21.1"}
    assert response.headers["content-type"].startswith("application/json")


def test_bridge_unimplemented_methods_are_honest(bridge_settings: Settings) -> None:
    app = create_app(bridge_settings)
    with TestClient(app) as client:
        headers = basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD)
        listfiles = _post_rpc(client, "/jsonrpc", _jsonrpc("listfiles", [0, 0, 1]), headers)
        postqueue = _post_rpc(client, "/jsonrpc", _jsonrpc("postqueue", []), headers)
        unknown = _post_rpc(client, "/jsonrpc", _jsonrpc("unknown_method"), headers)
    assert listfiles.status_code == 200
    assert listfiles.json()["error"]["code"] == 1
    assert "not implemented" in listfiles.json()["error"]["message"]
    assert "listfiles" in listfiles.json()["error"]["message"]
    assert postqueue.json()["error"]["code"] == 1
    assert unknown.json()["error"]["code"] == 1
    assert unknown.json()["error"]["message"] == "Invalid procedure"


def test_bridge_read_only_translation_from_synthetic_sab(
    bridge_settings: Settings,
) -> None:
    handler, calls = _synthetic_sab_handler()
    app = create_app(bridge_settings, sab_transport=httpx.MockTransport(handler))
    headers = basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD)
    with TestClient(app) as client:
        version = client.get("/jsonrpc/version", headers=headers)
        config = client.get("/jsonrpc/config", headers=headers)
        templates = client.post(
            "/jsonrpc/",
            content=json.dumps(_jsonrpc("configtemplates", [], 2)).encode(),
            headers={**headers, "Content-Type": "application/json"},
            follow_redirects=False,
        )
        listgroups = client.post(
            "/jsonrpc/",
            content=json.dumps(_jsonrpc("listgroups", [], 3)).encode(),
            headers={**headers, "Content-Type": "application/json"},
            follow_redirects=False,
        )
        status = client.post(
            "/jsonrpc/",
            content=json.dumps(_jsonrpc("status", [], 4)).encode(),
            headers={**headers, "Content-Type": "application/json"},
            follow_redirects=False,
        )
        history = client.get("/jsonrpc/history", headers=headers)
        hidden = client.post(
            "/jsonrpc/",
            content=json.dumps(_jsonrpc("history", [True], 5)).encode(),
            headers={**headers, "Content-Type": "application/json"},
            follow_redirects=False,
        )
        listfiles = client.post(
            "/jsonrpc/",
            content=json.dumps(_jsonrpc("listfiles", [0, 0, 1], 6)).encode(),
            headers={**headers, "Content-Type": "application/json"},
            follow_redirects=False,
        )

    assert version.status_code == 200
    assert version.json()["result"] == "21.1"
    assert "id" not in version.json()

    assert config.status_code == 200
    assert config.json()["result"] == [
        {"Name": "Category1.Name", "Value": "Default"},
        {"Name": "Category2.Name", "Value": "tv"},
        {"Name": "Category3.Name", "Value": "movies"},
    ]

    assert templates.status_code == 200
    assert templates.json()["result"] == []

    assert listgroups.status_code == 200
    assert "location" not in listgroups.headers
    groups = listgroups.json()["result"]
    assert len(groups) == 1
    group = groups[0]
    assert group["NZBID"] == 1
    assert group["Status"] == "DOWNLOADING"
    assert group["Category"] == "tv"
    assert group["FileSizeMB"] == 1024
    assert group["RemainingSizeMB"] == 512
    assert group["MaxPriority"] == 0
    assert group["ActiveDownloads"] == 1
    assert group["ExtraParBlocks"] == 0
    assert group["MaxPostTime"] > 0
    assert group["MinPostTime"] == group["MaxPostTime"]

    assert status.status_code == 200
    status_fields = status.json()["result"]
    assert status_fields["DownloadRate"] == 2048 * 1024
    assert status_fields["DownloadLimit"] == 10485760
    assert status_fields["RemainingSizeMB"] == 512
    assert status_fields["MonthSizeMB"] == 20480
    assert status_fields["DaySizeMB"] == 1024
    assert status_fields["FreeDiskSpaceMB"] == 100 * 1024
    assert status_fields["NewsServers"] == [{"ID": 1, "Active": True}]
    assert status_fields["DownloadPaused"] is False
    assert status_fields["ServerStandBy"] is False
    assert status_fields["ThreadCount"] == 8

    assert history.status_code == 200
    records = history.json()["result"]
    assert len(records) == 2
    completed = next(item for item in records if item["Name"].endswith("S01E01.1080p"))
    failed = next(item for item in records if "Failed" in item["Name"])
    assert completed["NZBID"] == group["NZBID"]
    assert completed["Status"] == "SUCCESS/ALL"
    assert completed["UnpackStatus"] == "SUCCESS"
    assert completed["URL"] == ""
    assert failed["NZBID"] != group["NZBID"]
    assert failed["Status"] == "FAILURE/UNPACK"
    assert failed["UnpackStatus"] == "FAILURE"

    assert hidden.status_code == 200
    assert hidden.json()["error"]["code"] == 4
    assert "Hidden history" in hidden.json()["error"]["message"]

    assert listfiles.status_code == 200
    assert listfiles.json()["error"]["code"] == 1
    assert "listfiles" in listfiles.json()["error"]["message"]

    assert calls.count("queue") == 2
    assert calls.count("status") == 1
    assert calls.count("server_stats") == 1
    assert calls.count("history") == 1
    assert calls.count("get_cats") == 1


def test_bridge_get_supported_arguments_and_cache_buster(
    bridge_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    handler, calls = _synthetic_sab_handler()
    app = create_app(bridge_settings, sab_transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        response = client.get(
            "/jsonrpc/history?Hidden=false&_=123456",
            headers=basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD),
        )
    assert response.status_code == 200
    assert "result" in response.json()
    assert calls.count("history") == 1
    captured = capsys.readouterr().out
    assert "bridge route diagnostic" in captured
    assert "/jsonrpc/history" in captured
    assert '"query_present": true' in captured
    assert "123456" not in captured


def test_bridge_get_hidden_history_is_explicit(
    bridge_settings: Settings,
) -> None:
    handler, calls = _synthetic_sab_handler()
    app = create_app(bridge_settings, sab_transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        response = client.get(
            "/jsonrpc/history?Hidden=true",
            headers=basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD),
        )
    assert response.status_code == 200
    assert response.json()["error"]["code"] == 4
    assert "Hidden history" in response.json()["error"]["message"]
    assert calls.count("history") == 0


def test_bridge_get_unknown_argument_is_explicit_and_redacted(
    bridge_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    app = create_app(bridge_settings)
    with TestClient(app) as client:
        response = client.get(
            "/jsonrpc/history?private-value=secret",
            headers=basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD),
        )
    assert response.status_code == 200
    assert response.json()["error"]["code"] == 2
    captured = capsys.readouterr().out
    assert "bridge route diagnostic" in captured
    assert "/jsonrpc/history" in captured
    assert '"query_present": true' in captured
    assert "private-value" not in captured
    assert "secret" not in captured


def test_bridge_malformed_json_and_parameter_errors(bridge_settings: Settings) -> None:
    app = create_app(bridge_settings)
    headers = basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD)
    with TestClient(app) as client:
        malformed = _post_rpc(client, "/jsonrpc", b'{"method":', headers)
        named = _post_rpc(
            client,
            "/jsonrpc",
            b'{"method":"version","params":{"x":1},"id":1}',
            headers,
        )
        extra = _post_rpc(client, "/jsonrpc", _jsonrpc("version", [1]), headers)
    assert malformed.json()["error"]["code"] == 4
    assert named.json()["error"]["code"] == 2
    assert extra.json()["error"]["code"] == 2


def test_bridge_jsonp_and_method_transport_errors(bridge_settings: Settings) -> None:
    app = create_app(bridge_settings)
    headers = basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD)
    with TestClient(app) as client:
        jsonp = _post_rpc(
            client,
            "/jsonprpc",
            {"method": "version", "params": ["cb1"], "id": 2},
            headers,
        )
        assert jsonp.text.startswith("cb1(")
        assert json.loads(jsonp.text[4:-1])["result"] == "21.1"

        get_response = client.get("/jsonrpc", headers=headers)
        assert get_response.status_code == 405
        wrong_type = client.post(
            "/jsonrpc",
            content=b"{}",
            headers={**headers, "Content-Type": "text/plain"},
        )
        assert wrong_type.status_code == 415
        xml = client.post("/xmlrpc", content=b"<x/>", headers=headers)
        assert xml.status_code == 501


def test_jsonp_rejects_mutations_without_backend_action(bridge_settings: Settings) -> None:
    handler, calls = _synthetic_sab_handler()
    app = create_app(bridge_settings, sab_transport=httpx.MockTransport(handler))
    headers = basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD)
    with TestClient(app) as client:
        response = _post_rpc(
            client,
            "/jsonprpc",
            {"method": "pausedownload", "params": ["cb1"], "id": 2},
            headers,
        )
        get_response = client.get("/jsonprpc", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert response.text.startswith("cb1(")
    payload = json.loads(response.text[response.text.find("(") + 1 : -1])
    assert payload["error"]["code"] == 3
    assert get_response.status_code == 405
    assert calls == []


def test_rpc_aliases_require_auth_and_cookies_do_not_authenticate(
    bridge_settings: Settings,
) -> None:
    handler, calls = _synthetic_sab_handler()
    app = create_app(bridge_settings, sab_transport=httpx.MockTransport(handler))
    body = _jsonrpc("version")
    with TestClient(app) as client:
        assert _post_rpc(client, "/jsonrpc", body).status_code == 401
        assert _post_rpc(client, "/jsonrpc/", body).status_code == 401
        jsonp = _post_rpc(client, "/jsonprpc", {"method": "version", "params": ["cb"]})
        assert jsonp.status_code == 401
        assert client.get("/jsonrpc/version").status_code == 401
        assert client.post("/jsonrpc/version", content=b"{}").status_code == 401
        assert client.post("/xmlrpc", content=b"<x/>").status_code == 401
        cookie_only = _post_rpc(
            client,
            "/jsonrpc/",
            body,
            headers={"Cookie": "bridge-session=not-an-auth-header"},
        )
        assert cookie_only.status_code == 401
    assert calls == []


def test_health_and_readiness_are_unauthenticated(bridge_settings: Settings) -> None:
    app = create_app(bridge_settings)
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200


def test_unexpected_handler_failure_is_an_error_not_a_success(
    bridge_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("synthetic programming defect with private-value")

    monkeypatch.setattr("nzbget_sab_bridge.main.dispatch", broken)
    app = create_app(bridge_settings)
    headers = basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD)
    with TestClient(app) as client:
        response = _post_rpc(client, "/jsonrpc", _jsonrpc("version"), headers)
    assert response.status_code == 200
    assert response.json()["error"]["code"] == 4
    assert "result" not in response.json()
    assert "private-value" not in response.text


def test_bridge_body_limit(bridge_settings: Settings) -> None:
    settings = bridge_settings.model_copy(
        update={"request_max_bytes": 50, "nzb_max_decoded_bytes": 40}
    )
    app = create_app(settings)
    headers = basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD)
    with TestClient(app) as client:
        response = _post_rpc(
            client,
            "/jsonrpc",
            b'{"method":"version","params":[],"id":1,"padding":"' + b"x" * 100 + b'"}',
            headers,
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == 4


def test_health_and_readyz_storage(bridge_settings: Settings) -> None:
    app = create_app(bridge_settings)
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["components"] == {"storage": "ok", "sab": "disabled"}


def test_readyz_checks_sab_when_enabled(bridge_settings: Settings) -> None:
    def backend(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"categories": ["*", "tv"]})

    settings = bridge_settings.model_copy(update={"readyz_check_sab": True})
    app = create_app(settings, sab_transport=httpx.MockTransport(backend))
    with TestClient(app) as client:
        ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["components"] == {"storage": "ok", "sab": "ok"}
    assert FAKE_SAB_API_KEY not in ready.text


def test_readyz_fails_when_sab_rejects_key(bridge_settings: Settings) -> None:
    def backend(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content=b"bad key")

    settings = bridge_settings.model_copy(update={"readyz_check_sab": True})
    app = create_app(settings, sab_transport=httpx.MockTransport(backend))
    with TestClient(app) as client:
        ready = client.get("/readyz")
    assert ready.status_code == 503
    assert ready.json()["components"]["sab"] == "error"
    assert FAKE_SAB_API_KEY not in ready.text


def test_liveness_survives_sab_outage_while_readiness_fails(bridge_settings: Settings) -> None:
    def backend(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("synthetic outage")

    settings = bridge_settings.model_copy(update={"readyz_check_sab": True})
    app = create_app(settings, sab_transport=httpx.MockTransport(backend))
    with TestClient(app) as client:
        health = client.get("/healthz")
        ready = client.get("/readyz")
    assert health.status_code == 200
    assert ready.status_code == 503
    assert ready.json()["components"]["sab"] == "error"


def test_readyz_fails_when_storage_is_unusable(bridge_settings: Settings, tmp_path: Path) -> None:
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("x", encoding="utf-8")
    settings = bridge_settings.model_copy(update={"data_dir": blocked})
    app = create_app(settings)
    with TestClient(app) as client:
        health = client.get("/healthz")
        ready = client.get("/readyz")
    assert health.status_code == 200
    assert ready.status_code == 503
    assert ready.json()["components"]["storage"] == "error"


def _capture_handler(
    *, status: int = 200, body: bytes = b'{"version":"1.1","result":"21.1"}'
) -> tuple[Any, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status,
            content=body,
            headers={
                "content-type": "application/json",
                "set-cookie": "backend-cookie=secret-cookie",
                "connection": "keep-alive",
            },
        )

    return handler, requests


def test_capture_forwards_exact_body_and_uses_separate_auth(
    capture_settings: Settings,
) -> None:
    backend, requests = _capture_handler()
    app = create_app(capture_settings, capture_transport=httpx.MockTransport(backend))
    headers = basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD)
    body = b'{"method":"version","params":[],"id":1}'
    with TestClient(app) as client:
        response = client.post(
            "/jsonrpc",
            content=body,
            headers={**headers, "Cookie": "client-cookie=private"},
        )
    assert response.status_code == 200
    assert response.content == b'{"version":"1.1","result":"21.1"}'
    assert response.headers["x-bridge-capture"] == "ok"
    assert "set-cookie" not in response.headers
    assert "connection" not in response.headers
    assert requests[0].content == body
    assert requests[0].headers["authorization"].startswith("Basic ")
    assert "client-cookie" not in requests[0].headers.get("cookie", "")
    assert FAKE_BRIDGE_PASSWORD.encode() not in requests[0].content
    assert FAKE_CAPTURE_PASSWORD.encode() not in requests[0].content
    fixture = next((capture_settings.capture_dir).glob("*.json"))
    content = fixture.read_text(encoding="utf-8")
    assert FAKE_INDEXER_URL not in content
    assert FAKE_SAB_API_KEY not in content


def test_capture_fixture_omits_secret_values_from_request_and_response(
    capture_settings: Settings,
) -> None:
    secret_body = json.dumps(
        {
            "method": "append",
            "params": [
                "private-name.nzb",
                FAKE_NZB_BASE64,
                FAKE_INDEXER_URL,
                {"password": FAKE_CAPTURE_PASSWORD},
            ],
            "id": 1,
        }
    ).encode()
    backend, _requests = _capture_handler(body=secret_body)
    app = create_app(capture_settings, capture_transport=httpx.MockTransport(backend))
    with TestClient(app) as client:
        response = client.post(
            "/jsonrpc",
            content=secret_body,
            headers=basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD),
        )
    assert response.status_code == 200
    fixture_text = next((capture_settings.capture_dir).glob("*.json")).read_text(encoding="utf-8")
    for secret in (
        "private-name",
        FAKE_NZB_BASE64,
        FAKE_INDEXER_URL,
        FAKE_CAPTURE_PASSWORD,
        FAKE_SAB_API_KEY,
    ):
        assert secret not in fixture_text
    assert '"method": "append"' in fixture_text
    assert '"param_count": 4' in fixture_text


def test_capture_handles_trailing_slash_without_redirect(
    capture_settings: Settings,
) -> None:
    backend, requests = _capture_handler()
    app = create_app(capture_settings, capture_transport=httpx.MockTransport(backend))
    body = b'{"method":"version","params":[],"id":1}'
    with TestClient(app) as client:
        response = client.post(
            "/jsonrpc/?probe=1",
            content=body,
            headers={
                **basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD),
                "Content-Type": "application/json",
            },
            follow_redirects=False,
        )
    assert response.status_code == 200
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/nzbget/jsonrpc/"
    assert requests[0].url.query == b"probe=1"
    assert requests[0].read() == body
    fixture = json.loads(
        next((capture_settings.capture_dir).glob("*.json")).read_text(encoding="utf-8")
    )
    assert fixture["path"] == "/jsonrpc/"
    assert fixture["request"]["method"] == "version"
    assert fixture["request"]["param_count"] == 0


def test_capture_forwards_get_jsonrpc_method_path_unchanged(
    capture_settings: Settings,
) -> None:
    backend, requests = _capture_handler()
    app = create_app(capture_settings, capture_transport=httpx.MockTransport(backend))
    with TestClient(app) as client:
        response = client.get(
            "/jsonrpc/version?probe=1",
            headers=basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD),
        )
    assert response.status_code == 200
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/nzbget/jsonrpc/version"
    assert requests[0].url.query == b"probe=1"
    assert requests[0].content == b""
    fixture = json.loads(
        next((capture_settings.capture_dir).glob("*.json")).read_text(encoding="utf-8")
    )
    assert fixture["transport"] == "jsonrpc"
    assert fixture["path"] == "/jsonrpc/version"
    assert fixture["request"]["method"] == "version"


def test_capture_jsonrpc_method_path_rejects_unknown_method(
    capture_settings: Settings,
) -> None:
    backend, requests = _capture_handler()
    app = create_app(capture_settings, capture_transport=httpx.MockTransport(backend))
    with TestClient(app) as client:
        response = client.get(
            "/jsonrpc/private-token",
            headers=basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD),
        )
    assert response.status_code == 404
    assert requests == []


def test_capture_forwards_get_requests(capture_settings: Settings) -> None:
    backend, requests = _capture_handler()
    app = create_app(capture_settings, capture_transport=httpx.MockTransport(backend))
    with TestClient(app) as client:
        response = client.get(
            "/jsonrpc?method=version",
            headers=basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD),
        )
    assert response.status_code == 200
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.query == b"method=version"
    assert requests[0].content == b""


def test_capture_never_replays_backend_cookies(capture_settings: Settings) -> None:
    backend, requests = _capture_handler()
    app = create_app(capture_settings, capture_transport=httpx.MockTransport(backend))
    headers = basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD)
    with TestClient(app) as client:
        for _ in range(2):
            response = client.post(
                "/jsonrpc",
                content=b'{"method":"version","params":[],"id":1}',
                headers={**headers, "Cookie": "client-cookie=private"},
            )
            assert response.status_code == 200
    assert len(requests) == 2
    assert "cookie" not in requests[0].headers
    assert "cookie" not in requests[1].headers


def test_capture_decodes_gzip_and_strips_encoding_headers(
    capture_settings: Settings,
) -> None:
    raw = b'{"version":"1.1","result":"21.1"}'
    compressed = gzip.compress(raw)

    def backend(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=compressed,
            headers={
                "content-type": "application/json",
                "content-encoding": "gzip",
                "content-length": str(len(compressed)),
            },
        )

    app = create_app(capture_settings, capture_transport=httpx.MockTransport(backend))
    with TestClient(app) as client:
        response = client.post(
            "/jsonrpc",
            content=b'{"method":"version","params":[],"id":1}',
            headers=basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD),
        )
    assert response.status_code == 200
    assert response.content == raw
    assert "content-encoding" not in response.headers
    assert response.headers["content-length"] == str(len(raw))


def test_capture_records_query_keys_but_not_values(
    capture_settings: Settings,
) -> None:
    backend, _requests = _capture_handler()
    app = create_app(capture_settings, capture_transport=httpx.MockTransport(backend))
    secret_query_value = "query-secret-value"
    with TestClient(app) as client:
        response = client.post(
            f"/jsonrpc?callback={secret_query_value}&mode=secret",
            content=b'{"method":"version","params":[],"id":1}',
            headers=basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD),
        )
    assert response.status_code == 200
    fixture = json.loads(
        next((capture_settings.capture_dir).glob("*.json")).read_text(encoding="utf-8")
    )
    assert fixture["query_keys"] == ["callback", "mode"]
    assert secret_query_value not in json.dumps(fixture)


def test_capture_route_diagnostic_redacts_path_and_query(
    capture_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    backend, requests = _capture_handler()
    app = create_app(capture_settings, capture_transport=httpx.MockTransport(backend))
    with TestClient(app) as client:
        response = client.post(
            "/jsonrpc/private-token?secret=query-secret",
            content=b"{}",
            headers=basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD),
        )
    assert response.status_code == 404
    assert requests == []
    captured = capsys.readouterr().out
    assert "capture route diagnostic" in captured
    assert '"/jsonrpc/<redacted>"' in captured or "/jsonrpc/<redacted>" in captured
    assert '"matched_capture_route": false' in captured
    assert "private-token" not in captured
    assert "query-secret" not in captured


def test_capture_rejects_unapproved_path_without_backend_call(
    capture_settings: Settings,
) -> None:
    backend, requests = _capture_handler()
    app = create_app(capture_settings, capture_transport=httpx.MockTransport(backend))
    with TestClient(app) as client:
        response = client.post(
            "/webui",
            content=b"{}",
            headers=basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD),
        )
    assert response.status_code == 404
    assert requests == []


def test_capture_body_limit_is_enforced(capture_settings: Settings) -> None:
    settings = capture_settings.model_copy(
        update={"request_max_bytes": 20, "nzb_max_decoded_bytes": 10}
    )
    backend, requests = _capture_handler()
    app = create_app(settings, capture_transport=httpx.MockTransport(backend))
    with TestClient(app) as client:
        response = client.post(
            "/jsonrpc",
            content=b'{"method":"version","params":[],"id":1}',
            headers=basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD),
        )
    assert response.status_code == 413
    assert requests == []


def test_capture_xml_dtd_is_forwarded_but_not_inspected_as_xml(
    capture_settings: Settings,
) -> None:
    xml = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "secret">]><x>&e;</x>'
    backend, requests = _capture_handler(body=xml)
    settings = capture_settings.model_copy(update={"capture_xmlrpc": True})
    app = create_app(settings, capture_transport=httpx.MockTransport(backend))
    with TestClient(app) as client:
        response = client.post(
            "/xmlrpc",
            content=xml,
            headers=basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD),
        )
    assert response.status_code == 200
    assert response.content == xml
    assert requests[0].content == xml
    fixture = json.loads(
        next((capture_settings.capture_dir).glob("*.json")).read_text(encoding="utf-8")
    )
    assert fixture["request"]["parse"] == "error"
    assert "secret" not in json.dumps(fixture)


def test_capture_preserves_backend_error_status_without_retry(
    capture_settings: Settings,
) -> None:
    backend, requests = _capture_handler(status=503, body=b'{"status":false}')
    app = create_app(capture_settings, capture_transport=httpx.MockTransport(backend))
    with TestClient(app) as client:
        response = client.post(
            "/jsonrpc",
            content=b'{"method":"version","params":[],"id":1}',
            headers=basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD),
        )
    assert response.status_code == 503
    assert response.content == b'{"status":false}'
    assert len(requests) == 1


def test_capture_auth_failure_does_not_forward(
    capture_settings: Settings,
) -> None:
    backend, requests = _capture_handler()
    app = create_app(capture_settings, capture_transport=httpx.MockTransport(backend))
    with TestClient(app) as client:
        response = client.post(
            "/jsonrpc",
            content=b'{"method":"version","params":[],"id":1}',
        )
    assert response.status_code == 401
    assert requests == []


def test_capture_preserves_malformed_body_and_records_parse_error(
    capture_settings: Settings,
) -> None:
    backend, requests = _capture_handler(body=b"not-json-at-all")
    app = create_app(capture_settings, capture_transport=httpx.MockTransport(backend))
    headers = basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD)
    with TestClient(app) as client:
        response = client.post("/jsonrpc", content=b"{broken", headers=headers)
    assert response.status_code == 200
    assert response.content == b"not-json-at-all"
    assert requests[0].content == b"{broken"
    fixture = json.loads(
        next((capture_settings.capture_dir).glob("*.json")).read_text(encoding="utf-8")
    )
    assert fixture["request"]["parse"] == "error"


def test_capture_refuses_redirect_without_leaking_location(
    capture_settings: Settings,
) -> None:
    requests: list[httpx.Request] = []

    def backend(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"location": f"http://redirect.invalid/?apikey={FAKE_SAB_API_KEY}"},
        )

    app = create_app(capture_settings, capture_transport=httpx.MockTransport(backend))
    headers = basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD)
    with TestClient(app) as client:
        response = client.post(
            "/jsonrpc",
            content=b'{"method":"version","params":[],"id":1}',
            headers=headers,
        )
    assert response.status_code == 502
    assert "location" not in response.headers
    assert FAKE_SAB_API_KEY.encode() not in response.content
    assert len(requests) == 1


def test_capture_timeout_is_not_replayed(capture_settings: Settings) -> None:
    requests: list[httpx.Request] = []

    def backend(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ReadTimeout("backend timeout")

    app = create_app(capture_settings, capture_transport=httpx.MockTransport(backend))
    headers = basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD)
    with TestClient(app) as client:
        response = client.post(
            "/jsonrpc",
            content=b'{"method":"version","params":[],"id":1}',
            headers=headers,
        )
    assert response.status_code == 504
    assert len(requests) == 1
    assert response.headers["x-bridge-capture"] == "ok"


def test_capture_recorder_failure_does_not_replay_or_claim_success(
    capture_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, requests = _capture_handler()
    app = create_app(capture_settings, capture_transport=httpx.MockTransport(backend))
    with TestClient(app) as client:
        recorder = app.state.runtime.capture._recorder
        monkeypatch.setattr(
            recorder,
            "write",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("disk full")),
        )
        response = client.post(
            "/jsonrpc",
            content=b'{"method":"version","params":[],"id":1}',
            headers=basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD),
        )
    assert response.status_code == 200
    assert response.headers["x-bridge-capture"] == "failed"
    assert len(requests) == 1


def test_capture_xml_is_opt_in_and_forwarded_unchanged(
    capture_settings: Settings,
) -> None:
    xml = b'<?xml version="1.0"?><methodCall><methodName>version</methodName></methodCall>'
    backend, requests = _capture_handler(body=xml)
    app = create_app(capture_settings, capture_transport=httpx.MockTransport(backend))
    headers = basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD)
    with TestClient(app) as client:
        disabled = client.post("/xmlrpc", content=xml, headers=headers)
        assert disabled.status_code == 404
        enabled_settings = capture_settings.model_copy(update={"capture_xmlrpc": True})
        enabled_app = create_app(enabled_settings, capture_transport=httpx.MockTransport(backend))
        with TestClient(enabled_app) as enabled_client:
            enabled = enabled_client.post("/xmlrpc", content=xml, headers=headers)
    assert enabled.status_code == 200
    assert enabled.content == xml
    assert requests[0].content == xml


def test_capture_readyz_does_not_require_sab(capture_settings: Settings) -> None:
    backend, _requests = _capture_handler()
    app = create_app(capture_settings, capture_transport=httpx.MockTransport(backend))
    with TestClient(app) as client:
        ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["components"] == {"capture": "ok"}


def test_capture_readyz_fails_when_backend_fails(capture_settings: Settings) -> None:
    def backend(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content=b"no")

    app = create_app(capture_settings, capture_transport=httpx.MockTransport(backend))
    with TestClient(app) as client:
        ready = client.get("/readyz")
    assert ready.status_code == 503
    assert ready.json()["components"]["capture"] == "error"
