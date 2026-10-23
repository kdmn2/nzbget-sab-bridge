from __future__ import annotations

import base64
import contextlib
import json
import socket
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs

import httpx
import pytest
import uvicorn
from pydantic import SecretStr

from nzbget_sab_bridge.config import Settings
from nzbget_sab_bridge.main import create_app
from nzbget_sab_bridge.storage.idmap import IdMap, IdMapUnavailable
from tests.conftest import (
    FAKE_BRIDGE_PASSWORD,
    FAKE_BRIDGE_USER,
    FAKE_INDEXER_URL,
    FAKE_SAB_API_KEY,
    basic_auth_header,
)

_VALID_NZB = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">'
    b'<file poster="synthetic" date="1700000000" subject="Synthetic.Release" bytes="1">'
    b"<groups><group>alt.test</group></groups>"
    b'<segments><segment bytes="1" number="1">synthetic@example.invalid</segment></segments>'
    b"</file></nzb>"
)
_VALID_NZB_B64 = base64.b64encode(_VALID_NZB).decode()


def _append_params(
    *,
    filename: str = "Synthetic.Release.nzb",
    content: str = _VALID_NZB_B64,
    category: str = "tv",
    priority: int = 0,
    add_to_top: bool = False,
    add_paused: bool = False,
    dupe_key: str = "",
    dupe_score: int = 0,
    dupe_mode: str = "SCORE",
    pp_parameters: list[Any] | None = None,
) -> list[Any]:
    return [
        filename,
        content,
        category,
        priority,
        add_to_top,
        add_paused,
        dupe_key,
        dupe_score,
        dupe_mode,
        [] if pp_parameters is None else pp_parameters,
    ]


_PRIORITY_LABELS = {2: "Force", 1: "High", 0: "Normal", -1: "Low"}


@dataclass
class FakeJob:
    nzo_id: str
    name: str
    category: str = "tv"
    status: str = "Paused"
    priority: int = 0


@dataclass
class FakeSabState:
    jobs: dict[str, FakeJob] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    categories: list[str] = field(default_factory=lambda: ["*", "tv", "movies"])
    category_priorities: dict[str, int] = field(default_factory=dict)
    paused: bool = False
    reject_pause: bool = False
    reject_resume: bool = False
    pause_timeout: float = 0.0
    resume_timeout: float = 0.0
    change_timeout: set[str] = field(default_factory=set)
    reject_change: set[str] = field(default_factory=set)
    reject_job_pause: set[str] = field(default_factory=set)
    reject_job_resume: set[str] = field(default_factory=set)
    reject_priority: set[str] = field(default_factory=set)
    job_pause_timeout: set[str] = field(default_factory=set)
    job_resume_timeout: set[str] = field(default_factory=set)
    priority_timeout: set[str] = field(default_factory=set)
    pause_calls: int = 0
    resume_calls: int = 0
    change_calls: list[tuple[str, str]] = field(default_factory=list)
    change_active: int = 0
    change_max_active: int = 0
    job_pause_calls: list[str] = field(default_factory=list)
    job_resume_calls: list[str] = field(default_factory=list)
    priority_calls: list[tuple[str, int]] = field(default_factory=list)
    bandwidth_limit: int = 10_485_760
    reject_speedlimit: bool = False
    speedlimit_timeout: float = 0.0
    speedlimit_calls: list[str] = field(default_factory=list)
    add_calls: list[dict[str, Any]] = field(default_factory=list)
    add_count: int = 0
    add_result_ids: list[str] | None = None
    reject_add: bool = False
    add_timeout: float = 0.0
    add_malformed: bool = False
    fail_status_reads: bool = False
    queue_read_fail_after: int | None = None
    queue_read_count: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


def _queue_payload(state: FakeSabState) -> dict[str, Any]:
    slots = []
    for index, job in enumerate(state.jobs.values()):
        slot_status = job.status
        if job.priority == 2 and state.paused:
            # SAB reports forced jobs as downloading even while the global
            # queue is paused: force priority bypasses global pause.
            slot_status = "Downloading"
        slots.append(
            {
                "status": slot_status,
                "index": index,
                "time_added": 1_700_000_000,
                "avg_age": "2h",
                "mb": "100.00",
                "mbleft": "100.00",
                "filename": job.name,
                "labels": [],
                "priority": _PRIORITY_LABELS.get(job.priority, "Normal"),
                "cat": job.category,
                "timeleft": "0:00:00",
                "percentage": "0",
                "nzo_id": job.nzo_id,
                "unpackopts": "3",
            }
        )
    return {
        "queue": {
            "status": "Paused" if state.paused else "Idle",
            "paused": state.paused,
            "slots": slots,
            "noofslots": len(slots),
            "noofslots_total": len(slots),
            "mb": "0.00",
            "mbleft": "0.00",
            "speed": "0 ",
            "kbpersec": "0.00",
            "version": "5.1.3",
            "diskspace2_norm": "100.0 G",
            "diskspacetotal2": "500.00",
            "have_quota": False,
        }
    }


def _history_payload(state: FakeSabState) -> dict[str, Any]:
    return {
        "history": {
            "noofslots": len(state.history),
            "ppslots": 0,
            "slots": state.history,
            "version": "5.1.3",
        }
    }


def _parse_body(
    body: bytes, content_type: str
) -> tuple[dict[str, str], dict[str, tuple[str, bytes, str]]]:
    if content_type.lower().startswith("multipart/form-data"):
        message = BytesParser(policy=email_policy).parsebytes(
            b"Content-Type: " + content_type.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
        )
        form: dict[str, str] = {}
        files: dict[str, tuple[str, bytes, str]] = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not isinstance(name, str) or not name:
                continue
            raw_payload = part.get_payload(decode=True)
            payload = raw_payload if isinstance(raw_payload, bytes) else b""
            filename = part.get_filename()
            if filename is None:
                form[name] = payload.decode("utf-8", errors="replace")
            else:
                files[name] = (filename, payload, part.get_content_type())
        return form, files
    form = {key: values[0] for key, values in parse_qs(body.decode()).items()}
    return form, {}


def _parse_speedlimit(value: str) -> int:
    if value == "0":
        return 0
    if value.endswith("K"):
        return int(float(value[:-1]) * 1024)
    return int(value)


def _status_payload(state: FakeSabState) -> dict[str, Any]:
    return {
        "status": {
            "version": "5.1.3",
            "paused": state.paused,
            "pause_int": "0",
            "speedlimit": "100",
            "speedlimit_abs": f"{state.bandwidth_limit}.0",
            "uptime": "1h",
            "diskspace2_norm": "100.0 G",
            "diskspacetotal2": "500.00",
            "cache_size": "0 B",
            "have_quota": False,
            "left_quota": "0 ",
            "servers": [],
        }
    }


class _FakeSabServer(ThreadingHTTPServer):
    state: FakeSabState


