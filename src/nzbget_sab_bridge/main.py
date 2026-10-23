"""FastAPI application factory and HTTP routes."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from .auth import auth_challenge, is_authenticated
from .capture.proxy import CaptureProxy
from .capture.recorder import CaptureError, CaptureRecorder
from .config import Settings
from .diagnostics import sanitize_capture_path
from .getargs import GetArgumentError, parse_get_params
from .handlers import dispatch
from .logging import configure_logging
from .rpc import (
    IMPLEMENTED_MUTATION_METHODS,
    IMPLEMENTED_READ_METHODS,
    KNOWN_RPC_METHODS,
    InvalidAction,
    InvalidParams,
    Protocol,
    RpcError,
    RpcParseError,
    RpcRequest,
    error_response,
    parse_rpc_request,
    peek_jsonp_callback,
    request_error_response,
    success_response,
)
from .sab.client import SabClient
from .storage.idmap import IdMap, IdMapError

logger = logging.getLogger("nzbget_sab_bridge")

_JSON_CONTENT_TYPES = {"application/json"}
_JSONP_CONTENT_TYPES = {
    "application/json",
    "application/javascript",
    "text/javascript",
}
_HEALTH_PATHS = {"/healthz", "/readyz"}


class BadRequest(Exception):
    pass


class RequestTooLarge(Exception):
    pass


@dataclass
class Runtime:
    settings: Settings
    idmap: IdMap | None = None
    sab: SabClient | None = None
    capture: CaptureProxy | None = None
    startup_errors: dict[str, str] = field(default_factory=dict)
    mutation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def create_app(
    settings: Settings | None = None,
    *,
    sab_transport: httpx.AsyncBaseTransport | None = None,
    capture_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    resolved = settings or Settings.from_env()
    runtime = Runtime(settings=resolved)
    app = FastAPI(
        title="nzbget-sab-bridge",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.runtime = runtime
    app.state.sab_transport = sab_transport
    app.state.capture_transport = capture_transport

    @app.middleware("http")
    async def request_logging(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "unhandled request failure",
                extra={"component": "http", "duration_ms": _elapsed_ms(started)},
            )
            response = JSONResponse(status_code=500, content={"error": "internal_error"})
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if request.url.path not in _HEALTH_PATHS:
            logger.info(
                "request",
                extra={
                    "component": "http",
                    "http_status": response.status_code,
                    "duration_ms": _elapsed_ms(started),
                },
            )
        if runtime.settings.is_capture and response.status_code in {
            307,
            308,
            404,
            405,
            415,
        }:
            matched = runtime.capture is not None and runtime.capture.allows_path(request.url.path)
            logger.warning(
                "capture route diagnostic",
                extra={
                    "component": "capture_route",
                    "http_method": request.method,
                    "path": sanitize_capture_path(request.url.path),
                    "http_status": response.status_code,
                    "matched_capture_route": matched,
                },
            )
        elif not runtime.settings.is_capture:
            rpc_method = getattr(request.state, "rpc_method", None)
            if response.status_code in {400, 404, 405, 415} or rpc_method in {
                "history",
                "config",
                "configtemplates",
                "version",
            }:
                logger.warning(
                    "bridge route diagnostic",
                    extra={
                        "component": "bridge_route",
                        "http_method": request.method,
                        "path": sanitize_capture_path(request.url.path),
                        "http_status": response.status_code,
                        "matched_route": request.scope.get("route") is not None,
                        "query_present": bool(request.url.query),
                        "rpc_method": rpc_method,
                    },
                )
        return response

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse(content={"status": "ok"})

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        components = await _readiness(runtime)
        ready = all(value in {"ok", "disabled"} for value in components.values())
        return JSONResponse(
            status_code=200 if ready else 503,
            content={
                "status": "ready" if ready else "not_ready",
                "mode": runtime.settings.mode,
                "components": components,
            },
        )

    @app.api_route("/jsonrpc", methods=["GET", "POST"])
    async def jsonrpc(request: Request) -> Response:
        return await _handle_rpc(request, "jsonrpc", runtime)

    @app.api_route("/jsonrpc/", methods=["GET", "POST"])
    async def jsonrpc_slash(request: Request) -> Response:
        if runtime.settings.is_capture:
            return await _handle_capture(request, runtime)
        return await _handle_rpc(request, "jsonrpc", runtime)

    @app.api_route("/jsonrpc/{method}", methods=["GET", "POST"])
    async def jsonrpc_method(request: Request, method: str) -> Response:
        if method.lower() not in KNOWN_RPC_METHODS:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        if runtime.settings.is_capture:
            return await _handle_capture(request, runtime)
        challenge = _challenge_if_needed(request, runtime.settings)
        if challenge is not None:
            return challenge
        if request.method != "GET":
            return JSONResponse(
                status_code=405,
                headers={"Allow": "GET"},
                content={"error": "method_not_allowed"},
            )
        if method.lower() not in IMPLEMENTED_READ_METHODS:
            return JSONResponse(
                status_code=405,
                headers={"Allow": "POST"},
                content={"error": "unsafe_method_for_get"},
            )
        return await _handle_get_method(request, method, runtime)

    @app.api_route("/jsonprpc", methods=["GET", "POST"])
    async def jsonprpc(request: Request) -> Response:
        return await _handle_rpc(request, "jsonprpc", runtime)

    @app.api_route("/xmlrpc", methods=["GET", "POST"])
    async def xmlrpc(request: Request) -> Response:
        challenge = _challenge_if_needed(request, runtime.settings)
        if challenge is not None:
            return challenge
        if runtime.settings.is_capture:
            if not runtime.settings.capture_xmlrpc:
                return JSONResponse(status_code=404, content={"error": "not_found"})
            return await _handle_capture(request, runtime)
        return JSONResponse(status_code=501, content={"error": "xmlrpc_not_implemented"})

    return app


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    runtime: Runtime = app.state.runtime
    settings = runtime.settings
    configure_logging(settings.log_level, settings.log_format)
    logger.info(
        "starting bridge",
        extra={"component": "startup", "mode": settings.mode},
    )
    for warning in settings.startup_warnings:
        logger.warning(warning, extra={"component": "startup"})

    if settings.mode == "bridge":
        try:
            idmap = IdMap(settings.data_dir / "bridge.sqlite3")
            idmap.initialize()
            runtime.idmap = idmap
        except IdMapError as exc:
            runtime.startup_errors["storage"] = type(exc).__name__
            logger.error("storage unavailable: %s", exc, extra={"component": "storage"})

        try:
            runtime.sab = SabClient(
                settings.sabnzbd_url or "",
                (settings.sabnzbd_api_key.get_secret_value() if settings.sabnzbd_api_key else ""),
                connect_timeout=settings.http_connect_timeout,
                read_timeout=settings.http_read_timeout,
                write_timeout=settings.http_write_timeout,
                pool_timeout=settings.http_pool_timeout,
                max_response_bytes=settings.sab_max_response_bytes,
                tls_verify=settings.tls_verify,
                transport=app.state.sab_transport,
            )
        except Exception as exc:
            runtime.startup_errors["sab"] = type(exc).__name__
            logger.error(
                "SABnzbd client unavailable (%s)",
                type(exc).__name__,
                extra={"component": "sab"},
            )
    else:
        recorder = CaptureRecorder(
            settings.capture_dir,
            max_records=settings.capture_max_records,
            max_total_bytes=settings.capture_max_total_bytes,
            max_record_bytes=settings.capture_max_record_bytes,
            retention_days=settings.capture_retention_days,
        )
        try:
            recorder.ensure_ready()
        except CaptureError as exc:
            runtime.startup_errors["recorder"] = type(exc).__name__
            logger.error("capture recorder unavailable: %s", exc, extra={"component": "capture"})
        try:
            runtime.capture = CaptureProxy(
                settings.capture_nzbget_url or "",
                backend_username=settings.capture_backend_username,
                backend_password=settings.capture_backend_password,
                recorder=recorder,
                connect_timeout=settings.http_connect_timeout,
                read_timeout=settings.http_read_timeout,
                write_timeout=settings.http_write_timeout,
                pool_timeout=settings.http_pool_timeout,
                tls_verify=settings.tls_verify,
                inspect_limit=settings.capture_inspect_max_bytes,
                max_response_bytes=settings.capture_max_response_bytes,
                xmlrpc_enabled=settings.capture_xmlrpc,
                transport=app.state.capture_transport,
            )
        except Exception as exc:
            runtime.startup_errors["capture"] = type(exc).__name__
            logger.error(
                "capture proxy unavailable (%s)",
                type(exc).__name__,
                extra={"component": "capture"},
            )

    try:
        yield
    finally:
        if runtime.sab is not None:
            await runtime.sab.aclose()
        if runtime.capture is not None:
            await runtime.capture.aclose()
        logger.info("bridge stopped", extra={"component": "shutdown"})


async def _handle_rpc(request: Request, protocol: Protocol, runtime: Runtime) -> Response:
    settings = runtime.settings
    challenge = _challenge_if_needed(request, settings)
    if challenge is not None:
        return challenge
    if settings.is_capture:
        return await _handle_capture(request, runtime)
    if request.method != "POST":
        return JSONResponse(
            status_code=405,
            headers={"Allow": "POST"},
            content={"error": "method_not_allowed"},
        )
    media_type = "application/javascript" if protocol == "jsonprpc" else "application/json"
    if not _content_type_allowed(request, protocol):
        return _rpc_response(
            error_response(RpcError("Unsupported media type")),
            status_code=415,
            media_type=media_type,
        )
    try:
        body = await read_bounded_body(request, settings.request_max_bytes)
    except RequestTooLarge:
        return _rpc_response(
            error_response(RpcError("Request too large")),
            status_code=413,
            media_type=media_type,
        )
    except BadRequest:
        return _rpc_response(
            error_response(RpcError("Bad request")),
            status_code=400,
            media_type=media_type,
        )

    callback = peek_jsonp_callback(body) if protocol == "jsonprpc" else None
    try:
        rpc_request = parse_rpc_request(body, protocol)
        request.state.rpc_method = rpc_request.method
    except RpcError as exc:
        return _rpc_response(
            error_response(exc, callback=callback),
            media_type=media_type,
        )
    except Exception:
        logger.exception("RPC parse failure", extra={"component": "rpc"})
        return _rpc_response(
            error_response(RpcParseError(), callback=callback),
            media_type=media_type,
        )

    if protocol == "jsonprpc" and rpc_request.method.lower() not in IMPLEMENTED_READ_METHODS:
        return _rpc_response(
            error_response(
                InvalidAction("JSON-P-RPC is read-only"),
                callback=rpc_request.callback,
            ),
            media_type=media_type,
        )

    try:
        if rpc_request.method.lower() in IMPLEMENTED_MUTATION_METHODS:
            async with runtime.mutation_lock:
                result = await dispatch(
                    rpc_request,
                    settings=settings,
                    idmap=runtime.idmap,
                    sab=runtime.sab,
                )
        else:
            result = await dispatch(
                rpc_request,
                settings=settings,
                idmap=runtime.idmap,
                sab=runtime.sab,
            )
    except RpcError as exc:
        return _rpc_response(request_error_response(exc, rpc_request), media_type=media_type)
    except Exception:
        logger.exception("RPC handler failure", extra={"component": "rpc"})
        return _rpc_response(
            request_error_response(RpcError("Internal error"), rpc_request),
            media_type=media_type,
        )
    return _rpc_response(success_response(rpc_request, result), media_type=media_type)


async def _handle_get_method(request: Request, method: str, runtime: Runtime) -> Response:
    challenge = _challenge_if_needed(request, runtime.settings)
    if challenge is not None:
        return challenge
    # NZBGet's GET form carries the method in the path and positional values in
    # the query. Parse supported arguments strictly; never log query values.
    request.state.rpc_method = method
    try:
        params = parse_get_params(method, request.url.query)
    except GetArgumentError as exc:
        return _rpc_response(
            error_response(InvalidParams(str(exc))),
            status_code=200,
        )
    rpc_request = RpcRequest(
        protocol="jsonrpc",
        method=method,
        params=params,
        callback=None,
        has_id=False,
        request_id=None,
    )
    try:
        result = await dispatch(
            rpc_request,
            settings=runtime.settings,
            idmap=runtime.idmap,
            sab=runtime.sab,
        )
    except RpcError as exc:
        return _rpc_response(request_error_response(exc, rpc_request))
    except Exception:
        logger.exception("RPC GET handler failure", extra={"component": "rpc"})
        return _rpc_response(request_error_response(RpcError("Internal error"), rpc_request))
    return _rpc_response(success_response(rpc_request, result))


async def _handle_capture(request: Request, runtime: Runtime) -> Response:
    settings = runtime.settings
    challenge = _challenge_if_needed(request, settings)
    if challenge is not None:
        return challenge
    if request.method not in {"GET", "POST"}:
        return JSONResponse(
            status_code=405,
            headers={"Allow": "GET, POST"},
            content={"error": "method_not_allowed"},
        )
    proxy = runtime.capture
    if proxy is None:
        return JSONResponse(status_code=503, content={"error": "capture_unavailable"})
    if not proxy.allows_path(request.url.path):
        return JSONResponse(status_code=404, content={"error": "not_found"})

    try:
        body = await read_bounded_body(request, settings.request_max_bytes)
    except RequestTooLarge:
        return JSONResponse(status_code=413, content={"error": "request_too_large"})
    except BadRequest:
        return JSONResponse(status_code=400, content={"error": "bad_request"})

    try:
        result = await proxy.forward(request, body)
    except Exception:
        logger.exception("capture forwarding failure", extra={"component": "capture"})
        return JSONResponse(status_code=502, content={"error": "capture_forward_failed"})
    return Response(
        content=result.body,
        status_code=result.status_code,
        headers=result.headers,
    )


def _challenge_if_needed(request: Request, settings: Settings) -> Response | None:
    if is_authenticated(request, settings):
        return None
    return auth_challenge(settings)


def _content_type_allowed(request: Request, protocol: str) -> bool:
    raw = request.headers.get("content-type", "")
    content_type = raw.split(";", 1)[0].strip().lower()
    allowed = _JSONP_CONTENT_TYPES if protocol == "jsonprpc" else _JSON_CONTENT_TYPES
    return content_type in allowed


def _rpc_response(
    body: bytes, *, status_code: int = 200, media_type: str = "application/json"
) -> Response:
    return Response(content=body, status_code=status_code, media_type=media_type)


async def read_bounded_body(request: Request, limit: int) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError as exc:
            raise BadRequest() from exc
        if declared < 0:
            raise BadRequest()
        if declared > limit:
            raise RequestTooLarge()

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise RequestTooLarge()
        chunks.append(chunk)
    return b"".join(chunks)


async def _readiness(runtime: Runtime) -> dict[str, str]:
    settings = runtime.settings
    components: dict[str, str] = {}
    if settings.is_capture:
        if runtime.capture is None:
            components["capture"] = "error"
            return components
        try:
            async with asyncio.timeout(settings.readyz_timeout_seconds):
                ready = await runtime.capture.ready()
        except TimeoutError:
            ready = False
        except Exception:
            ready = False
        components["capture"] = "ok" if ready else "error"
        return components

    storage_ok = False
    if runtime.idmap is not None and "storage" not in runtime.startup_errors:
        try:
            async with asyncio.timeout(settings.readyz_timeout_seconds):
                storage_ok = await asyncio.to_thread(runtime.idmap.healthcheck)
        except Exception:
            storage_ok = False
    components["storage"] = "ok" if storage_ok else "error"

    if not settings.readyz_check_sab:
        components["sab"] = "disabled"
        return components
    if runtime.sab is None:
        components["sab"] = "error"
        return components
    try:
        async with asyncio.timeout(settings.readyz_timeout_seconds):
            await runtime.sab.categories()
        sab_ok = True
    except TimeoutError:
        sab_ok = False
    except Exception:
        sab_ok = False
    components["sab"] = "ok" if sab_ok else "error"
    return components


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))
