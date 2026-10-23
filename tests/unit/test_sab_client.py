from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from nzbget_sab_bridge.sab.client import SabClient
from nzbget_sab_bridge.sab.errors import (
    SabApiError,
    SabHttpError,
    SabMalformedResponseError,
    SabRedirectError,
    SabTimeoutError,
    SabTransportError,
)
from tests.conftest import FAKE_INDEXER_URL, FAKE_SAB_API_KEY


async def _no_sleep(_delay: float) -> None:
    return None


def _client(handler: Callable[[httpx.Request], httpx.Response], **kwargs: Any) -> SabClient:
    return SabClient(
        "http://sab:8080/sab",
        FAKE_SAB_API_KEY,
        transport=httpx.MockTransport(handler),
        retry_sleep=_no_sleep,
        **kwargs,
    )


def _form(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.read().decode())


def _json(payload: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, json=payload, headers={"content-type": "application/json"})


async def test_api_key_is_in_form_not_url() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _json({"version": "5.1.3"})

    client = _client(handler)
    result = await client.version()
    await client.aclose()
    assert result.version == "5.1.3"
    request = captured[0]
    assert request.url.query == b""
    assert request.url.path == "/sab/api"
    form = _form(request)
    assert form["apikey"] == [FAKE_SAB_API_KEY]
    assert form["mode"] == ["version"]


async def test_queue_parse() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _json(
            {
                "queue": {
                    "status": "Downloading",
                    "slots": [
                        {
                            "nzo_id": "nzo-1",
                            "filename": "Example",
                            "status": "Downloading",
                            "mb": "100.00",
                            "mbleft": "50.00",
                            "priority": "Normal",
                            "cat": "tv",
                        }
                    ],
                    "noofslots": 1,
                }
            }
        )

    client = _client(handler)
    queue = await client.queue()
    await client.aclose()
    assert queue.status == "Downloading"
    assert queue.slots[0].nzo_id == "nzo-1"
    assert queue.slots[0].mb == "100.00"


async def test_history_and_status_parse() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        mode = _form(request)["mode"][0]
        if mode == "history":
            return _json(
                {
                    "history": {
                        "noofslots": 1,
                        "slots": [
                            {
                                "nzo_id": "nzo-h",
                                "name": "History",
                                "status": "Completed",
                                "bytes": 123,
                                "stage_log": [{"name": "Repair", "actions": ["ok"]}],
                            }
                        ],
                    }
                }
            )
        return _json(
            {
                "status": {
                    "paused": False,
                    "servers": [{"servername": "news", "serveractive": True}],
                }
            }
        )

    client = _client(handler)
    history = await client.history()
    status = await client.status()
    await client.aclose()
    assert history.slots[0].bytes == 123
    assert history.slots[0].stage_log[0].name == "Repair"
    assert status.servers[0].servername == "news"


async def test_add_file_uses_multipart_and_never_url_credentials() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _json({"status": True, "nzo_ids": ["nzo-added"]})

    client = _client(handler)
    result = await client.add_file(
        b"nzb-body-secret",
        "Example.nzb",
        nzbname="Example",
        category="tv",
        priority=-2,
    )
    await client.aclose()
    assert result.nzo_ids == ["nzo-added"]
    request = captured[0]
    assert request.url.query == b""
    assert request.headers["content-type"].startswith("multipart/form-data")
    body = request.read()
    assert b'name="mode"' in body
    assert b"\r\n\r\naddfile\r\n" in body
    assert b'name="apikey"' in body
    assert FAKE_SAB_API_KEY.encode() in body
    assert b"nzb-body-secret" in body
    assert b'filename="Example.nzb"' in body


async def test_add_url_form_contains_url_and_key_without_url_query() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _json({"status": True, "nzo_ids": ["nzo-url"]})

    client = _client(handler)
    result = await client.add_url(FAKE_INDEXER_URL, category="movies")
    await client.aclose()
    assert result.nzo_ids == ["nzo-url"]
    request = captured[0]
    assert request.url.query == b""
    form = _form(request)
    assert form["name"] == [FAKE_INDEXER_URL]
    assert form["apikey"] == [FAKE_SAB_API_KEY]