class _Handler(BaseHTTPRequestHandler):
    server_version = "fake-sab/phase3"
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    @property
    def state(self) -> FakeSabState:
        return cast(_FakeSabServer, self.server).state

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        form, files = _parse_body(body, self.headers.get("Content-Type", ""))
        mode = form.get("mode", "")
        try:
            payload = self._handle(mode, form, files)
            if mode in {"addfile", "addurl"} and self.state.add_malformed:
                self._send_raw(200, b'{"status": true, "nzo_ids": [')
            else:
                self._send(200, payload)
        except Exception:
            self._send(500, {"status": False, "error": "synthetic failure"})

    def _handle(
        self,
        mode: str,
        form: dict[str, str],
        files: dict[str, tuple[str, bytes, str]],
    ) -> dict[str, Any]:
        state = self.state
        if mode in {"addfile", "addurl"}:
            with state.lock:
                state.add_count += 1
                count = state.add_count
                state.add_calls.append(
                    {
                        "mode": mode,
                        "form": dict(form),
                        "files": dict(files),
                    }
                )
                if state.reject_add:
                    return {"status": False, "error": "synthetic add rejection"}
                if state.add_result_ids is None:
                    nzo_ids = [f"nzo_upload_{count}" if mode == "addfile" else f"nzo_url_{count}"]
                else:
                    nzo_ids = list(state.add_result_ids)
                upload = files.get("name")
                display_name = form.get("nzbname") or (
                    upload[0] if upload is not None else "Synthetic.URL"
                )
                category = form.get("cat", "*")
                priority = int(form.get("priority", "0"))
                if priority == -2:
                    # SAB pauses on -2 and then uses the category priority as
                    # the priority to restore after an explicit resume. A
                    # category default of Force falls back to Normal.
                    effective_priority = state.category_priorities.get(category, 0)
                    if effective_priority == 2:
                        effective_priority = 0
                    status = "Paused"
                else:
                    effective_priority = priority
                    status = "Grabbing" if mode == "addurl" else "Queued"
                for nzo_id in nzo_ids:
                    state.jobs[nzo_id] = FakeJob(
                        nzo_id,
                        display_name,
                        category=category,
                        status=status,
                        priority=effective_priority,
                    )
                delay = state.add_timeout
            if delay:
                time.sleep(delay)
            return {"status": True, "nzo_ids": nzo_ids}
        if mode == "queue" and form.get("name") == "pause":
            nzo_id = form.get("value", "")
            with state.lock:
                state.job_pause_calls.append(nzo_id)
                job = state.jobs.get(nzo_id)
                if job is None or nzo_id in state.reject_job_pause:
                    return {"status": False, "error": "synthetic job pause rejection"}
                job.status = "Paused"
                delay = 0.7 if nzo_id in state.job_pause_timeout else 0.0
            if delay:
                time.sleep(delay)
            return {"status": True, "nzo_ids": [nzo_id]}
        if mode == "queue" and form.get("name") == "resume":
            nzo_id = form.get("value", "")
            with state.lock:
                state.job_resume_calls.append(nzo_id)
                job = state.jobs.get(nzo_id)
                if job is None or nzo_id in state.reject_job_resume:
                    return {"status": False, "error": "synthetic job resume rejection"}
                job.status = "Queued"
                delay = 0.7 if nzo_id in state.job_resume_timeout else 0.0
            if delay:
                time.sleep(delay)
            return {"status": True, "nzo_ids": [nzo_id]}
        if mode == "queue" and form.get("name") == "priority":
            nzo_id = form.get("value", "")
            priority = int(form.get("value2", "0"))
            with state.lock:
                state.priority_calls.append((nzo_id, priority))
                job = state.jobs.get(nzo_id)
                if job is None or nzo_id in state.reject_priority:
                    return {"position": -1}
                job.priority = priority
                # SAB clears an individual paused status when priority changes.
                if job.status == "Paused":
                    job.status = "Queued"
                delay = 0.7 if nzo_id in state.priority_timeout else 0.0
            if delay:
                time.sleep(delay)
            return {"position": 0}
        if mode == "queue":
            with state.lock:
                state.queue_read_count += 1
                if (
                    state.queue_read_fail_after is not None
                    and state.queue_read_count > state.queue_read_fail_after
                ):
                    raise RuntimeError("synthetic queue read failure")
                return _queue_payload(state)
        if mode == "history":
            with state.lock:
                return _history_payload(state)
        if mode == "status":
            with state.lock:
                if state.fail_status_reads:
                    raise RuntimeError("synthetic status read failure")
                return _status_payload(state)
        if mode == "server_stats":
            return {"day": 0, "week": 0, "month": 0, "total": 0, "servers": {}}
        if mode == "get_cats":
            with state.lock:
                return {"categories": list(state.categories)}
        if mode == "pause":
            with state.lock:
                state.pause_calls += 1
                if state.reject_pause:
                    return {"status": False, "error": "synthetic pause rejection"}
                state.paused = True
                delay = state.pause_timeout
            if delay:
                time.sleep(delay)
            return {"status": True}
        if mode == "resume":
            with state.lock:
                state.resume_calls += 1
                if state.reject_resume:
                    return {"status": False, "error": "synthetic resume rejection"}
                state.paused = False
                delay = state.resume_timeout
            if delay:
                time.sleep(delay)
            return {"status": True}
        if mode == "config" and form.get("name") == "speedlimit":
            value = form.get("value", "")
            with state.lock:
                state.speedlimit_calls.append(value)
                if state.reject_speedlimit:
                    return {"status": False, "error": "synthetic speed limit rejection"}
                state.bandwidth_limit = _parse_speedlimit(value)
                delay = state.speedlimit_timeout
            if delay:
                time.sleep(delay)
            return {"status": True}
        if mode == "change_cat":
            nzo_id = form.get("value", "")
            category = form.get("value2", "")
            with state.lock:
                state.change_calls.append((nzo_id, category))
                job = state.jobs.get(nzo_id)
                if job is None or nzo_id in state.reject_change:
                    return {"status": False, "error": "synthetic category rejection"}
                job.category = category
                state.change_active += 1
                state.change_max_active = max(state.change_max_active, state.change_active)
                delay = 0.7 if nzo_id in state.change_timeout else 0.0
            try:
                if delay:
                    time.sleep(delay)
                return {"status": True}
            finally:
                with state.lock:
                    state.change_active -= 1
        return {"status": False, "error": "unexpected mode"}

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        self._send_raw(status, json.dumps(payload).encode())

    def _send_raw(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def running_bridge(
    tmp_path: Path,
    state: FakeSabState,
    *,
    read_timeout: float = 3.0,
    request_max_bytes: int = 32 * 1024 * 1024,
    nzb_max_decoded_bytes: int = 24 * 1024 * 1024,
) -> Iterator[tuple[FakeSabState, httpx.Client]]:
    sab_server = _FakeSabServer(("127.0.0.1", 0), _Handler)
    sab_server.daemon_threads = True
    sab_server.state = state
    sab_thread = threading.Thread(target=sab_server.serve_forever, daemon=True)
    sab_thread.start()
    sab_url = f"http://127.0.0.1:{sab_server.server_address[1]}"

    bridge_port = _free_port()
    settings = Settings(
        mode="bridge",
        listen_host="127.0.0.1",
        listen_port=bridge_port,
        nzbget_username=FAKE_BRIDGE_USER,
        nzbget_password=SecretStr(FAKE_BRIDGE_PASSWORD),
        sabnzbd_url=sab_url,
        sabnzbd_api_key=SecretStr(FAKE_SAB_API_KEY),
        data_dir=tmp_path / "data",
        readyz_check_sab=False,
        request_max_bytes=request_max_bytes,
        nzb_max_decoded_bytes=nzb_max_decoded_bytes,
        http_connect_timeout=1.0,
        http_read_timeout=read_timeout,
        http_write_timeout=1.0,
        http_pool_timeout=1.0,
    )
    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=bridge_port,
        log_config=None,
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    bridge_thread = threading.Thread(target=server.run, daemon=True)
    bridge_thread.start()

    base_url = f"http://127.0.0.1:{bridge_port}"
    client = httpx.Client(base_url=base_url, timeout=10.0)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if client.get("/healthz").status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    else:
        raise RuntimeError("bridge did not start")

    try:
        yield state, client
    finally:
        client.close()
        server.should_exit = True
        bridge_thread.join(timeout=5)
        sab_server.shutdown()
        sab_server.server_close()
        sab_thread.join(timeout=5)


def _rpc(
    client: httpx.Client, method: str, params: list[Any] | None = None, request_id: int = 1
) -> dict[str, Any]:
    response = client.post(
        "/jsonrpc/",
        headers=basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD),
        json={"method": method, "params": params or [], "id": request_id},
        follow_redirects=False,
    )
    assert response.status_code == 200
    return cast(dict[str, Any], response.json())


