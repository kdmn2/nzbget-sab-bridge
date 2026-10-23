"""Typed async client for the SABnzbd HTTP API.

Security properties:
* the API key is sent in POST form/multipart fields, never in the URL;
* redirects are never followed;
* only safe, idempotent reads are retried;
* exceptions never retain the request URL or original transport exception.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ValidationError

from ..urls import join_base_path, normalize_base_url
from .errors import (
    SabApiError,
    SabHttpError,
    SabMalformedResponseError,
    SabRedirectError,
    SabTimeoutError,
    SabTransportError,
    sanitize_api_error,
)
from .models import (
    SabAddResult,
    SabBasicResult,
    SabCategories,
    SabHistory,
    SabPriorityResult,
    SabQueue,
    SabServerStats,
    SabStatus,
    SabSwitchResult,
    SabVersion,
)

Sleep = Callable[[float], Awaitable[None]]
_READ_RETRIES = 2
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class SabClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        connect_timeout: float = 5.0,
        read_timeout: float = 30.0,
        write_timeout: float = 30.0,
        pool_timeout: float = 5.0,
        tls_verify: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._base_url = normalize_base_url(base_url, strip_api_suffix=True)
        self._api_url = join_base_path(self._base_url, "/api")
        self._api_key = api_key
        self._retry_sleep = retry_sleep
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=connect_timeout,
                read=read_timeout,
                write=write_timeout,
                pool=pool_timeout,
            ),
            follow_redirects=False,
            verify=tls_verify,
            trust_env=False,
            transport=transport,
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    def __repr__(self) -> str:
        return f"SabClient(api_url={self._api_url!r}, api_key=<redacted>)"

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> SabClient:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.aclose()

    async def version(self) -> SabVersion:
        payload = await self._request_json({"mode": "version"}, read=True)
        return self._validate(payload, SabVersion)

    async def queue(
        self,
        *,
        start: int = 0,
        limit: int = 500,
        search: str | None = None,
    ) -> SabQueue:
        params: dict[str, Any] = {"mode": "queue", "start": start, "limit": limit}
        if search:
            params["search"] = search
        payload = await self._request_json(params, read=True)
        return self._validate(payload, SabQueue, envelope="queue")

    async def history(
        self,
        *,
        start: int = 0,
        limit: int = 500,
        archive: bool = False,
        failed_only: bool = False,
        search: str | None = None,
    ) -> SabHistory:
        params: dict[str, Any] = {
            "mode": "history",
            "start": start,
            "limit": limit,
            "archive": 1 if archive else 0,
            "failed_only": 1 if failed_only else 0,
        }
        if search:
            params["search"] = search
        payload = await self._request_json(params, read=True)
        return self._validate(payload, SabHistory, envelope="history")

    async def status(self) -> SabStatus:
        payload = await self._request_json({"mode": "status", "skip_dashboard": 1}, read=True)
        return self._validate(payload, SabStatus, envelope="status")

    async def categories(self) -> SabCategories:
        payload = await self._request_json({"mode": "get_cats"}, read=True)
        return self._validate(payload, SabCategories)

    async def server_stats(self) -> SabServerStats:
        payload = await self._request_json({"mode": "server_stats"}, read=True)
        return self._validate(payload, SabServerStats)

    async def add_file(
        self,
        content: bytes,
        filename: str,
        *,
        nzbname: str | None = None,
        category: str | None = None,
        priority: int | None = None,
        pp: int | None = None,
        script: str | None = None,
        password: str | None = None,
    ) -> SabAddResult:
        if not content:
            raise SabMalformedResponseError("empty NZB payload")
        if not filename:
            raise SabMalformedResponseError("missing NZB filename")
        data: dict[str, Any] = {
            "mode": "addfile",
            "output": "json",
            "apikey": self._api_key,
        }
        for key, value in (
            ("nzbname", nzbname),
            ("cat", category),
            ("priority", priority),
            ("pp", pp),
            ("script", script),
            ("password", password),
        ):
            if value is not None and value != "":
                data[key] = value
        files = {"name": (filename, content, "application/xml")}
        payload = await self._request_json(data, read=False, files=files)
        return self._add_result(payload)

    async def add_url(
        self,
        url: str,
        *,
        nzbname: str | None = None,
        category: str | None = None,
        priority: int | None = None,
        pp: int | None = None,
        script: str | None = None,
        password: str | None = None,
    ) -> SabAddResult:
        if not self._is_http_url(url):
            raise ValueError("SABnzbd add URL must use http or https")
        data: dict[str, Any] = {
            "mode": "addurl",
            "output": "json",
            "apikey": self._api_key,
            "name": url,
        }
        for key, value in (
            ("nzbname", nzbname),
            ("cat", category),
            ("priority", priority),
            ("pp", pp),
            ("script", script),
            ("password", password),
        ):
            if value is not None and value != "":
                data[key] = value
        payload = await self._request_json(data, read=False)
        return self._add_result(payload)

    async def pause_queue(self) -> SabBasicResult:
        return await self._basic({"mode": "pause"})

    async def resume_queue(self) -> SabBasicResult:
        return await self._basic({"mode": "resume"})

    async def pause_jobs(self, nzo_ids: list[str]) -> SabBasicResult:
        return await self._basic(
            {"mode": "queue", "name": "pause", "value": self._join_ids(nzo_ids)}
        )

    async def resume_jobs(self, nzo_ids: list[str]) -> SabBasicResult:
        return await self._basic(
            {"mode": "queue", "name": "resume", "value": self._join_ids(nzo_ids)}
        )

    async def delete_jobs(
        self, nzo_ids: list[str], *, delete_files: bool = False
    ) -> SabBasicResult:
        return await self._basic(
            {
                "mode": "queue",
                "name": "delete",
                "value": self._join_ids(nzo_ids),
                "del_files": 1 if delete_files else 0,
            }
        )

    async def set_priority(self, nzo_ids: list[str], priority: int) -> SabPriorityResult:
        payload = await self._request_json(
            {
                "mode": "queue",
                "name": "priority",
                "value": self._join_ids(nzo_ids),
                "value2": priority,
            },
            read=False,
        )
        return self._validate(payload, SabPriorityResult)

    async def change_category(self, nzo_ids: list[str], category: str) -> SabBasicResult:
        return await self._basic(
            {
                "mode": "change_cat",
                "value": self._join_ids(nzo_ids),
                "value2": category,
            }
        )

    async def switch(self, nzo_id: str, value2: str | int) -> SabSwitchResult:
        payload = await self._request_json(
            {"mode": "switch", "value": nzo_id, "value2": value2}, read=False
        )
        return self._validate(payload, SabSwitchResult)

    async def retry(self, nzo_ids: list[str]) -> SabAddResult:
        payload = await self._request_json(
            {"mode": "retry", "value": self._join_ids(nzo_ids)}, read=False
        )
        return self._add_result(payload)

    async def delete_history(
        self, nzo_ids: list[str], *, archive: bool = True, delete_files: bool = False
    ) -> SabBasicResult:
        return await self._basic(
            {
                "mode": "history",
                "name": "delete",
                "value": self._join_ids(nzo_ids),
                "archive": 1 if archive else 0,
                "del_files": 1 if delete_files else 0,
            }
        )

    async def mark_completed(self, nzo_ids: list[str]) -> SabBasicResult:
        return await self._basic(
            {
                "mode": "history",
                "name": "mark_as_completed",
                "value": self._join_ids(nzo_ids),
            }
        )

    async def set_speedlimit(self, kilobytes_per_second: int) -> SabBasicResult:
        if kilobytes_per_second < 0:
            raise ValueError("speed limit must not be negative")
        value = "0" if kilobytes_per_second == 0 else f"{kilobytes_per_second}K"
        return await self._basic({"mode": "config", "name": "speedlimit", "value": value})

    async def _basic(self, params: Mapping[str, Any]) -> SabBasicResult:
        payload = await self._request_json(params, read=False)
        return self._validate(payload, SabBasicResult)

    async def _request_json(
        self,
        params: Mapping[str, Any],
        *,
        read: bool,
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
    ) -> dict[str, Any]:
        attempts = _READ_RETRIES + 1 if read else 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return await self._attempt(params, files=files)
            except SabApiError:
                raise
            except SabMalformedResponseError:
                raise
            except (SabTimeoutError, SabTransportError, SabHttpError) as exc:
                last_error = exc
                retryable = isinstance(exc, (SabTimeoutError, SabTransportError)) or (
                    isinstance(exc, SabHttpError)
                    and (exc.status_code == 429 or 500 <= exc.status_code <= 599)
                )
                if not read or not retryable or attempt == attempts - 1:
                    raise
                await self._retry_sleep(0.2 * (2**attempt))
        if last_error is not None:
            raise last_error
        raise SabTransportError()

    async def _attempt(
        self,
        params: Mapping[str, Any],
        *,
        files: Mapping[str, tuple[str, bytes, str]] | None,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {"output": "json", "apikey": self._api_key}
        data.update(params)
        try:
            if files is None:
                response = await self._client.post(self._api_url, data=data)
            else:
                response = await self._client.post(self._api_url, data=data, files=files)
        except httpx.TimeoutException:
            raise SabTimeoutError() from None
        except httpx.TransportError:
            raise SabTransportError() from None
        except httpx.HTTPError:
            raise SabTransportError() from None
        finally:
            self._client.cookies.clear()

        if 300 <= response.status_code < 400:
            raise SabRedirectError()
        if response.status_code < 200 or response.status_code >= 300:
            raise SabHttpError(response.status_code)

        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            raise SabMalformedResponseError() from None
        if not isinstance(payload, dict):
            raise SabMalformedResponseError("unexpected response shape")
        if payload.get("status") is False:
            raise SabApiError(sanitize_api_error(payload.get("error")))
        return payload

    def _validate(
        self,
        payload: dict[str, Any],
        model: type[_ModelT],
        *,
        envelope: str | None = None,
    ) -> _ModelT:
        candidate: Any = payload
        if envelope is not None:
            candidate = payload.get(envelope)
        if not isinstance(candidate, dict):
            raise SabMalformedResponseError("unexpected response shape")
        try:
            return model.model_validate(candidate)
        except ValidationError:
            raise SabMalformedResponseError("unexpected response shape") from None

    def _add_result(self, payload: dict[str, Any]) -> SabAddResult:
        result = self._validate(payload, SabAddResult)
        if result.status is not True:
            raise SabApiError(sanitize_api_error(result.error))
        if not result.nzo_ids and result.nzo_id:
            result.nzo_ids = [result.nzo_id]
        # A successful add with no ID is an accepted-but-unresolved outcome.
        # The caller must not turn that into a definite rejection or retry.
        return result

    @staticmethod
    def _join_ids(nzo_ids: list[str]) -> str:
        cleaned = [item for item in nzo_ids if item]
        if not cleaned:
            raise ValueError("at least one job id is required")
        return ",".join(cleaned)

    @staticmethod
    def _is_http_url(value: str) -> bool:
        if not value or len(value) > 8192:
            return False
        try:
            parts = urlsplit(value)
        except ValueError:
            return False
        return parts.scheme.lower() in {"http", "https"} and bool(parts.hostname)