async def test_safe_reads_retry_with_bounded_backoff() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, text="unavailable")
        return _json({"queue": {"slots": []}})

    client = _client(handler)
    queue = await client.queue()
    await client.aclose()
    assert attempts == 3
    assert queue.slots == []


async def test_writes_are_never_retried() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, text="unavailable")

    client = _client(handler)
    with pytest.raises(SabHttpError):
        await client.pause_queue()
    await client.aclose()
    assert attempts == 1


async def test_redirect_is_refused_without_location_leak() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            302,
            headers={"location": f"http://redirect.invalid/?apikey={FAKE_SAB_API_KEY}"},
        )

    client = _client(handler)
    with pytest.raises(SabRedirectError) as error:
        await client.queue()
    await client.aclose()
    assert attempts == 1
    assert FAKE_SAB_API_KEY not in str(error.value)


async def test_timeout_exception_is_sanitized() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"timed out for http://sab/api?apikey={FAKE_SAB_API_KEY}")

    client = _client(handler)
    with pytest.raises(SabTimeoutError) as error:
        await client.queue()
    await client.aclose()
    assert FAKE_SAB_API_KEY not in str(error.value)
    assert "http" not in str(error.value)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (httpx.ConnectError("connection refused"), SabTransportError),
        (httpx.ConnectTimeout("connect timeout"), SabTimeoutError),
        (httpx.ReadTimeout("read timeout"), SabTimeoutError),
        (httpx.WriteTimeout("write timeout"), SabTimeoutError),
        (httpx.PoolTimeout("pool timeout"), SabTimeoutError),
    ],
)
async def test_transport_failures_are_sanitized(
    error: Exception, expected: type[Exception]
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise error

    client = _client(handler)
    with pytest.raises(expected) as captured:
        await client.version()
    await client.aclose()
    assert "sab:8080" not in str(captured.value)
    assert FAKE_SAB_API_KEY not in str(captured.value)


async def test_http_auth_and_server_errors_are_bounded() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, text="API Key Incorrect")

    client = _client(handler)
    with pytest.raises(SabHttpError) as captured:
        await client.queue()
    await client.aclose()
    assert attempts == 1
    assert captured.value.status_code == 401
    assert "API Key Incorrect" not in str(captured.value)

    attempts = 0

    def server_error(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, text="unavailable")

    client = _client(server_error)
    with pytest.raises(SabHttpError):
        await client.queue()
    await client.aclose()
    assert attempts == 3


def _body_handler(_request: httpx.Request, body: bytes) -> httpx.Response:
    return httpx.Response(200, content=body)


def _payload_handler(_request: httpx.Request, payload: dict[str, object]) -> httpx.Response:
    return _json(payload)


async def test_html_and_truncated_json_are_malformed() -> None:
    for body in (b"<html>not json</html>", b'{"queue": {"slots": ['):
        client = _client(partial(_body_handler, body=body))
        with pytest.raises(SabMalformedResponseError):
            await client.queue()
        await client.aclose()


async def test_unexpected_shapes_and_missing_fields_are_malformed() -> None:
    payloads: list[dict[str, object]] = [
        {"queue": None},
        {"queue": {"slots": [{"status": "Queued"}]}},
        {"version": None},
    ]
    for payload in payloads:
        client = _client(partial(_payload_handler, payload=payload))
        with pytest.raises(SabMalformedResponseError):
            await client.queue()
        await client.aclose()


async def test_reads_recover_after_a_transient_outage() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts <= 3:
            raise httpx.ConnectError("temporary outage")
        return _json({"version": "5.1.3"})

    client = _client(handler)
    with pytest.raises(SabTransportError):
        await client.version()
    recovered = await client.version()
    await client.aclose()
    assert recovered.version == "5.1.3"
    assert attempts == 4