def _queue_id(client: httpx.Client) -> int:
    result = _rpc(client, "listgroups", [], 10)["result"]
    assert len(result) == 1
    return int(result[0]["NZBID"])


def test_pause_resume_success_and_aliases(tmp_path: Path) -> None:
    state = FakeSabState()
    with running_bridge(tmp_path, state) as (state, client):
        assert _rpc(client, "pausedownload")["result"] is True
        assert state.paused is True
        assert _rpc(client, "resume")["result"] is True
        assert bool(state.paused) is False
        assert _rpc(client, "pause")["result"] is True
        assert bool(state.paused) is True
        assert _rpc(client, "resumedownload2")["result"] is True
        assert state.paused is False


def test_pause_rejection_is_not_retried(tmp_path: Path) -> None:
    state = FakeSabState(reject_pause=True)
    with running_bridge(tmp_path, state) as (state, client):
        payload = _rpc(client, "pausedownload")
        assert payload["error"]["code"] == 4
        assert state.pause_calls == 1


def test_pause_timeout_reconciles_applied_state(tmp_path: Path) -> None:
    state = FakeSabState(pause_timeout=0.7)
    with running_bridge(tmp_path, state, read_timeout=0.2) as (state, client):
        payload = _rpc(client, "pausedownload")
        assert payload["result"] is True
        assert state.paused is True
        assert state.pause_calls == 1


def test_pause_reconciliation_read_failure_is_unknown(tmp_path: Path) -> None:
    state = FakeSabState(pause_timeout=0.7, fail_status_reads=True)
    with running_bridge(tmp_path, state, read_timeout=0.2) as (state, client):
        payload = _rpc(client, "pausedownload")
        assert payload["error"]["code"] == 4
        assert "outcome unknown" in payload["error"]["message"]
        assert state.pause_calls == 1
        assert state.paused is True


def test_category_change_success_and_default(tmp_path: Path) -> None:
    state = FakeSabState(jobs={"nzo_a": FakeJob("nzo_a", "Synthetic.A")})
    with running_bridge(tmp_path, state) as (state, client):
        nzb_id = _queue_id(client)
        assert _rpc(client, "editqueue", ["GroupSetCategory", "movies", [nzb_id]])["result"] is True
        assert state.jobs["nzo_a"].category == "movies"
        assert (
            _rpc(client, "editqueue", ["groupsetcategory", "Default", [nzb_id]])["result"] is True
        )
        assert state.jobs["nzo_a"].category == "*"
        assert state.change_calls == [("nzo_a", "movies"), ("nzo_a", "*")]


def test_category_change_duplicate_and_stale_prevalidation(tmp_path: Path) -> None:
    state = FakeSabState(
        jobs={
            "nzo_a": FakeJob("nzo_a", "Synthetic.A"),
            "nzo_b": FakeJob("nzo_b", "Synthetic.B"),
        }
    )
    with running_bridge(tmp_path, state) as (state, client):
        result = _rpc(client, "listgroups", [], 10)["result"]
        first_id = int(result[0]["NZBID"])
        assert (
            _rpc(client, "editqueue", ["GroupSetCategory", "movies", [first_id, first_id]])[
                "result"
            ]
            is True
        )
        assert state.change_calls == [("nzo_a", "movies")]
        state.change_calls.clear()
        payload = _rpc(
            client,
            "editqueue",
            ["GroupSetCategory", "movies", [first_id, 999999]],
        )
        assert payload["error"]["code"] == 4
        assert state.change_calls == []


def test_category_change_invalid_category_and_history_only(tmp_path: Path) -> None:
    state = FakeSabState(
        jobs={"nzo_a": FakeJob("nzo_a", "Synthetic.A")},
        history=[
            {
                "nzo_id": "nzo_h",
                "name": "Synthetic.History",
                "nzb_name": "Synthetic.History.nzb",
                "status": "Completed",
                "bytes": 1,
                "downloaded": 1,
                "completed": 1,
                "time_added": 1,
            }
        ],
    )
    with running_bridge(tmp_path, state) as (state, client):
        nzb_id = _queue_id(client)
        invalid = _rpc(client, "editqueue", ["GroupSetCategory", "not-configured", [nzb_id]])
        assert invalid["error"]["code"] == 2
        assert state.change_calls == []
        history = _rpc(client, "history", [], 11)["result"]
        history_id = int(history[0]["NZBID"])
        history_only = _rpc(client, "editqueue", ["GroupSetCategory", "movies", [history_id]])
        assert history_only["error"]["code"] == 4
        assert state.change_calls == []


def test_unsupported_editqueue_command_is_explicit(tmp_path: Path) -> None:
    state = FakeSabState(jobs={"nzo_a": FakeJob("nzo_a", "Synthetic.A")})
    with running_bridge(tmp_path, state) as (state, client):
        nzb_id = _queue_id(client)
        payload = _rpc(client, "editqueue", ["GroupDelete", "", [nzb_id]])
        assert payload["error"]["code"] == 3
        assert state.change_calls == []


