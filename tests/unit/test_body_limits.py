from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from nzbget_sab_bridge.main import BadRequest, RequestTooLarge, read_bounded_body


class _Request:
    def __init__(self, headers: dict[str, str], chunks: list[bytes]) -> None:
        self.headers = headers
        self._chunks = chunks

    async def stream(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


async def test_missing_content_length_is_stream_enforced() -> None:
    request = _Request({}, [b"a" * 4, b"b" * 4])
    with pytest.raises(RequestTooLarge):
        await read_bounded_body(request, 6)  # type: ignore[arg-type]


async def test_misleading_small_content_length_is_stream_enforced() -> None:
    request = _Request({"content-length": "1"}, [b"a" * 4, b"b" * 4])
    with pytest.raises(RequestTooLarge):
        await read_bounded_body(request, 6)  # type: ignore[arg-type]


async def test_declared_oversize_is_rejected_before_streaming() -> None:
    request = _Request({"content-length": "100"}, [b"short"])
    with pytest.raises(RequestTooLarge):
        await read_bounded_body(request, 10)  # type: ignore[arg-type]


async def test_malformed_and_negative_content_lengths_are_bad_requests() -> None:
    for value in ("not-a-number", "-1"):
        request = _Request({"content-length": value}, [b"x"])
        with pytest.raises(BadRequest):
            await read_bounded_body(request, 10)  # type: ignore[arg-type]


async def test_exact_limit_and_smaller_declared_body_are_accepted() -> None:
    exact = _Request({}, [b"1234", b"56"])
    assert await read_bounded_body(exact, 6) == b"123456"  # type: ignore[arg-type]

    smaller = _Request({"content-length": "1"}, [b"abc"])
    assert await read_bounded_body(smaller, 6) == b"abc"  # type: ignore[arg-type]
