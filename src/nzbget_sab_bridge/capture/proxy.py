"""Transparent, allowlisted capture forwarding to a real NZBGet backend."""

from __future__ import annotations

import base64
import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl

import httpx
from fastapi import Request

from ..rpc import KNOWN_RPC_METHODS
from ..urls import join_base_path
from .recorder import CaptureError, CaptureRecorder
from .sanitizer import request_metadata, response_metadata

logger = logging.getLogger("nzbget_sab_bridge.capture")

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_DO_NOT_FORWARD_RESPONSE = _HOP_BY_HOP | {
    "authorization",
    "content-encoding",
    "content-length",
    "content-md5",
    "cookie",
    "date",
    "server",
    "set-cookie",
    "www-authenticate",
}
_QUERY_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


@dataclass(frozen=True, slots=True)
class ProxyResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes
    capture_status: str


class CaptureProxy:
    def __init__(
        self,
        backend_url: str,
        *,
        backend_username: str,
        backend_password: str,
        recorder: CaptureRecorder,
        connect_timeout: float,
        read_timeout: float,
        write_timeout: float,
        pool_timeout: float,
        tls_verify: bool,
        inspect_limit: int,
        max_response_bytes: int,
        xmlrpc_enabled: bool,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._backend_url = backend_url
        self._recorder = recorder
        self._inspect_limit = inspect_limit
        self._max_response_bytes = max_response_bytes
        self._xmlrpc_enabled = xmlrpc_enabled
        self._backend_auth: str | None = None
        if backend_username or backend_password:
            token = base64.b64encode(f"{backend_username}:{backend_password}".encode()).decode(
                "ascii"
            )
            self._backend_auth = f"Basic {token}"
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
            headers={"Accept-Encoding": "identity"},
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def allows_path(self, path: str) -> bool:
        canonical = path.rstrip("/") or "/"
        if canonical in {"/jsonrpc", "/jsonprpc"}:
            return True
        if self._xmlrpc_enabled and canonical == "/xmlrpc":
            return True
        return self._path_method(path) is not None

    def transport_name(self, path: str) -> str:
        canonical = path.rstrip("/") or "/"
        first = canonical.lstrip("/").split("/", 1)[0]
        return first if first in {"jsonrpc", "jsonprpc", "xmlrpc"} else "unknown"

    @staticmethod
    def _path_method(path: str) -> str | None:
        segments = [segment for segment in path.strip("/").split("/") if segment]
        if len(segments) != 2 or segments[0].lower() != "jsonrpc":
            return None
        method = segments[1].lower()
        return method if method in KNOWN_RPC_METHODS else None

    async def forward(self, request: Request, body: bytes) -> ProxyResponse:
        path = request.url.path
        transport = self.transport_name(path)
        sequence = self._recorder.next_sequence()
        started = time.perf_counter()
        request_meta = self._safe_request_metadata(
            body, protocol=transport, content_type=request.headers.get("content-type")
        )
        path_method = self._path_method(path)
        if path_method is not None and not request_meta.get("method"):
            request_meta["method"] = path_method
        query_keys = sorted(
            {
                key
                for key, _value in parse_qsl(request.url.query, keep_blank_values=True)
                if _QUERY_KEY_RE.fullmatch(key)
            }
        )[:20]

        target = join_base_path(self._backend_url, path)
        if request.url.query:
            target = f"{target}?{request.url.query}"

        headers: dict[str, str] = {"Accept-Encoding": "identity"}
        for name in ("accept", "content-type", "content-encoding"):
            value = request.headers.get(name)
            if value:
                headers[name] = value
        if self._backend_auth:
            headers["Authorization"] = self._backend_auth

        self._client.cookies.clear()
        try:
            response = await self._client.request(
                request.method,
                target,
                headers=headers,
                content=body,
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            return self._backend_failure(
                sequence,
                transport,
                request.method,
                path,
                request_meta,
                query_keys,
                started,
                status_code=504,
                error="capture_backend_timeout",
            )
        except httpx.TransportError:
            return self._backend_failure(
                sequence,
                transport,
                request.method,
                path,
                request_meta,
                query_keys,
                started,
                status_code=502,
                error="capture_backend_unavailable",
            )
        except httpx.HTTPError:
            return self._backend_failure(
                sequence,
                transport,
                request.method,
                path,
                request_meta,
                query_keys,
                started,
                status_code=502,
                error="capture_backend_error",
            )
        finally:
            # Never keep backend cookies. httpx otherwise stores Set-Cookie and
            # replays it on later requests, which would violate capture's
            # separate-auth rule.
            self._client.cookies.clear()

        duration_ms = _elapsed_ms(started)
        if 300 <= response.status_code < 400:
            logger.warning(
                "capture backend redirect refused",
                extra={
                    "sequence": sequence,
                    "transport": transport,
                    "http_status": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
            capture_status = self._write_record(
                sequence=sequence,
                transport=transport,
                method=request.method,
                path=path,
                query_keys=query_keys,
                request_meta=request_meta,
                response_meta={
                    "parse": "redirect_refused",
                    "body_bytes": len(response.content),
                },
                status_code=response.status_code,
                duration_ms=duration_ms,
                error="redirect_refused",
            )
            return ProxyResponse(
                status_code=502,
                headers={
                    "content-type": "application/json",
                    "x-bridge-capture": capture_status,
                },
                body=b'{"error":"capture_backend_redirect_refused"}',
                capture_status=capture_status,
            )

        if len(response.content) > self._max_response_bytes:
            logger.error(
                "capture backend response exceeded safety limit",
                extra={
                    "sequence": sequence,
                    "transport": transport,
                    "http_status": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
            capture_status = self._write_record(
                sequence=sequence,
                transport=transport,
                method=request.method,
                path=path,
                query_keys=query_keys,
                request_meta=request_meta,
                response_meta={
                    "parse": "too_large",
                    "body_bytes": len(response.content),
                },
                status_code=response.status_code,
                duration_ms=duration_ms,
                error="response_too_large",
            )
            return ProxyResponse(
                status_code=502,
                headers={
                    "content-type": "application/json",
                    "x-bridge-capture": capture_status,
                },
                body=b'{"error":"capture_response_too_large"}',
                capture_status=capture_status,
            )

        response_headers = {
            name: value
            for name, value in response.headers.items()
            if name.lower() not in _DO_NOT_FORWARD_RESPONSE
        }
        response_meta = self._safe_response_metadata(
            response.content, content_type=response.headers.get("content-type")
        )
        capture_status = self._write_record(
            sequence=sequence,
            transport=transport,
            method=request.method,
            path=path,
            query_keys=query_keys,
            request_meta=request_meta,
            response_meta=response_meta,
            status_code=response.status_code,
            duration_ms=duration_ms,
            error=None,
        )
        response_headers["x-bridge-capture"] = capture_status
        return ProxyResponse(
            status_code=response.status_code,
            headers=response_headers,
            body=response.content,
            capture_status=capture_status,
        )

    async def ready(self) -> bool:
        try:
            if not self._recorder.check_ready():
                return False
            headers = {
                "Content-Type": "application/json",
                "Accept-Encoding": "identity",
            }
            if self._backend_auth:
                headers["Authorization"] = self._backend_auth
            self._client.cookies.clear()
            response = await self._client.post(
                join_base_path(self._backend_url, "/jsonrpc"),
                headers=headers,
                content=b'{"method":"version","params":[],"id":"readyz"}',
                follow_redirects=False,
            )
        except (httpx.HTTPError, OSError, CaptureError):
            return False
        finally:
            self._client.cookies.clear()
        return 200 <= response.status_code < 300

    def _safe_request_metadata(
        self,
        body: bytes,
        *,
        protocol: str,
        content_type: str | None,
    ) -> dict[str, Any]:
        try:
            return request_metadata(
                body,
                protocol=protocol,
                content_type=content_type,
                inspect_limit=self._inspect_limit,
            )
        except Exception:
            logger.exception("capture request inspection failed")
            return {"parse": "error", "reason": "inspection_failed"}

    def _safe_response_metadata(self, body: bytes, *, content_type: str | None) -> dict[str, Any]:
        try:
            return response_metadata(
                body,
                content_type=content_type,
                inspect_limit=self._inspect_limit,
            )
        except Exception:
            logger.exception("capture response inspection failed")
            return {"parse": "error", "reason": "inspection_failed"}

    def _backend_failure(
        self,
        sequence: int,
        transport: str,
        method: str,
        path: str,
        request_meta: dict[str, Any],
        query_keys: list[str],
        started: float,
        *,
        status_code: int,
        error: str,
    ) -> ProxyResponse:
        duration_ms = _elapsed_ms(started)
        logger.warning(
            "capture backend request failed",
            extra={
                "sequence": sequence,
                "transport": transport,
                "http_status": status_code,
                "duration_ms": duration_ms,
            },
        )
        capture_status = self._write_record(
            sequence=sequence,
            transport=transport,
            method=method,
            path=path,
            query_keys=query_keys,
            request_meta=request_meta,
            response_meta=None,
            status_code=status_code,
            duration_ms=duration_ms,
            error=error,
        )
        return ProxyResponse(
            status_code=status_code,
            headers={
                "content-type": "application/json",
                "x-bridge-capture": capture_status,
            },
            body=f'{{"error":"{error}"}}'.encode("ascii"),
            capture_status=capture_status,
        )

    def _write_record(
        self,
        *,
        sequence: int,
        transport: str,
        method: str,
        path: str,
        query_keys: list[str],
        request_meta: dict[str, Any],
        response_meta: dict[str, Any] | None,
        status_code: int,
        duration_ms: int,
        error: str | None,
    ) -> str:
        record = {
            "transport": transport,
            "http_method": method,
            "path": path,
            "query_keys": query_keys,
            "request": request_meta,
            "response": response_meta,
            "status": status_code,
            "duration_ms": duration_ms,
            "error": error,
        }
        try:
            self._recorder.write(record, sequence=sequence)
        except Exception:
            # A recorder bug or disk failure must never change the backend
            # response or cause the request to be retried.
            logger.exception(
                "capture recorder failed; backend request was not replayed",
                extra={"sequence": sequence, "transport": transport},
            )
            return "failed"
        return "ok"


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))