def test_category_change_partial_failure_is_reported(tmp_path: Path) -> None:
    state = FakeSabState(
        jobs={
            "nzo_a": FakeJob("nzo_a", "Synthetic.A"),
            "nzo_b": FakeJob("nzo_b", "Synthetic.B"),
        },
        reject_change={"nzo_b"},
    )
    with running_bridge(tmp_path, state) as (state, client):
        groups = _rpc(client, "listgroups", [], 10)["result"]
        ids = [int(group["NZBID"]) for group in groups]
        payload = _rpc(
            client,
            "editqueue",
            ["GroupSetCategory", "movies", ids],
        )
        assert payload["error"]["code"] == 4
        assert "partially applied" in payload["error"]["message"]
        assert state.jobs["nzo_a"].category == "movies"
        assert state.jobs["nzo_b"].category == "tv"
        assert len(state.change_calls) == 2


def test_overlapping_mutations_are_serialized_by_the_bridge(tmp_path: Path) -> None:
    state = FakeSabState(
        jobs={"nzo_a": FakeJob("nzo_a", "Synthetic.A")},
        change_timeout={"nzo_a"},
    )
    with running_bridge(tmp_path, state) as (state, client):
        nzb_id = _queue_id(client)
        barrier = threading.Barrier(2)

        def change(category: str) -> dict[str, Any]:
            barrier.wait(timeout=5)
            with httpx.Client(base_url=str(client.base_url), timeout=10.0) as local:
                return _rpc(local, "editqueue", ["GroupSetCategory", category, [nzb_id]])

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(change, ["movies", "tv"]))
        assert all("error" not in result for result in results)
        assert state.change_max_active == 1
        assert state.jobs["nzo_a"].category in {"movies", "tv"}


def test_category_timeout_reconciles_applied_state(tmp_path: Path) -> None:
    state = FakeSabState(
        jobs={"nzo_a": FakeJob("nzo_a", "Synthetic.A")},
        change_timeout={"nzo_a"},
    )
    with running_bridge(tmp_path, state, read_timeout=0.2) as (state, client):
        nzb_id = _queue_id(client)
        payload = _rpc(client, "editqueue", ["GroupSetCategory", "movies", [nzb_id]])
        assert payload["result"] is True
        assert state.jobs["nzo_a"].category == "movies"
        assert len(state.change_calls) == 1


def test_category_reconciliation_read_failure_is_unknown(tmp_path: Path) -> None:
    state = FakeSabState(jobs={"nzo_a": FakeJob("nzo_a", "Synthetic.A")})
    with running_bridge(tmp_path, state) as (state, client):
        nzb_id = _queue_id(client)
        # Next queue read is validation; the read after the write is forced to
        # fail.
        state.queue_read_fail_after = state.queue_read_count + 1
        payload = _rpc(client, "editqueue", ["GroupSetCategory", "movies", [nzb_id]])
        assert payload["error"]["code"] == 4
        assert "outcome unknown" in payload["error"]["message"]
        assert state.jobs["nzo_a"].category == "movies"
        assert len(state.change_calls) == 1


def test_unsafe_get_and_authentication_do_not_mutate(tmp_path: Path) -> None:
    state = FakeSabState(jobs={"nzo_a": FakeJob("nzo_a", "Synthetic.A")})
    with running_bridge(tmp_path, state) as (state, client):
        headers = basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD)
        unsafe = client.get("/jsonrpc/pausedownload", headers=headers)
        assert unsafe.status_code == 405
        edit = client.get("/jsonrpc/editqueue", headers=headers)
        assert edit.status_code == 405
        rate = client.get("/jsonrpc/rate", headers=headers)
        assert rate.status_code == 405
        no_auth = client.post("/jsonrpc/", json={"method": "pausedownload", "params": []})
        assert no_auth.status_code == 401
        assert state.pause_calls == 0
        assert state.change_calls == []
        assert state.speedlimit_calls == []


def test_job_pause_resume_preserves_priority_and_global_state(tmp_path: Path) -> None:
    state = FakeSabState(
        jobs={"nzo_a": FakeJob("nzo_a", "Synthetic.A", status="Queued", priority=1)}
    )
    with running_bridge(tmp_path, state) as (state, client):
        nzb_id = _queue_id(client)
        assert _rpc(client, "editqueue", ["GroupPause", "", [nzb_id]])["result"] is True
        assert state.jobs["nzo_a"].status == "Paused"
        assert state.jobs["nzo_a"].priority == 1
        assert state.paused is False
        assert state.pause_calls == 0
        assert state.job_pause_calls == ["nzo_a"]

        assert _rpc(client, "editqueue", ["groupresume", "", [nzb_id]])["result"] is True
        assert state.jobs["nzo_a"].status == "Queued"
        assert state.jobs["nzo_a"].priority == 1
        assert state.paused is False
        assert state.resume_calls == 0
        assert state.job_resume_calls == ["nzo_a"]


def test_job_resume_does_not_clear_global_pause(tmp_path: Path) -> None:
    state = FakeSabState(
        jobs={"nzo_a": FakeJob("nzo_a", "Synthetic.A", status="Paused", priority=1)},
        paused=True,
    )
    with running_bridge(tmp_path, state) as (state, client):
        nzb_id = _queue_id(client)
        assert _rpc(client, "editqueue", ["GroupResume", "", [nzb_id]])["result"] is True
        assert state.jobs["nzo_a"].status == "Queued"
        assert state.jobs["nzo_a"].priority == 1
        assert state.paused is True
        assert state.job_resume_calls == ["nzo_a"]


def test_job_pause_rejects_force_priority_without_write(tmp_path: Path) -> None:
    state = FakeSabState(
        jobs={"nzo_a": FakeJob("nzo_a", "Synthetic.A", status="Queued", priority=2)}
    )
    with running_bridge(tmp_path, state) as (state, client):
        nzb_id = _queue_id(client)
        payload = _rpc(client, "editqueue", ["GroupPause", "", [nzb_id]])
        assert payload["error"]["code"] == 4
        assert "force" in payload["error"]["message"].lower()
        assert state.job_pause_calls == []


def test_priority_change_paused_job_rejected_before_any_write(tmp_path: Path) -> None:
    state = FakeSabState(
        jobs={"nzo_a": FakeJob("nzo_a", "Synthetic.A", status="Paused", priority=0)}
    )
    with running_bridge(tmp_path, state) as (state, client):
        nzb_id = _queue_id(client)
        payload = _rpc(client, "editqueue", ["GroupSetPriority", "50", [nzb_id]])
        assert payload["error"]["code"] == 4
        assert "individually paused" in payload["error"]["message"]
        assert "resume" in payload["error"]["message"].lower()
        # The fake would set status=Queued on the priority write, exposing the
        # race window; the bridge must reject before that request is sent.
        assert state.priority_calls == []
        assert state.job_pause_calls == []
        assert state.jobs["nzo_a"].status == "Paused"
        assert state.jobs["nzo_a"].priority == 0