async def test_backend_response_size_is_bounded() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _json({"queue": {"slots": [], "padding": "x" * 200}})

    client = _client(handler, max_response_bytes=64)
    with pytest.raises(SabMalformedResponseError, match="limit"):
        await client.queue()
    await client.aclose()


async def test_500_item_history_response_is_accepted() -> None:
    slots = [
        {
            "nzo_id": f"nzo-{index}",
            "name": f"Synthetic {index}",
            "status": "Completed",
            "bytes": 1,
            "downloaded": 1,
            "completed": 1,
            "time_added": 1,
        }
        for index in range(500)
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        return _json({"history": {"noofslots": 500, "slots": slots}})

    client = _client(handler)
    history = await client.history(limit=500)
    await client.aclose()
    assert len(history.slots) == 500


async def test_malformed_response() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>error</html>")

    client = _client(handler)
    with pytest.raises(SabMalformedResponseError):
        await client.queue()
    await client.aclose()


async def test_sab_level_error_is_sanitized() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _json(
            {
                "status": False,
                "error": f"bad request to {FAKE_INDEXER_URL} apikey={FAKE_SAB_API_KEY}",
            }
        )

    client = _client(handler)
    with pytest.raises(SabApiError) as error:
        await client.queue()
    await client.aclose()
    assert FAKE_INDEXER_URL not in str(error.value)
    assert FAKE_SAB_API_KEY not in str(error.value)


async def test_sab_api_key_error_is_sanitized() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _json({"status": False, "error": "API Key Incorrect"})

    client = _client(handler)
    with pytest.raises(SabApiError, match="API Key Incorrect"):
        await client.queue()
    await client.aclose()


async def test_priority_category_switch_and_speedlimit() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        form = _form(request)
        if form.get("name") == ["priority"]:
            return _json({"position": 4})
        if form.get("mode") == ["switch"]:
            return _json({"result": {"position": 0, "priority": 0}})
        return _json({"status": True})

    client = _client(handler)
    priority = await client.set_priority(["nzo-1"], 2)
    normal = await client.set_priority(["nzo-1"], 0)
    category = await client.change_category(["nzo-1"], "tv")
    switched = await client.switch("nzo-1", 0)
    speed = await client.set_speedlimit(400)
    await client.set_speedlimit(0)
    await client.aclose()
    assert priority.position == 4
    assert normal.position == 4
    assert _form(captured[1])["value2"] == ["0"]
    assert category.status is True
    assert switched.result["position"] == 0
    assert speed.status is True
    assert _form(captured[4])["value"] == ["400K"]
    assert _form(captured[5])["value"] == ["0"]


async def test_delete_history_and_mark_completed() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _json({"status": True})

    client = _client(handler)
    await client.delete_history(["nzo-1"], archive=False, delete_files=True)
    await client.mark_completed(["nzo-1"])
    await client.aclose()
    first = _form(captured[0])
    assert first["mode"] == ["history"]
    assert first["name"] == ["delete"]
    assert first["archive"] == ["0"]
    assert first["del_files"] == ["1"]
    second = _form(captured[1])
    assert second["name"] == ["mark_as_completed"]


async def test_add_result_without_ids_is_accepted_but_unresolved() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _json({"status": True, "nzo_ids": []})

    client = _client(handler)
    result = await client.add_url(FAKE_INDEXER_URL)
    await client.aclose()
    assert result.status is True
    assert result.nzo_ids == []


async def test_repr_does_not_expose_key() -> None:
    client = _client(lambda _request: _json({"version": "5.1.3"}))
    assert FAKE_SAB_API_KEY not in repr(client)
    await client.aclose()


async def test_add_url_rejects_non_http() -> None:
    client = _client(lambda _request: _json({"status": True, "nzo_ids": ["x"]}))
    with pytest.raises(ValueError, match="http"):
        await client.add_url("file:///etc/passwd")
    await client.aclose()