def test_priority_paused_job_same_level_is_a_safe_noop(tmp_path: Path) -> None:
    state = FakeSabState(
        jobs={"nzo_a": FakeJob("nzo_a", "Synthetic.A", status="Paused", priority=1)}
    )
    with running_bridge(tmp_path, state) as (state, client):
        nzb_id = _queue_id(client)
        assert _rpc(client, "editqueue", ["GroupSetPriority", "100", [nzb_id]])["result"] is True
        assert state.priority_calls == []
        assert state.job_pause_calls == []
        assert state.jobs["nzo_a"].status == "Paused"
        assert state.jobs["nzo_a"].priority == 1


def test_priority_mixed_targets_reject_paused_change_before_any_write(tmp_path: Path) -> None:
    state = FakeSabState(
        jobs={
            "nzo_a": FakeJob("nzo_a", "Synthetic.A", status="Queued", priority=0),
            "nzo_b": FakeJob("nzo_b", "Synthetic.B", status="Paused", priority=0),
        }
    )
    with running_bridge(tmp_path, state) as (state, client):
        groups = _rpc(client, "listgroups", [], 10)["result"]
        ids = [int(group["NZBID"]) for group in groups]
        payload = _rpc(client, "editqueue", ["GroupSetPriority", "50", ids])
        assert payload["error"]["code"] == 4
        assert "individually paused" in payload["error"]["message"]
        assert state.priority_calls == []
        assert state.job_pause_calls == []
        assert state.jobs["nzo_a"].priority == 0
        assert state.jobs["nzo_b"].priority == 0
        assert state.jobs["nzo_b"].status == "Paused"


def test_priority_change_rejects_force_on_paused_job(tmp_path: Path) -> None:
    state = FakeSabState(
        jobs={"nzo_a": FakeJob("nzo_a", "Synthetic.A", status="Paused", priority=0)}
    )
    with running_bridge(tmp_path, state) as (state, client):
        nzb_id = _queue_id(client)
        payload = _rpc(client, "editqueue", ["GroupSetPriority", "900", [nzb_id]])
        assert payload["error"]["code"] == 4
        assert "force" in payload["error"]["message"].lower()
        assert state.priority_calls == []
        assert state.job_pause_calls == []


def test_priority_change_normal_job_and_force(tmp_path: Path) -> None:
    state = FakeSabState(
        jobs={"nzo_a": FakeJob("nzo_a", "Synthetic.A", status="Queued", priority=0)}
    )
    with running_bridge(tmp_path, state) as (state, client):
        nzb_id = _queue_id(client)
        assert _rpc(client, "editqueue", ["GroupSetPriority", "100", [nzb_id]])["result"] is True
        assert state.jobs["nzo_a"].priority == 1
        assert state.job_pause_calls == []
        assert _rpc(client, "editqueue", ["GroupSetPriority", "900", [nzb_id]])["result"] is True
        assert state.jobs["nzo_a"].priority == 2
        assert state.priority_calls == [("nzo_a", 1), ("nzo_a", 2)]


def test_priority_change_while_global_paused_keeps_states_separate(tmp_path: Path) -> None:
    state = FakeSabState(
        jobs={"nzo_a": FakeJob("nzo_a", "Synthetic.A", status="Queued", priority=0)},
        paused=True,
    )
    with running_bridge(tmp_path, state) as (state, client):
        nzb_id = _queue_id(client)
        assert _rpc(client, "editqueue", ["GroupSetPriority", "50", [nzb_id]])["result"] is True
        assert state.paused is True
        assert state.jobs["nzo_a"].status == "Queued"
        assert state.jobs["nzo_a"].priority == 1
        assert state.job_pause_calls == []


def test_force_priority_on_unpaused_job_bypasses_global_pause(tmp_path: Path) -> None:
    state = FakeSabState(
        jobs={"nzo_a": FakeJob("nzo_a", "Synthetic.A", status="Queued", priority=0)},
        paused=True,
    )
    with running_bridge(tmp_path, state) as (state, client):
        nzb_id = _queue_id(client)
        assert _rpc(client, "editqueue", ["GroupSetPriority", "900", [nzb_id]])["result"] is True
        assert state.paused is True
        assert state.jobs["nzo_a"].priority == 2
        groups = _rpc(client, "listgroups", [], 11)["result"]
        assert groups[0]["Status"] == "DOWNLOADING"
        assert groups[0]["MinPriority"] == 900


def test_priority_duplicate_stale_and_history_prevalidation(tmp_path: Path) -> None:
    state = FakeSabState(
        jobs={"nzo_a": FakeJob("nzo_a", "Synthetic.A", status="Queued")},
        history=[
            {
                "nzo_id": "nzo_h",
                "name": "Synthetic.History",
                "nzb_name": "Synthetic.History.nzb",
                "status": "Completed",
                "bytes": 1,
                "downloaded": 1,
                "completed": 1,
                "time_added": 1,
            }
        ],
    )
    with running_bridge(tmp_path, state) as (state, client):
        nzb_id = _queue_id(client)
        assert (
            _rpc(
                client,
                "editqueue",
                ["GroupSetPriority", "50", [nzb_id, nzb_id]],
            )["result"]
            is True
        )
        assert state.priority_calls == [("nzo_a", 1)]
        state.priority_calls.clear()

        stale = _rpc(client, "editqueue", ["GroupSetPriority", "50", [nzb_id, 999999]])
        assert stale["error"]["code"] == 4
        assert state.priority_calls == []

        history_id = int(_rpc(client, "history", [], 11)["result"][0]["NZBID"])
        history_only = _rpc(client, "editqueue", ["GroupSetPriority", "50", [history_id]])
        assert history_only["error"]["code"] == 4
        assert state.priority_calls == []


def test_priority_invalid_parameter_has_no_writes(tmp_path: Path) -> None:
    state = FakeSabState(jobs={"nzo_a": FakeJob("nzo_a", "Synthetic.A", status="Queued")})
    with running_bridge(tmp_path, state) as (state, client):
        nzb_id = _queue_id(client)
        for value in ("high", "", str(2**31)):
            payload = _rpc(client, "editqueue", ["GroupSetPriority", value, [nzb_id]])
            assert payload["error"]["code"] == 2
        assert state.priority_calls == []


def test_priority_partial_failure_is_reported(tmp_path: Path) -> None:
    state = FakeSabState(
        jobs={
            "nzo_a": FakeJob("nzo_a", "Synthetic.A", status="Queued"),
            "nzo_b": FakeJob("nzo_b", "Synthetic.B", status="Queued"),
        },
        reject_priority={"nzo_b"},
    )
    with running_bridge(tmp_path, state) as (state, client):
        groups = _rpc(client, "listgroups", [], 10)["result"]
        ids = [int(group["NZBID"]) for group in groups]
        payload = _rpc(client, "editqueue", ["GroupSetPriority", "50", ids])
        assert payload["error"]["code"] == 4
        assert "partially applied" in payload["error"]["message"]
        assert state.jobs["nzo_a"].priority == 1
        assert state.jobs["nzo_b"].priority == 0
        assert len(state.priority_calls) == 2


def test_priority_timeout_reconciles_normal_job(tmp_path: Path) -> None:
    state = FakeSabState(
        jobs={"nzo_a": FakeJob("nzo_a", "Synthetic.A", status="Queued")},
        priority_timeout={"nzo_a"},
    )
    with running_bridge(tmp_path, state, read_timeout=0.2) as (state, client):
        nzb_id = _queue_id(client)
        payload = _rpc(client, "editqueue", ["GroupSetPriority", "50", [nzb_id]])
        assert payload["result"] is True
        assert state.jobs["nzo_a"].priority == 1
        assert len(state.priority_calls) == 1


def test_priority_reconciliation_read_failure_is_unknown(tmp_path: Path) -> None:
    state = FakeSabState(jobs={"nzo_a": FakeJob("nzo_a", "Synthetic.A", status="Queued")})
    with running_bridge(tmp_path, state) as (state, client):
        nzb_id = _queue_id(client)
        state.queue_read_fail_after = state.queue_read_count + 1
        payload = _rpc(client, "editqueue", ["GroupSetPriority", "50", [nzb_id]])
        assert payload["error"]["code"] == 4
        assert "outcome unknown" in payload["error"]["message"]
        assert state.jobs["nzo_a"].priority == 1
        assert len(state.priority_calls) == 1


def test_job_pause_partial_failure_and_timeout(tmp_path: Path) -> None:
    state = FakeSabState(
        jobs={
            "nzo_a": FakeJob("nzo_a", "Synthetic.A", status="Queued"),
            "nzo_b": FakeJob("nzo_b", "Synthetic.B", status="Queued"),
        },
        reject_job_pause={"nzo_b"},
    )
    with running_bridge(tmp_path, state) as (state, client):
        groups = _rpc(client, "listgroups", [], 10)["result"]
        ids = [int(group["NZBID"]) for group in groups]
        payload = _rpc(client, "editqueue", ["GroupPause", "", ids])
        assert payload["error"]["code"] == 4
        assert "partially applied" in payload["error"]["message"]
        assert state.jobs["nzo_a"].status == "Paused"
        assert state.jobs["nzo_b"].status == "Queued"
        assert len(state.job_pause_calls) == 2

    state = FakeSabState(
        jobs={"nzo_c": FakeJob("nzo_c", "Synthetic.C", status="Queued")},
        job_pause_timeout={"nzo_c"},
    )
    with running_bridge(tmp_path / "second", state, read_timeout=0.2) as (state, client):
        nzb_id = _queue_id(client)
        payload = _rpc(client, "editqueue", ["GroupPause", "", [nzb_id]])
        assert payload["result"] is True
        assert state.jobs["nzo_c"].status == "Paused"
        assert len(state.job_pause_calls) == 1

    state = FakeSabState(
        jobs={"nzo_d": FakeJob("nzo_d", "Synthetic.D", status="Paused")},
        job_resume_timeout={"nzo_d"},
    )
    with running_bridge(tmp_path / "third", state, read_timeout=0.2) as (state, client):
        nzb_id = _queue_id(client)
        payload = _rpc(client, "editqueue", ["GroupResume", "", [nzb_id]])
        assert payload["result"] is True
        assert state.jobs["nzo_d"].status == "Queued"
        assert len(state.job_resume_calls) == 1


def test_rate_units_zero_and_status_round_trip(tmp_path: Path) -> None:
    state = FakeSabState()
    with running_bridge(tmp_path, state) as (state, client):
        assert _rpc(client, "rate", [400])["result"] is True
        assert state.bandwidth_limit == 400 * 1024
        assert state.speedlimit_calls == ["400K"]
        status = _rpc(client, "status", [], 12)["result"]
        assert status["DownloadLimit"] == 400 * 1024

        assert _rpc(client, "rate", [0])["result"] is True
        assert state.bandwidth_limit == 0
        assert state.speedlimit_calls == ["400K", "0"]
        status = _rpc(client, "status", [], 13)["result"]
        assert status["DownloadLimit"] == 0


@pytest.mark.parametrize("value", [-1, 1.5, True, "400"])
def test_rate_invalid_values_have_no_writes(tmp_path: Path, value: object) -> None:
    state = FakeSabState()
    with running_bridge(tmp_path, state) as (state, client):
        payload = _rpc(client, "rate", [value])
        assert payload["error"]["code"] == 2
        assert state.speedlimit_calls == []


def test_rate_rejection_timeout_and_reconciliation_failure(tmp_path: Path) -> None:
    state = FakeSabState(reject_speedlimit=True)
    with running_bridge(tmp_path / "reject", state) as (state, client):
        payload = _rpc(client, "rate", [400])
        assert payload["error"]["code"] == 4
        assert state.speedlimit_calls == ["400K"]
        assert state.bandwidth_limit == 10_485_760

    state = FakeSabState(speedlimit_timeout=0.7)
    with running_bridge(tmp_path / "timeout", state, read_timeout=0.2) as (state, client):
        payload = _rpc(client, "rate", [400])
        assert payload["result"] is True
        assert state.bandwidth_limit == 400 * 1024
        assert state.speedlimit_calls == ["400K"]

    state = FakeSabState(speedlimit_timeout=0.7, fail_status_reads=True)
    with running_bridge(tmp_path / "unknown", state, read_timeout=0.2) as (state, client):
        payload = _rpc(client, "rate", [400])
        assert payload["error"]["code"] == 4
        assert "outcome unknown" in payload["error"]["message"]
        assert state.speedlimit_calls == ["400K"]


def test_append_file_multipart_stable_id_and_queue_visibility(tmp_path: Path) -> None:
    state = FakeSabState()
    with running_bridge(tmp_path, state) as (state, client):
        payload = _rpc(
            client,
            "append",
            _append_params(category="movies", priority=50),
        )
        assert "error" not in payload
        nzb_id = int(payload["result"])
        assert nzb_id > 0
        assert len(state.add_calls) == 1
        call = state.add_calls[0]
        assert call["mode"] == "addfile"
        form = call["form"]
        assert form["apikey"] == FAKE_SAB_API_KEY
        assert form["cat"] == "movies"
        assert form["priority"] == "1"
        assert "name" not in form
        filename, content, content_type = call["files"]["name"]
        assert filename == "Synthetic.Release.nzb"
        assert content == _VALID_NZB
        assert content_type == "application/xml"

        groups = _rpc(client, "listgroups", [], 10)["result"]
        assert len(groups) == 1
        assert groups[0]["NZBID"] == nzb_id
        assert groups[0]["Category"] == "movies"
        assert groups[0]["MinPriority"] == 50
        assert groups[0]["Status"] == "QUEUED"
        assert groups[0]["NZBName"] == "Synthetic.Release.nzb"
        assert _rpc(client, "listgroups", [], 11)["result"][0]["NZBID"] == nzb_id


def test_append_file_paused_uses_initial_priority_and_a_stable_id(tmp_path: Path) -> None:
    state = FakeSabState()
    with running_bridge(tmp_path, state) as (state, client):
        payload = _rpc(
            client,
            "append",
            _append_params(category="", add_paused=True),
        )
        assert "error" not in payload
        nzb_id = int(payload["result"])
        assert nzb_id > 0
        form = state.add_calls[0]["form"]
        assert form["cat"] == "*"
        assert form["priority"] == "-2"
        assert state.job_pause_calls == []
        groups = _rpc(client, "listgroups", [], 10)["result"]
        assert groups[0]["NZBID"] == nzb_id
        assert groups[0]["Status"] == "PAUSED"
        assert state.jobs[next(iter(state.jobs))].status == "Paused"


@pytest.mark.parametrize(
    ("category_priority", "restored_nzbget_priority"),
    [(1, 50), (2, 0)],
)
def test_append_addpaused_starts_paused_and_restores_category_priority(
    tmp_path: Path, category_priority: int, restored_nzbget_priority: int
) -> None:
    state = FakeSabState(category_priorities={"movies": category_priority})
    with running_bridge(tmp_path, state) as (state, client):
        payload = _rpc(
            client,
            "append",
            _append_params(category="movies", add_paused=True),
        )
        assert "error" not in payload
        nzb_id = int(payload["result"])
        groups = _rpc(client, "listgroups", [], 10)["result"]
        assert groups[0]["NZBID"] == nzb_id
        assert groups[0]["Status"] == "PAUSED"
        assert groups[0]["MinPriority"] == restored_nzbget_priority
        assert state.add_calls[0]["form"]["priority"] == "-2"

        assert _rpc(client, "editqueue", ["GroupResume", "", [nzb_id]])["result"] is True
        groups = _rpc(client, "listgroups", [], 11)["result"]
        assert groups[0]["Status"] == "QUEUED"
        assert groups[0]["MinPriority"] == restored_nzbget_priority


@pytest.mark.parametrize("method", ["append", "appendurl"])
def test_append_url_uses_addurl_and_does_not_expose_the_url(tmp_path: Path, method: str) -> None:
    state = FakeSabState()
    with running_bridge(tmp_path / method, state) as (state, client):
        payload = _rpc(
            client,
            method,
            _append_params(
                filename="Synthetic.URL.nzb",
                content=FAKE_INDEXER_URL,
                category="movies",
            ),
        )
        assert "error" not in payload
        nzb_id = int(payload["result"])
        assert nzb_id > 0
        call = state.add_calls[0]
        assert call["mode"] == "addurl"
        assert call["form"]["name"] == FAKE_INDEXER_URL
        assert call["form"]["apikey"] == FAKE_SAB_API_KEY
        assert call["form"]["cat"] == "movies"
        assert call["files"] == {}

        groups = _rpc(client, "listgroups", [], 10)["result"]
        assert groups[0]["NZBID"] == nzb_id
        assert groups[0]["Status"] == "FETCHING"
        assert "indexer-secret" not in json.dumps(payload)


@pytest.mark.parametrize("method", ["append", "appendurl"])
def test_append_url_allows_empty_filename(tmp_path: Path, method: str) -> None:
    state = FakeSabState()
    with running_bridge(tmp_path / method, state) as (state, client):
        payload = _rpc(
            client,
            method,
            _append_params(filename="", content=FAKE_INDEXER_URL),
        )
        assert "error" not in payload
        assert int(payload["result"]) > 0
        assert state.add_calls[0]["mode"] == "addurl"
        assert state.add_calls[0]["form"]["name"] == FAKE_INDEXER_URL
        assert "nzbname" not in state.add_calls[0]["form"]


@pytest.mark.parametrize(
    ("params", "expected_sab_priority"),
    [
        (_append_params(priority=50), "1"),
        (_append_params(priority=50)[:-1], "1"),
        (
            [
                "Synthetic.Release.nzb",
                _VALID_NZB_B64,
                "tv",
                False,
                False,
                "",
                0,
                "SCORE",
            ],
            "0",
        ),
        (
            [
                "Synthetic.Release.nzb",
                _VALID_NZB_B64,
                "tv",
                False,
                False,
                "",
                0,
                "SCORE",
                [],
            ],
            "0",
        ),
    ],
)
def test_append_optional_priority_and_trailing_parameters(
    tmp_path: Path, params: list[Any], expected_sab_priority: str
) -> None:
    state = FakeSabState()
    with running_bridge(tmp_path, state) as (state, client):
        payload = _rpc(client, "append", params)
        assert "error" not in payload
        assert int(payload["result"]) > 0
        assert state.add_calls[0]["form"]["priority"] == expected_sab_priority


def test_appendurl_accepts_optional_priority_omitted(tmp_path: Path) -> None:
    state = FakeSabState()
    params = [
        "",
        FAKE_INDEXER_URL,
        "tv",
        False,
        False,
        "",
        0,
        "SCORE",
    ]
    with running_bridge(tmp_path, state) as (state, client):
        payload = _rpc(client, "appendurl", params)
        assert "error" not in payload
        assert int(payload["result"]) > 0
        assert state.add_calls[0]["mode"] == "addurl"
        assert state.add_calls[0]["form"]["priority"] == "0"


def test_append_url_addpaused_uses_initial_pause_priority(tmp_path: Path) -> None:
    state = FakeSabState()
    with running_bridge(tmp_path, state) as (state, client):
        payload = _rpc(
            client,
            "appendurl",
            _append_params(content=FAKE_INDEXER_URL, add_paused=True),
        )
        assert "error" not in payload
        assert int(payload["result"]) > 0
        assert state.add_calls[0]["mode"] == "addurl"
        assert state.add_calls[0]["form"]["priority"] == "-2"
        assert state.job_pause_calls == []
        groups = _rpc(client, "listgroups", [], 10)["result"]
        assert groups[0]["Status"] == "PAUSED"


def test_append_url_rejection_does_not_leak_source_url(tmp_path: Path) -> None:
    state = FakeSabState(reject_add=True)
    with running_bridge(tmp_path, state) as (state, client):
        payload = _rpc(
            client,
            "appendurl",
            _append_params(content=FAKE_INDEXER_URL),
        )
        assert payload["error"]["code"] == 4
        rendered = json.dumps(payload)
        assert FAKE_INDEXER_URL not in rendered
        assert "indexer-secret" not in rendered
        assert state.add_count == 1
        assert state.add_calls[0]["mode"] == "addurl"


@pytest.mark.parametrize(
    "content",
    [
        "not-base64",
        base64.b64encode(b"<nzb><file>").decode(),
        base64.b64encode(b"<?xml version='1.0'?><notnzb/>").decode(),
        base64.b64encode(b"<?xml version='1.0'?><nzb></nzb>").decode(),
        base64.b64encode(
            b'<?xml version="1.0"?><!DOCTYPE nzb [<!ENTITY xxe SYSTEM '
            b'"file:///etc/passwd">]><nzb><file>&xxe;</file></nzb>'
        ).decode(),
        base64.b64encode(
            b'<?xml version="1.0"?><!DOCTYPE nzb [<!ENTITY x "boom">]><nzb><file>&x;</file></nzb>'
        ).decode(),
    ],
)
def test_append_invalid_nzb_content_is_rejected_without_submission(
    tmp_path: Path, content: str
) -> None:
    state = FakeSabState()
    with running_bridge(tmp_path, state) as (state, client):
        payload = _rpc(client, "append", _append_params(content=content))
        assert payload["error"]["code"] == 2
        assert state.add_calls == []


def test_append_unsupported_options_and_invalid_arguments_are_prewrite_errors(
    tmp_path: Path,
) -> None:
    state = FakeSabState()
    with running_bridge(tmp_path, state) as (state, client):
        cases = [
            _append_params(add_to_top=True),
            _append_params(dupe_key="synthetic-key"),
            _append_params(dupe_score=1),
            _append_params(dupe_mode="FORCE"),
            _append_params(pp_parameters=[{"Name": "*Unpack:", "Value": "yes"}]),
            _append_params(priority=50, add_paused=True),
            _append_params(priority=900, add_paused=True),
            _append_params(filename="Synthetic.Release.zip"),
            _append_params(filename="Synthetic.Release"),
            _append_params(filename="../Synthetic.Release.nzb"),
            _append_params(category="not-configured"),
            _append_params(priority=True),
            _append_params(add_to_top=1),  # type: ignore[arg-type]
            _append_params(add_paused=1),  # type: ignore[arg-type]
            _append_params(dupe_score="0"),  # type: ignore[arg-type]
            _append_params(pp_parameters={"Name": "x"}),  # type: ignore[arg-type]
            _append_params(content="ftp://indexer.invalid/file.nzb"),
            [*_append_params(filename="Synthetic.Release.nzb", content=_VALID_NZB_B64), "extra"],
        ]
        for params in cases:
            payload = _rpc(client, "append", params)
            assert payload["error"]["code"] in {2, 3}
        assert state.add_calls == []
        assert state.add_count == 0


def test_appendurl_requires_http_or_https_url(tmp_path: Path) -> None:
    state = FakeSabState()
    with running_bridge(tmp_path, state) as (state, client):
        for content in (
            _VALID_NZB_B64,
            "ftp://indexer.invalid/file.nzb",
            "file:///tmp/file.nzb",
            "https://",
        ):
            payload = _rpc(
                client,
                "appendurl",
                _append_params(content=content),
            )
            assert payload["error"]["code"] == 2
        assert state.add_calls == []


def test_append_zero_and_multiple_ids_are_explicit(tmp_path: Path) -> None:
    state = FakeSabState(add_result_ids=[])
    with running_bridge(tmp_path, state) as (state, client):
        payload = _rpc(client, "append", _append_params())
        assert payload["error"]["code"] == 4
        assert "accepted" in payload["error"]["message"].lower()
        assert "no job id" in payload["error"]["message"].lower()
        assert state.add_count == 1

    state = FakeSabState(add_result_ids=["nzo_multi_a", "nzo_multi_b"])
    with running_bridge(tmp_path / "multi", state) as (state, client):
        payload = _rpc(client, "append", _append_params())
        assert payload["error"]["code"] == 4
        assert "2 jobs" in payload["error"]["message"]
        assert state.add_count == 1


def test_append_timeout_is_unknown_and_never_retried(tmp_path: Path) -> None:
    state = FakeSabState(add_timeout=0.7)
    with running_bridge(tmp_path, state, read_timeout=0.2) as (state, client):
        payload = _rpc(client, "append", _append_params())
        assert payload["error"]["code"] == 4
        assert "outcome unknown" in payload["error"]["message"]
        assert state.add_count == 1
        assert len(state.jobs) == 1
        groups = _rpc(client, "listgroups", [], 10)["result"]
        assert len(groups) == 1
        assert int(groups[0]["NZBID"]) > 0


def test_append_malformed_sab_response_is_unknown_and_never_retried(tmp_path: Path) -> None:
    state = FakeSabState(add_malformed=True)
    with running_bridge(tmp_path, state) as (state, client):
        payload = _rpc(client, "append", _append_params())
        assert payload["error"]["code"] == 4
        assert "outcome unknown" in payload["error"]["message"]
        assert state.add_count == 1
        assert len(state.jobs) == 1


def test_append_idmap_failure_after_acceptance_is_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = FakeSabState()

    def fail_allocation(_self: IdMap, *_args: object, **_kwargs: object) -> list[int]:
        raise IdMapUnavailable("synthetic allocation failure")

    monkeypatch.setattr(IdMap, "allocate_many", fail_allocation)
    with running_bridge(tmp_path, state) as (state, client):
        payload = _rpc(client, "append", _append_params())
        assert payload["error"]["code"] == 4
        assert "accepted" in payload["error"]["message"].lower()
        assert "persist" in payload["error"]["message"].lower()
        assert state.add_count == 1
        assert len(state.jobs) == 1


def test_append_decoded_size_and_body_limits(tmp_path: Path) -> None:
    state = FakeSabState()
    with running_bridge(
        tmp_path / "decoded",
        state,
        request_max_bytes=128 * 1024,
        nzb_max_decoded_bytes=100,
    ) as (state, client):
        payload = _rpc(client, "append", _append_params())
        assert payload["error"]["code"] == 2
        assert "size limit" in payload["error"]["message"].lower()
        assert state.add_calls == []

    state = FakeSabState()
    headers = basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD)
    with running_bridge(
        tmp_path / "body",
        state,
        request_max_bytes=300,
        nzb_max_decoded_bytes=100,
    ) as (state, client):
        response = client.post(
            "/jsonrpc/",
            headers=headers,
            json={
                "method": "append",
                "params": _append_params(),
                "id": 1,
            },
        )
        assert response.status_code == 413
        assert state.add_count == 0


def test_append_unsafe_get_and_authentication_do_not_submit(tmp_path: Path) -> None:
    state = FakeSabState()
    with running_bridge(tmp_path, state) as (state, client):
        headers = basic_auth_header(FAKE_BRIDGE_USER, FAKE_BRIDGE_PASSWORD)
        assert client.get("/jsonrpc/append", headers=headers).status_code == 405
        assert client.get("/jsonrpc/appendurl", headers=headers).status_code == 405
        unauthenticated = client.post(
            "/jsonrpc/",
            json={"method": "append", "params": _append_params(), "id": 1},
        )
        assert unauthenticated.status_code == 401
        assert state.add_count == 0
